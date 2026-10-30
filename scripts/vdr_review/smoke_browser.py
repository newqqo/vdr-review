"""Optional local Chrome/Edge smoke check using synthetic recordings only.

Developer verification, NOT part of the colleague distribution. Requires the
developer's installed Playwright and ffmpeg; never installs either or opens cases.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

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
        assert not page.locator(".left").is_visible()
        assert "2026-10-23" in page.locator("#sessioninfo").inner_text()
        page.wait_for_function("document.getElementById('au').duration > 59")
        page.locator("#edadd").click()
        page.locator("#edlist .tx").first.fill("SYNTHETIC 〔不明〕")
        page.locator("button[data-st='1']").click()
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
                    assert processing["kind"] == "enhanced_listening_only" and "gain" in processing
                else:
                    assert processing["kind"] == "decoded_original"
                    a, b = processing["sample_start"], processing["sample_end"]
                    assert clip[44:] == wav[44 + a * 2:44 + b * 2]
        result["original_and_enhanced_receipts"] = True
        # Changing extras must mark the same project dirty.
        page.locator("#showAux").check()
        page.locator("#events").fill("17:00:30 SYNTHETIC event")
        page.wait_for_timeout(6200)  # transient saved message expires
        assert "尚有變更" in page.locator("#edsave").inner_text()
        page.locator("#showAux").uncheck()
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
        assert "AAC" in page.locator("#datast").inner_text()
        assert page.locator("#ch option").count() == 2
        page.locator("#edadd").click()
        page.locator("#edlist .tx").first.fill("SYNTHETIC AAC")
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
        assert not network, network
        assert not errors, errors
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
