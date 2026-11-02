from .utils import identity as _


def get_custom_fields():
	return {
		"Shipment": [
			{
				"fieldname": "courier_booking",
				"label": _("Courier Booking"),
				"fieldtype": "Data",
				"description": _("The number of the courier booked for the parcels"),
				"read_only": 1,
				"allow_on_submit": 1,
				"no_copy": 1,
				"translatable": 0,
				"depends_on": "courier_booking",
				"insert_after": "awb_number",
				# Removed with the app
				"module": "Polish Shipping",
			},
		]
	}
