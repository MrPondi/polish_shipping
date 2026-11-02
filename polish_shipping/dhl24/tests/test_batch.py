import json
from io import BytesIO
from unittest.mock import patch

import frappe
from frappe.utils import add_days, today
from pypdf import PdfReader

from polish_shipping.dhl24.batch import (
	book,
	book_shipments,
	download_labels,
	download_pnp,
	get_booking_options,
)
from polish_shipping.dhl24.client import DHL24Error
from polish_shipping.dhl24.tests.utils import DHL24TestCase, make_address, make_shipment, set_settings

COURIER = {"from": "10:00", "to": "14:00", "note": "Szkło"}


class TestBookShipments(DHL24TestCase):
	def setUp(self):
		super().setUp()
		# The job commits after each booking and rolls back after each failure, which would end the test's
		# transaction
		for method in ("commit", "rollback"):
			patcher = patch.object(frappe.local.db, method)
			patcher.start()
			self.addCleanup(patcher.stop)

		self.publish_realtime = self.patch("frappe.publish_realtime")
		self.patch("frappe.publish_progress")
		self.day = add_days(today(), 2)
		self.warehouse = make_address("_Test DHL24 Warehouse")

	def patch(self, target: str):
		patcher = patch(target)
		self.addCleanup(patcher.stop)
		return patcher.start()

	def get_summary(self) -> dict:
		event, summary = self.publish_realtime.call_args.args
		self.assertEqual(event, "dhl24_booking_done")
		return summary

	def test_shipments_are_booked_for_the_day_with_a_courier(self):
		to_book = make_shipment(pickup_address_name=self.warehouse, pickup_date=today())
		booked_before = make_shipment(
			service_provider="DHL24",
			shipment_id="90000000009",
			status="Booked",
			pickup_address_name=self.warehouse,
		)
		draft = make_shipment(docstatus=0)
		booked_elsewhere = make_shipment(service_provider="SendCloud", shipment_id="123", status="Booked")

		book_shipments(
			[to_book.name, booked_before.name, draft.name, booked_elsewhere.name], "AH", self.day, COURIER
		)

		self.assertEqual(
			self.get_summary(),
			{
				"booked": [to_book.name],
				"failed": [
					{"shipment": draft.name, "error": "It isn't submitted."},
					{"shipment": booked_elsewhere.name, "error": "It is booked with SendCloud."},
				],
				"couriers": [{"booking": "ORDER-1", "shipments": [to_book.name, booked_before.name]}],
				"courier_errors": [],
				"labels": [to_book.name, booked_before.name],
				"pickup_date": self.day,
			},
		)

		[sent] = self.client.create_shipments.call_args.args[0]
		self.assertEqual(sent["shipmentDate"], self.day)
		self.client.book_courier.assert_called_once_with(
			self.day, "10:00", "14:00", ["90000000001", "90000000009"], "Szkło"
		)

		to_book.reload()
		self.assertEqual(
			(to_book.service_provider, to_book.shipment_id, to_book.carrier_service, to_book.status),
			("DHL24", "90000000001", "Domestic", "Booked"),
		)
		self.assertEqual(
			(str(to_book.pickup_date), str(to_book.pickup_from), to_book.courier_booking),
			(self.day, "10:00:00", "ORDER-1"),
		)
		self.assertEqual(to_book.shipment_amount, 22.0)

	def test_one_courier_per_pickup_address(self):
		from_warehouse = make_shipment(pickup_address_name=self.warehouse)
		from_shop = make_shipment(pickup_address_name=make_address("_Test DHL24 Shop"))
		self.client.create_shipments.side_effect = [["90000000001"], ["90000000002"]]
		self.client.book_courier.side_effect = [["ORDER-1"], ["ORDER-2"]]

		book_shipments([from_warehouse.name, from_shop.name], "AH", self.day, COURIER)

		self.assertEqual(
			self.get_summary()["couriers"],
			[
				{"booking": "ORDER-1", "shipments": [from_warehouse.name]},
				{"booking": "ORDER-2", "shipments": [from_shop.name]},
			],
		)

	def test_no_courier_unless_asked(self):
		shipment = make_shipment()

		book_shipments([shipment.name], "AH", self.day)

		self.client.book_courier.assert_not_called()
		self.assertEqual(self.get_summary()["couriers"], [])
		self.assertIsNone(frappe.db.get_value("Shipment", shipment.name, "courier_booking"))

	def test_courier_dhl24_refuses(self):
		shipment = make_shipment()
		self.client.book_courier.side_effect = DHL24Error("Kurier niedostępny w tych godzinach")

		book_shipments([shipment.name], "AH", self.day, COURIER)

		summary = self.get_summary()
		self.assertEqual(summary["booked"], [shipment.name])
		self.assertEqual(
			summary["courier_errors"],
			[{"shipments": [shipment.name], "error": "Kurier niedostępny w tych godzinach"}],
		)

	def test_a_shipment_dhl24_refuses_doesnt_stop_the_others(self):
		refused, booked = make_shipment(), make_shipment()
		self.client.create_shipments.side_effect = [DHL24Error("Nieprawidłowy kod pocztowy"), ["90000000002"]]

		book_shipments([refused.name, booked.name], "AH", self.day)

		summary = self.get_summary()
		self.assertEqual(
			summary["failed"], [{"shipment": refused.name, "error": "Nieprawidłowy kod pocztowy"}]
		)
		self.assertEqual((summary["booked"], summary["labels"]), ([booked.name], [booked.name]))

	def test_unexpected_error_is_logged(self):
		shipment = make_shipment()
		self.client.create_shipments.side_effect = ConnectionError("DHL24 is down")

		with patch.object(frappe, "log_error", return_value=frappe._dict(name="_Test Log")) as log_error:
			book_shipments([shipment.name], "AH", self.day, COURIER)

		log_error.assert_called_once()
		summary = self.get_summary()
		self.assertIn("_Test Log", summary["failed"][0]["error"])
		self.assertEqual(summary["labels"], [])
		self.client.book_courier.assert_not_called()


