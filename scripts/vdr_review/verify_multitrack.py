"""Opt-in Chrome/Edge validation of synchronized, bounded VDR multitrack waves.

Developer-only, excluded from the colleague package. All generated audio is a
small, explicitly synthetic fixture; real inputs remain read-only. No exports,
transcription, installs, network interception, or original-audio copies occur.
Only the shared exact NLSC WMTS GET image allowlist may use the network.
Observed cache/heap values do not certify an actual 8 GB workstation.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import traceback

from verify_longform import (BROWSERS, ROOT, START, assert_bounded, assert_map_only_network, diagnostic,
                             datetime_local_value, emit, enable_virtual_project_file,
                             minute_path, open_page, reveal_control, save_project, seek, wait_until)

CHANNELS = {"A1": 14000, "A2": 14000, "A3": 14000, "C1": 8000}
SAMPLES = {"A1": (5, 25), "A2": (15, 35), "A3": (25.25, 46.25), "C1": (10, 25)}
BAND_SAMPLES = {"A1": (5.25, 25.25), "A2": (15.25, 25.25), "A3": (25.25, 26.25), "C1": (10.25, 25.25)}


def active_second(channel: str, second: float) -> bool:
    if channel == "A3":
        # 48 real detected bursts in two minutes exercises the 40-chip limit.
        return second % 2.5 < .5
    position = second % 30
    if channel == "C1":
        return 5 <= position < 15
    phase = {"A1": 0, "A2": 10}[channel]
    return phase <= position < phase + 10


def synthetic_record(channel: str, rate: int, minute: int) -> bytes:
    per_half = rate * 3 // 16
    active = bytes((j * 73 + 19) % 256 for j in range(per_half))
    quiet = bytes([255]) * per_half
    payload = b"".join(active if active_second(channel, minute * 60 + half / 2) else quiet
                       for half in range(120))
    header = bytearray(32)
    header[:12] = b"VoyageDtRec "
    header[26:30] = (len(payload) + 2).to_bytes(4, "big")
    header[30:32] = b"\r\n"
    return bytes(header) + payload + b"\r\n"


def create_multitrack_fixture(root: Path) -> dict:
    """Two minutes, four channels; C1's second minute is deliberately absent."""
    expected, total = set(), 0
    for channel, rate in CHANNELS.items():
        for minute in range(1 if channel == "C1" else 2):
            path = minute_path(root, START + timedelta(minutes=minute), channel)
            record = synthetic_record(channel, rate, minute)
            if path.exists():
                if path.stat().st_size != len(record) or hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(record).digest():
                    raise ValueError("Refusing to overwrite changed synthetic fixture: " + str(path))
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(record)
            expected.add(path.resolve())
            total += len(record)
    actual = {path.resolve() for path in root.rglob("*") if path.is_file()}
    if actual != expected:
        raise ValueError("Synthetic fixture contains unexpected files; refusing to reuse")
    return {"synthetic": True, "files": len(expected), "bytes": total, "seconds": 120,
            "channels": list(CHANNELS), "missing": {"C1": [[60, 120]]},
            "patterns": "G.726 synthetic active/quiet code sequences; not speech or evidence"}


def canvas_time(start: float, end: float, fraction: float) -> float:
    if not all(math.isfinite(v) for v in (start, end, fraction)) or end <= start or not 0 <= fraction <= 1:
        raise ValueError("Invalid canvas time geometry")
    return start + fraction * (end - start)


def validate_rows(rows: list[dict], expected: int) -> None:
    assert len(rows) == expected, f"Expected {expected} visible tracks, got {len(rows)}"
    assert len({row["channel"] for row in rows}) == expected
    assert sum(row["active"] == "true" for row in rows) == 1
    spans = [(float(row["start"]), float(row["end"])) for row in rows]
    assert all(math.isfinite(a) and math.isfinite(b) and b > a for a, b in spans)
    assert all(abs(a - spans[0][0]) < 0.001 and abs(b - spans[0][1]) < 0.001 for a, b in spans)
    assert all(abs(row.get("left", 0) - rows[0].get("left", 0)) < 1 for row in rows)
    assert all(row["visible"] and row["width"] > 100 and row["height"] > 10 for row in rows)


