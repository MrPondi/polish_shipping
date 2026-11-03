"""
The requests of the DHL24 WebAPI, built from a shipment as erpnext_shipping gives it to providers. They are
plain dicts in the shape of the WSDL types (https://dhl24.com.pl/webapi2), which zeep takes as they are.

What DHL24 would refuse is thrown here, with a message that says what to correct, so that the user sees it
before a request is sent. Free text that is too long is cut; address fields are not, as the courier needs
them whole.
"""

import math

import frappe
from frappe import _
from frappe.utils import cint, flt, formatdate, getdate, today

from polish_shipping.utils.address import phone_digits, postcode_digits, split_street

CARRIER = "DHL"
COUNTRY = "PL"
CURRENCY = "PLN"

# The products a Shipment can be booked with, by their DHL24 codes. The names are stored as the carrier
# service of Shipments and Delivery Notes, so they stay as they are.
PRODUCTS = {
	"AH": "Domestic",
	"PR": "Premium",
	"09": "Domestic 09",
	"12": "Domestic 12",
	"DW": "Evening Delivery",
}

PAYER_TYPES = {"Shipper": "SHIPPER", "Third Party": "USER"}

# What DHL24 takes at most, in characters
NAME_LENGTH = 60
CITY_LENGTH = 17
STREET_LENGTH = 35
NUMBER_LENGTH = 10
NUMBERS_LENGTH = 15  # the house and flat numbers together
EMAIL_LENGTH = 60
CONTENT_LENGTH = 30
COMMENT_LENGTH = 100
REFERENCE_LENGTH = 200


def address(address: frappe._dict, name: str | None) -> dict:
	"""
	Where a party is, as the address types of DHL24 have it. `address` is what erpnext_shipping's
	`get_address` returns.
	"""
	check_country(address)

	if not (numbers := split_street(address.address_line1, address.address_line2)):
		frappe.throw(
			_(
				'DHL24 needs the street and the house number of Address {0} apart, and they could not be told apart in "{1}". Write them as, for example, "Prosta 12/4".'
			).format(address.address_title, address.address_line1),
			title=_("DHL24"),
		)
	street, house, flat = numbers

	try:
		postal_code = postcode_digits(address.pincode, address.country_code)
	except ValueError:
		frappe.throw(
			_("The postcode of Address {0} should be 5 digits, such as 00-001.").format(
				address.address_title
			),
			title=_("DHL24"),
		)

	check_length(address, _("City"), address.city, CITY_LENGTH)
	check_length(address, _("Street"), street, STREET_LENGTH)
	check_length(address, _("House Number"), house, NUMBER_LENGTH)
	check_length(address, _("Flat Number"), flat, NUMBER_LENGTH)
	check_length(address, _("House and Flat Number"), house + (flat or ""), NUMBERS_LENGTH)

	return compact(
		{
			"name": truncate(name or address.address_title, NAME_LENGTH),
			"postalCode": postal_code,
			"city": address.city,
			"street": street,
			"houseNumber": house,
			"apartmentNumber": flat,
		}
	)


def contact(contact: frappe._dict | None, address: frappe._dict) -> dict:
	"""
	Who to call about a party's parcels, as the address types of DHL24 have it. `contact` is what
	erpnext_shipping's ShipmentContext holds.
	"""
	if not contact:
		return {}

	person = " ".join(name for name in (contact.first_name, contact.last_name) if name)
	phone = contact.phone or contact.mobile_no
	try:
		phone = phone_digits(phone, address.country_code)
	except ValueError:
		frappe.throw(
			_("The phone number of {0}, {1}, should have 9 digits, besides the country code.").format(
				person, phone
			),
			title=_("DHL24"),
		)

	email = contact.email_id
	return compact(
		{
			"contactPerson": truncate(person, NAME_LENGTH),
			"contactPhone": phone,
			# DHL24 sends notices to the address, so one cut short is left out
			"contactEmail": email if email and len(email) <= EMAIL_LENGTH else None,
		}
	)


def receiver(address: dict) -> dict:
	"""The receiver's address, which also says where and what kind it is"""
	# B is a business address, which the Qt client that this comes from sent for every receiver
	return {"country": COUNTRY, "addressType": "B", **address}


