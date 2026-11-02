import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from .custom_fields import get_custom_fields
from .dhl24.request import CARRIER

# DHL Parcel Poland's tracking page, as the URL Reference of a Parcel Service
DHL_TRACKING_URL = "https://mojdhl.pl/sprawdz?paczki={{ tracking_number }}"


def after_install():
	create_custom_fields(get_custom_fields())
	make_parcel_service()


def make_parcel_service():
	"""The Parcel Service of DHL24's carrier, which the tracking links are made from. One the site has stays."""
	if frappe.db.exists("Parcel Service", CARRIER):
		return

	frappe.get_doc(
		{
			"doctype": "Parcel Service",
			"parcel_service_name": CARRIER,
			"parcel_service_code": CARRIER,
			"url_reference": DHL_TRACKING_URL,
		}
	).insert(ignore_permissions=True)