class TestBookRequest(DHL24TestCase):
	def setUp(self):
		super().setUp()
		self.shipments = json.dumps([make_shipment().name])
		self.tomorrow = add_days(today(), 1)

	def test_job_is_queued_once_per_user(self):
		with patch("frappe.enqueue") as enqueue:
			book(self.shipments, "AH", self.tomorrow, 1, "10:00:00", "14:00", "Szkło")

			kwargs = enqueue.call_args.kwargs
			self.assertEqual(kwargs["job_id"], f"dhl24-booking-{frappe.session.user}")
			self.assertTrue(kwargs["deduplicate"])
			self.assertEqual(
				(kwargs["shipments"], kwargs["product"], kwargs["pickup_date"], kwargs["courier"]),
				(json.loads(self.shipments), "AH", self.tomorrow, COURIER),
			)

			book(self.shipments, "AH", self.tomorrow, 0)
			self.assertIsNone(enqueue.call_args.kwargs["courier"])

			enqueue.return_value = None
			self.assertRaises(frappe.ValidationError, book, self.shipments, "AH", self.tomorrow, 0)

	def test_what_is_checked_before_booking(self):
		cases = {
			"service not offered": (self.shipments, "09", self.tomorrow, 0),
			"no Shipments": (json.dumps([]), "AH", self.tomorrow, 0),
			"past day": (self.shipments, "AH", add_days(today(), -1), 0),
			"courier without hours": (self.shipments, "AH", self.tomorrow, 1),
		}
		with patch("frappe.enqueue") as enqueue:
			for case, args in cases.items():
				with self.subTest(case=case):
					self.assertRaises(frappe.ValidationError, book, *args)

			set_settings(enabled=0)
			self.assertRaises(frappe.ValidationError, book, self.shipments, "AH", self.tomorrow, 0)
		enqueue.assert_not_called()

	def test_booking_options(self):
		set_settings(product_12=1, default_product="12")
		self.assertEqual(
			get_booking_options(),
			{
				"products": [{"value": "AH", "label": "Domestic"}, {"value": "12", "label": "Domestic 12"}],
				"default": "12",
			},
		)


class TestPrinting(DHL24TestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(
			lambda: [frappe.local.response.pop(key, None) for key in ("filename", "filecontent", "type")]
		)

	def test_labels_in_one_pdf(self):
		shipments = [
			make_shipment(service_provider="DHL24", shipment_id=f"9000000000{number}", status="Booked")
			for number in range(1, 5)
		]

		download_labels(json.dumps([shipment.name for shipment in shipments]))

		self.client.get_labels.assert_called_once_with(
			["90000000001", "90000000002", "90000000003", "90000000004"], "BLP"
		)
		self.assertEqual(frappe.local.response.type, "pdf")
		self.assertEqual(len(PdfReader(BytesIO(frappe.local.response.filecontent)).pages), 4)

	def test_labels_of_shipments_dhl24_didnt_book(self):
		for shipment in (make_shipment(), make_shipment(service_provider="SendCloud", shipment_id="123")):
			with self.subTest(shipment=shipment.name):
				self.assertRaises(frappe.ValidationError, download_labels, json.dumps([shipment.name]))
		self.client.get_labels.assert_not_called()

	def test_pnp(self):
		download_pnp("2026-11-03")

		self.client.get_pnp.assert_called_once_with("2026-11-03")
		self.assertEqual(frappe.local.response.type, "pdf")
		self.assertEqual(frappe.local.response.filename, "DHL24 PNP 2026-11-03.pdf")
