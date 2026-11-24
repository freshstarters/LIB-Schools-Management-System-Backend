"""Liberian phone numbers as E.164 ``+231`` plus nine digits."""

import re

_SEPARATORS = re.compile(r"[\s().-]+")


class InvalidLiberianPhone(ValueError):
    """The value is not a Liberian number."""


def normalize_liberian_phone(value):
    """Return ``+231`` and nine national digits.

    ``0770123456``, ``770123456``, ``231770123456``, and ``+231770123456``
    are the same number. Any other length or country code is rejected.
    """
    if not isinstance(value, str):
        raise InvalidLiberianPhone("Enter a Liberian phone number.")
    cleaned = _SEPARATORS.sub("", value.strip())
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    if not cleaned.isdigit():
        raise InvalidLiberianPhone("Enter a Liberian phone number.")
    if cleaned.startswith("231"):
        national = cleaned[3:]
    elif cleaned.startswith("0"):
        national = cleaned[1:]
    else:
        national = cleaned
    if len(national) != 9:
        raise InvalidLiberianPhone("Enter a Liberian phone number.")
    return f"+231{national}"
