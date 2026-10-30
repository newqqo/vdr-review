"""Fast checks for the opt-in real-browser harness; no browser or cases opened."""
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import wave
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "vdr_review"))
from verify_longform import (START, assert_bounded, assert_map_only_network, create_fixture,  # noqa: E402
                             datetime_local_value, inspect_clip, is_map_tile_request, minute_path,
                             open_page, record_console_error, record_network_request, reveal_control, srt_range)

TILE = "https://wmts.nlsc.gov.tw/wmts/PHOTO2/default/GoogleMapsCompatible/15/14222/27407"


@pytest.mark.parametrize("layer", ["PHOTO2", "EMAP"])
def test_network_allowlist_only_permits_intended_body_free_tile_images(layer):
    url = TILE.replace("PHOTO2", layer)
    assert is_map_tile_request(url)
    assert is_map_tile_request("https://wmts.nlsc.gov.tw/wmts/PHOTO2/default/GoogleMapsCompatible/0/0/0")


@pytest.mark.parametrize("url,method,resource_type,has_body", [
    (TILE, "POST", "image", False),
    (TILE, "GET", "fetch", False),
    (TILE, "GET", "image", True),
    (TILE + "?text=case-transcript", "GET", "image", False),
    (TILE + "#extra", "GET", "image", False),
    (TILE.replace("https:", "http:"), "GET", "image", False),
    (TILE.replace("wmts.nlsc.gov.tw", "wmts.nlsc.gov.tw.example.com"), "GET", "image", False),
    (TILE.replace("wmts.nlsc.gov.tw", "example.com@wmts.nlsc.gov.tw"), "GET", "image", False),
    (TILE.replace("wmts.nlsc.gov.tw", "wmts.nlsc.gov.tw:443"), "GET", "image", False),
    (TILE.replace("PHOTO2", "OTHER"), "GET", "image", False),
    (TILE.replace("/15/14222/27407", "/15/32768/0"), "GET", "image", False),
    (TILE.replace("/15/14222/27407", "/21/0/0"), "GET", "image", False),
    ("https://wmts.nlsc.gov.tw/audio.wav", "GET", "image", False),
    ("https://wmts.nlsc.gov.tw/transcript.srt", "GET", "image", False),
    ("wss://wmts.nlsc.gov.tw/wmts/PHOTO2/default/GoogleMapsCompatible/15/14222/27407", "CONNECT", "websocket", False),
])
def test_network_allowlist_rejects_upload_text_audio_other_hosts_and_websockets(url, method, resource_type, has_body):
    assert not is_map_tile_request(url, method, resource_type, has_body)


def test_network_observation_keeps_legacy_urls_but_requires_method_and_body_metadata():
    evidence = {"network_requests": [], "network_details": []}
    for local in ("file:///C:/tool.html", "blob:null/synthetic", "data:image/png;base64,eA=="):
        record_network_request(evidence, local, "GET", "document")
    record_network_request(evidence, TILE, "GET", "image")
    assert evidence["network_requests"] == [TILE]
    assert_map_only_network(evidence)
    record_network_request(evidence, TILE, "POST", "fetch", True)
    assert evidence["network_details"][-1] == {
        "url": TILE, "method": "POST", "resource_type": "fetch", "has_body": True,
    }
    with pytest.raises(AssertionError, match="Unexpected outgoing request"):
        assert_map_only_network(evidence)
    with pytest.raises(AssertionError, match="Missing request"):
        assert_map_only_network({"network_requests": [TILE]})
    with pytest.raises(AssertionError, match="Inconsistent request"):
        assert_map_only_network({"network_requests": ["https://example.com/"],
                                 "network_details": [evidence["network_details"][0]]})


def test_expected_map_resource_errors_stay_visible_without_hiding_javascript_errors():
    evidence = {"map_console_errors": [], "console_errors": []}
    failed = "Failed to load resource: net::ERR_CONNECTION_RESET"
    record_console_error(evidence, failed, {"url": TILE})
    record_console_error(evidence, "Uncaught Error: player failed", {"url": TILE})
    record_console_error(evidence, failed, {"url": "https://example.com/upload"})
    assert evidence["map_console_errors"] == [failed]
    assert evidence["console_errors"] == ["Uncaught Error: player failed", failed]