def tracks(page) -> list[dict]:
    return page.locator("#edwaveRows .wave-track").evaluate_all("""rows=>rows.map(row=>{
      const canvas=row.querySelector('canvas.wave-canvas'),b=canvas.getBoundingClientRect();
      return {channel:row.dataset.channel,active:row.dataset.active,
        start:canvas.dataset.start,end:canvas.dataset.end,unknown:row.dataset.unknown,
        gaps:row.dataset.gaps,label:row.querySelector('button.wave-label')?.textContent,
        left:b.left,width:b.width,height:b.height,visible:!!(b.width&&b.height),
        canvas_id:canvas.id};
    })""")


def wave_diagnostic(page) -> dict:
    return page.evaluate("""() => {
      const d=__vdrReview,rate=DSP.ENV_RATE;
      const rows=Array.from(document.querySelectorAll('#edwaveRows .wave-track'));
      return {rate,channels:Object.fromEntries(Object.entries(d.channels).map(([ch,c])=>{
        const row=rows.find(r=>r.dataset.channel===ch),canvas=row?.querySelector('canvas');
        const a=Number(canvas?.dataset.start),b=Number(canvas?.dataset.end);
        let known=0,required=0,unknown=0;
        for(let i=Math.max(0,Math.floor(a*rate));i<Math.min(c.envKnown.length,Math.ceil(b*rate));i++){
          const t=(i+.5)/rate,gap=c.qa.gaps.some(([s,e])=>s<=t&&t<e);
          if(!gap){required++;if(c.envKnown[i])known++;else unknown++;}
        }
        return [ch,{visible_known:known,visible_required:required,visible_unknown:unknown,
          total_known:c.envKnown.reduce((n,v)=>n+(v?1:0),0),total_bins:c.envKnown.length,
          gaps:c.qa.gaps,pending:c.qa.pending_records,decoded:c.sources.filter(s=>s.decoded).length,
          ownsFullPCM:Object.hasOwn(c,'pcm'),ownsFullBlob:Object.hasOwn(c,'blob')}];
      })),stats:d.store.stats(),status:document.querySelector('#edwaveStatus')?.textContent||''};
    }""")


def wait_waves(page, expected: int, timeout: float = 180) -> dict:
    def ready():
        if page.locator("#edwaveRows .wave-track").count() != expected:
            return False
        rows = tracks(page)
        try:
            validate_rows(rows, expected)
        except (AssertionError, ValueError, TypeError):
            return False
        result = wave_diagnostic(page)
        return (not result["stats"].get("activeEnvelopeRequests", 0)
                and all(c["visible_unknown"] == 0 for c in result["channels"].values()))
    wait_until(page, ready, "all visible track envelopes", timeout)
    result = wave_diagnostic(page)
    assert_bounded(result["stats"])
    assert not result["stats"].get("activeEnvelopeRequests", 0)
    assert all(not c["ownsFullPCM"] and not c["ownsFullBlob"] for c in result["channels"].values())
    return result


