"""Opt-in local Chrome/Edge verification of bounded long-recording playback.

Developer-only; excluded from the colleague package. Synthetic exports stay in
output/playwright. A supplied real source is read-only and is never exported or
sent to a service. Browser audio is muted. No packages are installed and network
requests are observed, not intercepted. Heap observations do not certify an
8 GB workstation or prove sample-accurate/vendor-equivalent playback.
Only body-free HTTPS GET image requests for the intended NLSC WMTS tiles are
allowed; audio, transcript, other-host and WebSocket traffic fail verification.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
import traceback
from urllib.parse import urlsplit
import wave
import zipfile

ROOT = Path(__file__).resolve().parents[2]
START = datetime(2026, 10, 23, 15, tzinfo=timezone.utc)  # 23:00 CST, crosses midnight.
BROWSERS = {
    "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "edge": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
}


def is_map_tile_request(url: str, method: str = "GET", resource_type: str = "image",
                        has_body: bool = False) -> bool:
    """Exact tile allowlist, not a general permission to contact the tile host."""
    if method != "GET" or resource_type != "image" or has_body:
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if (parsed.scheme != "https" or parsed.netloc != "wmts.nlsc.gov.tw"
            or parsed.query or parsed.fragment):
        return False
    match = re.fullmatch(r"/wmts/(?:PHOTO2|EMAP)/default/GoogleMapsCompatible/(\d{1,2})/(\d{1,7})/(\d{1,7})", parsed.path)
    if not match:
        return False
    zoom, row, column = map(int, match.groups())
    return 0 <= zoom <= 20 and 0 <= row < 2 ** zoom and 0 <= column < 2 ** zoom


def record_network_request(evidence: dict, url: str, method: str,
                           resource_type: str, has_body: bool = False) -> None:
    """Observe metadata only. Never retain request bodies or headers."""
    if urlsplit(url).scheme in ("file", "blob", "data", "about"):
        return
    evidence["network_requests"].append(url)
    evidence["network_details"].append({"url": url, "method": method,
                                        "resource_type": resource_type, "has_body": bool(has_body)})


def assert_map_only_network(evidence: dict) -> None:
    details = evidence.get("network_details", [])
    assert len(details) == len(evidence.get("network_requests", [])), "Missing request method/body diagnostics"
    for url, request in zip(evidence.get("network_requests", []), details):
        assert request.get("url") == url, "Inconsistent request URL diagnostics"
        assert is_map_tile_request(**request), "Unexpected outgoing request: " + str(request)


def record_console_error(evidence: dict, text: str, location: dict) -> None:
    # A tile service/firewall failure must not be mistaken for a JS exception;
    # the app's no-basemap fallback remains usable. Keep these failures visible.
    if text.startswith("Failed to load resource:") and is_map_tile_request(location.get("url", "")):
        evidence["map_console_errors"].append(text)
    else:
        evidence["console_errors"].append(text)


def emit(event: str, **values) -> None:
    print(json.dumps({"event": event, **values}, ensure_ascii=False), flush=True)


def minute_path(root: Path, stamp: datetime, channel: str) -> Path:
    return root / "Audio" / stamp.strftime("%Y%m%d%H%M") / (channel + stamp.strftime("%M%S") + "00.dat")


def create_fixture(root: Path, minutes: int = 240) -> dict:
    """Create/reuse clearly synthetic valid G.726 minute containers, never overwrite."""
    if not isinstance(minutes, int) or minutes <= 0 or minutes > 1440:
        raise ValueError("Synthetic duration must be 1..1440 minutes")
    files, total = 0, 0
    for channel, rate in (("A1", 14000), ("C1", 8000)):
        payload = bytes(rate * 60 * 3 // 8)
        header = bytearray(32)
        header[:12] = b"VoyageDtRec "
        header[26:30] = (len(payload) + 2).to_bytes(4, "big")
        header[30:32] = b"\r\n"
        record = bytes(header) + payload + b"\r\n"
        expected_hash = hashlib.sha256(record).digest()
        for minute in range(minutes):
            path = minute_path(root, START + timedelta(minutes=minute), channel)
            if path.exists():
                if path.stat().st_size != len(record) or hashlib.sha256(path.read_bytes()).digest() != expected_hash:
                    raise ValueError("Refusing to overwrite changed synthetic fixture: " + str(path))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(record)
            files += 1
            total += len(record)
    return {"synthetic": True, "files": files, "bytes": total, "minutes": minutes,
            "start_utc": START.isoformat(), "end_utc": (START + timedelta(minutes=minutes)).isoformat()}


def assert_bounded(stats: dict) -> None:
    for field in ("cacheBytes", "peakCacheBytes", "maxCacheBytes", "maxReadSeconds", "peakActiveDecoders"):
        if not isinstance(stats.get(field), (int, float)) or not math.isfinite(stats[field]):
            raise AssertionError("Missing/nonfinite streaming statistic: " + field)
    assert 0 <= stats["cacheBytes"] <= stats["maxCacheBytes"]
    assert stats["peakCacheBytes"] <= stats["maxCacheBytes"]
    assert stats["maxCacheBytes"] <= 32 * 1024 * 1024
    assert stats["maxReadSeconds"] <= 120.00001
    assert stats["peakActiveDecoders"] <= 1


def srt_range(text: str) -> tuple[float, float]:
    match = re.search(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})", text)
    if not match:
        raise AssertionError("No SRT cue")
    def seconds(values):
        h, m, s, ms = map(int, values)
        return h * 3600 + m * 60 + s + ms / 1000
    return seconds(match.groups()[:4]), seconds(match.groups()[4:])


def datetime_local_value(value: datetime) -> str:
    """Match Chromium's canonical datetime-local value, including zero seconds."""
    result = value.strftime("%Y-%m-%dT%H:%M:%S")
    milliseconds = value.microsecond // 1000
    if milliseconds:
        return result + "." + f"{milliseconds:03d}".rstrip("0")
    return result[:-3] if value.second == 0 else result