def test_browser_observers_preserve_open_page_contract_without_network_interception():
    class Page:
        def __init__(self):
            self.events = {}

        def set_default_timeout(self, _timeout):
            pass

        def on(self, event, callback):
            self.events[event] = callback

        def goto(self, url):
            self.opened = url

        def locator(self, selector):
            assert selector == "#fdir"
            return SimpleNamespace(count=lambda: 1)

    page = Page()
    context = SimpleNamespace(new_page=lambda: page)
    browser = SimpleNamespace(new_context=lambda **_options: context)
    returned_context, returned_page, evidence = open_page(browser, ROOT / "synthetic.html", True)
    assert returned_context is context and returned_page is page
    assert page.opened.startswith("file:")
    page.events["request"](SimpleNamespace(url=TILE, method="GET", resource_type="image", post_data_buffer=None))
    assert_map_only_network(evidence)
    page.events["request"](SimpleNamespace(url="https://example.com/upload", method="POST",
                                           resource_type="fetch", post_data_buffer=b"SYNTHETIC PRIVATE TEXT"))
    assert "SYNTHETIC PRIVATE TEXT" not in json.dumps(evidence)
    assert evidence["network_details"][-1]["has_body"] is True
    with pytest.raises(AssertionError, match="Unexpected outgoing request"):
        assert_map_only_network(evidence)
    page.events["websocket"](SimpleNamespace(url="wss://example.com/socket"))
    assert evidence["network_details"][-1]["resource_type"] == "websocket"


def test_secondary_control_setup_reveals_native_details_without_forced_clicks():
    calls = []

    class Locator:
        def evaluate(self, script):
            calls.append(script)

    def locate(selector):
        assert selector == "#imSrt"
        return Locator()

    reveal_control(SimpleNamespace(locator=locate), "#imSrt")
    assert len(calls) == 1 and "p.tagName==='DETAILS'" in calls[0]
    assert "p.open=true" in calls[0] and "p=p.parentElement" in calls[0]
    assert "click" not in calls[0] and "force" not in calls[0]


def test_longform_fixture_reuses_identical_minutes_and_refuses_changed_source(tmp_path):
    summary = create_fixture(tmp_path, minutes=2)
    assert summary["files"] == 4
    assert summary["bytes"] == 2 * ((315000 + 34) + (180000 + 34))
    first = minute_path(tmp_path, START, "A1")
    raw = first.read_bytes()
    assert raw[:12] == b"VoyageDtRec " and raw[30:32] == raw[-2:] == b"\r\n"
    assert int.from_bytes(raw[26:30], "big") == len(raw) - 32
    assert create_fixture(tmp_path, minutes=2) == summary
    first.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        create_fixture(tmp_path, minutes=2)
    assert first.read_bytes() == b"changed"


def test_longform_fixture_path_carries_date_and_minute_across_utc_midnight(tmp_path):
    stamp = START + timedelta(hours=10, minutes=23)
    path = minute_path(tmp_path, stamp, "C1")
    assert path.parts[-2:] == ("202610240123", "C1230000.dat")


def test_streaming_stats_reject_eager_or_unbounded_allocations():
    safe = {"cacheBytes": 1000, "peakCacheBytes": 2000, "maxCacheBytes": 32 * 1024 * 1024,
            "maxReadSeconds": 60, "peakActiveDecoders": 1}
    assert_bounded(safe)
    for field, value in (("cacheBytes", 40 * 1024 * 1024), ("peakCacheBytes", 40 * 1024 * 1024),
                         ("maxReadSeconds", 14400), ("peakActiveDecoders", 4),
                         ("maxCacheBytes", 128 * 1024 * 1024), ("cacheBytes", float("nan"))):
        with pytest.raises(AssertionError):
            assert_bounded({**safe, field: value})


def test_srt_hours_do_not_wrap_at_midnight():
    assert srt_range("1\n25:00:01,250 --> 25:00:04,500\nSynthetic\n") == (90001.25, 90004.5)


def test_datetime_local_values_match_browser_canonical_zero_seconds_and_fraction():
    assert datetime_local_value(START) == "2026-10-23T15:00"
    assert datetime_local_value(START + timedelta(seconds=2.3)) == "2026-10-23T15:00:02.3"
    assert datetime_local_value(START + timedelta(seconds=2)) == "2026-10-23T15:00:02"


def test_clip_verification_checks_pcm_length_and_output_digest(tmp_path):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(bytes(16000))
    wav = output.getvalue()
    receipt = {"kind": "vdr-transcription-export", "audio_sha256": hashlib.sha256(wav).hexdigest()}
    archive = tmp_path / "synthetic.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("synthetic.wav", wav)
        target.writestr("synthetic_轉錄時間對照.json", json.dumps(receipt))
    checked = inspect_clip(archive, 1)
    assert checked["duration"] == 1 and checked["rate"] == 8000
    with pytest.raises(AssertionError):
        inspect_clip(archive, 2)
