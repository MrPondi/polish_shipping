import frappe
from frappe import _

from polish_shipping.dhl24.provider import DHL24_ERRORS, get_dhl24_provider


def delete_cancelled_shipment(doc, method=None):
	"""
	Delete the DHL24 shipment of a cancelled Shipment, after cancelling the courier booked for it, as DHL24
	keeps a shipment that a courier is booked for. It never throws: a parcel on its way can't be deleted, and
	the Shipment is cancelled all the same.
	"""
	if not doc.shipment_id or not (provider := get_dhl24_provider(doc.service_provider)):
		return

	try:
		client = provider.get_client()
		if doc.get("courier_booking") and not cancel_courier_booking(client, doc):
			return
		delete_shipment(client, doc)
	except DHL24_ERRORS as e:
		alert(_("DHL24 shipment {0} couldn't be deleted: {1}").format(doc.shipment_id, e.message))
	except Exception:
		frappe.log_error(title=f"DHL24 shipment of Shipment {doc.name} couldn't be deleted")
		alert(
			_("DHL24 shipment {0} couldn't be deleted, see the Error Log. Delete it in DHL24.").format(
				doc.shipment_id
			)
		)


def cancel_courier_booking(client, doc) -> bool:
	"""
	Cancel the courier booked for a Shipment, and say whether it is cancelled. The other Shipments it was
	booked for are left without one, and named, so that a courier can be booked for them again.
	"""
	booking = doc.courier_booking
	failed = [result for result in client.cancel_courier_booking(booking.split(", ")) if not result.result]
	if failed:
		alert(
			_("DHL24 kept courier booking {0}: {1}. Cancel it in DHL24, then delete shipment {2}.").format(
				booking, ", ".join(result.error for result in failed if result.error), doc.shipment_id
			)
		)
		return False

	# The cancelled Shipment keeps the booking's number, for the record
	others = frappe.get_all(
		"Shipment",
		filters={"courier_booking": booking, "docstatus": 1, "name": ["!=", doc.name]},
		pluck="name",
	)
	for name in others:
		frappe.db.set_value("Shipment", name, "courier_booking", None, update_modified=False)

	if others:
		frappe.msgprint(
			_(
				"Courier booking {0} was also for Shipments {1}, which have no courier now. Book one for them again."
			).format(booking, ", ".join(others)),
			title=_("DHL24 Courier Cancelled"),
			indicator="orange",
		)
	return True


def delete_shipment(client, doc):
	results = client.delete_shipments([doc.shipment_id])
	if results and all(result.result for result in results):
		frappe.msgprint(
			_("DHL24 shipment {0} is deleted.").format(doc.shipment_id), indicator="green", alert=True
		)
	else:
		reasons = ", ".join(result.error for result in results if result.error) or _("no reason given")
		alert(_("DHL24 kept shipment {0}: {1}").format(doc.shipment_id, reasons))


def alert(message: str):
	frappe.msgprint(message, indicator="red", alert=True)
