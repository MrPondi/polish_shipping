// The dialogs of the DHL24 Shipment list, where Shipments are booked with DHL24 in batches

const dhl24 = frappe.provide("polish_shipping.dhl24");

// The realtime event that booking Shipments ends with
const BOOKING_DONE = "dhl24_booking_done";

const labels_url = (shipments) =>
	`/api/method/polish_shipping.dhl24.batch.download_labels?shipments=${encodeURIComponent(
		JSON.stringify(shipments)
	)}`;
const pnp_url = (date) =>
	`/api/method/polish_shipping.dhl24.batch.download_pnp?date=${encodeURIComponent(date)}`;

// The pickup day and hours of the first Shipment, as the dialogs start with them
async function get_pickup(shipment) {
	const { message } = await frappe.db.get_value("Shipment", shipment, [
		"pickup_date",
		"pickup_from",
		"pickup_to",
		"pickup_type",
	]);
	const today = frappe.datetime.get_today();
	return {
		pickup_date: message.pickup_date > today ? message.pickup_date : today,
		pickup_from: message.pickup_from || "09:00",
		pickup_to: message.pickup_to || "17:00",
		pickup_type: message.pickup_type,
	};
}

function courier_fields(pickup, depends_on) {
	return [
		{
			fieldname: "pickup_from",
			fieldtype: "Time",
			label: __("Ready From"),
			default: pickup.pickup_from,
			depends_on,
			mandatory_depends_on: depends_on,
		},
		{
			fieldname: "pickup_to",
			fieldtype: "Time",
			label: __("Ready Until"),
			default: pickup.pickup_to,
			depends_on,
			mandatory_depends_on: depends_on,
		},
		{
			fieldname: "note",
			fieldtype: "Data",
			label: __("Note for the Courier"),
			description: __("Up to 50 characters"),
			depends_on,
		},
	];
}

// Book the Shipments that aren't booked yet with a DHL24 service, and a courier for those without one
dhl24.book = async function (shipments, on_done) {
	if (!shipments.length) {
		frappe.throw(__("Select the Shipments to book."));
	}

	const [{ message: options }, pickup] = await Promise.all([
		frappe.call("polish_shipping.dhl24.batch.get_booking_options"),
		get_pickup(shipments[0]),
	]);

	const dialog = new frappe.ui.Dialog({
		title: __("Book {0} Shipments with DHL24", [shipments.length]),
		fields: [
			{
				fieldname: "product",
				fieldtype: "Select",
				label: __("Service"),
				options: options.products,
				default: options.default,
				reqd: 1,
			},
			{
				fieldname: "pickup_date",
				fieldtype: "Date",
				label: __("Pickup Date"),
				description: __("The day the parcels are sent"),
				default: pickup.pickup_date,
				reqd: 1,
			},
			{
				fieldname: "book_courier",
				fieldtype: "Check",
				label: __("Book a Courier"),
				default: pickup.pickup_type === "Self delivery" ? 0 : 1,
			},
			...courier_fields(pickup, "eval:doc.book_courier"),
		],
		primary_action_label: __("Book"),
		primary_action(values) {
			frappe.realtime.off(BOOKING_DONE);
			frappe.realtime.on(BOOKING_DONE, (summary) => {
				frappe.realtime.off(BOOKING_DONE);
				show_summary(summary);
				on_done && on_done(summary);
			});

			frappe.call({
				method: "polish_shipping.dhl24.batch.book",
				args: { shipments, ...values },
				freeze: true,
				callback(r) {
					if (r.exc) return;
					dialog.hide();
					frappe.show_alert({
						message: __("Booking {0} Shipments with DHL24", [shipments.length]),
						indicator: "blue",
					});
				},
				error() {
					frappe.realtime.off(BOOKING_DONE);
				},
			});
		},
	});
	dialog.show();
};

// Book a courier for Shipments booked with DHL24
dhl24.book_courier = async function (shipments, on_booked) {
	if (!shipments.length) {
		frappe.throw(__("Select the Shipments to book a courier for."));
	}

	const pickup = await get_pickup(shipments[0]);
	const dialog = new frappe.ui.Dialog({
		title: __("Book DHL Courier"),
		fields: [
			{
				fieldname: "pickup_date",
				fieldtype: "Date",
				label: __("Pickup Date"),
				default: pickup.pickup_date,
				reqd: 1,
			},
			...courier_fields(pickup, null).map((field) => ({
				...field,
				reqd: field.fieldtype === "Time",
			})),
		],
		primary_action_label: __("Book"),
		primary_action({ note, ...values }) {
			frappe.call({
				method: "polish_shipping.dhl24.courier.book_courier",
				args: { shipments, additional_info: note, ...values },
				freeze: true,
				freeze_message: __("Booking a DHL courier"),
				callback(r) {
					if (r.exc) return;
					dialog.hide();
					frappe.show_alert({
						message: __("DHL courier booked: {0}", [r.message]),
						indicator: "green",
					});
					on_booked && on_booked(r.message);
				},
			});
		},
	});
	dialog.show();
};

// The labels of Shipments booked with DHL24, in one PDF
dhl24.print_labels = function (shipments) {
	if (!shipments.length) {
		frappe.throw(__("Select the Shipments to print the labels of."));
	}
	window.open(labels_url(shipments));
};

// DHL24's protocol of the shipments sent on a day, which the courier signs
dhl24.print_pnp = function () {
	frappe.prompt(
		[
			{
				fieldname: "date",
				fieldtype: "Date",
				label: __("Pickup Date"),
				default: frappe.datetime.get_today(),
				reqd: 1,
			},
		],
		({ date }) => window.open(pnp_url(date)),
		__("Print DHL24 Protocol (PNP)"),
		__("Print")
	);
};

function show_summary(summary) {
	const link = (name) => frappe.utils.get_form_link("Shipment", name, true);
	const escape = frappe.utils.escape_html;
	const list = (items) => `<ul>${items.map((item) => `<li>${item}</li>`).join("")}</ul>`;
	const sections = [];

	if (summary.booked.length) {
		sections.push(`<p>${__("Booked")}</p>${list(summary.booked.map(link))}`);
	}
	if (summary.failed.length) {
		const rows = summary.failed.map((row) => `${link(row.shipment)}: ${escape(row.error)}`);
		sections.push(`<p>${__("Not booked")}</p>${list(rows)}`);
	}
	for (const courier of summary.couriers) {
		sections.push(
			`<p>${__("DHL courier booked: {0}, for {1}", [
				escape(courier.booking),
				courier.shipments.map(link).join(", "),
			])}</p>`
		);
	}
	for (const row of summary.courier_errors) {
		sections.push(
			`<p>${__("No courier for {0}: {1}", [
				row.shipments.map(link).join(", "),
				escape(row.error),
			])}</p>`
		);
	}

	const has_labels = summary.labels.length > 0;
	const dialog = new frappe.ui.Dialog({
		title: __("Booked with DHL24"),
		fields: [{ fieldtype: "HTML", fieldname: "summary", options: sections.join("") }],
		primary_action_label: has_labels ? __("Open Labels") : __("Close"),
		primary_action() {
			if (has_labels) {
				window.open(labels_url(summary.labels));
			} else {
				dialog.hide();
			}
		},
		secondary_action_label: has_labels ? __("Open PNP") : null,
		secondary_action: has_labels ? () => window.open(pnp_url(summary.pickup_date)) : null,
	});
	dialog.show();
}
