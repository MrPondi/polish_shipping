# Copyright (c) 2026, Karol Parzonka and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import today
from frappe.utils.jinja import validate_template

from polish_shipping.dhl24.client import DHL24Client, DHL24Error, DHL24UnreachableError
from polish_shipping.dhl24.request import PRODUCTS


class DHL24Settings(Document):
	def validate(self):
		validate_template(self.comment_template)

		# What is missing is reported by the mandatory check, which runs after this
		if not (self.enabled and self.username and self.password and self.account_number):
			return

		self.validate_account_number()
		self.validate_products()
		self.check_credentials()

	def validate_account_number(self):
		if not (self.account_number.isdigit() and len(self.account_number) <= 7):
			frappe.throw(_("The SAP Number is a number of up to 7 digits."))

	def validate_products(self):
		products = self.get_products()
		if not products:
			frappe.throw(_("Pick at least one of the services."))
		if self.default_product not in products:
			frappe.throw(
				_("The Default Service, {0}, isn't one of the services picked.").format(
					PRODUCTS.get(self.default_product, self.default_product)
				)
			)

	def check_credentials(self):
		# Today's shipment count is the cheapest request that needs a valid username and password
		try:
			DHL24Client(self).get_my_shipments_count(today(), today())
		except DHL24Error as e:
			frappe.throw(_("DHL24 refused the username or password: {0}").format(e.message), title=_("DHL24"))
		except DHL24UnreachableError as e:
			frappe.throw(e.message, title=_("DHL24"))

	def get_products(self) -> list[str]:
		"""The codes of the DHL24 products offered, such as AH"""
		return [code for code in PRODUCTS if self.get(f"product_{code.lower()}")]
