// Copyright (c) 2026, Karol Parzonka and contributors
// For license information, please see license.txt

frappe.listview_settings["DHL24 Shipment"] = {
	filters: [["state", "=", "To Book"]],

	get_indicator(doc) {
		const colors = { "To Book": "orange", Booked: "blue", "Courier Booked": "green" };
		return [__(doc.state), colors[doc.state], `state,=,${doc.state}`];
	},

	// A row is a Shipment, which is the form to open
	get_form_link(doc) {
		return `/desk/shipment/${encodeURIComponent(doc.name)}`;
	},

	onload(listview) {
		// Loaded now, so that a click opens the labels in its own moment, which popup blockers let through
		frappe.require("dhl24.bundle.js");
		const with_dhl24 = (callback) =>
			frappe.require("dhl24.bundle.js", () =>
				callback(frappe.provide("polish_shipping.dhl24"))
			);
		const selected = () => listview.get_checked_items(true);
		const refresh = () => listview.refresh();

		listview.page.add_actions_menu_item(
			__("Book"),
			() => with_dhl24((dhl24) => dhl24.book(selected(), refresh)),
			false
		);
		listview.page.add_actions_menu_item(
			__("Book Courier"),
			() => with_dhl24((dhl24) => dhl24.book_courier(selected(), refresh)),
			false
		);
		listview.page.add_actions_menu_item(
			__("Print Labels"),
			() => with_dhl24((dhl24) => dhl24.print_labels(selected())),
			false
		);
		listview.page.add_inner_button(__("Print PNP"), () =>
			with_dhl24((dhl24) => dhl24.print_pnp())
		);
	},
};
