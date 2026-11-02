# Copyright (c) 2026, Karol Parzonka and contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import today
from requests.exceptions import ConnectionError

from polish_shipping.dhl24.client import DHL24Error, DHL24UnreachableError
from polish_shipping.dhl24.tests.utils import SETTINGS, clear_settings_cache

CLIENT = "polish_shipping.polish_shipping.doctype.dhl24_settings.dhl24_settings.DHL24Client"


def make_settings(**values):
	settings = frappe.get_doc("DHL24 Settings")
	settings.update({**SETTINGS, "password": "_Test password", **values})
	return settings


class TestDHL24Settings(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.addCleanup(clear_settings_cache)
		self.addCleanup(frappe.db.rollback)
		patcher = patch(CLIENT)
		self.client_class = patcher.start()
		self.addCleanup(patcher.stop)

	def test_credentials_are_checked(self):
		make_settings().save()
		self.client_class.return_value.get_my_shipments_count.assert_called_once_with(today(), today())

	def test_credentials_dhl24_refuses_throw(self):
		self.client_class.return_value.get_my_shipments_count.side_effect = DHL24Error(
			"Nieprawidłowe dane autoryzacyjne"
		)
		self.assertRaises(frappe.ValidationError, make_settings().save)

		self.client_class.return_value.get_my_shipments_count.side_effect = DHL24UnreachableError(
			ConnectionError("No route")
		)
		self.assertRaises(frappe.ValidationError, make_settings().save)

	def test_disabled_settings_arent_checked(self):
		make_settings(enabled=0, account_number="not a number").save()
		self.client_class.assert_not_called()

	def test_sap_number(self):
		for account_number in ("12345678", "12a"):
			with self.subTest(account_number=account_number):
				self.assertRaises(frappe.ValidationError, make_settings(account_number=account_number).save)

	def test_default_service_is_one_offered(self):
		self.assertRaises(frappe.ValidationError, make_settings(product_ah=0, product_pr=1).save)
		self.assertRaises(frappe.ValidationError, make_settings(product_ah=0).save)
		make_settings(product_ah=0, product_pr=1, default_product="PR").save()

	def test_products(self):
		settings = make_settings(product_12=1, product_dw=1)
		self.assertEqual(settings.get_products(), ["AH", "12", "DW"])

	def test_comment_template(self):
		self.assertRaises(
			frappe.ValidationError, make_settings(enabled=0, comment_template="{{ doc.name").save
		)
		make_settings(enabled=0, comment_template="Zam. {{ doc.name }}").save()