def inspect_clip(path: Path, expected_seconds: float) -> dict:
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        waves = [name for name in archive.namelist() if name.lower().endswith(".wav")]
        assert len(waves) == 1
        receipts = [json.loads(archive.read(name)) for name in archive.namelist() if name.lower().endswith(".json")]
        receipt = next((value for value in receipts if value.get("kind") == "vdr-transcription-export"), None)
        source_manifest = next((value for value in receipts if "output_wav_sha256" in value), None)
        assert receipt is not None, "Missing transcription time receipt"
        digest = hashlib.sha256()
        with archive.open(waves[0]) as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        assert receipt["audio_sha256"] == digest.hexdigest()
        with archive.open(waves[0]) as source, wave.open(source, "rb") as decoded:
            duration = decoded.getnframes() / decoded.getframerate()
            assert decoded.getnchannels() == 1 and decoded.getsampwidth() == 2
            assert abs(duration - expected_seconds) < 0.001
            return {"entries": archive.namelist(), "wav_bytes": archive.getinfo(waves[0]).file_size, "duration": duration,
                    "rate": decoded.getframerate(), "receipt": receipt,
                    "export_cache_stats": source_manifest.get("cache_verification") if source_manifest else None}


def wait_until(page, predicate, stage: str, timeout: float = 300) -> None:
    deadline, next_heartbeat = time.monotonic() + timeout, time.monotonic() + 15
    while time.monotonic() < deadline:
        if predicate():
            return
        if time.monotonic() >= next_heartbeat:
            emit("waiting", stage=stage,
                 load_message=page.locator("#loadmsg").inner_text(),
                 play_status=page.locator("#playstatus").inner_text())
            next_heartbeat = time.monotonic() + 15
        page.wait_for_timeout(100)
    raise TimeoutError(stage)


def diagnostic(page) -> dict:
    return page.evaluate("""() => {
      const d=window.__vdrReview,p=d.player,c=d.channels||d.store.channels;
      return {currentTime:p.currentTime,duration:p.duration,channel:p.channel,
        paused:p.paused,seeking:p.seeking,readyState:p.readyState,error:p.error?.message||null,
        W0:d.W0,W1:d.W1,stats:d.store.stats(),
        channels:Object.entries(c).map(([name,v])=>({name,rate:v.rate,codec:v.codec,
          records:v.sources?.length,ownsFullPCM:Object.hasOwn(v,'pcm'),ownsFullBlob:Object.hasOwn(v,'blob')})),
        heap:performance.memory?{used:performance.memory.usedJSHeapSize,total:performance.memory.totalJSHeapSize,limit:performance.memory.jsHeapSizeLimit}:null};
    }""")


