"""
DHL24, the WebAPI of DHL Parcel Poland (https://dhl24.com.pl), as a shipping provider of erpnext_shipping.
"""

import frappe
from erpnext_shipping.erpnext_shipping.providers import (
	ShippingProvider,
	get_provider,
	get_provider_paths,
)
from erpnext_shipping.erpnext_shipping.utils import get_tracking_url
from frappe import _
from frappe.utils import flt

from polish_shipping.dhl24 import request
from polish_shipping.dhl24.client import DHL24Client, DHL24Error, DHL24UnreachableError, get_items
from polish_shipping.dhl24.request import CARRIER, CURRENCY, PRODUCTS

# What DHL24 answered instead of a result, or that it didn't answer, both of which the user is shown
DHL24_ERRORS = (DHL24Error, DHL24UnreachableError)

# The name the provider is registered under in hooks.py, which Shipments store as their service provider
PROVIDER = "DHL24"

# DHL24 publishes no list of its tracking codes. A shipment someone received (receivedBy) is delivered; codes
# join these once DHL24 has been seen answering with them.
DELIVERED_CODES: set[str] = set()
RETURNED_CODES: set[str] = set()

# The length of Shipment.tracking_status_info, a Data field
STATUS_INFO_LENGTH = 140


class DHL24Provider(ShippingProvider):
	settings_doctype = "DHL24 Settings"

	def get_client(self) -> DHL24Client:
		return DHL24Client(self.get_settings())

	def get_rates(self, context):
		"""
		None: Shipments are booked with DHL24 in batches, from the DHL24 Shipment list, as an operator confirms
		them on the DHL24 site.
		"""
		return []

	def get_rate(self, context, product: str) -> dict:
		"""The rate of one product for the shipment: the net price with the fuel surcharge"""
		request.shipment_date(context.pickup_date)
		shipment = self.get_price_request(self.get_settings(), context)
		try:
			return get_rate(self.get_client(), shipment, product, context.value_of_goods)
		except DHL24_ERRORS as e:
			frappe.throw(e.message, title=_("DHL24"))

	def get_price_request(self, settings, context) -> dict:
		return request.price_request(
			payment=request.price_payment(settings),
			shipper=request.address(context.pickup_address, get_pickup_name(context)),
			receiver=request.receiver(request.address(context.delivery_address, get_delivery_name(context))),
			pieces=request.pieces(context.parcels, has_pallets(context)),
		)

	def create_shipment(self, context, service):
		doc = context.doc
		if doc and doc.shipment_id:
			frappe.throw(_("Shipment {0} is booked already, as {1}.").format(doc.name, doc.shipment_id))

		settings = self.get_settings()
		# The service is what the browser sent back, so the product is one to check
		product = service.get("service_id")
		if product not in settings.get_products():
			frappe.throw(_("{0} isn't one of the services of DHL24 Settings.").format(product))

		shipment = request.shipment(
			shipper=self.get_shipper(context),
			receiver=self.get_receiver(context),
			pieces=request.pieces(context.parcels, has_pallets(context)),
			payment=request.payment(settings),
			service=request.service(product, context.value_of_goods),
			shipment_date=request.shipment_date(context.pickup_date),
			content=request.content(context.description_of_content),
			comment=render_comment(settings, doc),
			reference=context.shipment,
		)

		try:
			shipment_ids = self.get_client().create_shipments([shipment])
		except DHL24_ERRORS as e:
			frappe.throw(e.message, title=_("DHL24"))

		if not shipment_ids:
			frappe.throw(_("DHL24 created no shipment, and gave no reason."), title=_("DHL24"))

		return {
			"shipment_id": shipment_ids[0],
			# The shipment number is the number of its waybill
			"awb_number": shipment_ids[0],
			"carrier": CARRIER,
			"carrier_service": PRODUCTS[product],
			"shipment_amount": flt(service.get("total_price")),
		}

	def get_shipper(self, context) -> dict:
		address = request.address(context.pickup_address, get_pickup_name(context))
		contact = context.pickup_contact if context.pickup_contact_name else None
		return {**address, **request.contact(contact, context.pickup_address)}

	def get_receiver(self, context) -> dict:
		address = request.address(context.delivery_address, get_delivery_name(context))
		contact = context.delivery_contact if context.delivery_contact_name else None
		return request.receiver({**address, **request.contact(contact, context.delivery_address)})

	def get_labels(self, shipment):
		return list(self.get_label_files([shipment.shipment_id]).values())

	def get_label_files(self, shipment_ids: list[str]) -> dict[str, bytes]:
		"""The labels of DHL24 shipments by number, of the type DHL24 Settings has"""
		try:
			return self.get_client().get_labels(shipment_ids, self.get_settings().label_type or "BLP")
		except DHL24_ERRORS as e:
			frappe.throw(e.message, title=_("DHL24"))

	def get_tracking_data(self, shipment):
		try:
			info = self.get_client().get_track_and_trace_info(shipment.shipment_id)
		except DHL24_ERRORS as e:
			frappe.throw(e.message, title=_("DHL24"))

		tracking = {
			"awb_number": shipment.shipment_id,
			"tracking_url": get_tracking_url(CARRIER, shipment.shipment_id),
		}
		events = sorted(get_items(info.events), key=lambda event: event.timestamp or "")
		if events or info.receivedBy:
			tracking.update(get_status(events[-1] if events else None, info.receivedBy))
		return tracking


