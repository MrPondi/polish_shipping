import frappe
from frappe.utils import add_days, today

from polish_shipping.dhl24.client import DHL24Error
from polish_shipping.dhl24.courier import book_courier
from polish_shipping.dhl24.tests.utils import DHL24TestCase, make_address, make_shipment


class TestBookCourier(DHL24TestCase):
	def setUp(self):
		super().setUp()
		self.pickup_address = make_address("_Test DHL24 Warehouse")
		self.tomorrow = add_days(today(), 1)

	def make_booked_shipment(self, shipment_id: str, **values):
		return make_shipment(
			service_provider="DHL24",
			shipment_id=shipment_id,
			status="Booked",
			pickup_address_name=self.pickup_address,
			**values,
		)

	def test_one_courier_for_shipments_from_one_address(self):
		shipments = [self.make_booked_shipment("1"), self.make_booked_shipment("2")]

		booking = book_courier(
			[shipment.name for shipment in shipments], self.tomorrow, "10:00:00", "14:00", "Szkło"
		)

		self.assertEqual(booking, "ORDER-1")
		self.client.book_courier.assert_called_once_with(self.tomorrow, "10:00", "14:00", ["1", "2"], "Szkło")
		for shipment in shipments:
			values = frappe.db.get_value(
				"Shipment", shipment.name, ["courier_booking", "pickup_date", "pickup_from", "pickup_to"]
			)
			self.assertEqual(
				(values[0], str(values[1]), str(values[2]), str(values[3])),
				("ORDER-1", self.tomorrow, "10:00:00", "14:00:00"),
			)

	def test_note_is_cut_to_fit(self):
		book_courier([self.make_booked_shipment("1").name], self.tomorrow, "10:00", "14:00", "x" * 60)
		self.assertEqual(self.client.book_courier.call_args.args[4], "x" * 50)

	def test_shipments_a_courier_cant_be_booked_for(self):
		cases = {
			"not booked": make_shipment(pickup_address_name=self.pickup_address),
			"draft": self.make_booked_shipment("1", docstatus=0),
			"booked with another provider": make_shipment(
				service_provider="SendCloud", shipment_id="2", pickup_address_name=self.pickup_address
			),
			"courier booked already": self.make_booked_shipment("3", courier_booking="ORDER-0"),
		}
		for case, shipment in cases.items():
			with self.subTest(case=case):
				self.assertRaises(
					frappe.ValidationError, book_courier, [shipment.name], self.tomorrow, "10:00", "14:00"
				)
		self.client.book_courier.assert_not_called()

	def test_shipments_from_more_than_one_address(self):
		shipments = [self.make_booked_shipment("1"), make_shipment(service_provider="DHL24", shipment_id="2")]
		self.assertRaises(
			frappe.ValidationError,
			book_courier,
			[shipment.name for shipment in shipments],
			self.tomorrow,
			"10:00",
			"14:00",
		)

	def test_times(self):
		name = self.make_booked_shipment("1").name
		self.assertRaises(
			frappe.ValidationError, book_courier, [name], add_days(today(), -1), "10:00", "14:00"
		)
		self.assertRaises(frappe.ValidationError, book_courier, [name], self.tomorrow, "14:00", "10:00")
		self.assertRaises(frappe.ValidationError, book_courier, [name], self.tomorrow, "10:00", None)
		self.assertRaises(frappe.ValidationError, book_courier, [], self.tomorrow, "10:00", "14:00")

	def test_booking_dhl24_refuses_throws(self):
		shipment = self.make_booked_shipment("1")
		self.client.book_courier.side_effect = DHL24Error("Kurier niedostępny w tych godzinach")

		self.assertRaises(
			frappe.ValidationError, book_courier, [shipment.name], self.tomorrow, "10:00", "14:00"
		)
		self.assertIsNone(frappe.db.get_value("Shipment", shipment.name, "courier_booking"))