def load(page, source: Path, expected: int) -> dict:
    page.locator("#fdir").set_input_files(str(source), timeout=120000)
    wait_until(page, lambda: not page.locator("#loadbtn").is_disabled()
               or page.locator("#dirsum").inner_text() != "檢查錄音格式與日期中…", "multitrack source index")
    assert not page.locator("#loadbtn").is_disabled(), page.locator("#dirsum").inner_text()
    page.locator("#allRecording").click()
    page.locator("#loadbtn").click()
    wait_until(page, lambda: page.locator("#mainpane").is_visible()
               or "載入失敗" in page.locator("#loadmsg").inner_text(), "multitrack load")
    assert page.locator("#mainpane").is_visible(), page.locator("#loadmsg").inner_text()
    page.wait_for_function("window.__vdrReview && !__vdrReview.player.seeking && __vdrReview.player.readyState>0")
    early = tracks(page)
    analyzed = wait_waves(page, expected)
    assert diagnostic(page)["paused"], "Wave analysis must not require playback"
    selected = page.locator("#fdir").evaluate("e=>({files:e.files.length,bytes:Array.from(e.files).reduce((n,f)=>n+f.size,0)})")
    return {"source": str(source), "selected": selected, "rows_initial": early,
            "initial": diagnostic(page), "initial_waves": analyzed}


def label_click(page, channel: str, *, playing: bool) -> dict:
    before = diagnostic(page)
    page.locator(f"button.wave-label[data-channel='{channel}']").click()
    page.wait_for_function("o=>!__vdrReview.player.seeking&&__vdrReview.player.channel===o.ch&&__vdrReview.player.paused!==o.playing", arg={"ch": channel, "playing": playing})
    after = diagnostic(page)
    assert page.locator("#exportChannel").input_value() == channel
    assert after["paused"] != playing
    delta = after["currentTime"] - before["currentTime"]
    assert abs(delta) < (2 if playing else 0.25), f"Label changed playback time: {delta}"
    return {"channel": channel, "before": before["currentTime"], "after": after["currentTime"], "playing": playing}


def wave_click(page, channel: str, fraction: float, *, playing: bool) -> dict:
    canvas = page.locator(f"canvas.wave-canvas[data-channel='{channel}']")
    span = canvas.evaluate("e=>[Number(e.dataset.start),Number(e.dataset.end)]")
    expected = canvas_time(*span, fraction)
    box = canvas.bounding_box()
    assert box
    canvas.click(position={"x": box["width"] * fraction, "y": box["height"] / 2})
    page.wait_for_function("o=>!__vdrReview.player.seeking&&__vdrReview.player.channel===o.ch&&__vdrReview.player.paused!==o.playing", arg={"ch": channel, "playing": playing})
    after = diagnostic(page)
    assert page.locator("#exportChannel").input_value() == channel
    assert after["paused"] != playing
    assert abs(after["currentTime"] - expected) < (2 if playing else 0.25), (expected, after["currentTime"])
    assert_bounded(after["stats"])
    return {"channel": channel, "fraction": fraction, "span": span, "expected": expected,
            "actual": after["currentTime"], "playing": playing}


def pattern_check(page) -> dict:
    values = page.evaluate("""samples=>Object.fromEntries(Object.entries(samples).map(([ch,[a,b]])=>{
      const c=__vdrReview.channels[ch],r=DSP.ENV_RATE;
      const avg=t=>{const p=c.env.slice(Math.round((t-.1)*r),Math.round((t+.1)*r));return Array.from(p).reduce((n,v)=>n+v,0)/p.length};
      return [ch,{active:avg(a),quiet:avg(b),active_known:c.envKnown[Math.round(a*r)],quiet_known:c.envKnown[Math.round(b*r)]}];
    }))""", SAMPLES)
    for channel, value in values.items():
        assert value["active_known"] and value["quiet_known"], channel
        assert value["active"] >= 200 and value["quiet"] <= 50, (channel, value)
    return values


def activity_snapshot(page) -> dict:
    return page.evaluate("""() => {
      const box=document.querySelector('#vhfseg'),strip=document.querySelector('#edstrip');
      const n=(o,k)=>Number(o.dataset[k]);
      return {visible:!!box.getBoundingClientRect().height,text:box.textContent,
        channel:box.dataset.channel,start:n(box,'start'),end:n(box,'end'),
        total:n(box,'total'),shown:n(box,'shown'),pending:n(box,'pending'),unknown:n(box,'unknown'),
        chips:Array.from(box.querySelectorAll('button[data-t]')).map(b=>({
          channel:b.dataset.channel,start:n(b,'start'),end:n(b,'end'),target:n(b,'t'),text:b.textContent})),
        strip:{channel:strip.dataset.activityChannel,count:n(strip,'activityCount'),
          start:n(strip,'activityStart'),end:n(strip,'activityEnd')}};
    }""")


