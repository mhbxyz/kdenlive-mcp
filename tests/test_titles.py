"""Basic titles (#15), in the format Kdenlive 26.08.2 writes from its title editor.

Captured in the GUI lab (research/lab): a .kdenlivetitle opened in the title editor, then
"Add as new Title" and Save As. The bin clip is a `kdenlivetitle` producer with an empty
`resource`, `kdenlive:clip_type` 2 and the whole title document in `xmldata`:

    <kdenlivetitle LC_NUMERIC="C" duration="74" height="1080" out="73" width="1920">
      <item type="QGraphicsTextItem" z-index="0">
       <position x="200" y="400"><transform>1,0,0,0,1,0,0,0,1</transform></position>
       <content alignment="0" box-height="218" box-width="673.047" font="Noto Sans"
         font-color="255,255,255,255" font-italic="0" font-outline="2"
         font-outline-color="0,0,0,255" font-pixel-size="80" font-underline="0"
         font-weight="50" letter-spacing="0" shadow="0;#ff000000;21968;0;0" tab-width="80"
         typewriter="0;2;1;0;0">Été à Kyōto — 東京
    Deuxième ligne</content>
      </item>
     <startviewport rect="0,0,1920,1080"/><endviewport rect="0,0,1920,1080"/>
     <background color="0,0,0,0"/>
    </kdenlivetitle>

MLT's kdenlivetitle producer centres the text inside `box-width` when `alignment` is 4
(Qt::AlignHCenter), which the server uses for centred titles (checked with melt 7.42).
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from test_timeline_edit import Session

from mcp_kdenlive import edits, titles, writer
from mcp_kdenlive import timeline_edit as te
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.kdenlive_doc import props

FIX = Path(__file__).parent / "fixtures"
FONTS = {"Noto Sans", "DejaVu Sans", "Liberation Serif"}


@pytest.fixture
def s(tmp_path, monkeypatch) -> Session:
    monkeypatch.setattr(titles, "installed_fonts", lambda: FONTS)
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    session = Session(tmp_path / "p")  # kd26-ntsc-timeline: 1920x1080 29.97, AV clip [0,60)
    session.apply(edits.add_track, "video")  # V2, above the footage
    return session


def title_producer(s: Session, bin_id: str) -> ET.Element:
    edit = writer.open_for_edit(str(s.current))
    return next(
        el
        for el in edit.root
        if el.tag == "producer"
        and props(el).get("kdenlive:id") == bin_id
        and any(e.get("producer") == el.get("id") for e in edit.doc.main_bin.findall("entry"))
    )


def document(s: Session, bin_id: str) -> ET.Element:
    return ET.fromstring(props(title_producer(s, bin_id))["xmldata"])


def code(s: Session, *args, **kwargs) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(ProjectError) as err:
        s.apply(titles.add_title, *args, **kwargs)
    return err.value.code


# ------------------------------------------------------------------ format -------------
def test_title_in_kdenlive_format(s):
    r = s.apply(titles.add_title, "Bonjour", "V2", 30, duration=90)
    p = props(title_producer(s, r.bin_id))
    assert {k: p[k] for k in ("mlt_service", "kdenlive:clip_type", "resource", "length",
            "eof", "kdenlive:folderid", "meta.media.width", "meta.media.height")} == {
        "mlt_service": "kdenlivetitle", "kdenlive:clip_type": "2", "resource": "",
        "length": "90", "eof": "pause", "kdenlive:folderid": "-1",
        "meta.media.width": "1920", "meta.media.height": "1080",
    }  # fmt: skip
    assert p["kdenlive:clipname"] == "Bonjour"
    assert p["kdenlive:duration"] == "00:00:03:00"
    doc = document(s, r.bin_id)
    assert (doc.get("width"), doc.get("height"), doc.get("duration"), doc.get("out")) == (
        "1920", "1080", "90", "89",
    )  # fmt: skip
    (item,) = doc.findall("item")
    assert item.get("type") == "QGraphicsTextItem"
    content = item.find("content")
    assert content is not None and content.text == "Bonjour"
    assert content.get("font") == "Noto Sans"
    assert content.get("font-color") == "255,255,255,255"
    assert doc.find("background").get("color") == "0,0,0,0"  # transparent over the footage
    v2 = next(t for t in s.timeline().tracks if t.label == "V2")
    assert [(c.start, c.end, c.bin_id) for c in v2.clips] == [(30, 120, r.bin_id)]
    assert r.clips[0].ref == "V2@30" and r.font == "Noto Sans" and r.font_checked


def test_unicode_and_multiline_text_is_kept(s):
    text = "Été à Kyōto — 東京\r\nDeuxième ligne 🎬"
    r = s.apply(titles.add_title, text, "V2", 0)
    content = document(s, r.bin_id).find("item/content")
    assert content is not None and content.text == "Été à Kyōto — 東京\nDeuxième ligne 🎬"
    assert props(title_producer(s, r.bin_id))["kdenlive:clipname"] == "Été à Kyōto — 東京"


def test_style(s):
    r = s.apply(
        titles.add_title, "Fin", "V2", 0, font="DejaVu Sans", font_size=120, color="#FF8000C0",
        outline=3, outline_color="#102030", italic=True,
    )  # fmt: skip
    c = document(s, r.bin_id).find("item/content")
    assert c is not None
    assert {k: c.get(k) for k in ("font", "font-pixel-size", "font-color", "font-outline",
            "font-outline-color", "font-italic")} == {
        "font": "DejaVu Sans", "font-pixel-size": "120", "font-color": "255,128,0,192",
        "font-outline": "3", "font-outline-color": "16,32,48,255", "font-italic": "1",
    }  # fmt: skip


@pytest.mark.parametrize(
    ("options", "alignment", "x", "y", "width"),
    [
        # presets: a box inside 5 % margins (96 px, 54 px); one line of 80 px is 109 px high
        ({}, "4", 96, 485, 1728),  # centred
        ({"vertical": "bottom"}, "4", 96, 917, 1728),  # 1080 - 54 - 109
        ({"vertical": "top", "align": "left"}, "1", 96, 54, 1728),
        ({"align": "right"}, "2", 96, 485, 1728),
        ({"x": 300, "y": 200, "align": "left"}, "1", 300, 200, 1620),  # box to the right edge
    ],
)
def test_position(s, options, alignment, x, y, width):
    r = s.apply(titles.add_title, "Titre", "V2", 0, font_size=80, **options)
    item = document(s, r.bin_id).find("item")
    assert item is not None
    content, position = item.find("content"), item.find("position")
    assert content is not None and position is not None
    assert content.get("alignment") == alignment
    assert (position.get("x"), position.get("y")) == (str(x), str(y))
    assert (content.get("box-width"), content.get("box-height")) == (str(width), "109")
    assert (r.box.x, r.box.y, r.box.width, r.box.height) == (x, y, width, 109)


# ------------------------------------------------------------------ refusals -----------
@pytest.mark.parametrize(
    ("args", "kwargs", "expected"),
    [
        (("", "V2", 0), {}, "invalid_argument"),
        (("   \n ", "V2", 0), {}, "invalid_argument"),
        (("a\x07b", "V2", 0), {}, "invalid_argument"),
        (("x" * 5001, "V2", 0), {}, "invalid_argument"),
        (("Hi", "V2", 0), {"duration": 0}, "invalid_argument"),
        (("Hi", "V2", -1), {}, "invalid_argument"),
        (("Hi", "V2", 0), {"font_size": 0}, "invalid_argument"),
        (("Hi", "V2", 0), {"color": "red"}, "invalid_argument"),
        (("Hi", "V2", 0), {"outline": 99}, "invalid_argument"),
        (("Hi", "V2", 0), {"x": 10}, "invalid_argument"),  # x without y
        (("Hi", "V9", 0), {}, "track_not_found"),
        (("Hi", "A1", 0), {}, "incompatible_track"),
        (("Hi", "V1", 0), {}, "overlap"),  # the footage is there
        (("Hi", "V2", 0), {"font": "Comic Nonexistent"}, "dependency_missing"),
    ],
)
def test_refusals(s, args, kwargs, expected):
    assert code(s, *args, **kwargs) == expected


def test_missing_font_names_close_matches(s):
    with pytest.raises(ProjectError) as err:
        s.apply(titles.add_title, "Hi", "V2", 0, font="noto sans mono")
    assert "Noto Sans" in str(err.value)
    r = s.apply(titles.add_title, "Hi", "V2", 0, font="noto sans")  # case-insensitive match
    assert r.font == "Noto Sans"


def test_fonts_unchecked_without_fontconfig(s, monkeypatch):
    monkeypatch.setattr(titles, "installed_fonts", lambda: None)
    r = s.apply(titles.add_title, "Hi", "V2", 0, font="Whatever Sans")
    assert not r.font_checked and r.font == "Whatever Sans"
    assert any("fc-list" in w for w in r.warnings)


def test_default_font_is_an_installed_one(s, monkeypatch):
    monkeypatch.setattr(titles, "installed_fonts", lambda: {"DejaVu Sans"})
    assert s.apply(titles.add_title, "Hi", "V2", 0).font == "DejaVu Sans"


# ------------------------------------------------------------------ timeline -----------
def test_titles_are_ordinary_clips_afterwards(s):
    s.apply(titles.add_title, "Hi", "V2", 0, duration=60)
    s.apply(te.trim_clip, "V2@0", s.rev(), end=40)
    s.apply(te.move_clip, "V2@0", s.rev(), 20)
    s.apply(te.split_clip, "V2@20", s.rev(), 30)
    v2 = next(t for t in s.timeline().tracks if t.label == "V2")
    assert [(c.start, c.end) for c in v2.clips] == [(20, 30), (30, 60)]
    assert all(c.service == "kdenlivetitle" for c in v2.clips)


def test_titles_can_be_inserted_again_from_the_bin(s):
    r = s.apply(titles.add_title, "Hi", "V2", 0, duration=30)
    s.apply(te.insert_clip, r.bin_id, "V2", 100, audio_track=None)
    v2 = next(t for t in s.timeline().tracks if t.label == "V2")
    assert [(c.start, c.end) for c in v2.clips] == [(0, 30), (100, 130)]