def pieces(parcels: list[dict], pallets: bool = False) -> dict:
	"""The parcels of a shipment, a piece of each row of the Shipment's parcels"""
	items = []
	for idx, parcel in enumerate(parcels, start=1):
		width, height, length = (math.ceil(flt(parcel.get(side))) for side in ("width", "height", "length"))
		if not (width > 0 and height > 0 and length > 0):
			frappe.throw(
				_("DHL24 needs the length, width and height of parcel {0}, in centimetres.").format(idx),
				title=_("DHL24"),
			)

		weight = flt(parcel.get("weight"), 2)
		if weight <= 0:
			frappe.throw(
				_("DHL24 needs the weight of parcel {0}, in kilograms.").format(idx), title=_("DHL24")
			)

		items.append(
			{
				"type": "PALLET" if pallets else "PACKAGE",
				"width": width,
				"height": height,
				"length": length,
				"weight": weight,
				"quantity": cint(parcel.get("count")) or 1,
			}
		)

	if not items:
		frappe.throw(_("DHL24 needs the parcels of the shipment."), title=_("DHL24"))
	return {"item": items}


def service(product: str, value_of_goods: float | None) -> dict:
	"""The DHL24 product, with the shipment insured for the value of its goods"""
	service = {"product": product}
	if flt(value_of_goods) > 0:
		service.update(insurance=True, insuranceValue=flt(value_of_goods, 2))
	return service


def payment(settings) -> dict:
	"""Who pays for a shipment, and how"""
	return compact(
		{
			"paymentMethod": "BANK_TRANSFER",
			"payerType": PAYER_TYPES.get(settings.payer_type, "SHIPPER"),
			"accountNumber": settings.account_number,
			"costsCenter": settings.costs_center,
		}
	)


def price_payment(settings) -> dict:
	"""Who pays for a shipment, as getPrice asks"""
	return {
		"accountNumber": settings.account_number,
		"payerType": PAYER_TYPES.get(settings.payer_type, "SHIPPER"),
	}


def price_request(*, payment: dict, shipper: dict, receiver: dict, pieces: dict) -> dict:
	"""A GetPriceRequest, the shipment that getPrice prices, short of the service, which is set per product"""
	return {
		"payment": payment,
		# getPrice takes the shipper's country, which createShipments doesn't
		"shipper": {"country": COUNTRY, **shipper},
		"receiver": receiver,
		"pieceList": pieces,
	}


def shipment(
	*,
	shipper: dict,
	receiver: dict,
	pieces: dict,
	payment: dict,
	service: dict,
	shipment_date: str,
	content: str,
	comment: str | None = None,
	reference: str | None = None,
) -> dict:
	"""A ShipmentFullData, a shipment for createShipments"""
	return compact(
		{
			"shipper": shipper,
			"receiver": receiver,
			"pieceList": pieces,
			"payment": payment,
			"service": service,
			"shipmentDate": shipment_date,
			# The WSDL asks for it in every shipment
			"skipRestrictionCheck": False,
			"content": content,
			"comment": truncate(comment, COMMENT_LENGTH),
			"reference": truncate(reference, REFERENCE_LENGTH),
		}
	)


def content(description: str | None) -> str:
	"""What the parcels hold: the first line of the Shipment's Description of Content, cut to fit"""
	line = next((line for line in (description or "").splitlines() if line.strip()), "")
	if not line:
		frappe.throw(_("DHL24 needs a Description of Content."), title=_("DHL24"))
	return truncate(" ".join(line.split()), CONTENT_LENGTH, ellipsis=True)


def shipment_date(pickup_date) -> str:
	"""The day the parcels are sent, as DHL24 writes dates"""
	day = getdate(pickup_date)
	if day < getdate(today()):
		frappe.throw(_("The Pickup Date, {0}, is in the past.").format(formatdate(day)), title=_("DHL24"))
	return day.isoformat()


def check_country(address: frappe._dict):
	if (address.country_code or "").upper() != COUNTRY:
		frappe.throw(
			_("DHL24 is set up for addresses in Poland only, and Address {0} is in {1}.").format(
				address.address_title, address.country
			),
			title=_("DHL24"),
		)


def check_length(address: frappe._dict, label: str, value: str | None, length: int):
	if value and len(value) > length:
		frappe.throw(
			_(
				'DHL24 takes at most {0} characters for the {1} of an address, and Address {2} has {3}: "{4}". Shorten it in the Address.'
			).format(length, label, address.address_title, len(value), value),
			title=_("DHL24"),
		)


def truncate(text: str | None, length: int, ellipsis: bool = False) -> str | None:
	if not text or len(text) <= length:
		return text
	return text[: length - 3] + "..." if ellipsis else text[:length]


def compact(values: dict) -> dict:
	"""The values that are set. Those that aren't are left out, as the Qt client of DHL24 left them out."""
	return {key: value for key, value in values.items() if value is not None and value != ""}
