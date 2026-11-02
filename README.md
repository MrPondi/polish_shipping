### Polish Shipping

Polish shipping providers for [ERPNext Shipping](https://github.com/frappe/erpnext-shipping). Shipments are booked with them and print their labels next to the providers of ERPNext Shipping.

Providers:

- **DHL24**, the WebAPI of DHL Parcel Poland, for parcels within Poland

The providers plug into the `shipping_providers` hook of ERPNext Shipping. Until that hook is in [frappe/erpnext-shipping](https://github.com/frappe/erpnext-shipping), it is in the fork at [MrPondi/erpnext-shipping](https://github.com/MrPondi/erpnext-shipping).

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app erpnext --branch version-16
bench get-app https://github.com/MrPondi/erpnext-shipping --branch develop
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app polish_shipping
```

Installing the app installs ERPNext Shipping with it.

### DHL24

#### Setup

1. Ask DHL for WebAPI access, through the contact form on [dhl24.com.pl](https://dhl24.com.pl/pl/pomoc-techniczna/bledy.html#contact) with the category "WebAPI - uzyskanie dostępu". DHL gives you a username (the WebAPI key) and a password, and your account has a SAP number, the customer number DHL bills.
2. Open **DHL24 Settings**, enter the username, password and SAP Number, and tick **Enabled**. Saving checks the username and password with DHL24. Tick **Use Sandbox** for a sandbox account of DHL24, which books nothing real.
3. Pick the services Shipments can be booked with: Domestic (AH), Premium, Domestic 09 and 12, and Evening Delivery.

The other settings:

- **Default Service**: the service the Book dialog starts with.
- **Label Type**: BLP is the label as a PDF, LBLP the label on an A4 page, LP the waybill.
- **Payer** and **Cost Center** (MPK): who pays, and the cost center printed on the waybill.
- **Comment**: printed on the waybill, up to 100 characters. It is a Jinja template with `doc`, the Shipment, and `sales_orders`, the Sales Orders of its Delivery Notes. With WooCommerce orders synced to Sales Orders, for example: `Zam. nr {{ sales_orders|map(attribute="woocommerce_id")|join(", ") }}`.

Installing the app adds a Parcel Service named "DHL", whose URL Reference makes the tracking links of DHL24 Shipments. A Parcel Service of that name that the site has already stays as it is.

#### Booking in batches

Shipments are booked with DHL24 as an operator books them on the DHL24 site: all at once, from one list. The **DHL24 Shipment** list shows:

- **To Book**: every submitted Shipment that isn't booked yet;
- **Booked** and **Courier Booked**: the Shipments booked with DHL24 whose pickup date is today or later.

The list has no records of its own: its rows are read from the Shipments each time it loads, and a row opens its Shipment. A submitted Shipment that isn't booked has **Book with DHL24**, which opens the list. DHL24 offers no rates in Fetch Shipping Rates.

Select rows, then use the Actions menu:

- **Book** books the selected Shipments that aren't booked yet, with one service, in the background. The pickup date picked becomes the date of the DHL24 shipments and of the Shipments. With **Book a Courier**, one courier per pickup address comes for the selected Shipments that have none, in the hours given, and its number is kept on them. A summary then says what was booked, what wasn't and why, and opens the labels and DHL24's protocol of the day.
- **Book Courier** books one courier for Shipments booked with DHL24 that are picked up from the same address.
- **Print Labels** opens the labels of the selected Shipments in one PDF.

**Print PNP** opens DHL24's protocol of the shipments sent on a day (Potwierdzenie Nadania Przesyłek), which the courier signs. Labels and protocols are fetched from DHL24 each time, not stored.

What DHL24 needs of a Shipment:

- Prices are net, with DHL's fuel surcharge, and Shipments are insured for their Value of Goods.
- The street and house number of each Address are told apart from its first line, as in "ul. Prosta 12/4" or "Prosta 12 m. 4". The second line counts when it holds just numbers, such as the flat number.
- Each parcel needs its length, width, height and weight. Parcels are packages, or pallets when the Shipment's Pallets field says Yes.
- DHL24 takes at most 17 characters for a city. A longer one, such as "Grodzisk Mazowiecki", has to be shortened in the Address.

On a Shipment booked with DHL24, **Print Shipping Label** attaches its label, and **Update Tracking** fetches where the parcel is. ERPNext Shipping also updates tracking every day.

Cancelling a Shipment booked with DHL24 deletes its DHL24 shipment. DHL24 deletes a shipment only when no courier is booked for it, so the courier booking is cancelled first. When that booking was also for other Shipments, they lose it and are named, so that a courier can be booked for them again. A shipment DHL24 keeps, such as a parcel already on its way, is reported, and the Shipment is cancelled all the same.

#### What DHL24 doesn't do here yet

- Deliveries outside Poland, cash on delivery and delivery to DHL points
- Labels for Zebra printers, as ERPNext Shipping saves labels as PDF files

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/polish_shipping
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

To add a provider, subclass `ShippingProvider` of ERPNext Shipping and register it under `shipping_providers` in `hooks.py`, as DHL24 is. `polish_shipping/utils/address.py` has helpers that Polish carriers share, for street and house numbers, phone numbers and postcodes.

### CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI: Installs ERPNext, ERPNext Shipping and this app, and runs the tests on every push to the `develop` branch and on pull requests. ERPNext Shipping comes from the repository and branch set at the top of `.github/workflows/ci.yml`.
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.

### License

gpl-3.0
