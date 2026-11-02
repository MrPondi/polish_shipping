from types import SimpleNamespace
from unittest.mock import patch

import frappe

from polish_shipping.dhl24.client import DHL24Error
from polish_shipping.dhl24.events import delete_cancelled_shipment
from polish_shipping.dhl24.tests.utils import DHL24TestCase, make_shipment


def make_booked_shipment(shipment_id: str, **values):
	return make_shipment(service_provider="DHL24", shipment_id=shipment_id, status="Booked", **values)


class TestDeleteCancelledShipment(DHL24TestCase):
	def test_registered_for_cancelled_shipments(self):
		self.assertIn(
			"polish_shipping.dhl24.events.delete_cancelled_shipment",
			frappe.get_hooks("doc_events")["Shipment"]["on_cancel"],
		)

	def test_shipment_is_deleted(self):
		delete_cancelled_shipment(make_booked_shipment("90000000001"))

		self.client.delete_shipments.assert_called_once_with(["90000000001"])
		self.client.cancel_courier_booking.assert_not_called()

	def test_courier_is_cancelled_first_and_other_shipments_lose_it(self):
		cancelled = make_booked_shipment("1", courier_booking="ORDER-1")
		same_courier = make_booked_shipment("2", courier_booking="ORDER-1")
		other_courier = make_booked_shipment("3", courier_booking="ORDER-2")

		delete_cancelled_shipment(cancelled)

		self.client.cancel_courier_booking.assert_called_once_with(["ORDER-1"])
		self.client.delete_shipments.assert_called_once_with(["1"])
		self.assertIsNone(frappe.db.get_value("Shipment", same_courier.name, "courier_booking"))
		self.assertEqual(frappe.db.get_value("Shipment", other_courier.name, "courier_booking"), "ORDER-2")
		# For the record
		self.assertEqual(frappe.db.get_value("Shipment", cancelled.name, "courier_booking"), "ORDER-1")

	def test_shipment_stays_when_dhl24_keeps_the_courier(self):
		self.client.cancel_courier_booking.side_effect = None
		self.client.cancel_courier_booking.return_value = [
			SimpleNamespace(id="ORDER-1", result=False, error="Kurier w drodze")
		]
		cancelled = make_booked_shipment("1", courier_booking="ORDER-1")
		same_courier = make_booked_shipment("2", courier_booking="ORDER-1")

		delete_cancelled_shipment(cancelled)

		self.client.delete_shipments.assert_not_called()
		self.assertEqual(frappe.db.get_value("Shipment", same_courier.name, "courier_booking"), "ORDER-1")

	def test_errors_dont_stop_the_cancel(self):
		shipment = make_booked_shipment("90000000001")

		self.client.delete_shipments.side_effect = DHL24Error("Przesyłka w drodze")
		delete_cancelled_shipment(shipment)

		self.client.delete_shipments.side_effect = ConnectionError("DHL24 is down")
		with patch.object(frappe, "log_error") as log_error:
			delete_cancelled_shipment(shipment)
		log_error.assert_called_once()

	def test_shipments_dhl24_didnt_book_are_left_alone(self):
		delete_cancelled_shipment(make_shipment(service_provider="SendCloud", shipment_id="1"))
		delete_cancelled_shipment(make_shipment())

		self.client.delete_shipments.assert_not_called()
