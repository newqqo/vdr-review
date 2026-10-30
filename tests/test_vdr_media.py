"""Pure media helpers: explicit event date and labelled, non-mutating excerpts."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "vdr_review" / "src").as_posix()
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")


def run_node(code):
    script = f"""
global.ReviewState=require('{SRC}/review_state.js');
const M=require('{SRC}/media.js');
function failure(fn){{try{{fn();return null;}}catch(e){{return e.message;}}}}
{code}
"""
    result = subprocess.run([NODE, "-e", script], check=True, capture_output=True,
                            text=True, encoding="utf-8")
    return json.loads(result.stdout)


def test_event_window_requires_a_day_instead_of_guessing_between_recorded_days():
    result = run_node("""
const cover=[Date.UTC(2026,9,22),Date.UTC(2026,9,25)];
console.log(JSON.stringify({
 missing:failure(()=>M.eventWindow(cover,'','09:12',5,28800)),
 day1:M.eventWindow(cover,'2026-10-22','09:12',5,28800),
 day2:M.eventWindow(cover,'2026-10-23','09:12',5,28800),
 utc:M.eventWindow(cover,'2026-10-23','01:12',5,0)
}));
""")
    assert "事故日期" in result["missing"]
    assert result["day2"] == result["utc"]
    assert result["day2"][0] - result["day1"][0] == 86400000
    assert result["day2"][1] - result["day2"][0] == 600000


@pytest.mark.parametrize(("day", "clock", "minutes", "tz"), [
    ("2026-02-30", "09:12", "5", "28800"),
    ("2026-10-23", "24:00", "5", "28800"),
    ("2026-10-23", "09:60", "5", "28800"),
    ("2026-10-23", "09:12:60", "5", "28800"),
    ("2026-10-23", "09:12", "0", "28800"),
    ("2026-10-23", "09:12", "121", "28800"),
    ("2026-10-23", "09:12", "NaN", "28800"),
    ("2026-10-23", "09:12", "5", "Infinity"),
    ("2026-10-23", "09:12", "5", "15*3600"),
    ("2026-10-23", "09:12", "5", "-13*3600"),
])
def test_event_window_rejects_invalid_dates_clocks_spans_and_timezones(day, clock, minutes, tz):
    result = run_node(f"""
const cover=[Date.UTC(2026,0,1),Date.UTC(2027,0,1)];
console.log(JSON.stringify({{error:failure(()=>M.eventWindow(cover,'{day}','{clock}',{minutes},{tz}))}}));
""")
    assert result["error"]


def test_event_window_clips_to_recording_coverage_and_checks_exclusive_end():
    result = run_node("""
const cover=[Date.UTC(2026,9,23,1,10),Date.UTC(2026,9,23,1,15)];
console.log(JSON.stringify({window:M.eventWindow(cover,'2026-10-23','09：12',5,28800),
 wrongday:failure(()=>M.eventWindow(cover,'2026-10-24','09:12',5,28800)),
 end:failure(()=>M.eventWindow(cover,'2026-10-23','09:15',5,28800)),
 wrongzone:failure(()=>M.eventWindow(cover,'2026-10-23','09:12',5,0))}));
""")
    assert result["window"] == [1792717800000, 1792718100000]
    assert all(result[k] for k in ("wrongday", "end", "wrongzone"))


def test_original_clip_preserves_exact_samples_and_reports_sample_boundaries():
    result = run_node("""
const source=Int16Array.from([-32768,-10000,-999,-1,0,1,999,10000,32767,23]);
const before=[...source], r=M.clipPCM(source,8000,.0002,.0009,false);
const samples=[...r.pcm];r.pcm[0]=42;
console.log(JSON.stringify({samples,processing:r.processing,start:r.start,end:r.end,
 source:[...source],unchanged:JSON.stringify(before)===JSON.stringify([...source])}));
""")
    assert result["samples"] == [-999, -1, 0, 1, 999]
    assert result["processing"] == {"kind": "decoded_original", "sample_start": 2,
                                     "sample_end": 7, "gain": 1}
    assert (result["start"], result["end"]) == (2 / 8000, 7 / 8000)
    assert result["unchanged"]
    assert result["source"] == [-32768, -10000, -999, -1, 0, 1, 999, 10000, 32767, 23]


@pytest.mark.parametrize("rate", [8000, 14000, 16000])
def test_enhanced_clip_is_distinct_and_carries_filter_gain_receipt(rate):
    result = run_node(f"""
const rate={rate}, source=Int16Array.from({{length:rate}},(_,i)=>
 Math.round(500*Math.sin(2*Math.PI*800*i/rate)+200*Math.sin(2*Math.PI*50*i/rate)));
const before=source.slice(), r=M.clipPCM(source,rate,.1,.3,true);
let unchanged=true,different=0,peak=0;
for(let i=0;i<source.length;i++)if(source[i]!==before[i])unchanged=false;
for(let i=0;i<r.pcm.length;i++){{if(r.pcm[i]!==source[r.processing.sample_start+i])different++;peak=Math.max(peak,Math.abs(r.pcm[i]));}}
console.log(JSON.stringify({{processing:r.processing,unchanged,different,peak,n:r.pcm.length,start:r.start,end:r.end}}));
""")
    receipt = result["processing"]
    assert result["unchanged"] and result["different"] > 0
    assert receipt["kind"] == "enhanced_listening_only"
    assert receipt["sample_start"] == round(.1 * rate)
    assert receipt["sample_end"] == round(.3 * rate)
    assert receipt["highpass_hz"] == 300 and receipt["lowpass_hz"] == 3400
    assert receipt["filter"] == "RBJ Butterworth Q=0.7071, two cascaded biquads each"
    assert 0 < receipt["gain"] <= receipt["max_gain"] == 20
    assert receipt["peak_target"] == .9
    assert result["peak"] <= round(.9 * 32767) + 1
    assert result["n"] == round(.2 * rate)
    assert (result["start"], result["end"]) == (.1, .3)


def test_clip_clamps_edges_and_rejects_empty_or_invalid_ranges():
    result = run_node("""
const source=Int16Array.from([0,1,2,3]);
const r=M.clipPCM(source,8000,-1,1,false),silent=M.clipPCM(new Int16Array(80),8000,0,.01,true);
console.log(JSON.stringify({all:[...r.pcm],start:r.start,end:r.end,
 silent:silent.processing,errors:[()=>M.clipPCM(source,8000,1,0,false),
 ()=>M.clipPCM(source,8000,0,0,false),()=>M.clipPCM(source,8000,NaN,1,false)].map(f=>failure(f))}));
""")
    assert result["all"] == [0, 1, 2, 3] and result["start"] == 0
    assert result["end"] == 4 / 8000
    assert result["silent"]["gain"] == 1
    assert all(result["errors"])
