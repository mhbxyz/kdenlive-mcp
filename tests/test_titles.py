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

MLT's kdenlivetitle producer aligns lines inside `box-width` (`alignment` 4 = Qt::AlignHCenter),
but Kdenlive's title editor ignores `box-width` for ordinary text: it shows the item at its
position with the text's own width. So the server writes the text box where the text is (#15).
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from test_timeline_edit import Session

from mcp_kdenlive import edits, safexml, titles, writer
from mcp_kdenlive import timeline_edit as te
from mcp_kdenlive.errors import ProjectError
from mcp_kdenlive.kdenlive_doc import props

FIX = Path(__file__).parent / "fixtures"
FONTS = {"Noto Sans", "DejaVu Sans", "Liberation Serif"}


def fake_width(font: str, italic: bool, size: int, text: str) -> float:
    """Half the font size per character of the longest line (real fonts: fontmetrics)."""
    return size * 0.5 * max(len(line) for line in text.split("\n"))


@pytest.fixture
def s(tmp_path, monkeypatch) -> Session:
    monkeypatch.setattr(titles, "installed_fonts", lambda: FONTS)
    monkeypatch.setattr(titles, "text_width", fake_width)
    shutil.copytree(FIX / "kd26", tmp_path / "p")
    session = Session(tmp_path / "p")  # kd26-ntsc-timeline: 1280x720 29.97, AV clip [0,60)
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
    return safexml.parse_bytes(props(title_producer(s, bin_id))["xmldata"].encode())


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
        "meta.media.width": "1280", "meta.media.height": "720",
    }  # fmt: skip
    assert p["kdenlive:clipname"] == "Bonjour"
    assert p["kdenlive:duration"] == "00:00:03:00"
    doc = document(s, r.bin_id)
    assert (doc.get("width"), doc.get("height"), doc.get("duration"), doc.get("out")) == (
        "1280", "720", "90", "89",
    )  # fmt: skip
    (item,) = doc.findall("item")
    assert item.get("type") == "QGraphicsTextItem"
    content = item.find("content")
    assert content is not None and content.text == "Bonjour"
    assert content.get("font") == "Noto Sans"
    assert content.get("font-color") == "255,255,255,255"
    # Qt 6 weight scale (400 = normal): melt 7.42 draws Kdenlive's legacy "50" as Thin (#92)
    assert content.get("font-weight") == "400"
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
        # Kdenlive's title editor places a text item at its position with the text's own width
        # (box-width only matters for "%s" templates), so the server writes where the text is:
        # "Titre" at 80 px is 200 px wide here; inside 5 % margins (64 px, 36 px), 1152 px wide;
        # one line of 80 px is 109 px high.
        ({}, "4", 540, 305, 200),  # centred: 64 + (1152 - 200) / 2
        ({"vertical": "bottom"}, "4", 540, 575, 200),  # 720 - 36 - 109
        ({"vertical": "top", "align": "left"}, "1", 64, 36, 200),
        ({"align": "right"}, "2", 1016, 305, 200),  # 64 + 1152 - 200
        ({"x": 300, "y": 200, "align": "left"}, "1", 300, 200, 200),
        ({"x": 300, "y": 200, "align": "center"}, "4", 690, 200, 200),  # centred in [300, 1280)
        ({"x": 300, "y": 200, "align": "right"}, "2", 1080, 200, 200),  # ends at the edge
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


def test_timeline_uses_the_bin_producer_like_kdenlive(s):
    """Kdenlive 26.08.2 plays a title from its bin producer (no per-track copy, unlike media)."""
    r = s.apply(titles.add_title, "Hi", "V2", 0, duration=60)
    s.apply(te.split_clip, "V2@0", s.rev(), 30)
    s.apply(te.insert_clip, r.bin_id, "V2", 100, audio_track=None)
    edit = writer.open_for_edit(str(s.current))
    titles_found = [
        el for el in edit.root if el.tag == "producer" and props(el).get("kdenlive:id") == r.bin_id
    ]
    assert len(titles_found) == 1
    v2 = next(t for t in s.timeline().tracks if t.label == "V2")
    assert {c.producer_id for c in v2.clips} == {titles_found[0].get("id")}


