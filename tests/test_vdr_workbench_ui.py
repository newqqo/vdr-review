"""Pure checks for the developer-only workbench browser harness."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "vdr_review"))
from verify_workbench import (coordinate, create_workbench_fixture,  # noqa: E402
                              create_layout_fixture, layout_project, navigation_record, nmea,
                              cue_click_project, validate_click_seek, validate_layout,
                              validate_network, validate_transcript_space)


def test_navigation_fixture_is_small_exact_reusable_and_gps_only(tmp_path):
    result = create_workbench_fixture(tmp_path)
    assert result["files"] == 10 and result["bytes"] < 2_100_000
    assert result["navigation_points"] == 120
    assert create_workbench_fixture(tmp_path) == result
    assert len(list((tmp_path / "navigation").rglob("*.log"))) == 2
    assert not list((tmp_path / "audio_only").rglob("*.log"))
    data = navigation_record(0)
    assert data.startswith(b"\xff\xff\x00\x00\x00\x01$GPGGA,150000")
    assert data.count(b"$GPGGA") == data.count(b"$VRHDT") == data.count(b"$GPVTG") == 60
    assert b"VDM" not in data and b"VDO" not in data


def test_navigation_checksum_coordinate_and_refusal_to_overwrite(tmp_path):
    assert nmea("GPHDT,90.0,T") == b"$GPHDT,90.0,T*0C\r\n"
    assert coordinate(22.5, True) == "2230.0000"
    assert coordinate(120.5, False) == "12030.0000"
    create_workbench_fixture(tmp_path)
    log = next(tmp_path.rglob("*.log"))
    log.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        create_workbench_fixture(tmp_path)
    (tmp_path / "foreign.dat").write_bytes(b"foreign")
    with pytest.raises(ValueError, match="unexpected files"):
        create_workbench_fixture(tmp_path)


def test_layout_requires_two_desktop_panes_and_stacked_narrow_without_overflow():
    rect = lambda x, y, w, h: {"x": x, "y": y, "width": w, "height": h}
    desktop = {"viewport": 1440, "document_width": 1440,
               "navigation": rect(20, 300, 640, 550), "editor": rect(680, 300, 740, 650),
               "composer": rect(700, 340, 700, 100), "map": rect(20, 300, 640, 400)}
    validate_layout(desktop, narrow=False)
    narrow = {"viewport": 390, "document_width": 390,
              "navigation": rect(10, 300, 370, 350), "editor": rect(10, 670, 370, 650),
              "composer": rect(20, 710, 350, 100), "map": rect(10, 300, 370, 300)}
    validate_layout(narrow, narrow=True)
    with pytest.raises(AssertionError):
        validate_layout({**narrow, "document_width": 800}, narrow=True)
    with pytest.raises(AssertionError):
        validate_layout(desktop, narrow=True)


def test_network_policy_does_not_hide_a_missing_local_asset_as_a_map_error():
    evidence = {"requests": [], "request_failures": [], "page_errors": [],
                "downloads": [], "console_errors": []}
    validate_network(evidence)
    tile = "https://wmts.nlsc.gov.tw/wmts/PHOTO2/default/GoogleMapsCompatible/2/1/1"
    validate_network({**evidence, "request_failures": [{"url": tile, "failure": "network"}],
                      "console_errors": ["Failed to load resource: net::ERR_CONNECTION_RESET"]})
    with pytest.raises(AssertionError, match="application asset"):
        validate_network({**evidence, "request_failures": [{"url": "file:///output/images/layers.png"}],
                          "console_errors": ["Failed to load resource: net::ERR_FILE_NOT_FOUND"]})


def test_four_channel_layout_fixture_is_separate_exact_and_reusable(tmp_path):
    root = tmp_path / "four_channels"
    value = create_layout_fixture(root)
    assert value["channels"] == ["A1", "A2", "A3", "C1"]
    assert value["files"] == 10 and value["bytes"] < 2_400_000
    assert create_layout_fixture(root) == value
    assert len(list((root / "Audio").rglob("*.dat"))) == 8
    old = create_workbench_fixture(tmp_path / "old_two_channels")
    assert old["channels"] == ["A1", "C1"] and old["files"] == 10
    first = next((root / "Audio").rglob("*.dat"))
    first.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        create_layout_fixture(root)


def test_layout_fixture_rejects_foreign_files(tmp_path):
    create_layout_fixture(tmp_path)
    (tmp_path / "foreign.dat").write_bytes(b"foreign")
    with pytest.raises(ValueError, match="unexpected files"):
        create_layout_fixture(tmp_path)


def test_layout_project_populates_every_channel_without_mutating_binding():
    base = {"version": 4, "recording_id": "synthetic", "state": {"A1": []}, "uid": 1}
    result = layout_project(base)
    assert result["recording_id"] == "synthetic" and base["state"] == {"A1": []}
    assert set(result["state"]) == {"A1", "A2", "A3", "C1"}
    cues = [cue for values in result["state"].values() for cue in values]
    assert len(cues) == len({cue["id"] for cue in cues}) == 72
    assert all(len(values) == 18 for values in result["state"].values())
    assert result["uid"] == 73 and max(cue["end"] for cue in cues) < 120
    assert len({len(cue["text"]) for cue in cues}) >= 2


def transcript_measure(**changes):
    return {"row_count": 18, "document_width": 1366, "viewport_width": 1366,
            "viewport_height": 768, "client_height": 320, "scroll_height": 1200,
            "visible_height": 320, "visible_width": 600, "fully_visible_rows": 4,
            "overflow_y": "auto", "composer_outside_head": True, "waves_outside_head": True,
            **changes}


def test_transcript_measure_rejects_one_line_and_outer_pane_only_checks():
    validate_transcript_space(transcript_measure())
    with pytest.raises(AssertionError):
        validate_transcript_space(transcript_measure(client_height=40, visible_height=40, fully_visible_rows=1))
    with pytest.raises(AssertionError):
        validate_transcript_space(transcript_measure(visible_height=25, fully_visible_rows=0))
    with pytest.raises(AssertionError):
        validate_transcript_space(transcript_measure(row_count=0))
    with pytest.raises(AssertionError):
        validate_transcript_space(transcript_measure(composer_outside_head=False))


def test_transcript_measure_proves_real_scrolling_and_constrained_layout():
    value = transcript_measure(document_width=683, viewport_width=683, viewport_height=384,
                               visible_height=205, fully_visible_rows=2)
    validate_transcript_space(value, constrained=True)
    with pytest.raises(AssertionError):
        validate_transcript_space(value)
    with pytest.raises(AssertionError, match="real list scrolling"):
        validate_transcript_space(transcript_measure(scroll_height=320))


def test_cue_click_project_starts_reviewed_and_retains_real_gaps():
    value = cue_click_project({"version": 4, "recording_id": "synthetic", "state": {}})
    assert value["recording_id"] == "synthetic"
    for values in value["state"].values():
        assert len(values) == 18 and all(cue["st"] == 1 for cue in values)
        assert all(left["end"] < right["start"] for left, right in zip(values, values[1:]))
    assert value["state"]["A1"][14]["start"] == 73


def test_click_seek_check_rejects_bubbled_double_seek_or_wrong_position():
    validate_click_seek([2.85], 2.85)
    with pytest.raises(AssertionError, match="duplicate bubbling"):
        validate_click_seek([2.85, 2.85], 2.85)
    with pytest.raises(AssertionError):
        validate_click_seek([], 2.85)
    with pytest.raises(AssertionError):
        validate_click_seek([3.5], 2.85)
