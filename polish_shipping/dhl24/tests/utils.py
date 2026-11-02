"""What the DHL24 tests share: their settings, parties and Shipments, and a DHL24 that answers from memory"""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today
from pypdf import PdfWriter

from polish_shipping.dhl24.client import DHL24Client
from polish_shipping.dhl24.provider import DHL24Provider
from polish_shipping.install import make_parcel_service

SETTINGS = {
	"enabled": 1,
	"username": "_Test DHL24",
	"account_number": "1234567",
	"use_sandbox": 1,
	"product_ah": 1,
	"product_pr": 0,
	"product_09": 0,
	"product_12": 0,
	"product_dw": 0,
	"default_product": "AH",
	"label_type": "BLP",
	"payer_type": "Shipper",
	"costs_center": None,
	"comment_template": None,
}

PARCELS = [{"length": 30, "width": 20, "height": 10.5, "weight": 2, "count": 1}]


def set_settings(**values):
	"""Set DHL24 Settings without saving them, as a save asks DHL24 whether the username is right"""
	frappe.db.set_single_value("DHL24 Settings", {**SETTINGS, **values})


def clear_settings_cache():
	frappe.clear_document_cache("DHL24 Settings", "DHL24 Settings")


def make_client(**results) -> MagicMock:
	"""A DHL24 client whose methods return `results`, and record how they were called"""
	client = MagicMock(spec=DHL24Client)
	client.get_price.return_value = (20.0, 10.0)
	client.create_shipments.return_value = ["90000000001"]
	client.get_labels.side_effect = lambda shipment_ids, label_type: {
		shipment_id: make_pdf() for shipment_id in shipment_ids
	}
	client.get_track_and_trace_info.return_value = SimpleNamespace(
		shipmentId="90000000001", receivedBy=None, events=None
	)
	client.delete_shipments.side_effect = lambda shipment_ids: [
		SimpleNamespace(id=shipment_id, result=True, error=None) for shipment_id in shipment_ids
	]
	client.book_courier.return_value = ["ORDER-1"]
	client.cancel_courier_booking.side_effect = lambda order_ids: [
		SimpleNamespace(id=order_id, result=True, error=None) for order_id in order_ids
	]
	client.get_pnp.return_value = make_pdf()
	for method, result in results.items():
		getattr(client, method).side_effect = None
		getattr(client, method).return_value = result
	return client


def make_pdf() -> bytes:
	"""A blank page, as Frappe checks a PDF that is saved"""
	writer = PdfWriter()
	writer.add_blank_page(width=100, height=100)
	pdf = BytesIO()
	writer.write(pdf)
	return pdf.getvalue()


def make_address(title: str, address_line1: str = "ul. Prosta 12/4", **values) -> str:
	address = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": title,
			"address_type": "Shipping",
			"address_line1": address_line1,
			"city": "Warszawa",
			"pincode": "00-001",
			"country": "Poland",
			**values,
		}
	)
	return address.insert().name


def make_contact(first_name: str, phone: str = "+48 600 100 200") -> str:
	contact = frappe.get_doc(
		{
			"doctype": "Contact",
			"first_name": first_name,
			"last_name": "_Test Kowalska",
			"email_ids": [{"email_id": f"{first_name.lower()}@example.com", "is_primary": 1}],
			"phone_nos": [{"phone": phone, "is_primary_phone": 1}],
		}
	)
	return contact.insert().name


def shipment_values(**values) -> dict:
	"""What a Shipment holds for DHL24, sent from and to Customers in Warsaw"""
	return {
		"pickup_from_type": "Customer",
		"delivery_to_type": "Customer",
		"pickup_address_name": make_address("_Test DHL24 Pickup"),
		"delivery_address_name": make_address("_Test DHL24 Delivery", "Wąwozowa 2"),
		"pickup_contact_name": make_contact("Jan"),
		"delivery_contact_name": make_contact("Anna"),
		"description_of_content": "_Test robot",
		"pickup_date": add_days(today(), 1),
		"pickup_from": "09:00",
		"pickup_to": "17:00",
		"pickup_type": "Pickup",
		"value_of_goods": 1500,
		**values,
	}


def make_shipment(docstatus: int = 1, parcels: list[dict] | None = None, **values):
	"""
	A Shipment made without ERPNext's checks, which need a company and parties: only the fields DHL24 and
	erpnext_shipping read matter here. Its status is the one ERPNext gives it, unless `values` has another.
	"""
	shipment = frappe.get_doc(
		{
			"doctype": "Shipment",
			"shipment_parcel": parcels or PARCELS,
			"status": {0: "Draft", 1: "Submitted", 2: "Cancelled"}[docstatus],
			**shipment_values(**values),
		}
	)
	shipment.flags.ignore_validate = True
	shipment.flags.ignore_links = True
	shipment.flags.ignore_mandatory = True
	shipment.insert()
	if docstatus:
		shipment.db_set("docstatus", docstatus)
	return shipment


class DHL24TestCase(IntegrationTestCase):
	"""Tests with DHL24 Settings set up and a DHL24 client that answers from memory, as `self.client`"""

	def setUp(self):
		super().setUp()
		self.addCleanup(clear_settings_cache)
		self.addCleanup(frappe.db.rollback)
		set_settings()
		make_parcel_service()
		self.client = make_client()
		patcher = patch.object(DHL24Provider, "get_client", return_value=self.client)
		patcher.start()
		self.addCleanup(patcher.stop)
