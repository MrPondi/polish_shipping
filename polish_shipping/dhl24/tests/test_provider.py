import json
from types import SimpleNamespace

import frappe
from erpnext_shipping.erpnext_shipping.providers import ShipmentContext, get_provider
from erpnext_shipping.erpnext_shipping.shipping import create_shipment, fetch_shipping_rates
from frappe.utils import add_days, today

from polish_shipping.dhl24.batch import get_shipment_args
from polish_shipping.dhl24.client import DHL24Error
from polish_shipping.dhl24.provider import DHL24Provider, get_dhl24_provider, get_sales_orders
from polish_shipping.dhl24.tests.utils import DHL24TestCase, make_pdf, make_shipment, set_settings

PROVIDER_PATH = "polish_shipping.dhl24.provider.DHL24Provider"


def make_context(shipment) -> ShipmentContext:
	"""The shipment as the Shipment form gives it to erpnext_shipping"""
	return ShipmentContext(**get_shipment_args(shipment), shipment=shipment.name)


def event(status: str, timestamp: str, description: str) -> SimpleNamespace:
	return SimpleNamespace(status=status, timestamp=timestamp, description=description, terminal="WA")


class TestRegistry(DHL24TestCase):
	def test_registered_and_enabled_by_its_settings(self):
		provider = get_provider("DHL24")
		self.assertIsInstance(provider, DHL24Provider)
		self.assertTrue(provider.is_enabled(None))

		set_settings(enabled=0)
		self.assertFalse(provider.is_enabled(None))

	def test_provider_of_a_shipment(self):
		self.assertIsInstance(get_dhl24_provider("DHL24"), DHL24Provider)
		self.assertIsNone(get_dhl24_provider("SendCloud"))
		self.assertIsNone(get_dhl24_provider("No such provider"))
		self.assertIsNone(get_dhl24_provider(None))


class TestRates(DHL24TestCase):
	def test_no_rates_on_the_shipment_form(self):
		self.assertEqual(DHL24Provider().get_rates(make_context(make_shipment())), [])
		self.client.get_price.assert_not_called()

	def test_rate_of_a_product(self):
		rate = DHL24Provider().get_rate(make_context(make_shipment()), "AH")

		self.assertEqual(
			rate,
			{
				"carrier": "DHL",
				"service_name": "Domestic",
				"total_price": 22.0,
				"currency": "PLN",
				"service_id": "AH",
			},
		)
		shipment = self.client.get_price.call_args.args[0]
		self.assertEqual(shipment["payment"], {"accountNumber": "1234567", "payerType": "SHIPPER"})
		self.assertEqual(shipment["shipper"]["country"], "PL")
		self.assertEqual(shipment["shipper"]["name"], "_Test DHL24 Pickup")
		self.assertEqual(
			(shipment["shipper"]["street"], shipment["shipper"]["apartmentNumber"]), ("Prosta", "4")
		)
		self.assertEqual(shipment["receiver"]["addressType"], "B")
		self.assertEqual(shipment["service"], {"product": "AH", "insurance": True, "insuranceValue": 1500.0})
		self.assertEqual(shipment["pieceList"]["item"][0]["height"], 11)

	def test_rate_dhl24_refuses_throws(self):
		self.client.get_price.side_effect = DHL24Error("Nieprawidłowy kod pocztowy")
		self.assertRaises(
			frappe.ValidationError, DHL24Provider().get_rate, make_context(make_shipment()), "AH"
		)


