"""
Booking Shipments with DHL24 in batches from the DHL24 Shipment list, as an operator confirms them on the DHL24
site, and printing their labels and DHL24's protocol of the day in one PDF each
"""

import json
from io import BytesIO

import frappe
from erpnext_shipping.erpnext_shipping.providers import ShipmentContext
from erpnext_shipping.erpnext_shipping.shipping import create_shipment, update_tracking
from frappe import _
from frappe.utils import cint, getdate, strip_html, today
from frappe.utils.data import get_link_to_form
from pypdf import PdfWriter

from polish_shipping.dhl24.courier import book_courier_for, check_pickup_date, check_pickup_time
from polish_shipping.dhl24.provider import DHL24_ERRORS, PROVIDER, get_dhl24_provider
from polish_shipping.dhl24.request import PRODUCTS

# The realtime event the booking job ends with, which the DHL24 Shipment list waits for
BOOKING_DONE = "dhl24_booking_done"


@frappe.whitelist()
def get_booking_options() -> dict:
	"""The DHL24 services to book Shipments with, and the default one"""
	frappe.has_permission("Shipment", "write", throw=True)
	settings = get_enabled_settings()
	return {
		"products": [{"value": product, "label": PRODUCTS[product]} for product in settings.get_products()],
		"default": settings.default_product,
	}


@frappe.whitelist(methods=["POST"])
def book(
	shipments: list[str] | str,
	product: str,
	pickup_date: str,
	book_courier: int = 1,
	pickup_from: str | None = None,
	pickup_to: str | None = None,
	note: str | None = None,
):
	"""
	Book the Shipments that aren't booked yet with a DHL24 product, to be sent on `pickup_date`. With
	`book_courier`, a courier comes for them and for the others without one. It is done in a background job,
	which ends with the BOOKING_DONE event.
	"""
	shipments = frappe.parse_json(shipments)
	if not shipments:
		frappe.throw(_("Select the Shipments to book."))
	for name in shipments:
		frappe.has_permission("Shipment", "write", doc=name, throw=True)

	if product not in get_enabled_settings().get_products():
		frappe.throw(_("{0} isn't one of the services of DHL24 Settings.").format(product))

	day = check_pickup_date(pickup_date)
	courier = None
	if cint(book_courier):
		time_from, time_to = check_pickup_time(pickup_from, pickup_to)
		courier = {"from": time_from, "to": time_to, "note": note}

	job = frappe.enqueue(
		"polish_shipping.dhl24.batch.book_shipments",
		queue="long",
		job_id=f"dhl24-booking-{frappe.session.user}",
		deduplicate=True,
		shipments=shipments,
		product=product,
		pickup_date=day.isoformat(),
		courier=courier,
	)
	if not job:
		frappe.throw(_("The Shipments you picked before are still being booked. Try again once they are."))


def book_shipments(shipments: list[str], product: str, pickup_date: str, courier: dict | None = None):
	"""The job of `book`"""
	summary = {
		"booked": [],
		"failed": [],
		"couriers": [],
		"courier_errors": [],
		"labels": [],
		"pickup_date": pickup_date,
	}
	steps = len(shipments) + 1

	for step, name in enumerate(shipments, start=1):
		frappe.publish_progress(
			step * 100 / steps, title=_("DHL24"), description=_("Booking Shipment {0}").format(name)
		)
		try:
			if book_shipment(name, product, pickup_date):
				summary["booked"].append(name)
			summary["labels"].append(name)
		except Exception as e:
			frappe.db.rollback()
			summary["failed"].append({"shipment": name, "error": get_error(e, name)})

	if courier and summary["labels"]:
		frappe.publish_progress(
			len(shipments) * 100 / steps, title=_("DHL24"), description=_("Booking the courier")
		)
		for docs in get_courier_groups(summary["labels"]):
			names = [doc.name for doc in docs]
			try:
				booking = book_courier_for(
					docs, getdate(pickup_date), courier["from"], courier["to"], courier.get("note")
				)
				frappe.db.commit()
				summary["couriers"].append({"booking": booking, "shipments": names})
			except Exception as e:
				frappe.db.rollback()
				summary["courier_errors"].append({"shipments": names, "error": get_error(e)})

	# Committed before BOOKING_DONE, on which the list reloads
	frappe.db.commit()  # nosemgrep: frappe-manual-commit
	frappe.publish_progress(100, title=_("DHL24"))
	frappe.publish_realtime(BOOKING_DONE, summary, user=frappe.session.user)


