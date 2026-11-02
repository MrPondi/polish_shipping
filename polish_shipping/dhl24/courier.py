"""Couriers DHL24 sends for the parcels of Shipments booked with it"""

from datetime import date

import frappe
from frappe import _
from frappe.utils import formatdate, get_time, getdate, today

from polish_shipping.dhl24.provider import DHL24_ERRORS, get_dhl24_provider
from polish_shipping.dhl24.request import truncate

# What DHL24 takes at most for the note to the courier
NOTE_LENGTH = 50


@frappe.whitelist(methods=["POST"])
def book_courier(shipments, pickup_date, pickup_from, pickup_to, additional_info=None):
	"""Book one courier for Shipments booked with DHL24 that are sent from one address"""
	shipments = frappe.parse_json(shipments)
	if not shipments:
		frappe.throw(_("Select the Shipments to book a courier for."))

	docs = []
	for name in shipments:
		frappe.has_permission("Shipment", "write", doc=name, throw=True)
		docs.append(get_shipment(name))

	if len({doc.pickup_address_name for doc in docs}) > 1:
		frappe.throw(_("A courier picks parcels up at one address, and these Shipments have more than one."))

	day = check_pickup_date(pickup_date)
	time_from, time_to = check_pickup_time(pickup_from, pickup_to)
	return book_courier_for(docs, day, time_from, time_to, additional_info)


def book_courier_for(docs: list, day: date, time_from: str, time_to: str, note: str | None = None) -> str:
	"""
	Book one courier for Shipments booked with DHL24 that are sent from one address. The booking's number, day
	and hours are kept on the Shipments.
	"""
	provider = get_dhl24_provider(docs[0].service_provider)
	try:
		order_ids = provider.get_client().book_courier(
			day.isoformat(),
			time_from,
			time_to,
			[doc.shipment_id for doc in docs],
			truncate(note, NOTE_LENGTH) or None,
		)
	except DHL24_ERRORS as e:
		frappe.throw(e.message, title=_("DHL24"))

	if not order_ids:
		frappe.throw(_("DHL24 booked no courier, and gave no reason."), title=_("DHL24"))

	booking = ", ".join(order_ids)
	for doc in docs:
		doc.db_set(
			{"courier_booking": booking, "pickup_date": day, "pickup_from": time_from, "pickup_to": time_to}
		)
	return booking


def get_shipment(name: str):
	"""A Shipment to book a courier for"""
	doc = frappe.get_doc("Shipment", name)
	if doc.docstatus != 1 or not doc.shipment_id or not get_dhl24_provider(doc.service_provider):
		frappe.throw(_("Shipment {0} isn't booked with DHL24.").format(name))
	if doc.courier_booking:
		frappe.throw(
			_("A courier is booked for Shipment {0} already: {1}.").format(name, doc.courier_booking)
		)
	return doc


def check_pickup_date(pickup_date) -> date:
	"""The day parcels are sent, which can't be in the past"""
	day = getdate(pickup_date)
	if day < getdate(today()):
		frappe.throw(_("The pickup date, {0}, is in the past.").format(formatdate(day)))
	return day


def check_pickup_time(pickup_from, pickup_to) -> tuple[str, str]:
	"""The hours a courier can come, as DHL24 takes them"""
	if not (pickup_from and pickup_to):
		frappe.throw(_("Say from when the parcels are ready, and until when the courier can come."))

	time_from, time_to = format_time(pickup_from), format_time(pickup_to)
	if time_from >= time_to:
		frappe.throw(_("The parcels should be ready before the courier's time ends."))
	return time_from, time_to


def format_time(value) -> str:
	"""A time as DHL24 takes it, such as 09:00"""
	return get_time(value).strftime("%H:%M")
