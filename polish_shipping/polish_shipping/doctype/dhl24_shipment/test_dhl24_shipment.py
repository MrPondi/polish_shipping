# Copyright (c) 2026, Karol Parzonka and contributors
# See license.txt

import json

import frappe
from frappe.desk import reportview
from frappe.utils import add_days, today

from polish_shipping.dhl24.tests.utils import DHL24TestCase, make_shipment
from polish_shipping.polish_shipping.doctype.dhl24_shipment.dhl24_shipment import DHL24Shipment

# The rows are read from Shipments, so no other records are needed; ERPNext's test data would be committed to
# the site. A Link field added to the doctype has to be listed here.
IGNORE_TEST_RECORD_DEPENDENCIES = ["Address", "Shipment"]


def booked(shipment_id: str, **values):
	return make_shipment(service_provider="DHL24", shipment_id=shipment_id, status="Booked", **values)


class TestDHL24Shipment(DHL24TestCase):
	def get_names(self, *filters, **kwargs) -> list[str]:
		"""The rows of the Shipments of the test, as the list gives them"""
		in_test = ["name", "in", self.names]
		return [row.name for row in DHL24Shipment.get_list(filters=[in_test, *filters], **kwargs)]

	def test_rows_and_their_states(self):
		to_book = make_shipment(
			delivery_to="Anna Kowalska",
			parcels=[
				{"length": 30, "width": 20, "height": 10, "weight": 2, "count": 2},
				{"length": 40, "width": 30, "height": 20, "weight": 5, "count": 1},
			],
		)
		awaiting_courier = booked("1")
		with_courier = booked("2", courier_booking="ORDER-1")
		left_out = [
			make_shipment(docstatus=0),
			make_shipment(docstatus=2),
			make_shipment(service_provider="SendCloud", shipment_id="3", status="Booked"),
			booked("4", pickup_date=add_days(today(), -1)),
		]
		self.names = [shipment.name for shipment in [to_book, awaiting_courier, with_courier, *left_out]]

		rows = {
			row.name: row
			for row in DHL24Shipment.get_list(filters=[["name", "in", self.names]], page_length=0)
		}

		self.assertEqual(
			{name: row.state for name, row in rows.items()},
			{to_book.name: "To Book", awaiting_courier.name: "Booked", with_courier.name: "Courier Booked"},
		)
		row = rows[to_book.name]
		self.assertEqual(
			(row.delivery_to, row.city, row.parcels, row.pickup_date, row.pickup_type),
			("Anna Kowalska", "Warszawa", 3, add_days(today(), 1), "Pickup"),
		)
		self.assertEqual(
			(rows[with_courier.name].shipment_id, rows[with_courier.name].courier_booking), ("2", "ORDER-1")
		)

	def test_filters_sorting_and_paging(self):
		later = make_shipment(pickup_date=add_days(today(), 3))
		sooner = make_shipment(pickup_date=add_days(today(), 1))
		booked_between = booked("1", pickup_date=add_days(today(), 2))
		self.names = [later.name, sooner.name, booked_between.name]

		self.assertEqual(self.get_names(page_length=0), [sooner.name, booked_between.name, later.name])
		self.assertEqual(self.get_names(["state", "=", "To Book"], page_length=0), [sooner.name, later.name])
		self.assertEqual(
			self.get_names(["pickup_date", ">=", add_days(today(), 2)], page_length=0),
			[booked_between.name, later.name],
		)
		self.assertEqual(
			self.get_names(order_by="`tabDHL24 Shipment`.`pickup_date` desc", page_length=0),
			[later.name, booked_between.name, sooner.name],
		)
		self.assertEqual(self.get_names(start=1, page_length=1), [booked_between.name])
		self.assertEqual(
			DHL24Shipment.get_count(filters=[["name", "in", self.names], ["state", "=", "To Book"]]), 2
		)

	def test_through_the_list_of_the_desk(self):
		shipment = make_shipment()
		form_dict = frappe.local.form_dict
		self.addCleanup(setattr, frappe.local, "form_dict", form_dict)
		frappe.local.form_dict = frappe._dict(
			doctype="DHL24 Shipment",
			fields=json.dumps(["name", "state", "city"]),
			filters=json.dumps([["DHL24 Shipment", "name", "=", shipment.name]]),
			order_by="`tabDHL24 Shipment`.`pickup_date` asc",
			start=0,
			page_length=20,
		)

		data = reportview.get()
		row = dict(zip(data["keys"], data["values"][0], strict=False))
		self.assertEqual((row["name"], row["state"], row["city"]), (shipment.name, "To Book", "Warszawa"))
		self.assertEqual(reportview.get_count(), 1)

	def test_row_as_a_document(self):
		shipment = make_shipment()

		row = frappe.get_doc("DHL24 Shipment", shipment.name)

		self.assertEqual((row.state, row.city), ("To Book", "Warszawa"))
		self.assertRaises(frappe.ValidationError, row.save)
		self.assertRaises(
			frappe.DoesNotExistError, frappe.get_doc, "DHL24 Shipment", "_Test No Such Shipment"
		)