def validate_activity(value: dict, channel: str, *, expect_activity: bool) -> None:
    assert value["visible"] and "聲音活動段" in value["text"]
    assert value["channel"] == channel and value["strip"]["channel"] == channel
    assert all(isinstance(value[k], (int, float)) and math.isfinite(value[k])
               for k in ("start", "end", "total", "shown", "pending", "unknown"))
    assert value["end"] > value["start"]
    assert len(value["chips"]) == value["shown"] <= 40
    assert value["total"] >= value["shown"]
    for chip in value["chips"]:
        assert chip["channel"] == channel
        # Source segment bounds remain intact; only the visible overlap is listed.
        assert chip["start"] < value["end"] and chip["end"] > value["start"]
        assert chip["end"] > chip["start"]
        assert abs(chip["target"] - max(chip["start"], value["start"])) < .001
    if value["total"] > value["shown"]:
        assert re.search("另有|尚有|其餘|還有|超過|僅列|只列", value["text"])
    if value["pending"] or value["unknown"]:
        assert re.search("未分析|已分析|按需|尚未", value["text"]), value["text"]
    if expect_activity:
        assert value["chips"] and value["strip"]["count"] > 0, value


def validate_band_pixels(active: list[int], quiet: list[int]) -> None:
    """Actual rendered green overlay, compared with its adjacent quiet background."""
    assert active[0] < quiet[0] - 15 and active[1] < quiet[1] - 5, (active, quiet)
    assert active[1] - active[0] > 8, (active, quiet)


def strip_pixels(page, channel: str) -> dict:
    return page.locator("#edstrip").evaluate("""(c,times)=>{
      const a=Number(c.dataset.activityStart),b=Number(c.dataset.activityEnd),g=c.getContext('2d');
      const pixel=t=>{if(t<a||t>b)throw new Error('Synthetic sample outside strip');
        return Array.from(g.getImageData(Math.floor((t-a)/(b-a)*c.width),Math.floor(c.height*.3),1,1).data)};
      return {active:pixel(times[0]),quiet:pixel(times[1]),samples:times,start:a,end:b};
    }""", BAND_SAMPLES[channel])


def activity_checks(page, out: Path, *, synthetic: bool, expected: int) -> dict:
    """Inspect all channel chips before any playback, then exercise A1/C1 chips."""
    snapshots, pixels, clicks = {}, {}, []
    if synthetic:
        # The detailed cue strip is intentionally an advanced control now. Its
        # rendered-pixel regression opts in; the default view remains simple.
        reveal_control(page, "#editmode")
        page.locator("#editmode").check()
    channels = list(wave_diagnostic(page)["channels"])
    for channel in channels:
        label_click(page, channel, playing=False)
        wait_waves(page, expected)
        snapshot = activity_snapshot(page)
        validate_activity(snapshot, channel, expect_activity=synthetic)
        assert diagnostic(page)["paused"], "Activity discovery must not require playback"
        snapshots[channel] = snapshot
        if synthetic:
            values = strip_pixels(page, channel)
            validate_band_pixels(values["active"], values["quiet"])
            pixels[channel] = values
            if channel in ("A1", "C1"):
                page.screenshot(path=str(out / ("synthetic-activity-" + channel + ".png")), full_page=True)
    for channel in ("A1", "C1"):
        label_click(page, channel, playing=False)
        snapshot = activity_snapshot(page)
        if not snapshot["chips"]:
            clicks.append({"channel": channel, "skipped": "No detected activity in this analyzed real-data window"})
            continue
        target = max(0, snapshot["chips"][0]["target"] - .5)
        page.locator("#vhfseg button[data-t]").first.click()
        page.wait_for_function("ch=>!__vdrReview.player.seeking&&__vdrReview.player.channel===ch&&!__vdrReview.player.paused", arg=channel)
        after = diagnostic(page)
        assert page.locator("#ch").input_value() == channel
        assert page.locator("#exportChannel").input_value() == channel
        assert target - .1 <= after["currentTime"] <= target + 2, (target, after["currentTime"])
        page.locator("#playbtn").click()
        page.wait_for_function("__vdrReview.player.paused")
        clicks.append({"channel": channel, "target": target, "actual": after["currentTime"], "started_playing": True})
        seek(page, 0)
    overflow = None
    if synthetic:
        label_click(page, "A3", playing=False)
        page.locator("#edwaveScale").select_option("120")
        wait_waves(page, expected)
        overflow = activity_snapshot(page)
        validate_activity(overflow, "A3", expect_activity=True)
        assert overflow["total"] > 40 and overflow["shown"] == 40, overflow
        page.locator("#vhfseg").screenshot(path=str(out / "synthetic-activity-overflow.png"))
        page.locator("#edwaveScale").select_option("60")
    label_click(page, "A1", playing=False)
    seek(page, 0)
    wait_waves(page, expected)
    return {"before_playback": snapshots, "actual_canvas_pixels": pixels,
            "chip_clicks": clicks, "overflow": overflow}