class TestBooking(DHL24TestCase):
	def test_shipment_is_booked(self):
		shipment = make_shipment()

		booked = DHL24Provider().create_shipment(
			make_context(shipment), {"service_id": "AH", "total_price": 22}
		)

		self.assertEqual(
			booked,
			{
				"shipment_id": "90000000001",
				"awb_number": "90000000001",
				"carrier": "DHL",
				"carrier_service": "Domestic",
				"shipment_amount": 22.0,
			},
		)
		[sent] = self.client.create_shipments.call_args.args[0]
		self.assertEqual(sent["reference"], shipment.name)
		self.assertEqual(sent["content"], "_Test robot")
		self.assertEqual(sent["shipmentDate"], add_days(today(), 1))
		self.assertEqual(sent["service"], {"product": "AH", "insurance": True, "insuranceValue": 1500.0})
		self.assertEqual(sent["shipper"]["contactPerson"], "Jan _Test Kowalska")
		self.assertEqual(sent["receiver"]["contactPhone"], "600100200")
		self.assertNotIn("country", sent["shipper"])

	def test_booked_shipment_isnt_booked_again(self):
		shipment = make_shipment(service_provider="DHL24", shipment_id="90000000009")
		self.assertRaises(
			frappe.ValidationError,
			DHL24Provider().create_shipment,
			make_context(shipment),
			{"service_id": "AH"},
		)
		self.client.create_shipments.assert_not_called()

	def test_service_dhl24_settings_dont_offer_isnt_booked(self):
		self.assertRaises(
			frappe.ValidationError,
			DHL24Provider().create_shipment,
			make_context(make_shipment()),
			{"service_id": "09"},
		)

	def test_shipment_dhl24_refuses_throws(self):
		self.client.create_shipments.side_effect = DHL24Error("Nieprawidłowy kod pocztowy")
		self.assertRaises(
			frappe.ValidationError,
			DHL24Provider().create_shipment,
			make_context(make_shipment()),
			{"service_id": "AH"},
		)

	def test_comment_from_the_template(self):
		set_settings(comment_template="Zam. {{ doc.name }}")
		shipment = make_shipment()

		DHL24Provider().create_shipment(make_context(shipment), {"service_id": "AH"})

		[sent] = self.client.create_shipments.call_args.args[0]
		self.assertEqual(sent["comment"], f"Zam. {shipment.name}")

	def test_sales_orders_of_the_delivery_notes(self):
		sales_order = frappe.new_doc("Sales Order")
		sales_order.name = "_Test-DHL24-SO"
		sales_order.db_insert()
		delivery_note = frappe.new_doc("Delivery Note")
		delivery_note.name = "_Test-DHL24-DN"
		delivery_note.db_insert()
		item = frappe.new_doc("Delivery Note Item")
		item.update(
			{
				"name": "_Test-DHL24-DNI",
				"parent": delivery_note.name,
				"parenttype": "Delivery Note",
				"parentfield": "items",
				"against_sales_order": sales_order.name,
			}
		)
		item.db_insert()

		shipment = make_shipment(shipment_delivery_note=[{"delivery_note": delivery_note.name}])

		self.assertEqual([order.name for order in get_sales_orders(shipment)], [sales_order.name])


class TestLabelsAndTracking(DHL24TestCase):
	def test_labels(self):
		shipment = make_shipment(service_provider="DHL24", shipment_id="90000000001")

		self.assertEqual(DHL24Provider().get_labels(shipment), [make_pdf()])
		self.client.get_labels.assert_called_once_with(["90000000001"], "BLP")

	def test_tracking_from_the_latest_event(self):
		self.client.get_track_and_trace_info.return_value = SimpleNamespace(
			receivedBy=None,
			events=SimpleNamespace(
				item=[
					event("LK", "2026-11-04 08:00:00", "Przesyłka wydana do doręczenia"),
					event("SORT", "2026-11-03 20:00:00", "Przesyłka w sortowni"),
				]
			),
		)
		shipment = make_shipment(service_provider="DHL24", shipment_id="90000000001")

		self.assertEqual(
			DHL24Provider().get_tracking_data(shipment),
			{
				"awb_number": "90000000001",
				"tracking_url": "https://mojdhl.pl/sprawdz?paczki=90000000001",
				"tracking_status": "In Progress",
				"tracking_status_info": "Przesyłka wydana do doręczenia",
			},
		)

	def test_received_shipment_is_delivered(self):
		self.client.get_track_and_trace_info.return_value = SimpleNamespace(
			receivedBy="Anna Kowalska",
			events=[event("DOR", "2026-11-04 12:00:00", "Przesyłka doręczona")],
		)
		shipment = make_shipment(service_provider="DHL24", shipment_id="90000000001")

		tracking = DHL24Provider().get_tracking_data(shipment)

		self.assertEqual(tracking["tracking_status"], "Delivered")
		self.assertEqual(tracking["tracking_status_info"], "Przesyłka doręczona, received by Anna Kowalska")

	def test_tracking_without_events(self):
		shipment = make_shipment(service_provider="DHL24", shipment_id="90000000001")
		self.assertEqual(
			DHL24Provider().get_tracking_data(shipment),
			{"awb_number": "90000000001", "tracking_url": "https://mojdhl.pl/sprawdz?paczki=90000000001"},
		)


class TestThroughERPNextShipping(DHL24TestCase):
	def test_no_rates_and_booking_as_the_dhl24_shipment_list_books(self):
		shipment = make_shipment()
		args = get_shipment_args(shipment)

		with self.patch_hooks({"shipping_providers": {"DHL24": PROVIDER_PATH}}):
			self.assertEqual(fetch_shipping_rates(**args, shipment=shipment.name), [])

			rate = DHL24Provider().get_rate(make_context(shipment), "AH")
			args["shipment_parcel"] = args.pop("parcels")
			create_shipment(
				shipment=shipment.name, service_data=json.dumps({**rate, "service_provider": "DHL24"}), **args
			)

		shipment.reload()
		self.assertEqual(
			(
				shipment.service_provider,
				shipment.shipment_id,
				shipment.awb_number,
				shipment.carrier,
				shipment.carrier_service,
				shipment.shipment_amount,
				shipment.status,
			),
			("DHL24", "90000000001", "90000000001", "DHL", "Domestic", 22.0, "Booked"),
		)
