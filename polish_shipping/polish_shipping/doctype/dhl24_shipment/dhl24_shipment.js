// Copyright (c) 2026, Karol Parzonka and contributors
// For license information, please see license.txt

// A DHL24 Shipment is read from its Shipment, which is the one to open
frappe.ui.form.on("DHL24 Shipment", {
	refresh(frm) {
		frappe.set_route("Form", "Shipment", frm.doc.name);
	},
});