def cue_drag_check(page) -> dict:
    """SRT remains editable; selecting a far cue pins every track to its window."""
    label_click(page, "A1", playing=False)
    seek(page, 0)
    text = ("1\n00:00:12,000 --> 00:00:18,000\nSYNTHETIC NEAR CUE\n\n"
            "2\n00:01:40,000 --> 00:01:46,000\nSYNTHETIC FAR CUE\n")
    reveal_control(page, "#imSrt")
    page.locator("#imSrt").set_input_files({"name": "synthetic-multitrack.srt",
                                           "mimeType": "text/plain", "buffer": text.encode("utf-8")})
    page.locator("#srtImportPanel").wait_for(state="visible")
    page.locator("#srtImportChannel").select_option("A1")
    page.locator("#srtImportOrigin").select_option("manual")
    page.locator("#srtLocalStart").fill(datetime_local_value(START + timedelta(hours=8)))
    page.locator("#srtImportApply").click()
    page.locator("#srtImportPanel").wait_for(state="hidden")
    reveal_control(page, "#editmode")
    page.locator("#editmode").check()
    page.locator("#edlist .tx").nth(1).click()
    page.wait_for_function("()=>{const c=document.querySelector('canvas.wave-canvas[data-channel=A1]');return Number(c.dataset.start)<=100&&Number(c.dataset.end)>=106}")
    focused = tracks(page)
    validate_rows(focused, 4)

    def drag(channel: str, before: float, after: float) -> None:
        canvas = page.locator(f"canvas.wave-canvas[data-channel='{channel}']")
        box = canvas.bounding_box()
        span = canvas.evaluate("e=>[Number(e.dataset.start),Number(e.dataset.end)]")
        assert box
        x = lambda t: box["x"] + (t - span[0]) / (span[1] - span[0]) * box["width"]
        y = box["y"] + box["height"] * .6
        page.mouse.move(x(before), y)
        page.mouse.down()
        page.mouse.move(x(after), y, steps=8)
        page.mouse.up()

    drag("A1", 100, 99)
    drag("A1", 106, 107)
    enable_virtual_project_file(page)
    project = save_project(page)
    far = next(c for c in project["state"]["A1"] if c["text"] == "SYNTHETIC FAR CUE")
    assert abs(far["start"] - 99) < .15 and abs(far["end"] - 107) < .15, far
    # The same X position on an inactive track may switch track/seek, never drag A1's cue.
    drag("A2", far["start"], far["start"] + 1)
    project_after = save_project(page, 1)
    unchanged = next(c for c in project_after["state"]["A1"] if c["text"] == "SYNTHETIC FAR CUE")
    assert unchanged["start"] == far["start"] and unchanged["end"] == far["end"]
    assert len(project_after["state"]["A1"]) == 2
    return {"far_cue_focused_window": focused, "start": far["start"], "end": far["end"],
            "inactive_track_did_not_modify_cue": True, "srt_text_preserved_in_saved_project": True,
            "file_permission_boundary": "virtual; actual OS permission not tested"}


