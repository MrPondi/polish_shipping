"""
The DHL24 WebAPI (https://dhl24.com.pl/pl/webapi2/doc.html), a SOAP service, through zeep. The requests are
built by `request.py`.
"""

import base64
from itertools import batched

from frappe import _
from frappe.utils import cint, flt
from lxml.etree import XMLSyntaxError
from requests.exceptions import RequestException
from zeep import Client, Settings
from zeep.exceptions import Fault, TransportError
from zeep.transports import Transport

PRODUCTION_WSDL = "https://dhl24.com.pl/webapi2"
SANDBOX_WSDL = "https://sandbox.dhl24.com.pl/webapi2"

# DHL24 takes at most 3 shipments, or labels, in one request
BATCH_SIZE = 3

# What reaching DHL24 can fail with: no answer, an HTTP error, or an answer that isn't XML
UNREACHABLE = (RequestException, TransportError, XMLSyntaxError)

_soap_clients: dict[str, Client] = {}


class DHL24Error(Exception):
	"""What DHL24 answered instead of a result, such as that it doesn't deliver to a postcode"""

	def __init__(self, message: str):
		super().__init__(message)
		self.message = message


class DHL24UnreachableError(Exception):
	"""DHL24 didn't answer, or not with SOAP"""

	def __init__(self, error: Exception):
		self.message = _("DHL24 couldn't be reached: {0}").format(error)
		super().__init__(self.message)


class AuthData(dict):
	"""
	The authData of a request, which leaves its password out of what is printed of it. Frappe logs a traceback
	with the variables of each frame on a developer site, and only hides a dict's own keys.
	"""

	def __repr__(self):
		return repr({**self, "password": "********"})

	__str__ = __repr__


def get_soap_client(wsdl: str) -> Client:
	"""
	The zeep client of a WSDL. It is kept for the life of the process, as zeep reads the WSDL with a request
	of its own, and erpnext_shipping makes a provider for every call.
	"""
	if wsdl not in _soap_clients:
		try:
			_soap_clients[wsdl] = Client(
				wsdl,
				settings=Settings(strict=False, xml_huge_tree=True),
				transport=Transport(timeout=30, operation_timeout=60),
			)
		except UNREACHABLE as e:
			raise DHL24UnreachableError(e) from None
	return _soap_clients[wsdl]


class DHL24Client:
	"""The DHL24 WebAPI, with the account of DHL24 Settings"""

	def __init__(self, settings):
		self.soap = get_soap_client(SANDBOX_WSDL if settings.use_sandbox else PRODUCTION_WSDL)
		self.auth = AuthData(username=settings.username, password=settings.get_password("password"))

	def call(self, operation: str, **kwargs):
		return self.send(operation, authData=self.auth, **kwargs)

	def send(self, operation: str, **kwargs):
		"""An operation of DHL24, whose request has the authData where the operation wants it"""
		# Raised anew without the frames of zeep and requests, whose variables hold the request, password and all
		try:
			return getattr(self.soap.service, operation)(**kwargs)
		except Fault as e:
			raise DHL24Error(e.message) from None
		except UNREACHABLE as e:
			raise DHL24UnreachableError(e) from None

	def get_price(self, shipment: dict) -> tuple[float, float]:
		"""The net price of a GetPriceRequest, and the fuel surcharge on it in percent"""
		result = self.call("getPrice", shipment=shipment)
		return flt(result.price), flt(result.fuelSurcharge)

	def create_shipments(self, shipments: list[dict]) -> list[str]:
		"""Create up to 3 shipments (ShipmentFullData), returning their numbers"""
		result = self.call("createShipments", shipments={"item": shipments})
		return [str(item.shipmentId) for item in get_items(result) if item.shipmentId]

	def get_labels(self, shipment_ids: list[str], label_type: str) -> dict[str, bytes]:
		"""The labels of shipments by number, which DHL24 gives 3 at a time"""
		labels = {}
		for batch in batched(shipment_ids, BATCH_SIZE, strict=False):
			items = [{"labelType": label_type, "shipmentId": shipment_id} for shipment_id in batch]
			for label in get_items(self.call("getLabels", itemsToPrint={"item": items})):
				labels[str(label.shipmentId)] = base64.b64decode(label.labelData)
		return labels

	def get_track_and_trace_info(self, shipment_id: str):
		"""Where a shipment has been: its events, and who received it once delivered"""
		return self.call("getTrackAndTraceInfo", shipmentId=shipment_id)

	def delete_shipments(self, shipment_ids: list[str]) -> list:
		"""Delete shipments that no courier is booked for, returning whether each was (id, result, error)"""
		return get_items(self.call("deleteShipments", shipments={"item": shipment_ids}))

	def book_courier(
		self,
		pickup_date: str,
		time_from: str,
		time_to: str,
		shipment_ids: list[str],
		additional_info: str | None = None,
	) -> list[str]:
		"""Book a courier for shipments that are sent from one address, returning the booking's numbers"""
		result = self.call(
			"bookCourier",
			pickupDate=pickup_date,
			pickupTimeFrom=time_from,
			pickupTimeTo=time_to,
			additionalInfo=additional_info,
			shipmentIdList={"item": shipment_ids},
		)
		return [str(order_id) for order_id in get_items(result)]

	def cancel_courier_booking(self, order_ids: list[str]) -> list:
		"""Cancel courier bookings, returning whether each was (id, result, error)"""
		return get_items(self.call("cancelCourierBooking", orders={"item": order_ids}))

	def get_my_shipments_count(self, created_from: str, created_to: str) -> int:
		"""How many shipments the account created in the days given"""
		return cint(self.call("getMyShipmentsCount", createdFrom=created_from, createdTo=created_to))

	def get_pnp(self, date: str, shipment_type: str = "ALL") -> bytes:
		"""
		The PNP (Potwierdzenie Nadania Przesyłek) of a day: the PDF that lists the shipments sent on it, which
		the courier signs. `shipment_type` is ALL, EX (domestic parcels), DR (domestic freight), PR or 2EUROPE.
		"""
		# getPnp is the one operation that takes the authData inside its request
		result = self.send("getPnp", pnpRequest={"authData": self.auth, "date": date, "type": shipment_type})
		return base64.b64decode(result.fileData)


def get_items(result) -> list:
	"""The items of an ArrayOf… result, which zeep gives as a list, or as an object that holds it"""
	if result is None:
		return []
	if isinstance(result, list):
		return result
	return list(getattr(result, "item", None) or [])
