"""Opt-in four-language Chrome/Edge QA on tiny, synthetic VDR data only.

No real evidence is opened or copied. Normal map tile requests are observed,
not intercepted. A virtual case handle avoids OS file pickers and downloads.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import traceback

from verify_longform import (BROWSERS, ROOT, diagnostic, emit,
                             enable_virtual_project_file, reveal_control,
                             save_project, seek, wait_until)
from verify_multitrack import load
from verify_workbench import (create_layout_fixture, layout_project,
                              open_workbench, upload_project, validate_network)

LOCALES = ("zh-TW", "en", "ja", "zh-CN")
MIXED_TEXT = "合成原文 Captain 船長 日本語 简体 保持不變"


def case_cache(page) -> dict:
    return page.evaluate("""() => Object.fromEntries(Object.keys(localStorage)
      .filter(k=>k.startsWith('vdrreview')&&k!=='vdrreview:locale')
      .sort().map(k=>[k,localStorage.getItem(k)]))""")


def snapshot(page) -> dict:
    return page.evaluate("""() => {
      const d=__vdrReview,p=window.__i18nProbe,m=d.map,e=p.textarea;
      return {locale:VDRI18n.getLocale(),lang:document.documentElement.lang,
        samePlayer:d.player===p.player,sameStore:d.store===p.store,
        sameMap:d.map===p.map,sameShip:d.ship===p.ship,
        sameTextarea:document.querySelector('#edlist .tx')===e,
        text:e.value,selection:[e.selectionStart,e.selectionEnd,e.selectionDirection],
        focused:document.activeElement===e,position:d.player.currentTime,
        paused:d.player.paused,channel:d.player.channel,
        sourceWindow:[d.W0,d.W1],mapCenter:m.getCenter(),mapZoom:m.getZoom(),
        mapLayers:[...p.mapLayers].every(layer=>m.hasLayer(layer)),
        savedWrites:__longformWrites.length,pickerCalls:__longformPickerCalls,
        caseWrites:p.writes.filter(x=>x.key!=='vdrreview:locale'),
        savedStatus:document.querySelector('#edsave').textContent,
        play:document.querySelector('#playbtn').textContent,
        channelLabel:document.querySelector('#ch').selectedOptions[0].textContent,
        basemap:document.querySelector('#map').dataset.basemap,
        layerLabels:[...document.querySelectorAll('.leaflet-control-layers-base label')].map(e=>e.textContent.trim()),
        dataStatus:document.querySelector('#datast').textContent,
        title:document.title,pref:localStorage.getItem('vdrreview:locale')};
    }""")


def assert_controls(page) -> dict:
    reveal_control(page, "#edundo")
    reveal_control(page, "#imSrtBtn")
    reveal_control(page, "#exSrt")
    assert page.locator("#playbtn").is_visible()
    assert page.locator("#seek").is_visible()
    assert page.locator("#edtool [data-a='del']").is_visible()
    assert page.locator("#edundo").is_visible()
    assert page.locator("#imSrtBtn").is_visible()
    assert page.locator("#exSrt").is_visible()
    return page.evaluate("""() => {
      const t=VDRI18n.t;
      const labels={delete:document.querySelector('#edtool [data-a="del"]').textContent,
        undo:document.querySelector('#edundo').textContent,
        importSrt:document.querySelector('#imSrtBtn').textContent,
        exportSrt:document.querySelector('#exSrt').textContent};
      if(labels.delete!==t('action.delete')||labels.undo!==t('ui.undo')||
         labels.importSrt!==t('srt.import')||labels.exportSrt!==t('ui.srtExport'))throw Error('Untranslated primary controls');
      return labels;
    }""")


def install_probe(page) -> None:
    page.evaluate("""() => {
      const d=__vdrReview,textarea=document.querySelector('#edlist .tx');
      window.__i18nProbe={player:d.player,store:d.store,map:d.map,ship:d.ship,
        textarea,mapLayers:Object.values(d.map._layers),writes:[]};
      const original=Storage.prototype.setItem;
      Storage.prototype.setItem=function(key,value){
        if(this===localStorage)__i18nProbe.writes.push({key,value});
        return original.call(this,key,value);
      };
    }""")


def verify_initial_preferences(browser, html: Path) -> list:
    results = []
    for saved, expected in (("en", "en"), ("invalid-language", "zh-TW")):
        context = browser.new_context()
        context.add_init_script("localStorage.setItem('vdrreview:locale', " + json.dumps(saved) + ")")
        page = context.new_page()
        page.goto(html.resolve().as_uri())
        actual = page.evaluate("({locale:VDRI18n.getLocale(),select:document.querySelector('#uiLanguage').value,title:document.title})")
        assert actual["locale"] == actual["select"] == expected, actual
        results.append({"saved": saved, "expected": expected, "actual": actual})
        context.close()
    return results


def run(browser, html: Path, fixture: Path, out: Path) -> dict:
    context, page, evidence = open_workbench(browser, html)
    page.set_default_timeout(10000)
    result = {"ok": False}
    try:
        assert page.locator("#uiLanguage option").evaluate_all("es=>es.map(e=>e.value)") == list(LOCALES)
        assert page.locator("#uiLanguage").input_value() == "zh-TW"
        result["load"] = load(page, fixture, 4)
        enable_virtual_project_file(page)
        base = save_project(page)
        project = layout_project(base, count=3)
        project["spks"] = ["原始船長 Captain 船長", "日本語の話者", "简体讲者"]
        for cues in project["state"].values():
            cues[0].update(text=MIXED_TEXT, spk=project["spks"][0], st=1)
        upload_project(page, "SYNTHETIC_I18N.vdr.json", project)
        reveal_control(page, "#projectImportReplace")
        evidence["allow_replace"] = True
        page.locator("#projectImportReplace").click()
        page.locator("#projectImportPanel").wait_for(state="hidden")
        saved_project = save_project(page, 1)
        saved_bytes = page.evaluate("__longformWrites.at(-1)")
        saved_hash = hashlib.sha256(saved_bytes.encode("utf-8")).hexdigest()
        # Validation fails before a decoder or audio export is constructed.
        reveal_control(page, "#exportRange")
        page.locator("#exportStart").fill("invalid")
        page.locator("#exportRange").click()
        page.wait_for_function("document.querySelector('#exportstatus').textContent.includes(VDRI18n.t('appui.exportInvalidRange'))")
        first = page.locator("#edlist .tx").first
        first.click()
        page.wait_for_function("!__vdrReview.player.paused")
        page.locator("#playbtn").click()
        page.wait_for_function("__vdrReview.player.paused")
        first.fill(MIXED_TEXT + " — 未儲存修改")
        first.evaluate("e=>{e.focus();e.setSelectionRange(3,8,'backward')}")
        # Save feedback is intentionally transient; wait for the normal dirty label.
        wait_until(page, lambda: page.locator("#edsave").inner_text() == page.evaluate("VDRI18n.t('editor.unsavedDot')"), "normal dirty indicator", timeout=15)
        install_probe(page)
        before, cache = snapshot(page), case_cache(page)
        result["paused_switches"] = []
        for locale in LOCALES:
            page.locator("#uiLanguage").select_option(locale)
            after = snapshot(page)
            for key in ("samePlayer", "sameStore", "sameMap", "sameShip", "sameTextarea", "mapLayers"):
                assert after[key], (locale, key, after)
            for key in ("text", "selection", "position", "paused", "channel", "sourceWindow", "mapCenter", "mapZoom", "savedWrites", "pickerCalls"):
                assert after[key] == before[key], (locale, key, before[key], after[key])
            assert not after["caseWrites"], (locale, after["caseWrites"])
            assert case_cache(page) == cache, "Language change rewrote case cache"
            assert after["savedStatus"] == page.evaluate("VDRI18n.t('editor.unsavedDot')"), after
            assert after["play"] == page.evaluate("'▶ '+VDRI18n.t('action.play')"), after
            assert after["basemap"] == page.evaluate("VDRI18n.t('appui.map.photo')"), after
            assert after["pref"] == locale
            expected_error = page.evaluate("VDRI18n.t('appui.exportFailed',{message:VDRI18n.t('appui.exportInvalidRange')})")
            assert page.locator("#exportstatus").inner_text() == expected_error
            after["controls"] = assert_controls(page)
            assert page.evaluate("__i18nProbe.textarea===document.querySelector('#edlist .tx')")
            page.screenshot(path=str(out / (locale + "-workbench.png")), full_page=True)
            result["paused_switches"].append(after)
        assert hashlib.sha256(page.evaluate("__longformWrites.at(-1)").encode("utf-8")).hexdigest() == saved_hash
        result["unchanged_saved_sha256"] = saved_hash
        emit("paused_four_languages_passed", browser=out.name)

        result["srt_previews"] = []
        for locale in LOCALES:
            page.locator("#uiLanguage").select_option(locale)
            page.locator("#imSrt").set_input_files({"name": "SYNTHETIC_I18N.srt", "mimeType": "text/plain",
                "buffer": b"1\n00:00:01,000 --> 00:00:02,000\nSYNTHETIC preview only\n"})
            page.locator("#srtImportPanel").wait_for(state="visible")
            assert page.locator("#srtImportCancel").inner_text() == page.evaluate("VDRI18n.t('common.cancel')")
            result["srt_previews"].append({"locale": locale, "cancel": page.locator("#srtImportCancel").inner_text(),
                "panel": page.locator("#srtImportPanel").inner_text()})
            page.locator("#srtImportCancel").click()
            assert case_cache(page) == cache, "Cancelled SRT preview changed case"

        # Active composition is not a separate unsaved buffer: it must remain the
        # same native target and source-bound cue through a locale update.
        compose = page.locator("#edcompose")
        compose.focus()
        compose.dispatch_event("compositionstart", {"data": ""})
        compose.evaluate("e=>{e.value='輸入中 日本語 简体 IME';e.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertCompositionText',data:e.value,isComposing:true}));e.setSelectionRange(2,5)}")
        ime_cache = case_cache(page)
        page.evaluate("window.__i18nCompose=document.querySelector('#edcompose')")
        ime_results = []
        for locale in LOCALES:
            page.locator("#uiLanguage").select_option(locale)
            value = compose.evaluate("e=>({same:e===__i18nCompose,value:e.value,start:e.selectionStart,end:e.selectionEnd})")
            assert value == {"same": True, "value": "輸入中 日本語 简体 IME", "start": 2, "end": 5}, value
            assert case_cache(page) == ime_cache
            ime_results.append({"locale": locale, **value})
        compose.dispatch_event("compositionend", {"data": "輸入中 日本語 简体 IME"})
        compose.press("Enter")
        result["synthetic_ime_switches"] = ime_results

        seek(page, 30)
        page.locator("#playbtn").click()
        page.wait_for_function("!__vdrReview.player.paused&&__vdrReview.player.currentTime>30")
        result["playing_switches"] = []
        for locale in LOCALES:
            previous = diagnostic(page)
            page.locator("#uiLanguage").select_option(locale)
            after = diagnostic(page)
            assert not after["paused"] and after["channel"] == previous["channel"], after
            assert previous["currentTime"] <= after["currentTime"] < previous["currentTime"] + 3, after
            assert page.locator("#playbtn").inner_text() == page.evaluate("'⏸ '+VDRI18n.t('action.pause')")
            assert page.evaluate("__vdrReview.player===__i18nProbe.player&&__vdrReview.map===__i18nProbe.map")
            result["playing_switches"].append({"locale": locale, "before": previous["currentTime"], "after": after["currentTime"]})
        page.locator("#playbtn").click()
        page.wait_for_function("__vdrReview.player.paused")
        result["initial_preferences"] = verify_initial_preferences(browser, html)
        actual = save_project(page, page.evaluate("__longformWrites.length"))
        assert actual["recording_id"] == saved_project["recording_id"]
        assert actual["t0_utc"] == saved_project["t0_utc"]
        assert actual["spks"] == project["spks"]
        # A newly typed cue may sort ahead of the first imported cue. Identity,
        # not its current list index, identifies the transcript under test.
        edited = next(cue for cue in actual["state"]["A1"] if cue["id"] == project["state"]["A1"][0]["id"])
        assert edited["text"] == MIXED_TEXT + " — 未儲存修改", edited
        assert all(actual["state"][ch][0]["text"] == MIXED_TEXT for ch in ("A2", "A3", "C1"))
        result["source_and_speakers_preserved"] = True
        validate_network(evidence)
        result["ok"] = True
    except Exception as error:
        result["failure"] = type(error).__name__ + ": " + str(error)
        result["traceback"] = traceback.format_exc()
        emit("i18n_failed", failure=result["failure"])
        try:
            page.screenshot(path=str(out / "failure.png"), full_page=True)
        except Exception:
            pass
    finally:
        result["evidence"] = evidence
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
    out = args.out or ROOT / "output" / "playwright" / "vdr-i18n" / args.browser
    out.mkdir(parents=True, exist_ok=True)
    result = {"browser": args.browser, "html_sha256": hashlib.sha256(args.html.read_bytes()).hexdigest(),
              "fixture": create_layout_fixture(args.fixture), "network_interception": False,
              "audio_muted": True, "real_evidence_opened": False}
    emit("i18n_started", browser=args.browser, sha256=result["html_sha256"])
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=BROWSERS[args.browser], headless=True,
                                             args=["--mute-audio"], timeout=30000)
        result["browser_version"] = browser.version
        result["verification"] = run(browser, args.html, args.fixture, out)
        browser.close()
    result["ok"] = result["verification"]["ok"]
    destination = out / (args.browser + "_i18n_result.json")
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    emit("complete", ok=result["ok"], result=str(destination))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