def test_removing_the_clip_keeps_the_bin_title(s):
    r = s.apply(titles.add_title, "Hi", "V2", 0, duration=30)
    s.apply(te.remove_clip, "V2@0", s.rev())
    assert props(title_producer(s, r.bin_id))["kdenlive:clipname"] == "Hi"


def test_title_is_defined_before_the_tracks_that_play_it(s):
    """MLT only resolves producers defined earlier in the document (Kdenlive puts them first)."""
    r = s.apply(titles.add_title, "Hi", "V2", 0, duration=30)
    root = writer.open_for_edit(str(s.current)).root
    order = [el.get("id") for el in root]
    title = title_producer(s, r.bin_id).get("id")
    users = [
        pl.get("id") for pl in root.iter("playlist")
        if any(e.get("producer") == title for e in pl.findall("entry"))
    ]  # fmt: skip
    assert all(order.index(title) < order.index(u) for u in users)


def test_emoji_are_reported(s):
    """MLT's title producer (7.42, Kdenlive 26.08.2) renders CJK by font fallback, not emoji."""
    r = s.apply(titles.add_title, "Fin 🎬 東京", "V2", 0)
    assert any("emoji" in w and "🎬" in w for w in r.warnings)
    assert s.apply(titles.add_title, "東京 ★", "V2", 200).warnings == []


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("fc-list") is None, reason="needs fontconfig")
def test_installed_fonts_reads_fontconfig(monkeypatch):
    monkeypatch.undo()  # the real fc-list, not the fixture's list
    fonts = titles.installed_fonts()
    assert fonts and all(isinstance(f, str) and f.strip() == f and f for f in fonts)


# ------------------------------------------------------------------ review fixes ------
def test_xml_special_characters_round_trip(s):
    text = "a < b & \"c\" 'd' ]]> e\tf"
    r = s.apply(titles.add_title, text, "V2", 0)
    content = document(s, r.bin_id).find("item/content")
    assert content is not None and content.text == text


@pytest.mark.parametrize("char", ["￾", "￿"])
def test_characters_xml_cannot_hold_are_refused(s, char):
    assert code(s, f"a{char}b", "V2", 0) == "invalid_argument"


def test_rare_cjk_is_not_mistaken_for_emoji(s):
    assert s.apply(titles.add_title, "𠀋 ☀ ✂", "V2", 0).warnings == []  # U+2000B, U+2600 block


def test_name_is_the_first_non_empty_line(s):
    r = s.apply(titles.add_title, "  \n  Hello  \nworld", "V2", 0)
    assert r.name == "Hello"


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"font_size": 600}, "invalid_range"),  # 817 px of text in a 720 px frame
        ({"x": 1270, "y": 10, "font_size": 40}, "invalid_range"),  # a 10 px wide box
        ({"duration": 200, "start": 0, "track": "V1"}, "overlap"),
    ],
)
def test_boxes_and_timing_that_do_not_fit(s, kwargs, expected):
    args = {"text": "Hi", "track": "V2", "start": 0, **kwargs}
    assert code(s, args.pop("text"), args.pop("track"), args.pop("start"), **args) == expected


def test_title_runs_into_the_next_clip(s):
    s.apply(titles.add_title, "A", "V2", 100, duration=30)
    assert code(s, "B", "V2", 50, duration=60) == "overlap"  # 50..110 hits 100
    s.apply(titles.add_title, "B", "V2", 50, duration=50)  # 50..100 touches it: fine


