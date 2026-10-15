"""
Trim the whitespace people accidentally type or paste around form values.

A leading/trailing space (autocomplete adds one on phones, copy-paste brings
others, including non-breaking spaces and zero-width characters) used to be
saved as part of the value — " IM123456" and "IM123456" were two different IR
IDs, and a name with a trailing space looked right but never matched a search.

Applied to every incoming request body by core/parsers.py. Only the ends are
trimmed; spaces inside a value are left alone.

Not trimmed, because there the whitespace can be meaningful:
  * any key containing "password" — a password is exactly what was typed.
  * "content" — the text of a chat message.
"""
import re

# \s already covers Unicode spaces (incl. NBSP); add the zero-width characters
# that get pasted in from messaging apps and are invisible but not whitespace.
_ENDS = re.compile(r"^[\s\u200b\u200c\u200d\u2060\ufeff]+|[\s\u200b\u200c\u200d\u2060\ufeff]+$")

UNTRIMMED_KEYS = {"content"}
UNTRIMMED_KEY_PARTS = ("password",)


def _is_untrimmed(key):
    if not isinstance(key, str):
        return False
    k = key.lower()
    return k in UNTRIMMED_KEYS or any(part in k for part in UNTRIMMED_KEY_PARTS)


def trim_text(value):
    return _ENDS.sub("", value)


def trim_strings(value, key=None):
    """Return value with every string trimmed, recursing into dicts and lists."""
    if isinstance(value, str):
        return value if _is_untrimmed(key) else trim_text(value)
    if isinstance(value, dict):
        return {k: trim_strings(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [trim_strings(v, key) for v in value]
    return value
