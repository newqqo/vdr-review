"""Optional local Chrome/Edge smoke check using synthetic recordings only.

Developer verification, NOT part of the colleague distribution. Requires the
developer's installed Playwright and ffmpeg; never installs either or opens cases.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

from synthetic_charts import chart_zip, sample_layers

ROOT = Path(__file__).resolve().parents[2]


def fixtures(out: Path) -> tuple[Path, Path]:
    old = out / "SYNTHETIC_G726" / "Audio" / "202610230900"
    old.mkdir(parents=True, exist_ok=True)
    for ch, rate in [("A1", 14000), ("C1", 8000)]:
        payload = bytes(rate * 60 * 3 // 8)
        header = bytearray(32)
        header[:12] = b"VoyageDtRec "
        header[26:30] = (len(payload) + 2).to_bytes(4, "big")
        header[30:32] = b"\r\n"
        (old / f"{ch}000000.dat").write_bytes(header + payload + b"\r\n")
    modern = out / "SYNTHETIC_AAC" / "Internal_Storage" / "audio" / "202610230900"
    modern.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for a valid synthetic AAC smoke fixture")
    for ch, rate in [("A1", 16000), ("C1", 8000)]:
        payload = subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                                  f"sine=frequency=440:sample_rate={rate}:duration=2",
                                  "-ac", "1", "-c:a", "aac", "-profile:a", "aac_low", "-f", "adts", "-"],
                                 capture_output=True, check=True).stdout
        stamp = b"2026102309000000"
        raw = ((b"VoyageDtRec " + stamp).ljust(44, b"\0") + b"\xff\xff" +
               (b"\x01" if ch[0] == "A" else b"\x02") + b"\0" + len(payload).to_bytes(4, "big") +
               stamp + ch.encode() + b"\0\0" + payload + b"\0\0\r\n\0\0\r\n")
        (modern / f"{ch}_synthetic.dat").write_bytes(raw)
    return old.parents[1], modern.parents[2]


def load_folder(page, folder: Path) -> None:
    page.locator("#fdir").set_input_files(str(folder))
    page.wait_for_function("!document.getElementById('loadbtn').disabled")
    page.locator("#loadbtn").click()
    page.locator("#mainpane").wait_for(state="visible", timeout=60000)


def check_privacy_controls(browser, html: Path, folder: Path, out: Path) -> dict:
    """No basemap and chart layers before loading, change source, and clearing the browser copies, in a fresh profile."""
    layers = sample_layers()
    charts = out / "SYNTHETIC_CHARTS_SHP.zip"
    charts.write_bytes(chart_zip({k: v for k, v in layers.items() if not k.startswith("channel")}))
    more = out / "SYNTHETIC_FAIRWAYS_SHP.zip"
    more.write_bytes(chart_zip({k: v for k, v in layers.items() if k.startswith("channel")}))
    context = browser.new_context(viewport={"width": 1440, "height": 1080})
    network, errors = [], []
    context.route("http://**/*", lambda route: (network.append(route.request.url), route.abort()))
    context.route("https://**/*", lambda route: (network.append(route.request.url), route.abort()))
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(html.as_uri())
    page.locator("#basemap").select_option("blank")
    page.locator("#fchart").set_input_files(str(charts))
    page.wait_for_function("(()=>{const s=document.getElementById('chartsum');return !s.hidden&&!s.hasAttribute('aria-busy');})()")
    summary = page.locator("#chartsum").inner_text()
    assert "已讀取 海圖：助導航設施（點）：2 筆" in summary and "已讀取 海圖：等深線（線）：2 筆" in summary, summary
    assert "圖資日期（依檔名）：2026-09-18" in summary, summary
    load_folder(page, folder)
    page.wait_for_timeout(1500)
    # Chosen before loading, "no basemap" means the case area is never requested; chart layers are local.
    assert not network, network
    assert page.locator("#map").get_attribute("data-basemap") == "不載入底圖（不連網）"
    overlays = page.locator(".leaflet-control-layers-overlays").inner_text()
    assert "海圖：助導航設施（點）" in overlays and "海圖：等深線（線）" in overlays, overlays
    # More layers can be added after loading, from the navigation section.
    page.locator("#navigationDetails > summary").click()
    page.locator("#fchartAdd").set_input_files(str(more))
    page.wait_for_function("document.getElementById('chartaddsum').textContent.includes('航道')")
    assert "海圖：航道（區）" in page.locator(".leaflet-control-layers-overlays").inner_text()
    page.locator("#navigationDetails > summary").click()
    page.locator("#edcompose").fill("SYNTHETIC kept in the browser copy")
    page.locator("#edcompose").press("Enter")
    page.locator("#changeSource").click()
    page.locator("#loadpane").wait_for(state="visible")
    assert page.locator("#basemap").input_value() == "blank", "the basemap choice is remembered"
    load_folder(page, folder)
    page.wait_for_function("document.querySelectorAll('#edlist .tx').length===1")
    assert page.locator("#edlist .tx").first.input_value() == "SYNTHETIC kept in the browser copy"
    page.locator("#about > summary").click()
    page.locator("#clearCaches").click()
    assert page.locator("#cachemsg").inner_text().startswith("已清除 1 份案件暫存；目前開啟的案件")
    page.locator("#changeSource").click()
    page.locator("#loadpane").wait_for(state="visible")
    load_folder(page, folder)
    page.wait_for_timeout(500)
    assert page.locator("#edlist .tx").count() == 0, "a cleared case must not come back"
    assert not network and not errors, (network, errors)
    context.close()
    return {"no_basemap_requests": 0, "chart_layers_offline": True, "basemap_remembered": True,
            "change_source_restores_cache": True, "cleared_cache_not_restored": True}


def run(html: Path, executable: str, out: Path) -> dict:
    old, modern = fixtures(out)
    print('Synthetic fixtures ready; starting isolated browser driver', flush=True)
    result: dict = {"synthetic_only": True, "html_sha256": hashlib.sha256(html.read_bytes()).hexdigest()}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=executable, headless=True,
                                   args=["--disable-background-networking"], timeout=30000)
        print('Browser started; opening local HTML', flush=True)
        result['browser_version'] = browser.version
        context = browser.new_context(accept_downloads=True, viewport={"width": 1440, "height": 1080})
        network, errors = [], []
        context.route("http://**/*", lambda route: (network.append(route.request.url), route.abort()))
        context.route("https://**/*", lambda route: (network.append(route.request.url), route.abort()))
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(html.as_uri())
        assert not network, network  # opening the tool makes no requests
        result["native_file_picker_available"] = page.evaluate("typeof showSaveFilePicker === 'function'")
        page.screenshot(path=str(out / "01_start.png"), full_page=True)
        # Directory-input selection and normal UI events, not internal app calls.
        page.locator("#fdir").set_input_files(str(old))
        page.wait_for_function("!document.getElementById('loadbtn').disabled")
        page.locator("#tz").fill("0")
        page.locator("#tz").dispatch_event("change")
        assert page.locator("#wstart").input_value() == "2026-10-23 09:00:00"
        page.locator("#tz").fill("8")
        page.locator("#tz").dispatch_event("change")
        assert page.locator("#wstart").input_value() == "2026-10-23 17:00:00"
        result["timezone_preserves_absolute_window"] = True
        page.locator("#evt").fill("17:00:30")
        page.locator("#loadbtn").click()
        page.locator("#mainpane").wait_for(state="visible", timeout=60000)
        print('G726 local-folder load passed', flush=True)
        assert page.locator("#ch option").count() == 2
        assert page.locator("#fdir").is_disabled()
        # After loading, the start pane gives way; the vessel-track pane stays visible by design.
        assert page.locator("#loadpane").is_hidden()
        assert page.locator("#navigationPane").is_visible()
        assert "2026-10-23" in page.locator("#sessioninfo").inner_text()
        page.wait_for_function("document.getElementById('au').duration > 59")
        # Add a sentence the way people do: type it in the composer and press Enter.
        page.locator("#edcompose").fill("SYNTHETIC 〔不明〕")
        page.locator("#edcompose").press("Enter")
        # Select the sentence so its tools appear, then mark it reviewed.
        page.locator("#edlist .tx").first.click()
        page.locator("#edtool button[data-st='1']").click()
        assert page.locator("#edlist .est").first.inner_text() == "已核"
        page.locator("#edlist .tx").first.fill("SYNTHETIC changed")
        assert page.locator("#edlist .est").first.inner_text() == "未核"
        # Real browser JS integration, with a virtual OS picker/write boundary.
        page.evaluate("""() => {
          window.smokeWrites=[];window.smokePickerCalls=0;
          const h={name:'single-case.vdr.json',createWritable:async()=>({
            write:async d=>window.smokePending=d,close:async()=>window.smokeWrites.push(await window.smokePending.text())})};
          window.showSaveFilePicker=async()=>{window.smokePickerCalls++;return h;};
        }""")
        page.locator("#saveP").click()
        page.wait_for_function("smokeWrites.length===1")
        page.locator("#edlist .tx").first.fill("SYNTHETIC final")
        page.locator("#saveP").click()
        page.wait_for_function("smokeWrites.length===2")
        assert page.evaluate("smokePickerCalls") == 1
        main = page.evaluate("JSON.parse(smokeWrites[1])")
        result["same_handle_saved_twice"] = main["state"]["A1"][0]["text"] == "SYNTHETIC final"
        assert result["same_handle_saved_twice"]
        # The hand-off export sits in the collapsed "export / other" section, as people find it.
        page.locator("#deliveryOptions > summary").click()
        with page.expect_download() as download:
            page.locator("#exBundle").click()
        package = out / "synthetic-handoff.zip"
        download.value.save_as(package)
        with zipfile.ZipFile(package) as zf:
            assert zf.testzip() is None
            result["handoff_entries"] = zf.namelist()
            manifest = json.loads(zf.read(next(n for n in zf.namelist() if n.endswith("_來源清單.json"))))
            wav = zf.read(next(n for n in zf.namelist() if n.endswith(".wav")))
            assert hashlib.sha256(wav).hexdigest() == manifest["output_wav_sha256"]
            assert len(manifest["sources"]) == 1 and len(manifest["sources"][0]["sha256"]) == 64
            assert manifest["recording_id"] == main["recording_id"]
        for enhanced in [False, True]:
            action = "clipE" if enhanced else "clip"
            with page.expect_download() as download:
                page.locator(f"#edtool button[data-a='{action}']").click()
            path = out / ("synthetic-enhanced.zip" if enhanced else "synthetic-original.zip")
            download.value.save_as(path)
            with zipfile.ZipFile(path) as zf:
                receipt = json.loads(zf.read(next(n for n in zf.namelist() if n.endswith("_來源清單.json"))))
                clip = zf.read(next(n for n in zf.namelist() if n.endswith(".wav")))
                assert hashlib.sha256(clip).hexdigest() == receipt["output_wav_sha256"]
                processing = receipt["processing"]
                if enhanced:
                    # Gain is chosen per segment and recorded for each one.
                    assert processing["kind"] == "enhanced_listening_only" and processing["segments"]
                    assert all(segment["gain"] > 0 for segment in processing["segments"])
                else:
                    assert processing["kind"] == "decoded_original"
                    # The clip holds the same decoded samples as the full-channel WAV over its window.
                    utc = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
                    a = round((utc(receipt["window_utc"][0]) - utc(manifest["window_utc"][0])) * receipt["sample_rate"])
                    assert len(clip) > 44 and clip[44:] == wav[44 + a * 2:44 + a * 2 + len(clip) - 44]
        result["original_and_enhanced_receipts"] = True
        # Changing extras must mark the same project dirty.
        page.locator("#navigationDetails > summary").click()  # event notes live in this section
        page.locator("#events").fill("17:00:30 SYNTHETIC event")
        page.wait_for_timeout(6200)  # transient saved message expires
        assert "尚未儲存" in page.locator("#edsave").inner_text()
        page.locator("#navigationDetails > summary").click()
        page.screenshot(path=str(out / "02_review.png"), full_page=True)
        # Wrong-source import is refused before modifying the current text.
        main["recording_id"] = "f" * 64
        wrong = out / "synthetic-wrong-source.json"
        wrong.write_text(json.dumps(main), encoding="utf-8")
        page.evaluate("window.showOpenFilePicker=undefined")
        page.locator("#imJson").set_input_files(str(wrong))
        page.wait_for_timeout(100)
        assert page.locator("#edlist .tx").first.input_value() == "SYNTHETIC final"
        result["wrong_source_preserves_current"] = True
        # File origin again, clean browser context: AAC uses actual Chrome decoder.
        page.close()
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(html.as_uri())
        page.locator("#fdir").set_input_files(str(modern))
        page.wait_for_function("!document.getElementById('loadbtn').disabled")
        page.locator("#loadbtn").click()
        page.locator("#mainpane").wait_for(state="visible", timeout=60000)
        print('AAC local-folder load passed', flush=True)
        page.wait_for_function("document.getElementById('au').duration > 1")
        # The folder summary lists the format. The data status may instead report the last frame the
        # browser decoder leaves out as a short gap, which is honest, so it is not checked for the codec.
        assert "AAC" in page.locator("#dirsum").text_content()
        assert page.locator("#ch option").count() == 2
        page.locator("#edcompose").fill("SYNTHETIC AAC")
        page.locator("#edcompose").press("Enter")
        page.evaluate("window.showSaveFilePicker=undefined")
        with page.expect_download() as download:
            page.locator("#saveP").click()
        fallback = out / "synthetic-fallback.vdr.json"
        download.value.save_as(fallback)
        assert json.loads(fallback.read_text(encoding="utf-8"))["version"] == 4
        assert "尚未標記已儲存" in page.locator("#edsave").inner_text()
        result["aac_browser_decode"] = True
        result["honest_download_fallback"] = True
        page.screenshot(path=str(out / "03_aac.png"), full_page=True)
        result["network_requests"] = network
        result["browser_errors"] = errors
        # After loading, the only requests allowed are basemap tiles (the "no basemap" layer avoids them).
        assert all(url.startswith("https://wmts.nlsc.gov.tw/wmts/") for url in network), network
        assert not errors, errors
        result["privacy_controls"] = check_privacy_controls(browser, html, old, out)
        print('Basemap, chart layer, change-source and cache controls passed', flush=True)
        browser.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, default=ROOT / "output" / "VDR核聽工具.html")
    parser.add_argument("--browser", default=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    parser.add_argument("--out", type=Path, default=ROOT / "output" / "playwright" / "vdr")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    result = run(args.html.resolve(), args.browser, args.out.resolve())
    (args.out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
