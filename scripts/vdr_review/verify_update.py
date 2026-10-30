"""Opt-in Chrome/Edge check of the update button, on tiny synthetic VDR data only.

Every request to the update source is intercepted and answered here, so nothing
reaches the network. The file picker is replaced inside the page; no real file is
chosen or overwritten. Real Pages hosting stays on the release checklist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import traceback

from verify_longform import BROWSERS, ROOT, diagnostic, emit
from verify_multitrack import load
from verify_workbench import create_layout_fixture

SOURCE = "https://newqqo.github.io/vdr-review/"
MANIFEST_URL, PAGE_URL = SOURCE + "version.json", SOURCE + "vdr-review.html"
PAGE = b"<!doctype html><title>synthetic</title><script>window.__executed=1</script>"
LOCALES = ("en", "ja", "zh-CN", "zh-TW")
# Count page-level fetches and replace only the picker UI, which a headless browser cannot drive.
INIT = """
window.__fetches=[];const realFetch=window.fetch;
window.fetch=function(...args){window.__fetches.push(String(args[0]));return realFetch.apply(this,args);};
window.__saved=[];window.__pickerMode='save';window.__pickerCalls=[];
window.showSaveFilePicker=async options=>{
  window.__pickerCalls.push({suggestedName:options.suggestedName,activated:navigator.userActivation.isActive});
  if(window.__pickerMode==='cancel')throw new DOMException('cancelled','AbortError');
  return {name:'SYNTHETIC-old.html',createWritable:async()=>({
    write:async blob=>{const digest=await crypto.subtle.digest('SHA-256',await blob.arrayBuffer());
      window.__saved.push(Array.from(new Uint8Array(digest),x=>x.toString(16).padStart(2,'0')).join(''));},
    close:async()=>{},abort:async()=>{}})};
};
"""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reply(body: bytes, status: int = 200, cors: bool = True, content_type: str = "application/json"):
    # GitHub Pages sends "*"; a file:// page cannot read the response otherwise. Playwright
    # adds this header when it is absent, so a block needs a deliberately wrong origin.
    headers = {"content-type": content_type, "access-control-allow-origin": "*" if cors else "https://other.example"}
    return lambda route: route.fulfill(status=status, body=body, headers=headers)


def manifest(version: str, page: bytes = PAGE, **extra) -> bytes:
    return json.dumps({"version": version, "released": "2026-01-02", "html": "vdr-review.html",
                       "sha256": sha256(page), "bytes": len(page),
                       "notes_url": "https://github.example/releases/tag/v" + version, **extra}).encode("utf-8")


def status(page) -> dict:
    return page.evaluate("""() => {const s=document.querySelector('#updateStatus');
      return {key:s.dataset.i18n,text:s.textContent,error:s.classList.contains('err'),
        expected:VDRI18n.t(s.dataset.i18n,JSON.parse(s.dataset.i18nParams||'{}'))};}""")


def press(page, selector: str, key: str) -> dict:
    page.locator(selector).click()
    page.wait_for_function("k=>document.querySelector('#updateStatus').dataset.i18n===k&&!document.querySelector('#updateAction').disabled", arg=key)
    current = status(page)
    assert current["text"] == current["expected"] and current["text"], current
    return current


def controls(page) -> dict:
    return {name: page.locator(selector).is_visible() for name, selector in
            (("notes", "#updateNotes"), ("download", "#updateDownload"), ("save", "#updateSave"))}


def opened_page_checks(page, requests: list, label: str, protocol: str) -> dict:
    page.wait_for_function("document.querySelector('#buildinfo').textContent!==''")
    page.wait_for_timeout(1500)  # A delayed automatic check would show up here.
    fetches = page.evaluate("window.__fetches")
    assert fetches == [], fetches
    environment = page.evaluate("""key => ({protocol:location.protocol,secure:isSecureContext,subtle:!!crypto.subtle,
      label:document.querySelector('#updateAction').textContent,expected:VDRI18n.t(key),version:document.querySelector('#buildinfo').textContent})""", label)
    assert environment["protocol"] == protocol and environment["secure"] and environment["subtle"], environment
    assert environment["label"] == environment["expected"] and environment["version"].startswith("v"), environment
    assert not page.locator("#updatePanel").is_visible()
    return {**environment, "requests_on_open": list(requests)}


def languages(page) -> dict:
    texts = {}
    for locale in LOCALES:
        page.locator("#uiLanguage").select_option(locale)
        current = status(page)
        assert current["text"] == current["expected"], (locale, current)
        texts[locale] = {"status": current["text"], "action": page.locator("#updateAction").inner_text(),
                         "download": page.locator("#updateDownload").inner_text(), "notes": page.locator("#updateNotes").inner_text()}
    assert len({item["status"] for item in texts.values()}) == 4 and len({item["action"] for item in texts.values()}) == 4
    return texts


def check_file_page(context, html: Path, source: dict, requests: list, fixture: Path) -> dict:
    result = {}
    page = context.new_page()
    page.set_default_timeout(60000)
    page.goto(html.resolve().as_uri())
    result["opened"] = opened_page_checks(page, requests, "update.check", "file:")
    assert result["opened"]["requests_on_open"] == [], result["opened"]
    version = page.evaluate("BUILD_INFO.version")
    newer = f"{int(version.split('.')[0]) + 1}.0.0"

    source[MANIFEST_URL] = reply(manifest(version))
    result["same_version"] = press(page, "#updateAction", "update.latest")
    assert controls(page) == {"notes": False, "download": False, "save": False}

    source[MANIFEST_URL], source[PAGE_URL] = reply(manifest(newer)), reply(PAGE, content_type="text/html")
    result["newer"] = press(page, "#updateAction", "update.available")
    assert newer in result["newer"]["text"] and controls(page) == {"notes": True, "download": True, "save": False}
    assert page.locator("#updateNotes").get_attribute("href") == f"https://github.example/releases/tag/v{newer}"
    assert page.locator("#updateNotes").get_attribute("rel") == "noopener noreferrer"
    assert PAGE_URL not in page.evaluate("window.__fetches"), "The page may only be fetched on request"
    result["languages"] = languages(page)

    result["verified"] = press(page, "#updateDownload", "update.verified")
    assert page.evaluate("window.__pickerCalls.length") == 0 and controls(page)["save"]
    page.evaluate("window.__pickerMode='cancel'")
    result["cancelled"] = press(page, "#updateSave", "update.saveCancelled")
    assert controls(page)["save"] and page.evaluate("window.__saved.length") == 0
    page.evaluate("window.__pickerMode='save'")
    result["saved"] = press(page, "#updateSave", "update.saved")
    assert page.evaluate("window.__saved") == [sha256(PAGE)]
    calls = page.evaluate("window.__pickerCalls")
    assert all(call["suggestedName"] == "VDR核聽工具.html" and call["activated"] for call in calls), calls
    assert page.evaluate("window.__executed") is None, "A downloaded page must never run here"

    # A real Blob download from a file:// page, for browsers without the picker.
    press(page, "#updateAction", "update.available")
    press(page, "#updateDownload", "update.verified")
    page.evaluate("delete window.showSaveFilePicker")
    with page.expect_download() as download:
        page.locator("#updateSave").click()
    page.wait_for_function("document.querySelector('#updateStatus').dataset.i18n==='update.downloaded'")
    assert download.value.suggested_filename == "VDR核聽工具.html"
    assert sha256(Path(download.value.path()).read_bytes()) == sha256(PAGE)
    result["blob_download"] = status(page)

    failures = {"offline": (lambda route: route.abort("internetdisconnected"), "update.unreachable"),
                "blocked_by_cors": (reply(manifest(newer), cors=False), "update.unreachable"),
                "missing": (reply(b"", status=404), "update.httpError"),
                "broken_json": (reply(b"<html>not json</html>"), "update.badManifest"),
                "bad_version": (reply(manifest("latest")), "update.badVersion")}
    result["failures"] = {}
    for name, (route, key) in failures.items():
        source[MANIFEST_URL] = route
        current = press(page, "#updateAction", key)
        assert current["error"] and controls(page)["download"] is False, (name, current)
        result["failures"][name] = current

    source[MANIFEST_URL], source[PAGE_URL] = reply(manifest(newer)), reply(PAGE + b"tampered", content_type="text/html")
    press(page, "#updateAction", "update.available")
    saved_before = page.evaluate("window.__saved.length")
    result["failures"]["hash_mismatch"] = press(page, "#updateDownload", "update.hashMismatch")
    assert not controls(page)["save"] and page.evaluate("window.__saved.length") == saved_before

    # The panel must not disturb a review in progress, and unsaved work must prompt first.
    result["load"] = load(page, fixture, 4)
    before = diagnostic(page)
    source[MANIFEST_URL] = reply(b"", status=503)
    press(page, "#updateAction", "update.httpError")
    after = diagnostic(page)
    assert (before["currentTime"], before["channel"], before["paused"]) == (after["currentTime"], after["channel"], after["paused"])
    page.locator("#edcompose").fill("合成更新測試")
    page.locator("#edcompose").press("Enter")
    page.wait_for_function("Array.from(document.querySelectorAll('#edlist .tx')).some(e=>e.value==='合成更新測試')")
    source[MANIFEST_URL], source[PAGE_URL] = reply(manifest(newer)), reply(PAGE, content_type="text/html")
    press(page, "#updateAction", "update.available")
    dialogs = []

    def answer(item, accept):
        dialogs.append(item.message)
        if accept:
            item.accept()
        else:
            item.dismiss()

    fetched = page.evaluate("window.__fetches.length")
    page.once("dialog", lambda item: answer(item, False))
    page.locator("#updateDownload").click()
    page.wait_for_function("document.querySelector('#updateStatus').dataset.i18n==='update.available'&&!document.querySelector('#updateDownload').disabled")
    assert len(dialogs) == 1 and page.evaluate("window.__fetches.length") == fetched, "Declining must stop the download"
    assert dialogs[0] == page.evaluate("VDRI18n.t('update.dirtyConfirm')")
    page.once("dialog", lambda item: answer(item, True))
    result["dirty_accepted"] = press(page, "#updateDownload", "update.verified")
    assert len(dialogs) == 2
    assert page.evaluate("Array.from(document.querySelectorAll('#edlist .tx')).some(e=>e.value==='合成更新測試')")
    page.close()  # Without run_before_unload, the deliberately unsaved cue raises no prompt.
    return result


def check_online_page(context, html: Path, source: dict, requests: list) -> dict:
    # The real build served from its Pages address, with its real manifest.
    built = html.read_bytes()
    version = json.loads(re.search(rb"const BUILD_INFO=(\{.*?\});\n", built).group(1))["version"]
    source[SOURCE] = reply(built, content_type="text/html; charset=utf-8")
    source[MANIFEST_URL], source[PAGE_URL] = reply(manifest(version, built)), reply(built, content_type="text/html")
    requests.clear()
    page = context.new_page()
    page.set_default_timeout(60000)
    page.goto(SOURCE)
    result = {"opened": opened_page_checks(page, requests, "update.downloadOffline", "https:")}
    assert result["opened"]["requests_on_open"] == [SOURCE], result["opened"]
    result["verified"] = press(page, "#updateAction", "update.verified")
    assert page.evaluate("window.__fetches") == [MANIFEST_URL, PAGE_URL]
    assert controls(page) == {"notes": False, "download": False, "save": True}
    result["saved"] = press(page, "#updateSave", "update.saved")
    assert page.evaluate("window.__saved") == [sha256(built)]
    page.close()
    return result


def run(browser, html: Path, fixture: Path) -> dict:
    context = browser.new_context(accept_downloads=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script(INIT)
    source, requests, page_errors, console_errors = {}, [], [], []

    def handle(route):
        mock = source.get(route.request.url)
        if mock:
            mock(route)
        else:
            route.abort("internetdisconnected")

    context.route(SOURCE + "**", handle)
    context.on("request", lambda request: requests.append(request.url) if request.url.startswith(("http://", "https://")) else None)
    context.on("weberror", lambda error: page_errors.append(str(error.error)))
    context.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    result = {"ok": False}
    try:
        result["file_page"] = check_file_page(context, html, source, requests, fixture)
        result["online_page"] = check_online_page(context, html, source, requests)
        assert not page_errors, page_errors
        # Refused requests are logged by the browser itself; anything else is a page defect.
        unexpected = [text for text in console_errors if not text.startswith(("Failed to load resource:", "Access to fetch"))]
        assert not unexpected, unexpected
        result["ok"] = True
    except Exception as error:
        result["failure"] = type(error).__name__ + ": " + str(error)
        result["traceback"] = traceback.format_exc()
    finally:
        result["page_errors"], result["console_errors"] = page_errors, console_errors
        context.close()
    return result


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", choices=BROWSERS, default="chrome")
    parser.add_argument("--html", type=Path, default=ROOT / "output" / "VDR核聽工具.html")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--fixture", type=Path, default=ROOT / "output" / "playwright" / "vdr-workbench" / "SYNTHETIC_LAYOUT_4CH_2M")
    args = parser.parse_args()
    out = args.out or ROOT / "output" / "playwright" / "vdr-update" / args.browser
    out.mkdir(parents=True, exist_ok=True)
    result = {"browser": args.browser, "html_sha256": sha256(args.html.read_bytes()),
              "fixture": create_layout_fixture(args.fixture), "real_evidence_opened": False, "network_reached": False}
    emit("update_check_started", browser=args.browser, sha256=result["html_sha256"])
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=BROWSERS[args.browser], headless=True,
                                             args=["--mute-audio"], timeout=30000)
        result["browser_version"] = browser.version
        result["verification"] = run(browser, args.html, args.fixture)
        browser.close()
    result["ok"] = result["verification"]["ok"]
    destination = out / (args.browser + "_update_result.json")
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    emit("complete", ok=result["ok"], result=str(destination), failure=result["verification"].get("failure"))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
