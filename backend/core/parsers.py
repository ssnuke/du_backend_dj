"""
DRF parsers that trim whitespace from every text value in a request body (see
core/utils/text_clean.py for what is and isn't trimmed). Installed as the
project-wide default in settings, so every form and API call gets it without
each view having to remember to.
"""
from rest_framework.parsers import DataAndFiles, FormParser, JSONParser, MultiPartParser

from core.utils.text_clean import trim_strings, trim_text, _is_untrimmed


def _trim_querydict(data):
    cleaned = data.copy()  # a mutable copy; parsed QueryDicts are read-only
    for key in list(data.keys()):
        values = data.getlist(key)
        cleaned.setlist(
            key,
            [v if (not isinstance(v, str) or _is_untrimmed(key)) else trim_text(v) for v in values],
        )
    return cleaned


class TrimmingJSONParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        return trim_strings(super().parse(stream, media_type, parser_context))


class TrimmingFormParser(FormParser):
    def parse(self, stream, media_type=None, parser_context=None):
        return _trim_querydict(super().parse(stream, media_type, parser_context))


class TrimmingMultiPartParser(MultiPartParser):
    def parse(self, stream, media_type=None, parser_context=None):
        parsed = super().parse(stream, media_type, parser_context)
        return DataAndFiles(_trim_querydict(parsed.data), parsed.files)
