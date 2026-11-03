# Copyright (c) 2026, Karol Parzonka and contributors
# For license information, please see license.txt

import re

import frappe
from erpnext_shipping.erpnext_shipping.providers import get_provider_paths
from frappe import _
from frappe.model.document import Document
from frappe.query_builder.functions import Sum
from frappe.utils import cint, evaluate_filters, today

from polish_shipping.dhl24.provider import get_dhl24_provider

# The fields of the Shipments that the rows are made of
SHIPMENT_FIELDS = [
	"name",
	"owner",
	"creation",
	"modified",
	"modified_by",
	"status",
	"delivery_to",
	"delivery_address_name",
	"pickup_address_name",
	"pickup_date",
	"pickup_type",
	"value_of_goods",
	"total_weight",
	"description_of_content",
	"carrier_service",
	"shipment_id",
	"courier_booking",
]


# The field and order at the end of an ORDER BY term
ORDER_BY = re.compile(r"`?(?P<field>\w+)`?\s*(?P<order>asc|desc)?\s*$", re.IGNORECASE)


class DHL24Shipment(Document):
	"""
	A Shipment as DHL24 books it: one that waits to be booked, or one booked with DHL24 that is to be picked
	up yet. There are no records of these: they are read from the Shipments, which hold all there is.
	"""

	def load_from_db(self):
		rows = get_rows(name=self.name)
		if not rows:
			raise frappe.DoesNotExistError(_("{0} {1} not found").format(_(self.doctype), self.name))
		self.init_from_dict(rows[0])

	def db_insert(self, *args, **kwargs):
		throw_read_only()

	def db_update(self, *args, **kwargs):
		throw_read_only()

	def delete(self, *args, **kwargs):
		throw_read_only()

	@staticmethod
	def get_list(filters=None, start=0, page_length=20, order_by=None):
		rows = get_filtered_rows(filters)
		sort_rows(rows, order_by)
		start, page_length = cint(start), cint(page_length)
		return rows[start : start + page_length] if page_length else rows[start:]

	@staticmethod
	def get_count(filters=None):
		return len(get_filtered_rows(filters))

	@staticmethod
	def get_stats(**kwargs):
		return {}


def get_rows(name: str | None = None) -> list[frappe._dict]:
	"""
	The Shipments that wait to be booked, and those booked with DHL24 that are to be picked up yet, as rows.
	Only the Shipments the user may read are among them.
	"""
	by_name = {"name": name} if name else {}
	shipments = frappe.get_list(
		"Shipment",
		filters={"docstatus": 1, "status": "Submitted", **by_name},
		fields=SHIPMENT_FIELDS,
		limit_page_length=0,
	)

	if providers := [name for name in get_provider_paths() if get_dhl24_provider(name)]:
		shipments += frappe.get_list(
			"Shipment",
			filters={
				"docstatus": 1,
				"status": "Booked",
				"service_provider": ["in", providers],
				"pickup_date": [">=", today()],
				**by_name,
			},
			fields=SHIPMENT_FIELDS,
			limit_page_length=0,
		)

	if not shipments:
		return []

	cities = dict(
		frappe.get_all(
			"Address",
			filters={"name": ["in", [shipment.delivery_address_name for shipment in shipments]]},
			fields=["name", "city"],
			as_list=True,
		)
	)
	parcel = frappe.qb.DocType("Shipment Parcel")
	parcels = dict(
		frappe.qb.from_(parcel)
		.select(parcel.parent, Sum(parcel.count))
		.where(parcel.parent.isin([shipment.name for shipment in shipments]))
		.groupby(parcel.parent)
		.run()
	)

	return [
		frappe._dict(
			# Which list filters read the fields of
			doctype="DHL24 Shipment",
			name=shipment.name,
			owner=shipment.owner,
			creation=shipment.creation,
			modified=shipment.modified,
			modified_by=shipment.modified_by,
			state=get_state(shipment),
			delivery_to=shipment.delivery_to,
			city=cities.get(shipment.delivery_address_name),
			# As text, which list filters compare dates as
			pickup_date=str(shipment.pickup_date) if shipment.pickup_date else None,
			parcels=cint(parcels.get(shipment.name)),
			total_weight=shipment.total_weight,
			value_of_goods=shipment.value_of_goods,
			description_of_content=shipment.description_of_content,
			carrier_service=shipment.carrier_service,
			shipment_id=shipment.shipment_id,
			courier_booking=shipment.courier_booking,
			pickup_address_name=shipment.pickup_address_name,
			pickup_type=shipment.pickup_type,
		)
		for shipment in shipments
	]


def get_state(shipment) -> str:
	if shipment.status == "Submitted":
		return "To Book"
	return "Courier Booked" if shipment.courier_booking else "Booked"


def get_filtered_rows(filters) -> list[frappe._dict]:
	return [row for row in get_rows() if evaluate_filters(row, filters or [])]


def sort_rows(rows: list[frappe._dict], order_by: str | None):
	"""Sort the rows as the list asks, by the first field it sorts by, then by name"""
	field, descending = "pickup_date", False
	# Such as "`tabDHL24 Shipment`.`pickup_date` desc"
	if order_by and (match := ORDER_BY.search(order_by.split(",")[0])):
		field, descending = match["field"], (match["order"] or "").lower() == "desc"

	rows.sort(key=lambda row: row.name, reverse=descending)
	rows.sort(key=lambda row: (row.get(field) is not None, row.get(field)), reverse=descending)


def throw_read_only():
	frappe.throw(_("DHL24 Shipments are read from Shipments. Change the Shipment instead."))