def rapid_seek(page, expected: int, *, require_cancel: bool) -> dict:
    before = wave_diagnostic(page)
    page.locator("#edwaveScale").select_option("300")
    duration = diagnostic(page)["duration"]
    positions = [duration * x for x in (0.2, 0.8, 0.35, 0.9, 0.55)]
    unknown_seen = False
    for index, position in enumerate(positions):
        page.locator("#seek").evaluate("""(e,t)=>{e.value=String(t);
          e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));}""", position)
        if index == 0 and require_cancel:
            page.wait_for_function("n=>__vdrReview.store.stats().envelopeRequests>n", arg=before["stats"].get("envelopeRequests", 0))
            page.wait_for_function("Array.from(document.querySelectorAll('#edwaveRows .wave-track')).some(r=>Number(r.dataset.unknown)>0)", timeout=10000)
            activity_pending = activity_snapshot(page)
            validate_activity(activity_pending, diagnostic(page)["channel"], expect_activity=False)
            assert activity_pending["unknown"] > 0, activity_pending
        rows = tracks(page)
        unknown_seen = unknown_seen or any(float(r.get("unknown") or 0) > 0 for r in rows)
        page.wait_for_timeout(30)
    page.wait_for_function("t=>!__vdrReview.player.seeking&&Math.abs(__vdrReview.player.currentTime-t)<.25", arg=positions[-1], timeout=120000)
    after = wait_waves(page, expected)
    cancelled = after["stats"].get("envelopeCancelledRequests", 0) - before["stats"].get("envelopeCancelledRequests", 0)
    if require_cancel:
        assert cancelled > 0, "No cancelled envelope request observed during rapid UI seeks"
        assert unknown_seen, "Unanalyzed ranges must be visibly distinct from silence"
    return {"positions": positions, "before": before["stats"], "after": after,
            "activity_pending": activity_pending if require_cancel else None,
            "cancelled_requests": cancelled, "unknown_mark_observed": unknown_seen}


