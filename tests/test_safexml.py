from __future__ import annotations

import pytest

from mcp_kdenlive import safexml

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">]>
<mlt>&lol2;</mlt>"""

EXTERNAL = b"""<?xml version="1.0"?>
<!DOCTYPE mlt [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<mlt>&xxe;</mlt>"""


@pytest.mark.parametrize(
    "payload", [BILLION_LAUGHS, EXTERNAL, b'<!DOCTYPE mlt SYSTEM "http://example.com/x.dtd"><mlt/>']
)
def test_dtd_and_entities_are_refused(payload):
    with pytest.raises(safexml.UnsafeXMLError):
        safexml.parse_bytes(payload)


def test_undeclared_kdenlive_prefix_is_accepted():
    root = safexml.parse_bytes(b'<mlt><filter kdenlive:id="x"/></mlt>')
    assert root[0].get("kdenlive:id") == "x"


def test_comments_and_processing_instructions_are_kept():
    root = safexml.parse_bytes(b"<mlt><!-- keep me --><?pi data?><a/></mlt>")
    assert [type(c.tag).__name__ for c in root] == ["function", "function", "str"]


def test_malformed(tmp_path):
    with pytest.raises(safexml.ParseError):
        safexml.parse_bytes(b"<mlt><open></mlt>")


def test_size_limit(tmp_path):
    big = tmp_path / "big.xml"
    big.write_bytes(b"<mlt>" + b" " * 2000 + b"</mlt>")
    with pytest.raises(safexml.XMLTooLargeError):
        safexml.parse_file(big, max_bytes=1000)
    root, data = safexml.parse_file(big, max_bytes=5000)
    assert root.tag == "mlt" and len(data) == 2011
