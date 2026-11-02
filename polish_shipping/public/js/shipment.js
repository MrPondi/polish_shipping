// Copyright (c) 2026, Karol Parzonka and contributors
// For license information, please see license.txt

frappe.ui.form.on("Shipment", {
	refresh(frm) {
		// Shipments are booked with DHL24 in batches, from the DHL24 Shipment list
		if (frm.doc.docstatus === 1 && !frm.doc.shipment_id) {
			frm.add_custom_button(__("Book with DHL24"), () =>
				frappe.set_route("List", "DHL24 Shipment")
			);
		}
	},
});