def test_trimming_past_the_title_duration_is_refused(s):
    s.apply(titles.add_title, "Hi", "V2", 0, duration=30)
    with pytest.raises(ProjectError) as err:
        s.apply(te.trim_clip, "V2@0", s.rev(), end=40)
    assert err.value.code == "invalid_range"


def test_no_default_font_installed(s, monkeypatch):
    monkeypatch.setattr(titles, "installed_fonts", lambda: {"Some Script Font"})
    assert code(s, "Hi", "V2", 0) == "dependency_missing"
    monkeypatch.setattr(titles, "installed_fonts", set)
    assert code(s, "Hi", "V2", 0) == "dependency_missing"


def test_bin_ids_do_not_reuse_folder_ids(s, tmp_path):
    text = s.current.read_text(encoding="utf-8").replace(
        '<property name="kdenlive:folder.-1.2">Sequences</property>',
        '<property name="kdenlive:folder.-1.2">Sequences</property>'
        '<property name="kdenlive:folder.-1.40">Titles</property>',
    )
    s.current = tmp_path / "folders.kdenlive"
    s.current.write_text(text, encoding="utf-8")
    assert int(s.apply(titles.add_title, "Hi", "V2", 0).bin_id) > 40


def test_unmeasured_text_keeps_a_box_across_the_frame(s, monkeypatch):
    """Without font metrics the box spans the safe area: MLT still centres the text, but
    Kdenlive's title editor would show it at the box's left edge, so the result says so."""
    monkeypatch.setattr(titles, "text_width", lambda *a: None)
    r = s.apply(titles.add_title, "Titre", "V2", 0, font_size=80)
    content = document(s, r.bin_id).find("item/content")
    assert content is not None and content.get("box-width") == "1152"
    assert (r.box.x, r.box.width) == (64, 1152)
    assert any("title editor" in w for w in r.warnings)


def test_text_wider_than_the_safe_area(s):
    r = s.apply(titles.add_title, "W" * 40, "V2", 0, font_size=80)  # 1600 px here
    assert r.box.x == 0 and r.box.width == 1600  # centred on the frame, clamped at its left
    assert any("wider than" in w for w in r.warnings)


# ------------------------------------------------------------------ font metrics -------
def _system_font() -> Path | None:
    from mcp_kdenlive import fontmetrics

    for family in ("Noto Sans", "DejaVu Sans", "Liberation Sans"):
        found = fontmetrics.font_file(family, italic=False)
        if found:
            return found
    return None


def test_font_metrics_read_advance_widths():
    from mcp_kdenlive import fontmetrics

    path = _system_font()
    if path is None:
        pytest.skip("no Noto Sans, DejaVu Sans or Liberation Sans")
    width = fontmetrics.text_width(path, 100, "WWWW")
    assert width is not None and width > 0
    assert width == pytest.approx(4 * (fontmetrics.text_width(path, 100, "W") or 0))
    assert (fontmetrics.text_width(path, 100, "iiii") or 0) < width
    two_lines = fontmetrics.text_width(path, 100, "ii\nWWWW")
    assert two_lines == pytest.approx(width)  # the widest line


def test_noto_sans_matches_kdenlive_title_editor():
    """Kdenlive 26.08.2's title editor reports 473 px for this line at 90 px (box-width)."""
    from mcp_kdenlive import fontmetrics

    path = fontmetrics.font_file("Noto Sans", italic=False)
    if path is None or "NotoSans" not in path.name:
        pytest.skip("Noto Sans is not installed")
    assert fontmetrics.text_width(path, 90, "Été à Kyōto\nJour 1") == pytest.approx(473, abs=2)


def test_unreadable_font_files_give_no_width(tmp_path):
    from mcp_kdenlive import fontmetrics

    bad = tmp_path / "bad.ttf"
    bad.write_bytes(b"not a font")
    assert fontmetrics.text_width(bad, 90, "Hi") is None
    assert fontmetrics.text_width(tmp_path / "missing.ttf", 90, "Hi") is None
