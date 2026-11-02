from frappe.tests import UnitTestCase

from polish_shipping.utils.address import phone_digits, postcode_digits, split_street


class TestSplitStreet(UnitTestCase):
	def test_street_house_and_flat(self):
		cases = {
			"ul. Prosta 1": ("Prosta", "1", None),
			"Prosta 1/2": ("Prosta", "1", "2"),
			"Prosta 1 m. 2": ("Prosta", "1", "2"),
			"Prosta 1 m 2": ("Prosta", "1", "2"),
			"Prosta 1 lok. 2": ("Prosta", "1", "2"),
			"Prosta 1 2": ("Prosta", "1", "2"),
			"Prosta 1, m. 2": ("Prosta", "1", "2"),
			"Prosta 12A m. 3": ("Prosta", "12A", "3"),
			"Prosta 12 A": ("Prosta", "12A", None),
			"Prosta 12 A/3": ("Prosta", "12A", "3"),
			"Al. Jerozolimskie 123A lok. 4": ("Al. Jerozolimskie", "123A", "4"),
			"os. Kosmonautów 12/34": ("os. Kosmonautów", "12", "34"),
			"ulica Długa 7": ("Długa", "7", None),
			"ul.Prosta 4": ("Prosta", "4", None),
		}
		for line, parts in cases.items():
			with self.subTest(line=line):
				self.assertEqual(split_street(line), parts)

	def test_street_that_starts_with_a_number(self):
		self.assertEqual(split_street("ul. 3 Maja 5"), ("3 Maja", "5", None))
		self.assertEqual(split_street("11 Listopada 2"), ("11 Listopada", "2", None))
		self.assertEqual(split_street("Aleja 3 Maja 5 7"), ("Aleja 3 Maja", "5", "7"))

	def test_street_with_letters_like_numbers_or_a_prefix(self):
		self.assertEqual(split_street("Jana Pawła II 5"), ("Jana Pawła II", "5", None))
		# Not "ul" before a street name
		self.assertEqual(split_street("Ulanowska 3"), ("Ulanowska", "3", None))

	def test_village_without_streets(self):
		self.assertEqual(split_street("Wólka 15"), ("Wólka", "15", None))
		self.assertEqual(split_street("Wólka Duża 15A"), ("Wólka Duża", "15A", None))

	def test_second_line_counts_when_it_holds_numbers(self):
		self.assertEqual(split_street("Prosta", "12/4"), ("Prosta", "12", "4"))
		self.assertEqual(split_street("Prosta 12", "m. 3"), ("Prosta", "12", "3"))
		self.assertEqual(split_street("Prosta 12", "3"), ("Prosta", "12", "3"))
		self.assertEqual(split_street("Prosta 12", "piętro 2"), ("Prosta", "12", None))
		self.assertEqual(split_street("Prosta 12", "Kod do bramy 1234"), ("Prosta", "12", None))

	def test_no_house_number(self):
		for line in ("Prosta", "12", "ul. 12", "", None):
			with self.subTest(line=line):
				self.assertIsNone(split_street(line))


class TestPhoneDigits(UnitTestCase):
	def test_polish_number_without_country_code(self):
		for phone in ("+48 600 100 200", "600-100-200", "0048600100200", "48600100200"):
			with self.subTest(phone=phone):
				self.assertEqual(phone_digits(phone, "pl"), "600100200")

	def test_landline_with_old_trunk_prefix(self):
		self.assertEqual(phone_digits("(22) 123 45 67", "PL"), "221234567")
		self.assertEqual(phone_digits("0 22 123 45 67", "PL"), "221234567")

	def test_polish_number_of_other_length(self):
		self.assertRaises(ValueError, phone_digits, "12345", "pl")
		self.assertRaises(ValueError, phone_digits, "+48 600 100 2000", "pl")

	def test_no_number(self):
		self.assertIsNone(phone_digits("", "pl"))
		self.assertIsNone(phone_digits(None, "pl"))
		self.assertIsNone(phone_digits("brak", "pl"))

	def test_foreign_number_keeps_its_digits(self):
		self.assertEqual(phone_digits("+49 30 1234567", "de"), "49301234567")


class TestPostcodeDigits(UnitTestCase):
	def test_polish_postcode(self):
		self.assertEqual(postcode_digits("00-001", "pl"), "00001")
		self.assertEqual(postcode_digits("00 001", "PL"), "00001")
		self.assertRaises(ValueError, postcode_digits, "0001", "pl")
		self.assertRaises(ValueError, postcode_digits, None, "pl")

	def test_foreign_postcode(self):
		self.assertEqual(postcode_digits("1234 AB", "nl"), "1234")
