import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from polish_shipping.dhl24 import request


def make_address(**values) -> frappe._dict:
	"""An address as erpnext_shipping's get_address returns it"""
	return frappe._dict(
		{
			"address_title": "_Test Address",
			"address_line1": "ul. Prosta 12/4",
			"address_line2": None,
			"city": "Warszawa",
			"pincode": "00-001",
			"country": "Poland",
			"country_code": "pl",
			**values,
		}
	)


def make_contact(**values) -> frappe._dict:
	"""A contact as erpnext_shipping's ShipmentContext holds it"""
	return frappe._dict(
		{
			"first_name": "Anna",
			"last_name": "Kowalska",
			"email_id": "anna@example.com",
			"phone": "+48 600 100 200",
			"mobile_no": None,
			**values,
		}
	)


class TestAddress(IntegrationTestCase):
	def test_address(self):
		self.assertEqual(
			request.address(make_address(), "Anna Kowalska"),
			{
				"name": "Anna Kowalska",
				"postalCode": "00001",
				"city": "Warszawa",
				"street": "Prosta",
				"houseNumber": "12",
				"apartmentNumber": "4",
			},
		)

	def test_name_is_the_address_title_by_default_and_cut_to_fit(self):
		self.assertEqual(request.address(make_address(), None)["name"], "_Test Address")
		self.assertEqual(request.address(make_address(), "x" * 70)["name"], "x" * 60)

	def test_flat_number_is_left_out_when_there_is_none(self):
		self.assertNotIn("apartmentNumber", request.address(make_address(address_line1="Prosta 12"), None))

	def test_what_dhl24_would_refuse_throws(self):
		cases = {
			"no house number": {"address_line1": "Prosta"},
			"postcode": {"pincode": "0001"},
			"city": {"city": "Grodzisk Mazowiecki"},
			"street": {"address_line1": "Bardzo Długa Nazwa Ulicy Która Się Nie Mieści 1"},
			"house and flat numbers": {"address_line1": "Prosta 1234567A/12345678"},
			"country": {"country": "Germany", "country_code": "de"},
		}
		for case, values in cases.items():
			with self.subTest(case=case):
				self.assertRaises(frappe.ValidationError, request.address, make_address(**values), None)


class TestContact(IntegrationTestCase):
	def test_contact(self):
		self.assertEqual(
			request.contact(make_contact(), make_address()),
			{
				"contactPerson": "Anna Kowalska",
				"contactPhone": "600100200",
				"contactEmail": "anna@example.com",
			},
		)

	def test_mobile_number_without_a_phone(self):
		contact = make_contact(phone=None, mobile_no="600 200 300")
		self.assertEqual(request.contact(contact, make_address())["contactPhone"], "600200300")

	def test_phone_dhl24_would_refuse_throws(self):
		self.assertRaises(
			frappe.ValidationError, request.contact, make_contact(phone="12345"), make_address()
		)

	def test_email_too_long_for_dhl24_is_left_out(self):
		contact = make_contact(email_id="a" * 60 + "@example.com")
		self.assertNotIn("contactEmail", request.contact(contact, make_address()))

	def test_no_contact(self):
		self.assertEqual(request.contact(None, make_address()), {})

	def test_receiver(self):
		self.assertEqual(
			request.receiver({"name": "Anna"}), {"country": "PL", "addressType": "B", "name": "Anna"}
		)


class TestPieces(IntegrationTestCase):
	def test_pieces(self):
		parcels = [{"length": 30, "width": 20.2, "height": 10, "weight": 2.5, "count": 2}]
		self.assertEqual(
			request.pieces(parcels),
			{
				"item": [
					{"type": "PACKAGE", "width": 21, "height": 10, "length": 30, "weight": 2.5, "quantity": 2}
				]
			},
		)

	def test_pallets(self):
		parcels = [{"length": 120, "width": 80, "height": 100, "weight": 200}]
		piece = request.pieces(parcels, pallets=True)["item"][0]
		self.assertEqual((piece["type"], piece["quantity"]), ("PALLET", 1))

	def test_parcel_without_sides_or_weight_throws(self):
		for parcel in (
			{"length": 30, "width": 20, "weight": 2},
			{"length": 30, "width": 20, "height": 10, "weight": 0},
		):
			with self.subTest(parcel=parcel):
				self.assertRaises(frappe.ValidationError, request.pieces, [parcel])

		self.assertRaises(frappe.ValidationError, request.pieces, [])


class TestService(IntegrationTestCase):
	def test_insured_for_the_value_of_goods(self):
		self.assertEqual(
			request.service("AH", 1500), {"product": "AH", "insurance": True, "insuranceValue": 1500.0}
		)
		self.assertEqual(request.service("AH", 0), {"product": "AH"})

	def test_payment(self):
		settings = frappe._dict(payer_type="Third Party", account_number="1234567", costs_center=None)
		self.assertEqual(
			request.payment(settings),
			{"paymentMethod": "BANK_TRANSFER", "payerType": "USER", "accountNumber": "1234567"},
		)
		self.assertEqual(request.price_payment(settings), {"accountNumber": "1234567", "payerType": "USER"})


class TestShipment(IntegrationTestCase):
	def test_shipment(self):
		shipment = request.shipment(
			shipper={"name": "A"},
			receiver={"name": "B"},
			pieces={"item": []},
			payment={"payerType": "SHIPPER"},
			service={"product": "AH"},
			shipment_date="2026-11-03",
			content="Robot",
			comment="x" * 120,
		)
		self.assertIs(shipment["skipRestrictionCheck"], False)
		self.assertEqual(shipment["comment"], "x" * 100)
		self.assertNotIn("reference", shipment)

	def test_price_request_has_the_shippers_country(self):
		price_request = request.price_request(payment={}, shipper={"name": "A"}, receiver={}, pieces={})
		self.assertEqual(price_request["shipper"], {"country": "PL", "name": "A"})

	def test_content_is_the_first_line_cut_to_fit(self):
		self.assertEqual(request.content("\n  Robot  koszący \nsecond line"), "Robot koszący")
		self.assertEqual(request.content("x" * 40), "x" * 27 + "...")
		self.assertRaises(frappe.ValidationError, request.content, " \n ")

	def test_shipment_date(self):
		self.assertEqual(request.shipment_date(today()), today())
		self.assertRaises(frappe.ValidationError, request.shipment_date, add_days(today(), -1))
