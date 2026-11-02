"""
The DHL24 client against the WSDL of DHL24's sandbox, kept in fixtures, with answers in the shape of that
WSDL. zeep refuses an element the WSDL doesn't have, so the requests of `request.py` are checked too.
"""

import base64
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase
from lxml import etree
from requests.exceptions import ConnectionError
from zeep import Client, Settings
from zeep.transports import Transport

from polish_shipping.dhl24 import client as dhl24_client
from polish_shipping.dhl24 import request
from polish_shipping.dhl24.client import (
	SANDBOX_WSDL,
	DHL24Client,
	DHL24Error,
	DHL24UnreachableError,
	get_items,
)

WSDL = Path(__file__).parent / "fixtures" / "webapi2.wsdl"
NAMESPACE = "https://sandbox.dhl24.com.pl/webapi2/provider/service.html?ws=1"


class FakeResponse:
	status_code = 200
	encoding = "utf-8"

	def __init__(self, content: str):
		self.content = content.encode()
		self.headers = {"Content-Type": "text/xml; charset=utf-8"}


class FakeTransport(Transport):
	"""
	Answers each request with the next answer to its operation, and keeps the requests. An answer that is an
	exception is raised, as requests raises when there is no answer.
	"""

	def __init__(self, answers: dict[str, list[str | Exception]]):
		super().__init__()
		self.answers = answers
		self.requests = []

	def post_xml(self, address, envelope, headers):
		body = envelope.find("{*}Body")[0]
		operation = etree.QName(body).localname
		self.requests.append((operation, body))

		answer = self.answers[operation].pop(0)
		if isinstance(answer, Exception):
			raise answer
		return FakeResponse(
			'<?xml version="1.0" encoding="UTF-8"?>'
			'<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"'
			f' xmlns:ns1="{NAMESPACE}"><SOAP-ENV:Body>{answer}</SOAP-ENV:Body></SOAP-ENV:Envelope>'
		)


class FakeSettings(frappe._dict):
	def get_password(self, fieldname):
		return "_Test password"


def answer(operation: str, result: str) -> str:
	return (
		f"<ns1:{operation}Response><{operation}Result>{result}</{operation}Result></ns1:{operation}Response>"
	)


def label(shipment_id: str) -> str:
	data = base64.b64encode(f"%PDF {shipment_id}".encode()).decode()
	return f"<item><shipmentId>{shipment_id}</shipmentId><labelType>BLP</labelType><labelData>{data}</labelData></item>"


def text(body, path: str) -> str | None:
	return body.findtext(path)


