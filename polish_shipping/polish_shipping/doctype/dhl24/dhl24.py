# Copyright (c) 2025, Karol Parzonka and contributors
# For license information, please see license.txt

from typing import Any

import frappe
import frappe.utils
import regex
from erpnext_shipping.erpnext_shipping.utils import show_error_alert
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt
from frappe.utils.data import get_link_to_form
from zeep import Client, Settings
from zeep.exceptions import Fault

DHL_PROVIDER = "DHL24"
WEIGHT_DECIMALS = 3
CURRENCY_DECIMALS = 2

PROD_WSDL_URL = "https://dhl24.com.pl/webapi2/"
TEST_WSDL_URL = "https://sandbox.dhl24.com.pl/webapi2"


class DHL24(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		account_number: DF.Int
		enabled: DF.Check
		password: DF.Password | None
		use_test_environment: DF.Check
		username: DF.Data | None
	# end: auto-generated types
	pass


class DHL24Utils:
	def __init__(self):
		settings: DHL24 = frappe.get_cached_doc("DHL24")  # type: ignore
		self.username = settings.username
		self.password = settings.get_password("password")
		self.enabled = settings.enabled
		self.account_number = settings.account_number
		self.use_test_environment = settings.use_test_environment
		self.insurance_value = 15000

		if not self.enabled:
			link = get_link_to_form("DHL24", "DHL24", _("DHL24 Settings"))
			frappe.throw(_("Please enable DHL24 Integration in {0}").format(link))

		# Zeep client with type_factory
		soap_settings = Settings(strict=False, xml_huge_tree=True)  # type: ignore
		self.client = Client(
			wsdl=TEST_WSDL_URL if self.use_test_environment else PROD_WSDL_URL,
			settings=soap_settings,
		)

		self.factory = self.client.type_factory("ns0")
		# Prepare auth struct once
		self.auth = self.factory.AuthData(username=self.username, password=self.password)

	def get_available_services(
		self,
		pickup_address,
		pickup_contact,
		delivery_address,
		delivery_contact,
		parcels: list[dict[str, Any]],
	) -> list[dict[str, Any]]:
		"""
		Get available DHL24 services for the given address and parcel details.

		Args:
		    pickup_address: The pickup address object
		    pickup_contact: The pickup contact details
		    delivery_address: The delivery address object
		    delivery_contact: The delivery contact details
		    parcels: List of parcel dictionaries with dimensions

		Returns:
		    List of available service dictionaries with pricing
		"""
		# Validate required fields
		if not self.enabled or not self.username or not self.password:
			return []

		# 1) Fetch avaliable services
		try:
			parsed_delivery_addr = parse_address(
				delivery_address.address_line1, delivery_address.address_line2
			)
			parsed_pickup_addr = parse_address(pickup_address.address_line1, pickup_address.address_line2)

			# Check if address parsing failed
			if "error" in parsed_delivery_addr:
				frappe.log_error(
					"DHL24 Address Parsing", f"DHL24 Delivery Address Error: {parsed_delivery_addr['error']}"
				)
				show_error_alert("parsing delivery address")
				return []

			if "error" in parsed_pickup_addr:
				frappe.log_error(
					"DHL24 Address Parsing", f"DHL24 Pickup Address Error: {parsed_pickup_addr['error']}"
				)
				show_error_alert("parsing pickup address")
				return []

			GetPostalCodeServices = self.client.get_element("ns0:getPostalCodeServices")

			svc_req = GetPostalCodeServices(
				authData=self.auth,
				postCode=delivery_address.pincode.replace("-", ""),
				pickupDate=frappe.utils.nowdate(),
				city=delivery_address.city,
				street=parsed_delivery_addr["street"],
				houseNumber=parsed_delivery_addr["house_number"],
			)
			# Add apartment number
			if parsed_delivery_addr["apartment_number"]:
				svc_req.apartmentNumber = parsed_delivery_addr["apartment_number"]

			svc_res = self.client.service.getPostalCodeServices(svc_req)
			svc = svc_res.get("getPostalCodeServicesResult")  # ns0:PostalCodeServicesResponse
		except Fault as e:
			frappe.log_error("DHL24 Soap Error", f"{type(e).__name__}: {e!s}")
			show_error_alert("fetching DHL24 service availability")
			return []
		except Exception as e:
			frappe.log_error("DHL24 General Error", f"{type(e).__name__}: {e!s}")
			show_error_alert("processing DHL24 service availability")
			return []

		# 2) Fetch prices for these services
		available_services = []

		payment = self.factory.PricePayment(accountNumber=self.account_number, payerType="SHIPPER")

		shipper = self.factory.ShipperAddress(
			country=pickup_address.country_code.upper(),
			name=f"{pickup_contact.first_name} {pickup_contact.last_name}"
			if not pickup_contact.company_name
			else pickup_contact.company_name,
			postalCode=pickup_address.pincode.replace("-", ""),
			city=pickup_address.city,
			street=parsed_pickup_addr["street"],
			houseNumber=parsed_pickup_addr["house_number"],
		)
		if parsed_pickup_addr["apartment_number"]:
			shipper.apartmentNumber = parsed_pickup_addr["apartment_number"]

		receiver = self.factory.ReceiverAddress(
			country=delivery_address.country_code.upper(),
			addressType="C",
			name=f"{delivery_contact.first_name} {delivery_contact.last_name}"
			if not delivery_contact.company_name
			else delivery_contact.company_name,
			postalCode=delivery_address.pincode.replace("-", ""),
			city=delivery_address.city,
			street=parsed_delivery_addr["street"],
			houseNumber=parsed_delivery_addr["house_number"],
		)
		if parsed_delivery_addr["apartment_number"]:
			receiver.apartmentNumber = parsed_delivery_addr["apartment_number"]

		PricePieceArray = []
		for parcel in parcels:
			PricePiece = self.factory.PricePiece(
				type="PACKAGE",
				width=int(parcel.get("width", 10)),
				height=int(parcel.get("height", 10)),
				length=int(parcel.get("length", 10)),
				weight=int(parcel.get("weight", 1)),
				quantity=int(parcel.get("count", 1)),
			)
			PricePieceArray.append(PricePiece)

		ArrayOfPricepiece = self.factory.ArrayOfPricepiece(item=PricePieceArray)

		for product in (("AH", "Standard"), ("PR", "Premium")):
			try:
				service = self.factory.PriceService(product=product[0])

				if self.insurance_value:
					service.insurance = True
					service.insuranceValue = self.insurance_value

				get_price_req = self.factory.GetPriceRequest(
					payment=payment,
					shipper=shipper,
					receiver=receiver,
					service=service,
					priceList=ArrayOfPricepiece,
					customs=None,
				)

				get_price = self.factory.getPrice(authData=self.auth, shipment=get_price_req)

				pr_res = self.client.service.getPrice(get_price)
				pr = pr_res.get("getPriceResult")
				total = flt(pr.price, CURRENCY_DECIMALS)

				available_service = self.get_service_dict(product, total)
				available_services.append(available_service)
			except Exception as e:
				frappe.log_error(
					f"DHL24 {product[1]} pricing", f"DHL24 Pricing Error: {type(e).__name__}: {e!s}"
				)
				show_error_alert(f"pricing DHL24 {product[1]}")
				continue

		for product in (
			("domesticExpress9", "Domestic 09:00"),
			("domesticExpress12", "Domestic 12:00"),
			("deliveryEvening", "Evening delivery shipment"),
			("pickupOnSaturday", "Pickup on Saturday"),
			("deliverySaturday", "Delivery on Saturday"),
		):
			if getattr(svc, product[0], False):
				try:
					if product[0] == "domesticExpress9":
						service = self.factory.PriceService(product="09")
					elif product[0] == "domesticExpress12":
						service = self.factory.PriceService(product="12")
					elif product[0] == "deliveryEvening":
						service = self.factory.PriceService(product="DW", deliveryEvening=True)
					elif product[0] == "pickupOnSaturday":
						service = self.factory.PriceService(product="AH", pickupOnSaturday=True)
					elif product[0] == "deliverySaturday":
						service = self.factory.PriceService(product="AH", deliveryOnSaturday=True)

					if self.insurance_value:
						service.insurance = True
						service.insuranceValue = self.insurance_value

					get_price_req = self.factory.GetPriceRequest(
						payment=payment,
						shipper=shipper,
						receiver=receiver,
						service=service,
						priceList=ArrayOfPricepiece,
						customs=None,
					)

					get_price = self.factory.getPrice(authData=self.auth, shipment=get_price_req)

					pr_res = self.client.service.getPrice(get_price)
					pr = pr_res.get("getPriceResult")
					total = flt(pr.price, CURRENCY_DECIMALS)

					available_service = self.get_service_dict(product, total)
					available_services.append(available_service)

				except Exception as e:
					frappe.log_error(
						f"DHL24 {product[1]} pricing", f"DHL24 Pricing Error: {type(e).__name__}: {e!s}"
					)
					show_error_alert(f"pricing DHL24 {product[1]}")
					continue

		return available_services

	def create_shipment(
		self, shipment, pickup_address, delivery_address, service_info, parcels
	) -> dict[str, Any] | None:
		"""
		Create a DHL24 shipment.

		Returns:
		    Dictionary with shipment details on success, None on failure
		"""
		try:
			shipment_data = self._prepare_shipment_data(
				shipment, pickup_address, delivery_address, service_info, parcels
			)

			response = self.client.service.createShipments(authData=self.auth, shipments=shipment_data)

			return self._parse_shipment_response(response)
		except Fault as e:
			frappe.log_error(
				"DHL24 Shipment Error",
				f"DHL24 Shipment Error has occured while creating shipment: {type(e).__name__}: {e!s}",
			)
			show_error_alert("creating shipment")
			return None
		except Exception as e:
			frappe.log_error(
				"DHL24 General Error",
				f"DHL24 General Error has occured while creating shipment: {type(e).__name__}: {e!s}",
			)
			show_error_alert("creating shipment")
			return None

	def get_label(self, shipment_id) -> list[Any]:
		"""
		Get shipping label for a shipment.

		Returns:
		    List of label data on success, empty list on failure
		"""
		try:
			item_to_print = self.factory.ItemToPrint(labelType="LP", shipmentId=shipment_id)

			response = self.client.service.getLabels(authData=self.auth, itemsToPrint=[item_to_print])

			if response and hasattr(response, "item"):
				return [item.labelData for item in response.item]
			return []
		except Fault as e:
			frappe.log_error(
				"DHL24 Label Error",
				f"DHL24 Label Error has occured while fetching label: {type(e).__name__}: {e!s}",
			)
			show_error_alert("fetching label")
			return []
		except Exception as e:
			frappe.log_error(
				"DHL24 General Error",
				f"DHL24 General Error has occured while fetching label: {type(e).__name__}: {e!s}",
			)
			show_error_alert("fetching label")
			return []

	def get_tracking_data(self, shipment_id) -> dict[str, Any] | None:
		"""
		Get tracking information for a shipment.

		Returns:
		    Dictionary with tracking details on success, None on failure
		"""
		try:
			response = self.client.service.getTrackAndTraceInfo(authData=self.auth, shipmentId=shipment_id)

			return self._parse_tracking_response(response)
		except Fault as e:
			frappe.log_error(
				"DHL24 Tracking Error",
				f"DHL24 Tracking Error has occured while fetching tracking data: {type(e).__name__}: {e!s}",
			)
			show_error_alert("fetching tracking data")
			return None
		except Exception as e:
			frappe.log_error(
				"DHL24 General Error",
				f"DHL24 General Error has occured while fetching tracking data: {type(e).__name__}: {e!s}",
			)
			show_error_alert("fetching tracking data")
			return None

	def _prepare_shipment_data(self, shipment, pickup_address, delivery_address, service_info, parcels):
		"""
		Prepare data for a shipment.

		Returns:
		    List of shipment data objects
		"""
		parsed_delivery_addr = parse_address(delivery_address.address_line1, delivery_address.address_line2)
		parsed_pickup_addr = parse_address(pickup_address.address_line1, pickup_address.address_line2)

		# Check if address parsing failed
		if "error" in parsed_delivery_addr:
			frappe.log_error(
				"DHL24 Shipment Address Parsing",
				f"DHL24 Delivery Address Error: {parsed_delivery_addr['error']}",
			)
			frappe.throw(_("Could not parse delivery address for DHL24 shipment"))

		if "error" in parsed_pickup_addr:
			frappe.log_error(
				"DHL24 Shipment Address Parsing", f"DHL24 Pickup Address Error: {parsed_pickup_addr['error']}"
			)
			frappe.throw(_("Could not parse pickup address for DHL24 shipment"))

		address_data = self.factory.AddressData(
			name=pickup_address.address_title,
			postalCode=pickup_address.pincode.replace("-", ""),
			city=pickup_address.city,
			street=parsed_pickup_addr["street"],
			houseNumber=parsed_pickup_addr["house_number"],
		)
		if parsed_pickup_addr["apartment_number"]:
			address_data.apartmentNumber = parsed_pickup_addr["apartment_number"]

		receiver_address = self.factory.ReceiverAddressData(
			country=delivery_address.country_code.upper(),
			postalCode=delivery_address.pincode.replace("-", ""),
			city=delivery_address.city,
			street=parsed_delivery_addr["street"],
			houseNumber=parsed_delivery_addr["house_number"],
		)
		if parsed_delivery_addr["apartment_number"]:
			receiver_address.apartmentNumber = parsed_delivery_addr["apartment_number"]

		pieces = []
		for parcel in parcels:
			piece = self.factory.PieceDefinition(
				type="PACKAGE",
				width=int(parcel.get("width", 10)),
				height=int(parcel.get("height", 10)),
				length=int(parcel.get("length", 10)),
				weight=int(parcel.get("weight", 1)),
				quantity=int(parcel.get("count", 1)),
			)
			pieces.append(piece)

		shipment_full_data = self.factory.ShipmentFullData(
			shipper=address_data,
			receiver=receiver_address,
			pieceList=pieces,
			service=self.factory.ServiceDefinition(
				product=service_info.get("service_id", "AH"),
				insurance=True,
				insuranceValue=self.insurance_value,
			),
			content=shipment.get("content_description", "General Goods"),
		)

		# TODO: Make the service product work with other ids since currently it will only work with AH and PR

		return [shipment_full_data]

	def _parse_shipment_response(self, response):
		"""
		Parse the shipment creation response.

		Returns:
		    Dictionary with shipment details
		"""
		if (
			not hasattr(response, "createShipmentsResult")
			or not hasattr(response.createShipmentsResult, "item")
			or not response.createShipmentsResult.item
		):
			frappe.throw(_("No shipment created in DHL response"))

		return {
			"service_provider": DHL_PROVIDER,
			"shipment_id": response.createShipmentsResult.item[0].shipmentId,
			"carrier": "DHL",
			"carrier_service": response.createShipmentsResult.item[0].orderStatus,
			"awb_number": response.createShipmentsResult.item[0].shipmentId,
			"tracking_url": f"https://www.dhl.com/pl-en/home/tracking.html?tracking-id={response.createShipmentsResult.item[0].shipmentId}",
		}

	def _parse_tracking_response(self, response):
		"""
		Parse the tracking information response.

		Returns:
		    Dictionary with tracking details
		"""
		events = []
		if hasattr(response, "events") and hasattr(response.events, "item"):
			for event in response.events.item:
				events.append(f"{event.timestamp}: {event.status} - {event.description}")

		return {
			"awb_number": response.shipmentId,
			"tracking_status": response.receivedBy if hasattr(response, "receivedBy") else "In Transit",
			"tracking_status_info": "\n".join(events),
			"tracking_url": f"https://www.dhl.com/pl-en/home/tracking.html?tracking-id={response.shipmentId}",
		}

	def get_service_dict(self, product, total):
		"""
		Returns a dictionary with service info.

		Args:
		    product: Tuple containing service ID and service name
		    total: Total price for the service

		Returns:
		    Dictionary with service details
		"""
		available_service = frappe._dict()
		available_service.service_provider = DHL_PROVIDER
		available_service.carrier = "DHL"
		available_service.service_name = product[1]
		available_service.service_id = product[0]
		available_service.multicollo = True
		available_service.total_price = total
		available_service.currency = "PLN"

		return available_service


def normalize_word_case(text: str) -> str:
	"""
	Normalize text to have each word capitalized.

	Args:
	    text: The input text string

	Returns:
	    A string with each word capitalized
	"""
	return " ".join(word.capitalize() for word in text.split())


def clean_street_name(street: str) -> str:
	"""
	Clean a street name by removing common prefixes and normalizing case.

	Args:
	    street: The street name to clean

	Returns:
	    The cleaned street name
	"""
	# Use a comprehensive set of street prefixes
	street_prefixes = r"^(ulica|ul[.\s])\s*"
	street = regex.sub(street_prefixes, "", street, flags=regex.IGNORECASE)
	return normalize_word_case(street.strip())


def parse_address(address_1: str, address_2: str | None = None) -> dict[str, Any]:
	"""
	Parse address string into components (street, house number, apartment).

	Args:
	    address_1: The primary address line
	    address_2: The secondary address line (optional)

	Returns:
	    A dictionary with parsed address components and any error message
	"""
	# Combine addresses
	combined = f"{address_1.strip()} {address_2.strip() if address_2 else ''}".strip()

	# Handle different address formats
	# More flexible pattern that can handle various formats
	patterns = [
		# Format: All in one
		regex.compile(
			r"^(?P<street>[\p{L}\d\s\.'-]+?)\s+(?|(?P<number>\d+\w*)[\/-](?P<apt>\d+\w*)|(?P<number>\d+\w*)(?:\s+(?P<apt>\d+\w*))?)$",
			regex.VERBOSE,
		),
		# Format: "Street Name 123/45" or "Street Name 123-45"
		regex.compile(r"^(?P<street>[\p{L}\d\s\.'-]+?)\s+(?P<number>\d+\w*)[\/-](?P<apt>\d+\w*)$"),
		# Format: "Street Name 123 apt 45" or "Street Name 123 m 45"
		regex.compile(
			r"^(?P<street>[\p{L}\d\s\.'-]+?)\s+(?P<number>\d+\w*)\s+(?:apt|m|mieszkanie|lok\.?|lokal)\s+(?P<apt>\d+\w*)$",
			regex.IGNORECASE,
		),
		# Format: "Street Name 123" (no apartment)
		regex.compile(r"^(?P<street>[\p{L}\d\s\.'-]+?)\s+(?P<number>\d+\w*)$"),
		# Format: "123 Street Name" (American style)
		regex.compile(r"^(?P<number>\d+\w*)\s+(?P<street>[\p{L}\d\s\.'-]+?)$"),
	]

	for pattern in patterns:
		match = pattern.match(combined)
		if match:
			street = clean_street_name(match.group("street"))
			if street and match.group("number"):
				return {
					"street": street,
					"house_number": match.group("number"),
					"apartment_number": match.group("apt")
					if "apt" in match.groupdict() and match.group("apt")
					else None,
				}

	# If we've tried all patterns and none matched
	error_msg = f"Could not parse address: '{combined}', skipping"
	frappe.log_error("DHL24 Address Parsing", error_msg)

	# Last resort: try to extract just the street and house number
	# This is a simplified fallback that might work for some cases
	last_resort = regex.search(r"(?P<street>.*?)\s+(?P<number>\d+\w*)[^\d]*$", combined)
	if last_resort:
		street = clean_street_name(last_resort.group("street"))
		if street:
			return {
				"street": street,
				"house_number": last_resort.group("number"),
				"apartment_number": None,
				"warning": "Address parsed with fallback method - may be inaccurate",
			}

	return {"error": error_msg}