def book_shipment(name: str, product: str, pickup_date: str) -> bool:
	"""
	Book a Shipment with a DHL24 product, to be sent on a day. Returns False when it was booked with DHL24
	already, and throws when it can't be booked.
	"""
	doc = frappe.get_doc("Shipment", name, for_update=True)
	if doc.docstatus != 1:
		frappe.throw(_("It isn't submitted."))
	if doc.shipment_id:
		if get_dhl24_provider(doc.service_provider):
			return False
		frappe.throw(_("It is booked with {0}.").format(doc.service_provider))

	args = {**get_shipment_args(doc), "pickup_date": pickup_date}
	rate = get_dhl24_provider(PROVIDER).get_rate(ShipmentContext(**args, shipment=name), product)
	args["shipment_parcel"] = args.pop("parcels")
	create_shipment(
		shipment=name,
		service_data=json.dumps({**rate, "service_provider": PROVIDER}),
		delivery_notes=[row.delivery_note for row in doc.shipment_delivery_note],
		**args,
	)
	# The day DHL24 has the shipment sent on
	frappe.db.set_value("Shipment", name, "pickup_date", pickup_date)
	# DHL24 has the shipment now, whatever happens next
	frappe.db.commit()  # nosemgrep: frappe-manual-commit

	try:
		update_tracking(name)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title=f"Tracking of Shipment {name} couldn't be updated")
	return True


def get_courier_groups(shipments: list[str]) -> list[list]:
	"""The Shipments without a courier, by the address a courier would pick them up at"""
	groups = {}
	for name in shipments:
		doc = frappe.get_doc("Shipment", name)
		if not doc.courier_booking:
			groups.setdefault(doc.pickup_address_name, []).append(doc)
	return list(groups.values())


def get_shipment_args(doc) -> dict:
	"""What the Shipment form sends to erpnext_shipping about a Shipment"""
	return {
		"pickup_from_type": doc.pickup_from_type,
		"delivery_to_type": doc.delivery_to_type,
		"pickup_address_name": doc.pickup_address_name,
		"delivery_address_name": doc.delivery_address_name,
		"parcels": [
			{field: row.get(field) for field in ("length", "width", "height", "weight", "count")}
			for row in doc.shipment_parcel
		],
		"description_of_content": doc.description_of_content,
		"pickup_date": str(doc.pickup_date),
		"value_of_goods": doc.value_of_goods,
		# For a pickup from the Company, the contact is a User
		"pickup_contact_name": doc.pickup_contact_person
		if doc.pickup_from_type == "Company"
		else doc.pickup_contact_name,
		"delivery_contact_name": doc.delivery_contact_name,
	}


@frappe.whitelist(methods=["GET"])
def download_labels(shipments: list[str] | str):
	"""The labels of Shipments booked with DHL24, fetched from DHL24, in one PDF"""
	shipment_ids = []
	for name in frappe.parse_json(shipments):
		frappe.has_permission("Shipment", "read", doc=name, throw=True)
		shipment = frappe.db.get_value("Shipment", name, ["shipment_id", "service_provider"], as_dict=True)
		if not (shipment and shipment.shipment_id and get_dhl24_provider(shipment.service_provider)):
			frappe.throw(_("Shipment {0} isn't booked with DHL24.").format(name))
		shipment_ids.append(shipment.shipment_id)

	if not shipment_ids:
		frappe.throw(_("Select the Shipments to print the labels of."))

	labels = get_dhl24_provider(PROVIDER).get_label_files(shipment_ids)
	send_pdf(
		f"DHL24 labels {today()}.pdf", merge_pdfs(labels.get(shipment_id) for shipment_id in shipment_ids)
	)


@frappe.whitelist(methods=["GET"])
def download_pnp(date: str | None = None):
	"""DHL24's protocol of the shipments sent on a day (PNP), which the courier signs"""
	frappe.has_permission("Shipment", "read", throw=True)
	day = getdate(date or today())
	try:
		pnp = get_dhl24_provider(PROVIDER).get_client().get_pnp(day.isoformat())
	except DHL24_ERRORS as e:
		frappe.throw(e.message, title=_("DHL24"))
	send_pdf(f"DHL24 PNP {day.isoformat()}.pdf", pnp)


def merge_pdfs(pdfs) -> bytes:
	writer = PdfWriter()
	for pdf in pdfs:
		if pdf:
			writer.append(BytesIO(pdf))
	if not writer.pages:
		frappe.throw(_("DHL24 gave no labels."), title=_("DHL24"))

	content = BytesIO()
	writer.write(content)
	return content.getvalue()


def send_pdf(filename: str, content: bytes):
	"""Answer the request with a PDF, which the browser shows"""
	frappe.local.response.filename = filename
	frappe.local.response.filecontent = content
	frappe.local.response.type = "pdf"


def get_error(error: Exception, shipment: str | None = None) -> str:
	"""What went wrong, in words for the user. An error no one expected is logged."""
	frappe.clear_messages()
	if isinstance(error, (frappe.ValidationError, frappe.PermissionError, *DHL24_ERRORS)):
		return strip_html(str(error))

	title = f"DHL24 couldn't book Shipment {shipment}" if shipment else "DHL24 couldn't book a courier"
	log = frappe.log_error(title=title)
	return _("An unexpected error, logged as Error Log {0}.").format(log.name)


def get_enabled_settings():
	settings = frappe.get_cached_doc("DHL24 Settings")
	if not settings.enabled:
		frappe.throw(
			_("Enable DHL24 in {0} first.").format(get_link_to_form("DHL24 Settings", "DHL24 Settings"))
		)
	return settings