def open_page(browser, html: Path, synthetic: bool):
    context = browser.new_context(accept_downloads=synthetic, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    page.set_default_timeout(60000)
    evidence = {"network_requests": [], "network_details": [], "page_errors": [],
                "console_errors": [], "map_console_errors": [], "downloads": []}
    page.on("request", lambda req: record_network_request(evidence, req.url, req.method,
                                                           req.resource_type, req.post_data_buffer is not None))
    page.on("websocket", lambda socket: record_network_request(evidence, socket.url, "CONNECT", "websocket"))
    page.on("pageerror", lambda error: evidence["page_errors"].append(str(error)))
    page.on("console", lambda message: record_console_error(evidence, message.text, message.location)
            if message.type == "error" else None)
    page.on("download", lambda item: evidence["downloads"].append(item.suggested_filename))
    page.on("dialog", lambda dialog: dialog.accept() if synthetic else dialog.dismiss())
    page.goto(html.resolve().as_uri())
    assert page.locator("#fdir").count() == 1
    return context, page, evidence


def load_recording(page, source: Path, review_hours: int | None = None) -> dict:
    page.locator("#fdir").set_input_files(str(source), timeout=120000)
    wait_until(page, lambda: not page.locator("#loadbtn").is_disabled()
               or page.locator("#dirsum").inner_text() != "檢查錄音格式與日期中…", "source index")
    summary = page.locator("#dirsum").inner_text()
    assert not page.locator("#loadbtn").is_disabled(), summary
    if page.locator("#allRecording").count():
        page.locator("#allRecording").click()
    start_text = page.locator("#wstart").input_value()
    if review_hours:
        start = datetime.strptime(start_text, "%Y-%m-%d %H:%M:%S")
        page.locator("#wend").fill((start + timedelta(hours=review_hours)).strftime("%Y-%m-%d %H:%M:%S"))
    window = [page.locator("#wstart").input_value(), page.locator("#wend").input_value()]
    page.locator("#loadbtn").click()
    wait_until(page, lambda: page.locator("#mainpane").is_visible()
               or "載入失敗" in page.locator("#loadmsg").inner_text(), "load long recording")
    assert page.locator("#mainpane").is_visible(), page.locator("#loadmsg").inner_text()
    page.wait_for_function("window.__vdrReview && !__vdrReview.player.seeking && __vdrReview.player.readyState>0", timeout=120000)
    first = diagnostic(page)
    assert_bounded(first["stats"])
    assert all(not c["ownsFullPCM"] and not c["ownsFullBlob"] for c in first["channels"])
    total_records = sum(c["records"] or 0 for c in first["channels"])
    assert first["stats"]["decodedRecords"] < total_records / 2, "Eager full-source decode at load"
    if review_hours:
        assert abs(first["duration"] - review_hours * 3600) < 0.05
    selected = page.locator("#fdir").evaluate("e=>({files:e.files.length,bytes:Array.from(e.files).reduce((n,f)=>n+f.size,0)})")
    return {"source": str(source), "directory_summary": summary, "window_local": window,
            "selected": selected, "initial": first}


def seek(page, seconds: float) -> dict:
    page.locator("#seek").evaluate("""(e,t)=>{e.value=String(t);
      e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));}""", seconds)
    page.wait_for_function("t=>!__vdrReview.player.seeking && Math.abs(__vdrReview.player.currentTime-t)<0.25", arg=seconds, timeout=120000)
    result = diagnostic(page)
    assert not result["error"], result["error"]
    assert_bounded(result["stats"])
    result["display_clock"] = page.locator("#clock").inner_text()
    return result


def playback_checks(page, result: dict) -> None:
    duration = result["initial"]["duration"]
    positions = [0.5, duration / 2, duration - 1.5, min(3602, duration - 2)]
    result["seeks"] = [seek(page, value) for value in positions]
    channels = page.locator("#ch option").evaluate_all("els=>els.map(e=>e.value)")
    assert len(channels) >= 2
    at = diagnostic(page)["currentTime"]
    result["channel_switches"] = []
    for channel in channels:
        page.locator("#ch").select_option(channel)
        page.wait_for_function("ch=>!__vdrReview.player.seeking && __vdrReview.player.channel===ch", arg=channel, timeout=120000)
        switched = diagnostic(page)
        assert abs(switched["currentTime"] - at) < 0.25
        assert not switched["error"]
        assert_bounded(switched["stats"])
        result["channel_switches"].append(switched)
    page.locator("#ch").select_option(channels[0])
    page.wait_for_function("ch=>!__vdrReview.player.seeking && __vdrReview.player.channel===ch", arg=channels[0], timeout=120000)
    seconds_to_midnight = 86400 - ((result["initial"]["W0"] / 1000 + 8 * 3600) % 86400)
    crossings = [("cross_minute_playback", 58)]
    if 2 < seconds_to_midnight < duration - 2:
        crossings.append(("cross_midnight_playback", seconds_to_midnight - 2))
    for key, start in crossings:
        seek(page, start)
        page.locator("#playbtn").click()
        page.wait_for_function("t=>__vdrReview.player.currentTime>=t && !__vdrReview.player.seeking", arg=start + 4, timeout=45000)
        page.locator("#playbtn").click()
        crossing = diagnostic(page)
        crossing["display_clock"] = page.locator("#clock").inner_text()
        assert crossing["paused"] and crossing["currentTime"] >= start + 4 and not crossing["error"]
        assert_bounded(crossing["stats"])
        result[key] = crossing
    result["final"] = diagnostic(page)
    assert_bounded(result["final"]["stats"])


def enable_virtual_project_file(page) -> None:
    page.evaluate("""() => {
      window.__longformWrites=[];window.__longformPickerCalls=0;
      const h={name:'SYNTHETIC_LONGFORM.vdr.json',createWritable:async()=>({
        write:async d=>window.__longformPending=d,
        close:async()=>window.__longformWrites.push(await window.__longformPending.text())})};
      window.showSaveFilePicker=async()=>{window.__longformPickerCalls++;return h};
      window.showOpenFilePicker=undefined;
    }""")


def save_project(page, previous: int = 0) -> dict:
    page.locator("#saveP").click()
    page.wait_for_function("n=>__longformWrites.length>n", arg=previous)
    return page.evaluate("JSON.parse(__longformWrites.at(-1))")


def reveal_control(page, selector: str) -> None:
    """Open native details ancestors for an explicitly tested secondary action."""
    page.locator(selector).evaluate("""e=>{
      for(let p=e.parentElement;p;p=p.parentElement){if(p.tagName==='DETAILS')p.open=true;}
    }""")


def import_srt(page, path: Path, *, receipt: Path | None = None, local_start: str | None = None,
               panel_screenshot: Path | None = None) -> None:
    reveal_control(page, "#imSrt")
    page.locator("#imSrt").set_input_files(str(path))
    page.locator("#srtImportPanel").wait_for(state="visible")
    page.locator("#srtImportChannel").select_option("A1")
    if receipt:
        page.locator("#srtReceiptFile").set_input_files(str(receipt))
        page.wait_for_function("!document.querySelector('#srtImportApply').disabled || document.querySelector('#srtImportError').textContent.trim()")
        assert not page.locator("#srtImportError").inner_text().strip(), page.locator("#srtImportError").inner_text()
        values = page.locator("#srtImportOrigin option").evaluate_all("els=>els.map(e=>e.value).filter(v=>v.startsWith('receipt:'))")
        page.locator("#srtImportOrigin").select_option(values[-1])
    else:
        page.locator("#srtImportOrigin").select_option("manual")
        page.locator("#srtLocalStart").fill(local_start)
    if panel_screenshot:
        page.screenshot(path=str(panel_screenshot), full_page=True)
    page.locator("#srtImportApply").click()
    page.locator("#srtImportPanel").wait_for(state="hidden")


def synthetic_roundtrip(page, out: Path, time_offset: float = 0) -> dict:
    first = diagnostic(page)
    raw_start = first["W0"] / 1000
    offset, length = 3658, 6
    local = datetime.fromtimestamp(raw_start + 8 * 3600, timezone.utc)
    reveal_control(page, "#exportChannel")
    page.locator("#exportChannel").select_option("A1")
    page.locator("#exportStart").fill((local + timedelta(seconds=offset)).strftime("%Y-%m-%d %H:%M:%S"))
    page.locator("#exportEnd").fill((local + timedelta(seconds=offset + length)).strftime("%Y-%m-%d %H:%M:%S"))
    if time_offset:
        page.locator("#tcbtn").click()
        page.locator("#tcofs").fill(str(time_offset))
        page.locator("#tcbasis").fill("SYNTHETIC timing precision check; not an evidence calibration")
        page.locator("#tcok").click()
        for field, seconds in (("#exportStart", offset), ("#exportEnd", offset + length)):
            shifted = datetime.fromisoformat(page.locator(field).input_value())
            assert abs((shifted - local.replace(tzinfo=None)).total_seconds() - seconds - time_offset) < 0.001
    with page.expect_download(timeout=120000) as download:
        page.locator("#exportRange").click()
    clip = out / "synthetic-range.zip"
    download.value.save_as(clip)
    exported = inspect_clip(clip, length)
    receipt = exported["receipt"]
    assert abs(datetime.fromisoformat(receipt["source_start_utc"].replace("Z", "+00:00")).timestamp() - raw_start - offset) < 0.001
    assert receipt["time_correction"]["offset_s"] == time_offset
    assert abs(datetime.fromisoformat(receipt["corrected_start_utc"].replace("Z", "+00:00")).timestamp() - raw_start - offset - time_offset) < 0.001
    receipt_path = out / "synthetic-transcription-receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    external = out / "synthetic-external.srt"
    external.write_text("1\n00:00:01,000 --> 00:00:04,000\nSYNTHETIC LONGFORM ROUNDTRIP\n", encoding="utf-8")
    import_srt(page, external, receipt=receipt_path,
               panel_screenshot=out / "synthetic-srt-import-panel.png")
    enable_virtual_project_file(page)
    project = save_project(page)
    cue = next(c for c in project["state"]["A1"] if "SYNTHETIC LONGFORM" in c["text"])
    assert abs(cue["start"] - offset - 1) < 0.001 and abs(cue["end"] - offset - 4) < 0.001
    reveal_control(page, "#exSrt")
    with page.expect_download() as download:
        page.locator("#exSrt").click()
    full_srt = out / "synthetic-review-window.srt"
    download.value.save_as(full_srt)
    assert srt_range(full_srt.read_text(encoding="utf-8-sig")) == (offset + 1, offset + 4)
    reveal_control(page, "#edreset")
    page.locator("#edreset").click()
    import_srt(page, full_srt, local_start=datetime_local_value(local + timedelta(seconds=time_offset)))
    page.locator("#edlist .tx").first.fill("SYNTHETIC LONGFORM EDITED")
    project = save_project(page, 1)
    assert page.evaluate("__longformPickerCalls") == 1
    assert project["state"]["A1"][0]["start"] == offset + 1
    assert project["state"]["A1"][0]["end"] == offset + 4
    project_path = out / "SYNTHETIC_LONGFORM.vdr.json"
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return {"export": exported, "time_offset_s": time_offset,
            "receipt_alignment": [cue["start"], cue["end"]],
            "srt_roundtrip": [offset + 1, offset + 4], "project_file": str(project_path),
            "same_file_handle_reused": True, "file_permission_boundary": "virtual; actual OS permission not tested"}


def run_one(browser, html: Path, source: Path, out: Path, *, synthetic: bool,
            review_hours: int | None, whole_export: bool = False, skip_roundtrip: bool = False,
            time_offset: float = 0) -> dict:
    context, page, evidence = open_page(browser, html, synthetic)
    result = {"synthetic": synthetic, "review_hours": review_hours, "ok": False}
    try:
        emit("scenario_started", synthetic=synthetic, hours=review_hours)
        result.update(load_recording(page, source, review_hours))
        emit("source_loaded", synthetic=synthetic, hours=review_hours,
             selected=result["selected"], initial=result["initial"])
        playback_checks(page, result)
        emit("playback_passed", synthetic=synthetic, hours=review_hours,
             final=result["final"])
        if synthetic and review_hours == 4:
            page.screenshot(path=str(out / "synthetic-longform-main.png"), full_page=True)
            if not skip_roundtrip:
                result["roundtrip"] = synthetic_roundtrip(page, out, time_offset)
            else:
                result["roundtrip_skipped"] = True
            if whole_export:
                emit("whole_export_started", synthetic_hours=4, expected_wav_bytes=403200044)
                reveal_control(page, "#exportWhole")
                with page.expect_download(timeout=600000) as download:
                    page.locator("#exportChannel").select_option("A1")
                    page.locator("#exportWhole").click()
                whole = out / "synthetic-whole-4h.zip"
                download.value.save_as(whole)
                result["whole_export"] = inspect_clip(whole, 14400)
                assert_bounded(result["whole_export"]["export_cache_stats"])
        assert not evidence["page_errors"], evidence["page_errors"]
        assert not evidence["console_errors"], evidence["console_errors"]
        assert_map_only_network(evidence)
        if not synthetic:
            assert not evidence["downloads"]
        result["ok"] = True
    except Exception as error:
        result["failure"] = type(error).__name__ + ": " + str(error)
        result["traceback"] = traceback.format_exc()
        result["load_message"] = page.locator("#loadmsg").inner_text()
        result["play_status"] = page.locator("#playstatus").inner_text()
        try:
            result["failure_diagnostic"] = diagnostic(page)
        except Exception:
            pass
        emit("scenario_failed", failure=result["failure"])
    finally:
        result.update(evidence)
        context.close()
    if result.get("roundtrip") and result["ok"]:
        # Reopen in a clean browser storage context, not just the same editor object.
        context, page, reopened_evidence = open_page(browser, html, True)
        try:
            load_recording(page, source, review_hours)
            page.evaluate("window.showOpenFilePicker=undefined")
            page.locator("#imJson").set_input_files(result["roundtrip"]["project_file"])
            page.locator("#projectImportPanel").wait_for(state="visible")
            reveal_control(page, "#projectImportReplace")
            page.locator("#projectImportReplace").click()
            page.wait_for_function("document.querySelector('#edlist .tx')?.value==='SYNTHETIC LONGFORM EDITED'")
            enable_virtual_project_file(page)
            reopened = save_project(page)
            assert reopened["state"]["A1"][0]["start"] == 3659
            result["roundtrip"]["clean_context_reopened"] = True
            assert not reopened_evidence["page_errors"] and not reopened_evidence["console_errors"]
            assert_map_only_network(reopened_evidence)
        except Exception as error:
            result["ok"] = False
            result["reopen_failure"] = type(error).__name__ + ": " + str(error)
        finally:
            result["reopen_evidence"] = reopened_evidence
            context.close()
    emit("scenario_done", synthetic=synthetic, hours=review_hours, ok=result["ok"])
    return result


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", choices=BROWSERS, default="chrome")
    parser.add_argument("--html", type=Path, default=ROOT / "output" / "VDR核聽工具.html")
    parser.add_argument("--out", type=Path, default=ROOT / "output" / "playwright" / "vdr-longform")
    parser.add_argument("--fixture", type=Path, help="Reuse an exact synthetic fixture between browser output folders")
    parser.add_argument("--real-source", type=Path, help="Explicitly authorized real source; read-only, never exported")
    parser.add_argument("--only", choices=("synthetic", "real", "both"), default="synthetic")
    parser.add_argument("--whole-export", action="store_true", help="Also export and verify the 4h synthetic source (~403 MB WAV)")
    parser.add_argument("--review-hours", type=int, choices=(2, 4), action="append",
                        help="Synthetic windows to run; default runs both 2h and 4h")
    parser.add_argument("--skip-roundtrip", action="store_true",
                        help="Explicit export-only diagnostic mode; does not count as a roundtrip pass")
    parser.add_argument("--fractional-offset", action="store_true", help="Also verify a synthetic +2.3s display correction")
    args = parser.parse_args()
    if args.only in ("real", "both") and not args.real_source:
        parser.error("--real-source is required for real data")
    args.out.mkdir(parents=True, exist_ok=True)
    result = {"browser": args.browser, "html_sha256": hashlib.sha256(args.html.read_bytes()).hexdigest(),
              "network_interception": False, "audio_muted": True,
              "network_policy": "Only body-free HTTPS GET image requests for NLSC PHOTO2/EMAP WMTS tiles; no audio/text uploads or other hosts.",
              "heap_limit_caveat": "Bounded cache/observed JS heap do not establish actual 8 GB workstation suitability.",
              "scenarios": []}
    source = args.fixture or args.out / "SYNTHETIC_4H"
    if args.only in ("synthetic", "both"):
        result["fixture"] = create_fixture(source)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=BROWSERS[args.browser], headless=True,
                                             args=["--mute-audio"], timeout=30000)
        result["browser_version"] = browser.version
        if args.only in ("synthetic", "both"):
            for hours in (args.review_hours or (2, 4)):
                result["scenarios"].append(run_one(browser, args.html, source, args.out,
                                                    synthetic=True, review_hours=hours, whole_export=args.whole_export,
                                                    skip_roundtrip=args.skip_roundtrip,
                                                    time_offset=2.3 if args.fractional_offset else 0))
        if args.only in ("real", "both"):
            result["scenarios"].append(run_one(browser, args.html, args.real_source.resolve(), args.out,
                                                synthetic=False, review_hours=None))
        browser.close()
    result["ok"] = all(item["ok"] for item in result["scenarios"])
    destination = args.out / (args.browser + "_" + args.only + "_result.json")
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    emit("complete", ok=result["ok"], result=str(destination))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