def run(browser, html: Path, source: Path, out: Path, *, synthetic: bool) -> dict:
    expected = 4 if synthetic else 6
    context, page, evidence = open_page(browser, html, False)
    result = {"synthetic": synthetic, "expected_channels": expected, "ok": False}
    try:
        emit("multitrack_started", synthetic=synthetic, channels=expected)
        result.update(load(page, source, expected))
        emit("multitrack_loaded", synthetic=synthetic, selected=result["selected"], waves=result["initial_waves"])
        validate_rows(tracks(page), expected)
        result["activity"] = activity_checks(page, out, synthetic=synthetic, expected=expected)
        if synthetic:
            # One minute was decoded for all four tracks without listening to them.
            result["patterns"] = pattern_check(page)
            page.screenshot(path=str(out / "synthetic-four-tracks.png"), full_page=True)
            page.locator("#edwaves").screenshot(path=str(out / "synthetic-four-tracks-detail.png"))
        seek(page, 20)
        result["label_clicks_paused"] = [label_click(page, ch, playing=False)
                                         for ch in result["initial_waves"]["channels"]]
        wait_waves(page, expected)
        result["wave_click_paused"] = wave_click(page, "A2", 0.55, playing=False)
        page.locator("#playbtn").click()
        page.wait_for_function("!__vdrReview.player.paused")
        result["label_click_playing"] = label_click(page, "A3", playing=True)
        result["wave_click_playing"] = wave_click(page, "A1", 0.65, playing=True)
        page.locator("#playbtn").click()
        page.wait_for_function("__vdrReview.player.paused")
        if synthetic:
            result["cue_drag"] = cue_drag_check(page)
            seek(page, 90)
            result["gap_waves"] = wait_waves(page, expected)
            rows = tracks(page)
            c1 = next(row for row in rows if row["channel"] == "C1")
            assert float(c1["gaps"]) > 0, c1
            assert result["gap_waves"]["channels"]["C1"]["gaps"] == [[60, 120]]
            result["gap_rows"] = rows
            page.screenshot(path=str(out / "synthetic-missing-minute.png"), full_page=True)
        result["rapid_seek"] = rapid_seek(page, expected, require_cancel=not synthetic)
        result["final"] = diagnostic(page)
        result["final_rows"] = tracks(page)
        validate_rows(result["final_rows"], expected)
        assert_bounded(result["final"]["stats"])
        if not synthetic:
            total = sum(c["records"] for c in result["final"]["channels"])
            assert result["final"]["stats"]["decodedRecords"] < total / 2, "Eager full-source decode"
        assert not evidence["page_errors"] and not evidence["console_errors"], evidence
        assert_map_only_network(evidence)
        assert not evidence["downloads"], "Multitrack viewing must not export audio"
        result["ok"] = True
    except Exception as error:
        result["failure"] = type(error).__name__ + ": " + str(error)
        result["traceback"] = traceback.format_exc()
        try:
            result["failure_waves"] = wave_diagnostic(page)
            result["failure_rows"] = tracks(page)
            if synthetic:
                page.screenshot(path=str(out / "synthetic-failure.png"), full_page=True)
        except Exception:
            pass
        emit("multitrack_failed", synthetic=synthetic, failure=result["failure"])
    finally:
        result.update(evidence)
        context.close()
    emit("multitrack_done", synthetic=synthetic, ok=result["ok"])
    return result


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", choices=BROWSERS, default="chrome")
    parser.add_argument("--html", type=Path, default=ROOT / "output" / "VDR核聽工具.html")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--fixture", type=Path, default=ROOT / "output" / "playwright" / "vdr-multitrack" / "SYNTHETIC_ACTIVITY_2M")
    parser.add_argument("--only", choices=("synthetic", "real", "both"), default="synthetic")
    parser.add_argument("--real-source", type=Path)
    args = parser.parse_args()
    if args.only in ("real", "both") and not args.real_source:
        parser.error("--real-source is required for explicitly scoped real-data validation")
    out = args.out or ROOT / "output" / "playwright" / "vdr-multitrack" / args.browser
    out.mkdir(parents=True, exist_ok=True)
    result = {"browser": args.browser, "html_sha256": hashlib.sha256(args.html.read_bytes()).hexdigest(),
              "network_interception": False, "audio_muted": True, "real_audio_exported": False,
              "network_policy": "Only body-free HTTPS GET image requests for NLSC PHOTO2/EMAP WMTS tiles; no audio/text uploads or other hosts.",
              "caveat": "Bounded cache/JS heap observations do not certify actual 8 GB hardware or vendor timing.", "scenarios": []}
    if args.only in ("synthetic", "both"):
        result["fixture"] = create_multitrack_fixture(args.fixture)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=BROWSERS[args.browser], headless=True,
                                             args=["--mute-audio"], timeout=30000)
        result["browser_version"] = browser.version
        if args.only in ("synthetic", "both"):
            result["scenarios"].append(run(browser, args.html, args.fixture, out, synthetic=True))
        if args.only in ("real", "both"):
            result["scenarios"].append(run(browser, args.html, args.real_source.resolve(), out, synthetic=False))
        browser.close()
    result["ok"] = all(s["ok"] for s in result["scenarios"])
    destination = out / (args.browser + "_" + args.only + "_result.json")
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    emit("complete", ok=result["ok"], result=str(destination))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
