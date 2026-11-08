from __future__ import annotations

import pytest

from mcp_kdenlive.timecode import (
    FrameRate,
    TimeFormatError,
    frames_to_clock,
    frames_to_seconds,
    frames_to_timecode,
    parse_time,
)

PAL = FrameRate(25, 1)
NTSC = FrameRate(30000, 1001)


@pytest.mark.parametrize(
    ("text", "rate", "frames"),
    [
        ("125", PAL, 125),
        ("00:00:04.960", PAL, 124),
        ("00:00:03.720", PAL, 93),
        ("00:00:01:11", PAL, 36),  # SMPTE written by older Kdenlive
        ("00:00:01.969", NTSC, 59),
        ("00:00:02.002", NTSC, 60),
        ("01:00:00.000", NTSC, 107892),  # 3600 s at 29.97 fps, rounded
        ("00:01:00;02", NTSC, 1800),  # drop-frame: first label of minute 1
        ("0:04.960", PAL, 124),
        ("00:00:04,960", PAL, 124),
    ],
)
def test_parse_time(text, rate, frames):
    assert parse_time(text, rate) == frames


@pytest.mark.parametrize(
    "rate",
    [FrameRate(30000, 1001), FrameRate(24000, 1001), FrameRate(60000, 1001), PAL, FrameRate(50, 1)],
)
def test_clock_strings_round_trip_frame_exact(rate):
    # Kdenlive writes milliseconds; every frame must survive frames -> clock -> frames.
    for frame in range(0, 400_000, 7):
        assert parse_time(frames_to_clock(frame, rate), rate) == frame


@pytest.mark.parametrize("bad", ["", "abc", "1:2:3:4:5", "00:00:xx"])
def test_invalid_values(bad):
    with pytest.raises(TimeFormatError):
        parse_time(bad, PAL)


def test_labels():
    assert frames_to_timecode(36, PAL) == "00:00:01:11"
    assert frames_to_timecode(60, NTSC) == "00:00:02:00"
    assert frames_to_clock(124, PAL) == "00:00:04.960"
    assert frames_to_seconds(60, NTSC) == pytest.approx(2.002)


def test_invalid_rate():
    with pytest.raises(ValueError, match="invalid frame rate"):
        FrameRate(0, 1)
