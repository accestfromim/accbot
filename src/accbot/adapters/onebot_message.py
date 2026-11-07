"""Normalize OneBot 11 CQ strings to the same segments as array messages."""

import re

# Split before unescaping: literal text such as &#91;CQ:at,qq=123&#93;
# must never turn into an actual mention. Parameter values may contain '='.
_CQ = re.compile(r"\[CQ:([^,\[\]]+)((?:,[^,=\[\]]+=[^,\[\]]*)*)\]")


def _unescape(value: str, *, parameter=False) -> str:
    if parameter:
        value = value.replace("&#44;", ",")
    return value.replace("&#91;", "[").replace("&#93;", "]").replace("&amp;", "&")


def parse_cq(message: str) -> list[dict]:
    segments, offset = [], 0
    for match in _CQ.finditer(message):
        if match.start() > offset:
            segments.append({"type": "text", "data": {"text": _unescape(message[offset : match.start()])}})
        data = {}
        for parameter in match[2].split(",")[1:]:
            name, value = parameter.split("=", 1)
            data[name] = _unescape(value, parameter=True)
        segments.append({"type": match[1], "data": data})
        offset = match.end()
    if offset < len(message):
        segments.append({"type": "text", "data": {"text": _unescape(message[offset:])}})
    return segments