def get_dhl24_provider(service_provider: str | None) -> DHL24Provider | None:
	"""The provider of a Shipment when it is DHL24, under whichever name an app registered it"""
	if not service_provider or service_provider not in get_provider_paths():
		return None
	provider = get_provider(service_provider)
	return provider if isinstance(provider, DHL24Provider) else None


def get_rate(client, shipment: dict, product: str, value_of_goods: float) -> dict:
	"""The service rate of a product for a GetPriceRequest: the net price with the fuel surcharge"""
	price, fuel_surcharge = client.get_price(
		{**shipment, "service": request.service(product, value_of_goods)}
	)
	return {
		"carrier": CARRIER,
		"service_name": PRODUCTS[product],
		"total_price": flt(price * (1 + fuel_surcharge / 100), 2),
		"currency": CURRENCY,
		"service_id": product,
	}


def get_status(event, received_by: str | None) -> dict:
	"""The tracking status of a shipment, from its latest event"""
	code = event.status if event else None
	if received_by or code in DELIVERED_CODES:
		status = "Delivered"
	elif code in RETURNED_CODES:
		status = "Returned"
	else:
		status = "In Progress"

	info = (event.description if event else None) or ""
	if received_by:
		info = (
			_("{0}, received by {1}").format(info, received_by)
			if info
			else _("Received by {0}").format(received_by)
		)
	return {"tracking_status": status, "tracking_status_info": info[:STATUS_INFO_LENGTH]}


def get_pickup_name(context) -> str | None:
	"""The name of the Company, Customer or Supplier the parcels are sent from"""
	doc = context.doc
	if not doc:
		return None

	party_type = doc.pickup_from_type
	party = doc.get(f"pickup_{party_type.lower()}") if party_type else None
	if not party:
		return None

	name_field = {"Company": "company_name", "Customer": "customer_name", "Supplier": "supplier_name"}
	return frappe.db.get_value(party_type, party, name_field[party_type]) or party


def get_delivery_name(context) -> str | None:
	"""The name of the Company, Customer or Supplier the parcels go to"""
	return context.delivery_company_name


def has_pallets(context) -> bool:
	return bool(context.doc and context.doc.pallets == "Yes")


def render_comment(settings, doc) -> str | None:
	"""The comment printed on the waybill, from the template of DHL24 Settings"""
	if not (settings.comment_template and doc):
		return None
	return frappe.render_template(
		settings.comment_template, {"doc": doc, "sales_orders": get_sales_orders(doc)}
	).strip()


def get_sales_orders(doc) -> list:
	"""The Sales Orders of a Shipment's Delivery Notes"""
	delivery_notes = [row.delivery_note for row in doc.shipment_delivery_note if row.delivery_note]
	if not delivery_notes:
		return []

	names = frappe.get_all(
		"Delivery Note Item",
		filters={"parent": ["in", delivery_notes], "against_sales_order": ["is", "set"]},
		pluck="against_sales_order",
		distinct=True,
		order_by="against_sales_order",
	)
	return [frappe.get_doc("Sales Order", name) for name in names]