class TestDHL24Client(UnitTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.soap = Client(str(WSDL), settings=Settings(strict=False, xml_huge_tree=True))

	def make_client(self, **answers) -> DHL24Client:
		self.transport = FakeTransport({operation: list(answers) for operation, answers in answers.items()})
		self.soap.transport = self.transport
		patcher = patch.dict(dhl24_client._soap_clients, {SANDBOX_WSDL: self.soap})
		patcher.start()
		self.addCleanup(patcher.stop)
		return DHL24Client(FakeSettings(use_sandbox=1, username="_Test user"))

	def test_shipment_is_created(self):
		client = self.make_client(
			createShipments=[
				answer("createShipments", "<item><shipmentId>90000000001</shipmentId></item>"),
			]
		)
		address = frappe._dict(
			address_title="_Test",
			address_line1="Wąwozowa 2/3",
			city="Warszawa",
			pincode="02-796",
			country_code="pl",
		)
		contact = frappe._dict(first_name="Anna", last_name="Kowalska", phone="600100200", email_id="a@b.pl")
		shipment = request.shipment(
			shipper={**request.address(address, "Firma"), **request.contact(contact, address)},
			receiver=request.receiver(
				{**request.address(address, "Anna"), **request.contact(contact, address)}
			),
			pieces=request.pieces([{"length": 30, "width": 20, "height": 10, "weight": 2.5, "count": 1}]),
			payment=request.payment(frappe._dict(payer_type="Shipper", account_number="1234567")),
			service=request.service("AH", 1500),
			shipment_date="2026-11-03",
			content="Robot",
			comment="Zam. 1",
			reference="SHIP-0001",
		)

		self.assertEqual(client.create_shipments([shipment]), ["90000000001"])

		operation, body = self.transport.requests[0]
		item = "shipments/item"
		self.assertEqual(operation, "createShipments")
		self.assertEqual(text(body, "authData/username"), "_Test user")
		self.assertEqual(text(body, f"{item}/receiver/apartmentNumber"), "3")
		self.assertEqual(text(body, f"{item}/receiver/addressType"), "B")
		self.assertEqual(text(body, f"{item}/service/insuranceValue"), "1500.0")
		self.assertEqual(text(body, f"{item}/skipRestrictionCheck"), "false")
		self.assertEqual(text(body, f"{item}/reference"), "SHIP-0001")

	def test_price_and_fuel_surcharge(self):
		client = self.make_client(
			getPrice=[answer("getPrice", "<price>15.5</price><fuelSurcharge>12.3</fuelSurcharge>")]
		)
		address = frappe._dict(
			address_title="_Test",
			address_line1="Prosta 1",
			city="Warszawa",
			pincode="00-001",
			country_code="pl",
		)
		shipment = request.price_request(
			payment=request.price_payment(frappe._dict(payer_type="Shipper", account_number="1234567")),
			shipper=request.address(address, "Firma"),
			receiver=request.receiver(request.address(address, "Anna")),
			pieces=request.pieces([{"length": 30, "width": 20, "height": 10, "weight": 2.5, "count": 1}]),
		)

		self.assertEqual(client.get_price({**shipment, "service": request.service("AH", 100)}), (15.5, 12.3))
		_operation, body = self.transport.requests[0]
		self.assertEqual(text(body, "shipment/shipper/country"), "PL")
		self.assertEqual(text(body, "shipment/service/product"), "AH")

	def test_labels_three_at_a_time(self):
		client = self.make_client(
			getLabels=[
				answer("getLabels", label("1") + label("2") + label("3")),
				answer("getLabels", label("4") + label("5")),
			]
		)

		labels = client.get_labels(["1", "2", "3", "4", "5"], "BLP")

		self.assertEqual(labels, {shipment_id: f"%PDF {shipment_id}".encode() for shipment_id in "12345"})
		self.assertEqual(
			[len(body.findall("itemsToPrint/item")) for _op, body in self.transport.requests], [3, 2]
		)
		self.assertEqual(text(self.transport.requests[0][1], "itemsToPrint/item/labelType"), "BLP")

	def test_track_and_trace(self):
		client = self.make_client(
			getTrackAndTraceInfo=[
				answer(
					"getTrackAndTraceInfo",
					"<shipmentId>1</shipmentId><receivedBy>Anna</receivedBy><events>"
					"<item><status>SORT</status><description>W sortowni</description><terminal>WA</terminal>"
					"<timestamp>2026-11-03 10:00:00</timestamp></item></events>",
				)
			]
		)

		info = client.get_track_and_trace_info("1")
		self.assertEqual(info.receivedBy, "Anna")
		self.assertEqual([event.status for event in get_items(info.events)], ["SORT"])

	def test_deleting_shipments_and_courier_bookings(self):
		result = "<item><id>1</id><result>true</result></item><item><id>2</id><result>false</result><error>Kurier zamówiony</error></item>"
		client = self.make_client(
			deleteShipments=[answer("deleteShipments", result)],
			cancelCourierBooking=[answer("cancelCourierBooking", result)],
		)

		for results in (client.delete_shipments(["1", "2"]), client.cancel_courier_booking(["1", "2"])):
			self.assertEqual(
				[(row.id, row.result, row.error) for row in results],
				[("1", True, None), ("2", False, "Kurier zamówiony")],
			)

	def test_courier_booking(self):
		client = self.make_client(bookCourier=[answer("bookCourier", "<item>ORDER-1</item>")])

		self.assertEqual(
			client.book_courier("2026-11-03", "10:00", "14:00", ["1", "2"], "Szkło"), ["ORDER-1"]
		)
		_operation, body = self.transport.requests[0]
		self.assertEqual(text(body, "pickupTimeFrom"), "10:00")
		self.assertEqual([item.text for item in body.findall("shipmentIdList/item")], ["1", "2"])

	def test_pnp_has_the_authdata_inside_its_request(self):
		pnp = base64.b64encode(b"%PDF PNP").decode()
		client = self.make_client(
			getPnp=[
				answer(
					"getPnp",
					f"<fileName>pnp.pdf</fileName><fileData>{pnp}</fileData><fileMimeType>application/pdf</fileMimeType>",
				)
			]
		)

		self.assertEqual(client.get_pnp("2026-11-03"), b"%PDF PNP")
		_operation, body = self.transport.requests[0]
		self.assertEqual(text(body, "pnpRequest/authData/username"), "_Test user")
		self.assertEqual(text(body, "pnpRequest/date"), "2026-11-03")
		self.assertEqual(text(body, "pnpRequest/type"), "ALL")
		self.assertIsNone(body.find("authData"))

	def test_shipments_count(self):
		client = self.make_client(getMyShipmentsCount=[answer("getMyShipmentsCount", "3")])
		self.assertEqual(client.get_my_shipments_count("2026-11-03", "2026-11-03"), 3)

	def test_fault_is_a_dhl24_error(self):
		client = self.make_client(
			getMyShipmentsCount=[
				"<SOAP-ENV:Fault><faultcode>100</faultcode><faultstring>Nieprawidłowe dane autoryzacyjne</faultstring></SOAP-ENV:Fault>"
			]
		)

		with self.assertRaises(DHL24Error) as error:
			client.get_my_shipments_count("2026-11-03", "2026-11-03")
		self.assertEqual(error.exception.message, "Nieprawidłowe dane autoryzacyjne")
		self.assert_nothing_chained(error.exception)

	def test_dhl24_that_doesnt_answer(self):
		client = self.make_client(getMyShipmentsCount=[ConnectionError("No route to host")])

		with self.assertRaises(DHL24UnreachableError) as error:
			client.get_my_shipments_count("2026-11-03", "2026-11-03")
		self.assertIn("No route to host", error.exception.message)
		self.assert_nothing_chained(error.exception)

	def test_password_is_left_out_of_what_is_printed(self):
		client = self.make_client()

		self.assertEqual(client.auth["password"], "_Test password")
		for printed in (repr(client.auth), str({"kwargs": {"authData": client.auth}})):
			self.assertNotIn("_Test password", printed)
			self.assertIn("_Test user", printed)

	def assert_nothing_chained(self, error: Exception):
		"""The frames of zeep and requests, whose variables hold the password, aren't in the traceback"""
		self.assertIsNone(error.__cause__)
		self.assertTrue(error.__suppress_context__)
