"""
Addresses as Polish carriers take them: the street apart from the house and flat numbers, and postcodes and
phone numbers as digits.
"""

import re

# A street, a house number with or without a letter, then a flat number after "/", "m.", "lok." or a space.
# The house number is the last one but the flat's, so a street that starts with a number ("3 Maja") stays
# whole. A letter apart from the house number ("12 A") counts only at the end or before a "/".
ADDRESS = re.compile(
	r"^(?P<street>.+?)\s+"
	r"(?P<house>\d+[a-z]?|\d+\s[a-z](?=$|\s*/))"
	r"(?:(?:\s*/\s*|\s+(?:m|lok|lokal|mieszk|apt)\.?\s*|\s+)(?P<flat>\d+[a-z]?))?$",
	re.IGNORECASE,
)

# A second address line that holds only numbers: the flat ("m. 4", "4") or the house and flat ("12/4")
NUMBERS_LINE = re.compile(
	r"^/?\s*(?:(?:m|lok|lokal|mieszk|apt)\.?\s*)?\d+[a-z]?(?:\s*/\s*\d+[a-z]?)?$",
	re.IGNORECASE,
)

# "ul. ", "ul " or "ulica " before a street name, but not the "Ul" of "Ulanowska"
STREET_PREFIX = re.compile(r"^(?:ulica\s+|ul\.\s*|ul\s+)", re.IGNORECASE)


def split_street(line1: str | None, line2: str | None = None) -> tuple[str, str, str | None] | None:
	"""
	The street, house number and flat number of an address's lines, or None when they have no house number.
	The second line counts only when it holds numbers alone, such as the flat number.
	"""
	lines = [line1 or ""]
	if line2 and NUMBERS_LINE.match(line2.strip()):
		lines.append(line2)
	text = " ".join(" ".join(lines).replace(",", " ").split())

	if not (match := ADDRESS.match(text)):
		return None

	street = STREET_PREFIX.sub("", match["street"]).strip()
	if not street:
		return None

	return street, match["house"].replace(" ", ""), match["flat"]


def phone_digits(phone: str | None, country_code: str | None) -> str | None:
	"""
	The digits of a phone number, without the country code of a Polish number, which leaves 9 digits. None
	when there is no number. Raises ValueError for a Polish number that doesn't come to 9 digits.
	"""
	digits = re.sub(r"\D", "", phone or "")
	if not digits:
		return None
	if (country_code or "").upper() != "PL":
		return digits

	if digits.startswith("0048"):
		digits = digits[4:]
	elif digits.startswith("48") and len(digits) == 11:
		digits = digits[2:]
	elif digits.startswith("0") and len(digits) == 10:
		# The trunk prefix of before 2005, as in "0 22 …"
		digits = digits[1:]

	if len(digits) != 9:
		raise ValueError(phone)
	return digits


def postcode_digits(postcode: str | None, country_code: str | None) -> str:
	"""The digits of a postcode. Raises ValueError for a Polish one that isn't 5 digits."""
	digits = re.sub(r"\D", "", postcode or "")
	if (country_code or "").upper() == "PL" and len(digits) != 5:
		raise ValueError(postcode)
	return digits
