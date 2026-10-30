"""Opt-in Chrome/Edge check of the VDR map and direct-listening workbench.

Developer-only. Inputs are tiny, explicitly synthetic G.726/NMEA files; no case
evidence is opened, copied or exported. Normal map tile requests are observed,
never intercepted. Project-file writes use a virtual handle, so this does not
certify Windows file-picker permissions or any particular physical workstation.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sys
import traceback

from verify_longform import (BROWSERS, ROOT, START, diagnostic, emit,
                             enable_virtual_project_file, minute_path,
                             reveal_control, save_project, seek, wait_until)
from verify_multitrack import label_click, load, synthetic_record, wait_waves, wave_click


def nmea(body: str) -> bytes:
    checksum = 0
    for char in body:
        checksum ^= ord(char)
    return ("$" + body + "*" + f"{checksum:02X}" + "\r\n").encode("ascii")


def coordinate(value: float, latitude: bool) -> str:
    degrees = int(value)
    return f"{degrees:0{2 if latitude else 3}d}{(value-degrees)*60:07.4f}"


def navigation_record(minute: int) -> bytes:
    """GPS-only vessel movement: no invented MMSI, dimensions or AIS identity."""
    records = []
    for second in range(60):
        elapsed = minute * 60 + second
        stamp = START + timedelta(seconds=elapsed)
        latitude, longitude = 22.535 + elapsed * .00001, 120.305 + elapsed * .00002
        sentences = (
            f"GPGGA,{stamp:%H%M%S},{coordinate(latitude, True)},N,{coordinate(longitude, False)},E,1,08,0.9,0,M,0,M,,",
            "GPVTG,61.5,T,,M,4.6,N,8.5,K,A",
            "VRHDT,61.5,T",
        )
        prefix = b"\xff\xff" + (second * 256).to_bytes(2, "big") + b"\x00\x01"
        records.extend(prefix + nmea(sentence) for sentence in sentences)
    return b"".join(records)


def create_workbench_fixture(root: Path) -> dict:
    """Create/reuse exact separate navigation and audio-only synthetic roots."""
    expected = {}
    for name in ("navigation", "audio_only"):
        source = root / name
        for channel, rate in (("A1", 14000), ("C1", 8000)):
            for minute in range(2):
                stamp = START + timedelta(minutes=minute)
                expected[minute_path(source, stamp, channel)] = synthetic_record(channel, rate, minute)
        if name == "navigation":
            for minute in range(2):
                stamp = START + timedelta(minutes=minute)
                path = source / "Log" / stamp.strftime("%Y%m%d%H%M") / ("L0" + stamp.strftime("%M%S") + "00.log")
                expected[path] = navigation_record(minute)
    existing = {path.resolve() for path in root.rglob("*") if path.is_file()}
    if existing - {path.resolve() for path in expected}:
        raise ValueError("Synthetic workbench fixture contains unexpected files")
    for path, content in expected.items():
        if path.exists() and path.read_bytes() != content:
            raise ValueError("Refusing to overwrite changed synthetic workbench fixture: " + str(path))
    for path, content in expected.items():
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    return {"synthetic": True, "files": len(expected), "bytes": sum(map(len, expected.values())),
            "seconds": 120, "channels": ["A1", "C1"], "navigation_points": 120,
            "notice": "Synthetic code sequences and GPS-only movement, not speech or evidence"}


def create_layout_fixture(root: Path) -> dict:
    """A separate four-channel root; never mutate/reuse the two-channel fixture."""
    expected = {}
    for channel, rate in (("A1", 14000), ("A2", 14000), ("A3", 14000), ("C1", 8000)):
        for minute in range(2):
            expected[minute_path(root, START + timedelta(minutes=minute), channel)] = synthetic_record(channel, rate, minute)
    for minute in range(2):
        stamp = START + timedelta(minutes=minute)
        path = root / "Log" / stamp.strftime("%Y%m%d%H%M") / ("L0" + stamp.strftime("%M%S") + "00.log")
        expected[path] = navigation_record(minute)
    existing = {path.resolve() for path in root.rglob("*") if path.is_file()}
    if existing - {path.resolve() for path in expected}:
        raise ValueError("Synthetic layout fixture contains unexpected files")
    for path, content in expected.items():
        if path.exists() and path.read_bytes() != content:
            raise ValueError("Refusing to overwrite changed synthetic layout fixture: " + str(path))
    for path, content in expected.items():
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    return {"synthetic": True, "files": len(expected), "bytes": sum(map(len, expected.values())),
            "seconds": 120, "channels": ["A1", "A2", "A3", "C1"], "navigation_points": 120}


def layout_project(base: dict, count: int = 18) -> dict:
    result = json.loads(json.dumps(base))
    result["state"] = {}
    next_id = 1
    for channel in ("A1", "A2", "A3", "C1"):
        result["state"][channel] = []
        for index in range(count):
            text = f"合成字幕 {index + 1:02d}：{channel} 航行口令確認。"
            if index % 4 == 2:
                text += "這是較長的合成聽打內容，用來確認換行後仍可閱讀及捲動，不代表任何實際海事對話。"
            result["state"][channel].append({"id": next_id, "start": 3 + index * 5, "end": 6 + index * 5,
                "text": text, "spk": "", "st": 0, "orig": "", "origNo": None, "ovl": False})
            next_id += 1
    result["uid"] = next_id
    return result


def cue_click_project(base: dict) -> dict:
    """Known reviewed cues, with real gaps, make accidental edits detectable."""
    result = layout_project(base)
    for values in result["state"].values():
        for cue in values:
            cue["st"] = 1
    return result


def validate_click_seek(assignments: list, expected: float) -> None:
    assert len(assignments) == 1, ("Expected one seek, not duplicate bubbling", assignments)
    assert abs(assignments[0] - expected) < .025, (assignments, expected)


def validate_transcript_space(value: dict, *, constrained: bool = False) -> None:
    """Measure usable list space, not merely its outer pane or a full-page image."""
    assert value["row_count"] >= 12, value
    assert value["document_width"] <= value["viewport_width"] + 2, value
    assert value["client_height"] >= 240 or value["fully_visible_rows"] >= 3, value
    # At a 200%-equivalent viewport the sticky player can use a sizeable part of
    # the physical screen. The list must still expose useful, reachable content.
    minimum_visible = min(240, value["viewport_height"] * .5) if constrained else 240
    assert value["visible_height"] >= minimum_visible or value["fully_visible_rows"] >= 3, value
    assert value["visible_width"] >= 150, value
    assert value["scroll_height"] > value["client_height"] + 10, "Fixture did not exercise real list scrolling"
    assert value["overflow_y"] in ("auto", "scroll"), value
    assert value["composer_outside_head"] and value["waves_outside_head"], value


def transcript_space(page) -> dict:
    return page.evaluate("""() => {
      const list=document.querySelector('#edlist'),r=list.getBoundingClientRect();
      let top=Math.max(0,r.top),bottom=Math.min(innerHeight,r.bottom),left=Math.max(0,r.left),right=Math.min(innerWidth,r.right);
      for(let p=list.parentElement;p;p=p.parentElement){const s=getComputedStyle(p),b=p.getBoundingClientRect();
        if(/hidden|auto|scroll|clip/.test(s.overflowY)){top=Math.max(top,b.top);bottom=Math.min(bottom,b.bottom)}
        if(/hidden|auto|scroll|clip/.test(s.overflowX)){left=Math.max(left,b.left);right=Math.min(right,b.right)}}
      const bar=document.querySelector('#playbackBar'),br=bar.getBoundingClientRect(),bs=getComputedStyle(bar);
      if((bs.position==='sticky'||bs.position==='fixed')&&br.top<=1&&br.bottom>top)top=Math.min(bottom,br.bottom);
      const rows=Array.from(list.querySelectorAll('.er')),head=document.querySelector('#edpanel .edhead');
      return {viewport_width:innerWidth,viewport_height:innerHeight,document_width:document.documentElement.scrollWidth,
        client_height:list.clientHeight,scroll_height:list.scrollHeight,scroll_top:list.scrollTop,
        visible_height:Math.max(0,bottom-top),visible_width:Math.max(0,right-left),overflow_y:getComputedStyle(list).overflowY,
        row_count:rows.length,fully_visible_rows:rows.filter(e=>{const b=e.getBoundingClientRect();return b.top>=top-1&&b.bottom<=bottom+1}).length,
        first:rows[0]?.textContent,last:rows.at(-1)?.textContent,
        composer_outside_head:!head.contains(document.querySelector('#edcomposer')),
        waves_outside_head:!head.contains(document.querySelector('#edwaves'))};
    }""")


def scroll_transcript(page) -> dict:
    page.locator("#edlist").evaluate("e=>{e.scrollTop=0;e.scrollIntoView({block:'center'})}")
    page.wait_for_timeout(100)
    before = transcript_space(page)
    page.locator("#edlist").evaluate("e=>{e.scrollTop=e.scrollHeight}")
    page.wait_for_timeout(100)
    after = transcript_space(page)
    assert after["scroll_top"] > before["scroll_top"] + 10, (before, after)
    page.locator("#edlist .er").last.scroll_into_view_if_needed()
    after = transcript_space(page)
    assert page.locator("#edlist .er").last.evaluate("""e=>{
      const b=e.getBoundingClientRect(),r=e.parentElement.getBoundingClientRect();
      const bar=document.querySelector('#playbackBar').getBoundingClientRect();
      const top=Math.max(0,r.top,bar.top<=1?bar.bottom:0),bottom=Math.min(innerHeight,r.bottom);
      return b.top>=top-1&&b.bottom<=bottom+1;
    }"""), "Last transcript row is clipped or unreachable"
    assert after["fully_visible_rows"] >= 1, after
    page.locator("#edlist").evaluate("e=>{e.scrollTop=0}")
    return {"before": before, "after": after, "last_row_reachable": True}


def map_stacking_check(page) -> dict:
    """Map controls may pass behind the sticky player, never cover its hit area."""
    checks = []
    for expanded in (False, True):
        page.evaluate("scrollTo(0,0)")
        page.mouse.move(1, 1)
        page.locator("#map").click(position={"x": 100, "y": 100})
        page.locator("#map").evaluate("e=>scrollTo(0,scrollY+e.getBoundingClientRect().top+10)")
        if expanded:
            # Keyboard focus expands Leaflet's actual control even while its
            # upper edge lies behind the sticky bar; do not inject CSS classes.
            page.locator("#map .leaflet-control-layers-toggle").focus()
            page.locator("#map .leaflet-control-layers-toggle").press("Enter")
            page.wait_for_function("document.querySelector('#map .leaflet-control-layers').classList.contains('leaflet-control-layers-expanded')", timeout=5000)
        page.wait_for_timeout(100)
        result = page.evaluate("""() => {
          const bar=document.querySelector('#playbackBar'),b=bar.getBoundingClientRect(),map=document.querySelector('#map'),m=map.getBoundingClientRect();
          const controls=Array.from(map.querySelectorAll('.leaflet-control-zoom,.leaflet-control-layers'));
          const points=[];
          for(const c of controls){const r=c.getBoundingClientRect();
            const left=Math.max(r.left,b.left,0),right=Math.min(r.right,b.right,innerWidth),top=Math.max(r.top,b.top,0),bottom=Math.min(r.bottom,b.bottom,innerHeight);
            if(right>left&&bottom>top){const x=(left+right)/2,y=(top+bottom)/2,node=document.elementFromPoint(x,y);
              points.push({x,y,inPlayer:!!node?.closest('#playbackBar'),topElement:node?.id||node?.className||node?.tagName})}}
          return {barTop:b.top,intersections:points,
            layersExpanded:map.querySelector('.leaflet-control-layers').classList.contains('leaflet-control-layers-expanded'),
            controlsContained:controls.every(c=>{const r=c.getBoundingClientRect();return r.left>=m.left-1&&r.right<=m.right+1&&r.top>=m.top-1&&r.bottom<=m.bottom+1})};
        }""")
        assert abs(result["barTop"]) < 2, result
        assert result["intersections"] and all(point["inPlayer"] for point in result["intersections"]), result
        assert result["controlsContained"], result
        assert result["layersExpanded"] == expanded, result
        checks.append({"requested_expanded": expanded, **result})
    page.evaluate("scrollTo(0,0)")
    page.mouse.move(1, 1)
    return {"checks": checks, "sticky_player_wins_hit_testing": True}


def run_transcript_layout(browser, html: Path, fixture: Path, out: Path) -> dict:
    """Populated four-track layout and sticky hit-testing, without case data."""
    context, page, evidence = open_workbench(browser, html)
    result = {"ok": False, "viewports": []}
    try:
        result["load"] = load(page, fixture, 4)
        emit("layout_loaded", channels=4, selected=result["load"]["selected"])
        enable_virtual_project_file(page)
        base = current_project(page)
        incoming = layout_project(base)
        upload_project(page, "SYNTHETIC_LAYOUT_72_CUES.vdr.json", incoming)
        reveal_control(page, "#projectImportReplace")
        evidence["allow_replace"] = True
        page.locator("#projectImportReplace").click()
        page.locator("#projectImportPanel").wait_for(state="hidden")
        page.wait_for_function("document.querySelectorAll('#edlist .er').length===18")
        assert page.locator("#waveDetails").evaluate("e=>e.open")
        assert page.locator("#edwaveRows .wave-track:visible").count() == 4
        assert not page.locator("#edpanel").evaluate("e=>e.contains(document.querySelector('#edwaves'))")
        for width, height, constrained in ((1366, 768, False), (1440, 900, False),
                (1280, 720, False), (683, 384, True), (390, 844, False)):
            page.set_viewport_size({"width": width, "height": height})
            page.evaluate("scrollTo(0,0)")
            page.locator("#edlist").evaluate("e=>{e.scrollTop=0;e.scrollIntoView({block:'center'})}")
            page.wait_for_timeout(150)
            value = transcript_space(page)
            validate_transcript_space(value, constrained=constrained)
            # Selecting an existing cue opens the edit tool: measure that state
            # too, rather than only an empty or unselected transcript.
            focus_before = page.evaluate("({scroll_y:scrollY,map_top:document.querySelector('#map').getBoundingClientRect().top})")
            page.locator("#edlist .tx").first.click()
            page.wait_for_timeout(100)
            focus_after = page.evaluate("({scroll_y:scrollY,map_top:document.querySelector('#map').getBoundingClientRect().top})")
            # First-click continuous playback is verified by --cue-click-only.
            # This layout run needs a stable paused clock for its later label
            # position checks, so explicitly pause after selecting a sentence.
            if not diagnostic(page)["paused"]:
                page.locator("#playbtn").click()
                page.wait_for_function("__vdrReview.player.paused")
            page.locator("#edlist").evaluate("e=>e.scrollIntoView({block:'center'})")
            page.wait_for_timeout(100)
            selected = transcript_space(page)
            validate_transcript_space(selected, constrained=constrained)
            screenshot = out / f"layout-{width}x{height}.png"
            page.screenshot(path=str(screenshot))
            scrolling = scroll_transcript(page)
            item = {"width": width, "height": height, "equivalent_200_percent_layout": constrained,
                    "unselected": value, "selected": selected, "scrolling": scrolling,
                    "cue_focus_page_position": {"before": focus_before, "after": focus_after},
                    "screenshot": str(screenshot)}
            result["viewports"].append(item)
            emit("transcript_height_passed", viewport=f"{width}x{height}",
                 client_height=selected["client_height"], visible_height=selected["visible_height"],
                 visible_rows=selected["fully_visible_rows"])
        page.set_viewport_size({"width": 1366, "height": 768})
        page.locator("#waveDetails > summary").click()
        assert not page.locator("#edwaves").is_visible()
        page.locator("#edlist").evaluate("e=>e.scrollIntoView({block:'center'})")
        result["collapsed"] = transcript_space(page)
        validate_transcript_space(result["collapsed"])
        page.locator("#waveDetails > summary").click()
        wait_waves(page, 4)
        result["track_switches"] = [label_click(page, channel, playing=False)
                                    for channel in ("A2", "A3", "C1", "A1")]
        result["wave_seek"] = wave_click(page, "A2", .5, playing=False)
        page.locator("#edcompose").fill("四軌高度驗證：仍可直接聽打。")
        page.locator("#edcomposeAdd").click()
        project = current_project(page)
        assert any(cue["text"] == "四軌高度驗證：仍可直接聽打。" for cue in project["state"]["A2"])
        result["composer_saved_on_current_channel"] = True
        page.evaluate("scrollTo(0,0)")
        zoom = page.evaluate("__vdrReview.map.getZoom()")
        page.locator("#map .leaflet-control-zoom-in").click()
        page.wait_for_function("z=>__vdrReview.map.getZoom()===z+1&&!__vdrReview.map._animatingZoom", arg=zoom)
        page.locator("#map .leaflet-control-zoom-out").click()
        page.wait_for_function("z=>__vdrReview.map.getZoom()===z&&!__vdrReview.map._animatingZoom", arg=zoom)
        result["map_zoom_controls_operable"] = True
        emit("layout_map_zoom_passed")
        result["map_stacking"] = map_stacking_check(page)
        # Exercise actual player controls while map controls intersect its bar.
        page.locator("#map").evaluate("e=>scrollTo(0,scrollY+e.getBoundingClientRect().top+10)")
        page.locator("#playbtn").click()
        page.wait_for_function("!__vdrReview.player.paused")
        page.locator("#playbtn").click()
        page.wait_for_function("__vdrReview.player.paused")
        page.locator("#ch").select_option("C1")
        page.wait_for_function("__vdrReview.player.channel==='C1'&&!__vdrReview.player.seeking")
        assert page.locator("#exportChannel").input_value() == "C1"
        seek(page, 45)
        result["sticky_player_controls_operable"] = True
        page.screenshot(path=str(out / "layout-sticky-player.png"))
        result["map"] = map_snapshot(page)
        validate_network(evidence)
        result["zoom_test_boundary"] = "683x384 CSS viewport equals the layout area of 1366x768 at 200%; actual OS/DPI text zoom not tested"
        result["ok"] = True
    except Exception as error:
        result["failure"] = type(error).__name__ + ": " + str(error)
        result["traceback"] = traceback.format_exc()
        emit("layout_failed", failure=result["failure"])
        try:
            result["failure_space"] = transcript_space(page)
            page.screenshot(path=str(out / "layout-failure.png"), full_page=True)
        except Exception:
            pass
    finally:
        result["evidence"] = evidence
        context.close()
    return result


def run_cue_click(browser, html: Path, fixture: Path, out: Path) -> dict:
    """Focused continuous cue-click regression, isolated synthetic recording."""
    context, page, evidence = open_workbench(browser, html)
    page.set_viewport_size({"width": 1366, "height": 768})
    result = {"ok": False}
    try:
        result["load"] = load(page, fixture, 4)
        enable_virtual_project_file(page)
        expected = cue_click_project(current_project(page))
        upload_project(page, "SYNTHETIC_CUE_CLICK_REVIEWED.vdr.json", expected)
        reveal_control(page, "#projectImportReplace")
        evidence["allow_replace"] = True
        page.locator("#projectImportReplace").click()
        page.locator("#projectImportPanel").wait_for(state="hidden")
        page.wait_for_function("document.querySelectorAll('#edlist .er').length===18")
        page.evaluate("""() => {
          const p=__vdrReview.player,d=Object.getOwnPropertyDescriptor(p,'currentTime');
          window.__cueSeeks=[];
          window.__cueClickEvents=[];
          Object.defineProperty(p,'currentTime',{...d,set(v){__cueSeeks.push(Number(v));d.set.call(this,v)}});
          for(const type of ['pointerdown','pointerup','focusin','click'])document.querySelector('#edlist').addEventListener(type,e=>{
            const t=e.target,r=t.closest('.er');__cueClickEvents.push({type,target:t.className,id:r?.dataset.id,
              x:e.clientX,y:e.clientY,detail:e.detail,focused:document.activeElement===t,
              selection:t.classList.contains('tx')?[t.selectionStart,t.selectionEnd]:null});},true);
        }""")
        seek(page, 40)
        first = page.locator("#edlist .er[data-id='1'] .tx")
        page.locator("#edlist").evaluate("e=>{e.scrollTop=0;e.scrollIntoView({block:'center'})}")
        start_count = page.evaluate("__cueSeeks.length")
        first.click()
        page.wait_for_function("!__vdrReview.player.paused&&!__vdrReview.player.seeking&&document.querySelector('#edlist .er[data-id=\"1\"]').classList.contains('is-playing')", timeout=5000)
        assert not first.evaluate("e=>e.readOnly"), "First click must retain direct typing"
        validate_click_seek(page.evaluate("n=>__cueSeeks.slice(n)", start_count), 2.85)
        assert "暫停" in page.locator("#playbtn").inner_text()
        assert page.locator("#edlist .er[data-id='1'] .cue-playstate").inner_text() == "▶ 播放中"
        assert page.locator("#edlist .er[data-id='1']").get_attribute("data-playing") == "true"
        page.screenshot(path=str(out / "cue-click-playing-1366x768.png"))
        emit("cue_click_playing_passed", time=diagnostic(page)["currentTime"])
        # The first click leaves a normal editable textarea. Another click only
        # moves the caret, without seeking or losing reviewed status.
        page.wait_for_timeout(250)
        before = diagnostic(page)["currentTime"]
        start_count = page.evaluate("__cueSeeks.length")
        first.click(position={"x": 100, "y": 15})
        page.wait_for_timeout(150)
        assert page.evaluate("__cueSeeks.length") == start_count
        assert diagnostic(page)["currentTime"] >= before - .1
        result["focused_caret_click_did_not_replay"] = True
        # Continue naturally across the end into an actual gap, then next cue.
        # Media time advances continuously; native timeupdate arrives roughly
        # every 250ms. Wait for the rendered gap state within the real 2s gap.
        page.wait_for_function("__vdrReview.player.currentTime>6.2&&__vdrReview.player.currentTime<7.8&&document.querySelectorAll('#edlist .er.is-playing').length===0", timeout=10000)
        assert page.locator("#edlist .er.is-playing").count() == 0
        assert page.locator("#edlist .cue-playstate:visible").count() == 0
        assert not diagnostic(page)["paused"], "Whole-row playback stopped at cue end"
        page.wait_for_function("__vdrReview.player.currentTime>8.15&&document.querySelector('#edlist .er[data-id=\"2\"]').classList.contains('is-playing')", timeout=10000)
        assert page.locator("#edlist .er[data-id='2'] .cue-playstate").inner_text() == "▶ 播放中"
        page.locator("#playbtn").click()
        page.wait_for_function("__vdrReview.player.paused")
        assert "播放" in page.locator("#playbtn").inner_text()
        assert page.locator("#edlist .er.is-playing").count() == 0
        assert page.locator("#edlist .er[data-id='2'] .cue-playstate").inner_text() == "⏸ 目前位置"
        result["continuous_boundary_gap_and_pause_badges"] = True
        # Card metadata is also a continuous-play target; controls are not.
        start_count = page.evaluate("__cueSeeks.length")
        page.locator("#edlist .er[data-id='1'] .elt").click()
        page.wait_for_function("!__vdrReview.player.paused&&!__vdrReview.player.seeking")
        validate_click_seek(page.evaluate("n=>__cueSeeks.slice(n)", start_count), 2.85)
        page.locator("#playbtn").click()
        page.wait_for_function("__vdrReview.player.paused")
        result["card_click_continuous"] = True
        # A real drag over another textarea selects text without auto-play.
        third = page.locator("#edlist .er[data-id='3'] .tx")
        third.scroll_into_view_if_needed()
        box = third.bounding_box()
        start_count = page.evaluate("__cueSeeks.length")
        before = diagnostic(page)["currentTime"]
        page.mouse.move(box["x"] + 14, box["y"] + 15)
        page.mouse.down()
        page.mouse.move(box["x"] + min(240, box["width"] - 15), box["y"] + 15, steps=12)
        page.mouse.up()
        assert third.evaluate("e=>e.selectionEnd>e.selectionStart"), "Drag did not select text"
        assert diagnostic(page)["paused"] and abs(diagnostic(page)["currentTime"] - before) < .1
        assert page.evaluate("__cueSeeks.length") == start_count
        result["text_selection_drag_did_not_play"] = True
        reveal_control(page, "#editmode")
        page.locator("#editmode").check()
        start_count = page.evaluate("__cueSeeks.length")
        advanced_text = page.locator("#edlist .er[data-id='4'] .tx")
        advanced_text.click()
        page.wait_for_function("!__vdrReview.player.paused&&!__vdrReview.player.seeking")
        validate_click_seek(page.evaluate("n=>__cueSeeks.slice(n)", start_count), 17.85)
        start_count = page.evaluate("__cueSeeks.length")
        advanced_text.click(position={"x": 100, "y": 15})
        assert page.evaluate("__cueSeeks.length") == start_count
        page.locator("#playbtn").click()
        page.wait_for_function("__vdrReview.player.paused")
        start_count = page.evaluate("__cueSeeks.length")
        advanced_text.click()
        page.wait_for_function("!__vdrReview.player.paused&&!__vdrReview.player.seeking")
        validate_click_seek(page.evaluate("n=>__cueSeeks.slice(n)", start_count), 17.85)
        page.locator("#playbtn").click()
        page.wait_for_function("__vdrReview.player.paused")
        page.locator("#editmode").uncheck()
        result["shown_tools_kept_first_click_and_focused_caret_semantics"] = True
        # Tiny triangle remains bounded and has exactly one seek assignment.
        start_count = page.evaluate("__cueSeeks.length")
        page.locator("#edlist .er[data-id='1'] .etime").click()
        page.wait_for_function("!__vdrReview.player.paused&&!__vdrReview.player.seeking")
        validate_click_seek(page.evaluate("n=>__cueSeeks.slice(n)", start_count), 2.85)
        page.wait_for_function("__vdrReview.player.paused", timeout=10000)
        assert 5.9 <= diagnostic(page)["currentTime"] <= 6.5
        result["tiny_triangle_bounded_without_double_fire"] = True
        assert current_project(page)["state"] == expected["state"], "Click or selection changed text/reviewed state"
        # Follow a late cue from the list top: only the list may scroll.
        seek(page, 72.8)
        page.locator("#edlist").evaluate("e=>{e.scrollTop=0;e.scrollIntoView({block:'center'})}")
        page.locator("#playbtn").click()
        position = page.evaluate("({outer:scrollY,inner:document.querySelector('#edlist').scrollTop})")
        page.wait_for_function("document.querySelector('#edlist .er[data-id=\"15\"]').classList.contains('is-playing')", timeout=6000)
        page.wait_for_timeout(300)
        followed = page.evaluate("({outer:scrollY,inner:document.querySelector('#edlist').scrollTop})")
        assert abs(followed["outer"] - position["outer"]) < 1, (position, followed)
        assert followed["inner"] > position["inner"] + 20, (position, followed)
        page.locator("#playbtn").click()
        page.wait_for_function("__vdrReview.player.paused")
        result["follow_scrolled_list_only"] = {"before": position, "after": followed}
        # Boundary handles are always available: no advanced-tools checkbox is
        # needed to shorten a selected sentence's end and save the correction.
        assert not page.locator("#editmode").is_checked()
        page.locator("#edlist .er[data-id='2'] .elt").click()
        page.wait_for_function("!__vdrReview.player.paused&&!__vdrReview.player.seeking")
        page.locator("#playbtn").click()
        page.wait_for_function("__vdrReview.player.paused")
        canvas = page.locator("canvas.wave-canvas[data-channel='A1']")
        canvas.scroll_into_view_if_needed()
        page.wait_for_function("()=>{const d=document.querySelector('canvas.wave-canvas[data-channel=A1]').dataset;return Number(d.start)<=8&&Number(d.end)>=11}")
        span = canvas.evaluate("e=>[Number(e.dataset.start),Number(e.dataset.end)]")
        box = canvas.bounding_box()
        x = lambda t: box["x"] + (t - span[0]) / (span[1] - span[0]) * box["width"]
        y = box["y"] + box["height"] * .6
        page.mouse.move(x(11), y)
        page.mouse.down()
        page.mouse.move(x(10), y, steps=10)
        page.mouse.up()
        after_drag = current_project(page)
        shortened = next(cue for cue in after_drag["state"]["A1"] if cue["id"] == 2)
        assert abs(shortened["end"] - 10) < .15 and shortened["start"] == 8 and shortened["st"] == 0, shortened
        for channel, cues in expected["state"].items():
            for cue in cues:
                if channel != "A1" or cue["id"] != 2:
                    assert cue in after_drag["state"][channel], "Boundary drag changed another sentence"
        result["boundary_drag_without_tools"] = {"start": shortened["start"], "end": shortened["end"],
            "reviewed_cleared": True, "all_other_cues_unchanged": True, "saved": True}
        # Real editing invalidates only that cue's reviewed flag.
        reveal_control(page, "#editmode")
        page.locator("#editmode").check()
        changed = page.locator("#edlist .er[data-id='1'] .tx")
        changed.fill(expected["state"]["A1"][0]["text"] + "（真實輸入測試）")
        saved = current_project(page)
        assert saved["state"]["A1"][0]["st"] == 0
        assert saved["state"]["A1"][0]["text"].endswith("（真實輸入測試）")
        for channel, cues in after_drag["state"].items():
            for cue in cues:
                if channel != "A1" or cue["id"] != 1:
                    assert cue in saved["state"][channel]
        result["click_preserved_content_review_edit_cleared_only_changed_cue"] = True
        # Composer blur commits and may rerender the list between pointer-down
        # and click. That normal workflow must keep both draft and click intent.
        page.locator("#editmode").uncheck()
        seek(page, 30)
        page.locator("#edcompose").fill("合成草稿：點既有句仍須保留。")
        start_count = page.evaluate("__cueSeeks.length")
        page.locator("#edlist .er[data-id='1'] .tx").click()
        page.wait_for_function("!__vdrReview.player.paused&&!__vdrReview.player.seeking&&__vdrReview.player.currentTime<5", timeout=10000)
        validate_click_seek(page.evaluate("n=>__cueSeeks.slice(n)", start_count), 2.85)
        page.locator("#playbtn").click()
        page.wait_for_function("__vdrReview.player.paused")
        composed = current_project(page)
        draft = next(cue for cue in composed["state"]["A1"] if cue["text"] == "合成草稿：點既有句仍須保留。")
        assert abs(draft["start"] - 30) < .1 and abs(draft["end"] - 32) < .1
        result["composer_blur_kept_draft_and_first_click_played"] = True
        assert not page.locator("#editmode").is_checked()
        page.locator("#edlist .er[data-id='3'] .elt").click()
        page.wait_for_function("!__vdrReview.player.paused&&!__vdrReview.player.seeking")
        page.locator("#playbtn").click()
        page.wait_for_function("__vdrReview.player.paused")
        assert page.locator("#edtool [data-a='del']").is_visible()
        seek(page, 14.5)
        page.keyboard.press("Alt+]")
        shortcut = current_project(page)
        adjusted = next(cue for cue in shortcut["state"]["A1"] if cue["id"] == 3)
        assert abs(adjusted["end"] - 14.5) < .1 and adjusted["st"] == 0, adjusted
        result["delete_visible_and_end_shortcut_without_tools"] = True
        page.locator("#edcompose").focus()
        non_latin_alt_p = "e=>e.dispatchEvent(new KeyboardEvent('keydown',{key:'з',code:'KeyP',altKey:true,bubbles:true,cancelable:true}))"
        page.locator("#edcompose").evaluate(non_latin_alt_p)
        page.wait_for_function("!__vdrReview.player.paused", timeout=5000)
        page.locator("#edcompose").evaluate(non_latin_alt_p)
        page.wait_for_function("__vdrReview.player.paused", timeout=5000)
        assert page.locator("#edcompose").input_value() == ""
        result["composer_non_latin_physical_alt_p_toggle"] = {
            "played_and_paused": True, "boundary": "Synthetic key=з/code=KeyP event; actual OS keyboard layout not tested"}
        # Synthetic IME order: composing input, pointer-down on its own mirrored
        # transcript, composer blur, composition-end, pointer-up/click. Browser
        # events are real; this is not certification of an installed OS IME.
        seek(page, 33)
        page.locator("#edcompose").evaluate("""e=>{
          e.focus();e.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true,data:'注音'}));
          e.value='合成注音組字';e.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertCompositionText',data:'注音',isComposing:true}));
          window.__imeRow=document.querySelector('#edlist .er.act .tx');
          window.__imeId=__imeRow.closest('.er').dataset.id;
        }""")
        ime_row = page.locator("#edlist .er.act .tx")
        assert ime_row.input_value() == "合成注音組字"
        ime_row.scroll_into_view_if_needed()
        box = ime_row.bounding_box()
        page.mouse.move(box["x"] + 100, box["y"] + 15)
        page.mouse.down()
        assert page.locator("#edcompose").evaluate("e=>document.activeElement!==e"), "Composer did not blur during IME test"
        page.locator("#edcompose").evaluate("""e=>{
          e.value='合成注音組字完成全文';
          e.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'完成全文'}));
        }""")
        identity = page.evaluate("({connected:__imeRow.isConnected,same:document.querySelector('#edlist .er[data-id=\"'+__imeId+'\"] .tx')===__imeRow,text:__imeRow.value})")
        assert identity == {"connected": True, "same": True, "text": "合成注音組字完成全文"}, identity
        page.mouse.up()
        page.wait_for_function("!__vdrReview.player.paused&&!__vdrReview.player.seeking&&__vdrReview.player.currentTime<35", timeout=10000)
        page.locator("#playbtn").click()
        page.wait_for_function("__vdrReview.player.paused")
        ime_saved = current_project(page)
        ime_cue = next(cue for cue in ime_saved["state"]["A1"] if cue["text"] == "合成注音組字完成全文")
        assert abs(ime_cue["start"] - 33) < .1 and abs(ime_cue["end"] - 35) < .1
        result["synthetic_ime_same_row_identity_and_full_text"] = {**identity,
            "saved": True, "boundary": "Synthetic CompositionEvent/InputEvent sequence; actual OS IME not tested"}
        validate_network(evidence)
        result["ok"] = True
    except Exception as error:
        result["failure"] = type(error).__name__ + ": " + str(error)
        result["traceback"] = traceback.format_exc()
        emit("cue_click_failed", failure=result["failure"])
        try:
            result["failure_player"] = diagnostic(page)
            result["click_events"] = page.evaluate("window.__cueClickEvents||[]")
            result["seek_assignments"] = page.evaluate("window.__cueSeeks||[]")
            page.screenshot(path=str(out / "cue-click-failure.png"), full_page=True)
        except Exception:
            pass
    finally:
        result["evidence"] = evidence
        context.close()
    return result


def validate_layout(value: dict, *, narrow: bool) -> None:
    width = value["viewport"]
    assert value["document_width"] <= width + 2, value
    left, right = value["navigation"], value["editor"]
    for rect in (left, right, value["composer"], value["map"]):
        assert rect["width"] > 100 and rect["height"] > 10
        assert rect["x"] >= -1 and rect["x"] + rect["width"] <= width + 2, value
    if narrow:
        assert right["y"] >= left["y"] + left["height"] - 2, value
    else:
        assert left["x"] + left["width"] <= right["x"] + 2, value
        assert abs(left["y"] - right["y"]) <= 30, value


def layout(page) -> dict:
    return page.evaluate("""() => {
      const box=id=>{const r=document.querySelector(id).getBoundingClientRect();
        return {x:r.x,y:r.y,width:r.width,height:r.height}};
      return {viewport:innerWidth,document_width:document.documentElement.scrollWidth,
        navigation:box('#navigationPane'),editor:box('#edpanel'),
        composer:box('#edcomposer'),map:box('#map')};
    }""")


def open_workbench(browser, html: Path):
    context = browser.new_context(accept_downloads=False, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    page.set_default_timeout(60000)
    evidence = {"requests": [], "responses": [], "request_failures": [], "dialogs": [],
                "page_errors": [], "console_errors": [], "downloads": []}
    page.on("request", lambda request: evidence["requests"].append({"url": request.url, "method": request.method,
            "resource_type": request.resource_type, "has_body": request.post_data is not None})
            if request.url.startswith(("http://", "https://")) else None)
    page.on("websocket", lambda socket: evidence["requests"].append({"url": socket.url, "method": "WEBSOCKET",
            "resource_type": "websocket", "has_body": False}))
    page.on("response", lambda response: evidence["responses"].append({"url": response.url, "status": response.status})
            if response.url.startswith(("http://", "https://")) else None)
    page.on("requestfailed", lambda request: evidence["request_failures"].append({"url": request.url, "failure": request.failure}))
    page.on("pageerror", lambda error: evidence["page_errors"].append(str(error)))
    page.on("console", lambda message: evidence["console_errors"].append(message.text) if message.type == "error" else None)
    page.on("download", lambda item: evidence["downloads"].append(item.suggested_filename))
    def dialog(item):
        evidence["dialogs"].append({"type": item.type, "message": item.message})
        # Only the explicit source-bound project reopen is expected to confirm.
        if (item.type == "confirm" and evidence.get("allow_replace", False)
                and item.message.startswith("確定取代目前全部聽打內容？")):
            item.accept()
        else:
            item.dismiss()
    page.on("dialog", dialog)
    page.goto(html.resolve().as_uri())
    return context, page, evidence


def validate_network(evidence: dict) -> None:
    from verify_longform import is_map_tile_request
    assert not evidence["page_errors"], evidence["page_errors"]
    assert not evidence["downloads"], evidence["downloads"]
    for request in evidence["requests"]:
        assert is_map_tile_request(request["url"], request["method"], request["resource_type"],
                                   request["has_body"]), request
    for failure in evidence["request_failures"]:
        assert is_map_tile_request(failure["url"]), "Missing/unexpected application asset: " + failure["url"]
    # Tile failures are recorded separately from application JavaScript errors.
    # A successful decoded PHOTO image is an independent mandatory check below.
    unexpected = [message for message in evidence["console_errors"]
                  if not message.startswith("Failed to load resource:")]
    assert not unexpected, unexpected


def map_snapshot(page) -> dict:
    return page.evaluate("""() => {
      const el=document.querySelector('#map'),d=el.dataset;
      return {visible:!!el.getBoundingClientRect().height,navState:d.navState,
        latitude:d.shipLat?Number(d.shipLat):null,longitude:d.shipLon?Number(d.shipLon):null,
        markerAttached:!!(__vdrReview.ship&&__vdrReview.ship._map===__vdrReview.map),
        marker:__vdrReview.ship?.getLatLng?.(),
        status:document.querySelector('#mapstatus')?.textContent||'',
        loadedTiles:Array.from(el.querySelectorAll('img.leaflet-tile')).filter(i=>i.complete&&i.naturalWidth>0).length,
        checkedLayer:Array.from(el.querySelectorAll('.leaflet-control-layers-base label')).filter(l=>l.querySelector('input')?.checked).map(l=>l.textContent.trim())};
    }""")


def assert_default_folds(page) -> dict:
    folds = page.locator("#transcriptionFlow,#navigationDetails,#reviewOptions,#deliveryOptions,#keyboardHelp,#dataDetails,#about").evaluate_all(
        "els=>Object.fromEntries(els.map(e=>[e.id,{tag:e.tagName,open:e.open}]))")
    assert set(folds) == {"transcriptionFlow", "navigationDetails", "reviewOptions", "deliveryOptions", "keyboardHelp", "dataDetails", "about"}
    assert all(value["tag"] == "DETAILS" and not value["open"] for value in folds.values()), folds
    assert not page.locator("#editmode").is_checked()
    if page.locator("#tips").count():
        assert not page.locator("#tips").is_visible()
    assert page.locator("#edcompose").is_visible()
    return folds


def direct_typing(page, out: Path) -> dict:
    """Real input events, composition guards, channel/time binding and save."""
    enable_virtual_project_file(page)
    seek(page, 12)
    page.locator("#edcompose").fill("合成聽寫第一句")
    assert page.locator("#edlist .tx").first.input_value() == "合成聽寫第一句"
    page.locator("#edcompose").evaluate("""e=>{
      e.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true,data:'句'}));
      e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true,cancelable:true}));
      e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',keyCode:229,bubbles:true,cancelable:true}));
    }""")
    assert page.locator("#edcompose").input_value() == "合成聽寫第一句"
    page.locator("#edcompose").evaluate("e=>e.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'句'}))")
    # Save while typing, without Enter: the cue already exists and is committed.
    draft = save_project(page)
    cue = next(c for c in draft["state"]["A1"] if c["text"] == "合成聽寫第一句")
    assert abs(cue["start"] - 12) < .25 and abs(cue["end"] - 14) < .25, cue
    assert page.locator("#edcompose").input_value() == ""
    # Shift+Enter remains an editable line break, not an accidental submit.
    seek(page, 58)
    page.locator("#playbtn").click()
    page.wait_for_function("!__vdrReview.player.paused")
    page.locator("#edcompose").fill("合成換行")
    page.wait_for_function("__vdrReview.player.currentTime>=62&&!__vdrReview.player.seeking", timeout=45000)
    assert page.locator("#edcompose").input_value() == "合成換行", "Automatic minute buffering cleared the sentence"
    page.locator("#edcompose").press("Shift+Enter")
    assert page.locator("#edcompose").input_value() == "合成換行\n"
    page.locator("#edcompose").fill("合成換行\n第二行")
    assert "\n" in page.locator("#edcompose").input_value()
    page.locator("#edcompose").press("Enter")
    assert page.locator("#edcompose").input_value() == ""
    assert not diagnostic(page)["paused"], "Direct typing must not silently pause playback"
    page.locator("#playbtn").click()
    page.wait_for_function("__vdrReview.player.paused")
    seek(page, 40)
    page.locator("#edcompose").fill("切軌前 A1 草稿")
    page.locator("#ch").select_option("C1")
    page.wait_for_function("__vdrReview.player.channel==='C1'&&!__vdrReview.player.seeking")
    assert page.locator("#edcompose").input_value() == ""
    seek(page, 50)
    page.locator("#edcompose").fill("C1 手動聽寫")
    seek(page, 70)
    assert page.locator("#edcompose").input_value() == "", "Seeking must finish the prior source-bound sentence"
    page.locator("#ch").select_option("A1")
    page.wait_for_function("__vdrReview.player.channel==='A1'&&!__vdrReview.player.seeking")
    page.locator("#edlist .tx").first.fill("合成聽寫第一句（已編輯）")
    final = save_project(page, 1)
    first = next(c for c in final["state"]["A1"] if c["text"] == "合成聽寫第一句（已編輯）")
    bound = next(c for c in final["state"]["A1"] if c["text"] == "切軌前 A1 草稿")
    other = next(c for c in final["state"]["C1"] if c["text"] == "C1 手動聽寫")
    assert abs(first["start"] - 12) < .25 and abs(bound["start"] - 40) < .25
    assert abs(bound["end"] - 42) < .25 and abs(other["start"] - 50) < .25
    assert abs(other["end"] - 52) < .25, "Seek must not extend an earlier draft into the new position"
    assert not any(c["text"] == "切軌前 A1 草稿" for c in final["state"]["C1"])
    assert len(final["state"]["A1"]) == 3 and len(final["state"]["C1"]) == 1
    destination = out / "SYNTHETIC_WORKBENCH.vdr.json"
    destination.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"project": final, "project_path": str(destination), "ime_guarded": True,
            "shift_enter_multiline": True, "source_channel_bound": True,
            "seek_preserved_prior_time": True, "typing_kept_playback_running": True,
            "automatic_minute_buffer_preserved_sentence": True,
            "draft_saved_before_enter": True, "existing_transcript_edited": True,
            "file_permission_boundary": "virtual file handle; Windows permissions not tested"}


def current_project(page) -> dict:
    return save_project(page, page.evaluate("__longformWrites.length"))


def upload_project(page, name: str, value: dict) -> None:
    page.locator("#imJson").set_input_files({"name": name, "mimeType": "application/json",
                                           "buffer": json.dumps(value, ensure_ascii=False).encode("utf-8")})
    page.locator("#projectImportPanel").wait_for(state="visible")


def merge_checks(page, out: Path) -> dict:
    """Incoming company progress never silently replaces the current front half."""
    destination = out / "SYNTHETIC_WORKBENCH.vdr.json"
    before = json.loads(destination.read_text(encoding="utf-8"))
    incoming = json.loads(json.dumps(before))
    prototype = before["state"]["A1"][0]
    incoming["state"] = {ch: [] for ch in before["state"]}
    incoming["state"]["A1"] = [
        {**prototype, "id": 9001, "start": 80, "end": 85, "text": "合成公司中後段", "st": 0},
        {**prototype, "id": 9002, "start": 12.5, "end": 13.5, "text": "合成重疊異文，兩筆保留", "st": 0},
    ]
    incoming["uid"] = 9003
    upload_project(page, "SYNTHETIC_WORKBENCH_COMPANY.vdr.json", incoming)
    page.screenshot(path=str(out / "workbench-merge-preview.png"), full_page=True)
    page.locator("#projectImportCancel").click()
    page.locator("#projectImportPanel").wait_for(state="hidden")
    cancelled = current_project(page)
    assert cancelled["state"] == before["state"], "Cancel changed existing progress"
    assert page.evaluate("__longformPickerCalls") == 1, "Cancel replaced the current file handle"
    upload_project(page, "SYNTHETIC_WORKBENCH_COMPANY.vdr.json", incoming)
    page.locator("#projectImportMerge").click()
    page.locator("#projectImportPanel").wait_for(state="hidden")
    merged = current_project(page)
    for channel, cues in before["state"].items():
        assert all(cue in merged["state"][channel] for cue in cues), "Merge overwrote current cues"
    texts = {cue["text"] for cue in merged["state"]["A1"]}
    assert {"合成公司中後段", "合成重疊異文，兩筆保留", "合成聽寫第一句（已編輯）"} <= texts
    assert page.evaluate("__longformPickerCalls") == 1, "Merge must keep the current master-file handle"

    # v2 has seconds-of-UTC-day but no date/source hash: no silent date guessing.
    legacy = {"version": 2, "t0_utc_s": 15 * 3600,
              "state": {"A1": [{**prototype, "id": 9010, "start": 95, "end": 99,
                                 "text": "合成舊版公司中後段", "st": 0}]},
              "spks": [], "uid": 9011, "spkColor": {}}
    upload_project(page, "SYNTHETIC_WORKBENCH_COMPANY_v2.json", legacy)
    assert not page.locator("#projectLegacyConfirm").is_checked()
    assert page.locator("#projectImportMerge").is_disabled(), "Unbound legacy JSON must not merge"
    page.locator("#projectImportCancel").click()
    page.locator("#projectImportPanel").wait_for(state="hidden")
    assert current_project(page)["state"] == merged["state"], "Unconfirmed legacy import changed progress"
    upload_project(page, "SYNTHETIC_WORKBENCH_COMPANY_v2.json", legacy)
    page.locator("#projectLegacyDate").fill(START.strftime("%Y-%m-%d"))
    assert page.locator("#projectImportMerge").is_disabled(), "A date alone is not source confirmation"
    page.locator("#projectLegacyConfirm").check()
    page.locator("#projectLegacyPrepare").click()
    page.wait_for_function("!document.querySelector('#projectImportMerge').disabled")
    page.locator("#projectImportMerge").click()
    page.locator("#projectImportPanel").wait_for(state="hidden")
    final = current_project(page)
    for channel, cues in before["state"].items():
        assert all(cue in final["state"][channel] for cue in cues), "Legacy merge overwrote the front half"
    converted = next(cue for cue in final["state"]["A1"] if cue["text"] == "合成舊版公司中後段")
    assert abs(converted["start"] - 95) < .001 and abs(converted["end"] - 99) < .001
    assert len(final["state"]["A1"]) == len(before["state"]["A1"]) + 3
    assert page.evaluate("__longformPickerCalls") == 1
    destination.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"cancel_preserved_state_and_handle": True, "overlap_kept_both": True,
            "current_front_cues_exactly_preserved": True, "legacy_unconfirmed_unchanged": True,
            "legacy_explicit_date_and_source_confirmation": True,
            "legacy_aligned_range": [converted["start"], converted["end"]],
            "same_master_handle_reused": True,
            "cues_by_channel": {ch: len(cues) for ch, cues in final["state"].items()}}


def reopen_project(browser, html: Path, fixture: Path, out: Path) -> dict:
    context, page, evidence = open_workbench(browser, html)
    result = {"ok": False}
    try:
        load(page, fixture / "navigation", 2)
        assert not evidence["dialogs"]
        enable_virtual_project_file(page)
        destination = out / "SYNTHETIC_WORKBENCH.vdr.json"
        expected = json.loads(destination.read_text(encoding="utf-8"))
        page.locator("#imJson").set_input_files(str(destination))
        page.locator("#projectImportPanel").wait_for(state="visible")
        reveal_control(page, "#projectImportReplace")
        evidence["allow_replace"] = True
        page.locator("#projectImportReplace").click()
        page.locator("#projectImportPanel").wait_for(state="hidden")
        page.wait_for_function("Array.from(document.querySelectorAll('#edlist .tx')).some(e=>e.value==='合成聽寫第一句（已編輯）')")
        actual = save_project(page)
        assert actual["state"] == expected["state"], "Saved and reopened cue state differs"
        assert len(evidence["dialogs"]) == 1 and evidence["dialogs"][0]["message"].startswith("確定取代目前全部聽打內容？")
        validate_network(evidence)
        result.update(ok=True, source_bound=True, state_equal=True,
                      cues_by_channel={ch: len(cues) for ch, cues in actual["state"].items()})
    except Exception as error:
        result["failure"] = type(error).__name__ + ": " + str(error)
        result["traceback"] = traceback.format_exc()
    finally:
        result["evidence"] = evidence
        context.close()
    return result


def no_navigation_fallback(browser, html: Path, fixture: Path, out: Path) -> dict:
    context, page, evidence = open_workbench(browser, html)
    result = {"ok": False}
    try:
        result["load"] = load(page, fixture / "audio_only", 2)
        value = map_snapshot(page)
        assert value["latitude"] is None and value["longitude"] is None, value
        assert value["navState"] == "missing" and not value["markerAttached"], value
        assert value["visible"] and any(word in value["status"] for word in ("沒有", "缺", "無", "尚未")), value
        assert page.locator("#edcompose").is_visible() and page.locator("#edcompose").is_enabled()
        page.locator("#edcompose").fill("無導航仍可聽寫（合成）")
        page.locator("#edcomposeAdd").click()
        assert page.locator("#edlist .tx").first.input_value() == "無導航仍可聽寫（合成）"
        assert not evidence["dialogs"], evidence["dialogs"]
        validate_network(evidence)
        page.screenshot(path=str(out / "workbench-no-navigation.png"), full_page=True)
        result.update(ok=True, map=value, direct_typing_available=True)
    except Exception as error:
        result["failure"] = type(error).__name__ + ": " + str(error)
        result["traceback"] = traceback.format_exc()
    finally:
        result["evidence"] = evidence
        context.close()
    return result


def run(browser, html: Path, fixture: Path, out: Path) -> dict:
    context, page, evidence = open_workbench(browser, html)
    result = {"ok": False}
    try:
        result["load"] = load(page, fixture / "navigation", 2)
        emit("workbench_loaded", selected=result["load"]["selected"])
        result["folds"] = assert_default_folds(page)
        wait_until(page, lambda: map_snapshot(page)["loadedTiles"] > 0, "default basemap successful tiles", 60)
        assert not evidence["dialogs"], "Default map must not display a consent prompt"
        before = map_snapshot(page)
        assert before["latitude"] is not None and before["longitude"] is not None, before
        assert before["navState"] == "position" and before["markerAttached"], before
        assert any("PHOTO" in item["url"] and item["status"] == 200 for item in evidence["responses"])
        seek(page, 60)
        page.wait_for_function("lat=>Math.abs(Number(document.querySelector('#map').dataset.shipLat)-lat)>.0001", arg=before["latitude"])
        after = map_snapshot(page)
        assert after["latitude"] > before["latitude"] + .0004 and after["longitude"] > before["longitude"] + .0008, (before, after)
        for value in (before, after):
            assert abs(value["marker"]["lat"] - value["latitude"]) < .000001
            assert abs(value["marker"]["lng"] - value["longitude"]) < .000001
        assert page.evaluate("!!__vdrReview.map"), "Actual map diagnostic unavailable"
        result["map"] = {"before": before, "after_seek": after, "no_consent_prompt": True}
        emit("default_map_passed", loaded_tiles=before["loadedTiles"], start=before["marker"], end=after["marker"])
        result["desktop"] = layout(page)
        validate_layout(result["desktop"], narrow=False)
        page.screenshot(path=str(out / "workbench-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(300)
        result["narrow"] = layout(page)
        validate_layout(result["narrow"], narrow=True)
        page.screenshot(path=str(out / "workbench-narrow.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.locator("#reviewOptions summary").first.click()
        assert page.locator("#editmode").is_visible()
        page.locator("#editmode").check()
        assert page.locator("body").evaluate("e=>e.classList.contains('editing')")
        page.locator("#editmode").uncheck()
        page.locator("#reviewOptions summary").first.click()
        result["typing"] = direct_typing(page, out)
        result["typing"].pop("project")
        page.screenshot(path=str(out / "workbench-typed.png"), full_page=True)
        emit("direct_typing_passed", automatic_minute_buffer=True, ime_guard=True)
        result["merge"] = merge_checks(page, out)
        emit("safe_merge_passed", summary=result["merge"])
        validate_network(evidence)
        result["ok"] = True
    except Exception as error:
        result["failure"] = type(error).__name__ + ": " + str(error)
        result["traceback"] = traceback.format_exc()
        emit("workbench_failed", failure=result["failure"])
        try:
            result["failure_map"] = map_snapshot(page)
            result["failure_layout"] = layout(page)
            page.screenshot(path=str(out / "workbench-failure.png"), full_page=True)
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
    parser.add_argument("--fixture", type=Path, default=ROOT / "output" / "playwright" / "vdr-workbench" / "SYNTHETIC_WORKBENCH_NAV_2M")
    focus = parser.add_mutually_exclusive_group()
    focus.add_argument("--layout-only", action="store_true", help="Four-channel populated transcript height and sticky-map checks only")
    focus.add_argument("--cue-click-only", action="store_true", help="Continuous cue click, reviewed-state and list-only follow checks")
    parser.add_argument("--layout-fixture", type=Path, default=ROOT / "output" / "playwright" / "vdr-workbench" / "SYNTHETIC_LAYOUT_4CH_2M")
    args = parser.parse_args()
    out = args.out or ROOT / "output" / "playwright" / "vdr-workbench" / args.browser
    out.mkdir(parents=True, exist_ok=True)
    result = {"browser": args.browser, "html_sha256": hashlib.sha256(args.html.read_bytes()).hexdigest(),
              "fixture": create_layout_fixture(args.layout_fixture) if args.layout_only or args.cue_click_only else create_workbench_fixture(args.fixture), "network_interception": False,
              "audio_muted": True, "real_evidence_opened": False}
    emit("workbench_started", browser=args.browser, sha256=result["html_sha256"])
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=BROWSERS[args.browser], headless=True,
                                             args=["--mute-audio"], timeout=30000)
        result["browser_version"] = browser.version
        if args.cue_click_only:
            result["cue_click"] = run_cue_click(browser, args.html, args.layout_fixture, out)
        elif args.layout_only:
            result["transcript_layout"] = run_transcript_layout(browser, args.html, args.layout_fixture, out)
        else:
            result["workbench"] = run(browser, args.html, args.fixture, out)
            if result["workbench"]["ok"]:
                result["reopen"] = reopen_project(browser, args.html, args.fixture, out)
                result["no_navigation"] = no_navigation_fallback(browser, args.html, args.fixture, out)
        browser.close()
    stages = ("cue_click",) if args.cue_click_only else (("transcript_layout",) if args.layout_only else ("workbench", "reopen", "no_navigation"))
    result["ok"] = all(result.get(stage, {}).get("ok", False) for stage in stages)
    suffix = "_cue_click_result.json" if args.cue_click_only else ("_layout_result.json" if args.layout_only else "_workbench_result.json")
    destination = out / (args.browser + suffix)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    emit("complete", ok=result["ok"], result=str(destination))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
