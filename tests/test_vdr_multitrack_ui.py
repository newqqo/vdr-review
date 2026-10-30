"""Fast checks for the opt-in multitrack browser harness; no cases/browsers."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "vdr_review"))
from verify_multitrack import (CHANNELS, active_second, assert_map_only_network, canvas_time,  # noqa: E402
                              create_multitrack_fixture, synthetic_record, validate_activity,
                              validate_band_pixels, validate_rows)


def test_multitrack_uses_the_same_exact_map_only_network_policy():
    tile = "https://wmts.nlsc.gov.tw/wmts/EMAP/default/GoogleMapsCompatible/15/14222/27407"
    details = {"url": tile, "method": "GET", "resource_type": "image", "has_body": False}
    assert_map_only_network({"network_requests": [tile], "network_details": [details]})
    for changed in ({"method": "POST"}, {"has_body": True}, {"url": "https://example.com/audio.wav"}):
        with pytest.raises(AssertionError, match="Unexpected outgoing request"):
            altered = {**details, **changed}
            assert_map_only_network({"network_requests": [altered["url"]], "network_details": [altered]})


def test_small_four_track_fixture_is_exact_reusable_and_has_explicit_missing_minute(tmp_path):
    summary = create_multitrack_fixture(tmp_path)
    assert summary["files"] == 7 and summary["bytes"] == 2070238
    assert summary["missing"] == {"C1": [[60, 120]]}
    assert create_multitrack_fixture(tmp_path) == summary
    names = [p.name for p in tmp_path.rglob("*.dat")]
    assert len([name for name in names if name.startswith("C1")]) == 1
    path = next(tmp_path.rglob("*.dat"))
    path.write_bytes(b"changed synthetic")
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        create_multitrack_fixture(tmp_path)


def test_fixture_refuses_extra_files_instead_of_silently_mixing_inputs(tmp_path):
    create_multitrack_fixture(tmp_path)
    (tmp_path / "foreign.dat").write_bytes(b"foreign")
    with pytest.raises(ValueError, match="unexpected files"):
        create_multitrack_fixture(tmp_path)


def test_minute_header_size_trailers_and_channel_patterns_are_deterministic():
    for channel, rate in CHANNELS.items():
        record = synthetic_record(channel, rate, 0)
        assert len(record) == rate * 60 * 3 // 8 + 34
        assert record[:12] == b"VoyageDtRec " and record[30:32] == record[-2:] == b"\r\n"
        assert int.from_bytes(record[26:30], "big") == len(record) - 32
        assert record == synthetic_record(channel, rate, 0)
    assert active_second("A1", 5) and not active_second("A1", 15)
    assert active_second("A2", 15) and not active_second("A2", 25)
    assert active_second("A3", 25.25) and not active_second("A3", 46.25)
    assert active_second("C1", 10) and not active_second("C1", 25)


def test_canvas_coordinates_map_same_fraction_to_same_global_time():
    assert canvas_time(20, 80, 0.25) == 35
    assert canvas_time(20, 80, 0) == 20 and canvas_time(20, 80, 1) == 80
    for values in ((0, 0, .5), (0, 1, -1), (0, 1, 2), (float("nan"), 1, .5)):
        with pytest.raises(ValueError):
            canvas_time(*values)


def test_track_rows_require_one_active_and_identical_time_spans():
    rows = [{"channel": channel, "active": "true" if i == 0 else "false",
             "start": "0", "end": "60", "visible": True, "width": 1000, "height": 48}
            for i, channel in enumerate(CHANNELS)]
    validate_rows(rows, 4)
    with pytest.raises(AssertionError):
        validate_rows(rows, 6)
    with pytest.raises(AssertionError):
        validate_rows([*rows[:-1], {**rows[-1], "start": "1"}], 4)
    with pytest.raises(AssertionError):
        validate_rows([*rows[:-1], {**rows[-1], "active": "true"}], 4)


def test_activity_chips_require_current_channel_scope_and_explicit_overflow():
    chips = [{"channel": "A1", "start": float(i), "end": i + .5, "target": float(i)} for i in range(40)]
    value = {"visible": True, "text": "聲音活動段；僅列40段，其餘8段；僅已分析部分",
             "channel": "A1", "start": 0, "end": 120, "total": 48, "shown": 40,
             "pending": 1, "unknown": 0, "chips": chips,
             "strip": {"channel": "A1", "count": 2}}
    validate_activity(value, "A1", expect_activity=True)
    with pytest.raises(AssertionError):
        validate_activity({**value, "text": "聲音活動段"}, "A1", expect_activity=True)
    with pytest.raises(AssertionError):
        validate_activity({**value, "end": 20}, "A1", expect_activity=True)
    with pytest.raises(AssertionError):
        validate_activity({**value, "channel": "C1"}, "A1", expect_activity=True)
    with pytest.raises(AssertionError):
        validate_activity({**value, "strip": {"channel": "A1", "count": 0}}, "A1", expect_activity=True)
    clipped = {**value, "start": .25, "chips": [{**c, "target": max(c["start"], .25)} for c in chips]}
    validate_activity(clipped, "A1", expect_activity=True)


def test_activity_band_assertion_checks_rendered_overlay_not_only_segment_metadata():
    validate_band_pixels([205, 222, 221, 255], [241, 244, 243, 255])
    with pytest.raises(AssertionError):
        validate_band_pixels([241, 244, 243, 255], [241, 244, 243, 255])
    with pytest.raises(AssertionError):
        validate_band_pixels([180, 180, 180, 255], [241, 244, 243, 255])
