import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "vdr_review" / "src").as_posix()
sys.path.insert(0, str(ROOT / "scripts" / "vdr_review"))
from build import build  # noqa: E402

NODE = shutil.which("node")
FFMPEG = shutil.which("ffmpeg") or ("C:/ffmpeg/bin/ffmpeg.exe" if Path("C:/ffmpeg/bin/ffmpeg.exe").exists() else None)
needs_node = pytest.mark.skipif(not NODE, reason="node is not installed")


def run_node(code: str):
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


def test_build_inlines_every_module_once():
    html = build()
    assert "/*APP_JS*/" not in html and "/*LEAFLET_JS*/" not in html and "/*LEAFLET_CSS*/" not in html
    assert html.count("const WORKER_SRC=") == 1
    assert "G726" in html and "Editor" in html and "AISCSV" in html
    assert "/*LICENSE_LGPL*/" not in html and "GNU LESSER GENERAL PUBLIC LICENSE" in html
    assert "/*LICENSE_MIT*/" not in html and "Copyright (c) 2026 newqqo" in html
    assert "Vladimir Agafonkin" in html and html.count("const BUILD_INFO=") == 1


@needs_node
def test_review_window_clips_subtitles_without_changing_saved_progress():
    result = run_node(f"""
const R=require('{SRC}/review_state.js');
const cues=[{{start:-9,end:-1}},{{start:-1,end:2,text:'left'}},{{start:58,end:65,text:'right'}},{{start:60,end:70}},{{start:1,end:1.0001}}];
const original=JSON.stringify(cues), clipped=R.clipCues(cues,60), shifted=R.shiftState({{C1:cues}},10).C1;
console.log(JSON.stringify({{clipped,unchanged:original===JSON.stringify(cues),shifted:shifted[1]}}));""")
    assert result == {"clipped": [{"start": 0, "end": 2, "text": "left"},
                                  {"start": 58, "end": 60, "text": "right"}], "unchanged": True,
                      "shifted": {"start": 9, "end": 12, "text": "left"}}


@needs_node
def test_review_dates_and_legacy_ambiguity_fail_closed():
    result = run_node(f"""
const R=require('{SRC}/review_state.js');
console.log(JSON.stringify({{
bad:['2026-02-30 18:43','2026-10-22 25:99','2026-10-22 18:43:60'].map(s=>R.parseDateTime(s,28800)),
good:R.parseDateTime('2026-10-22 18:43',28800),
one:R.autoLegacy([{{id:1}}]),many:R.autoLegacy([{{id:1}},{{id:2}}]),
invalid:R.validProgress({{state:{{A1:[{{start:5,end:2}}]}}}}),valid:R.validProgress({{state:{{A1:[{{start:-1,end:2}}]}}}})
}}));""")
    assert result["bad"] == [None, None, None]
    assert result["good"] == 1792665780000
    assert result["one"] == {"id": 1} and result["many"] is None
    assert result["valid"] and not result["invalid"]


@needs_node
def test_vdr_rejects_multiple_recording_roots():
    result = run_node(f"""
const V=require('{SRC}/vdr.js');let error='';
try{{V.classify([{{path:'parent/ship1/Audio/202601010800/A1195345.dat'}},{{path:'parent/ship2/Log/202601010800/L0190000.log'}}])}}catch(e){{error=e.message}}
const c=V.classify([{{path:'ship/Audio/202601010800/A1195345.dat'}},{{path:'ship/Log/202601010800/L0190000.log'}},{{path:'ship/other/202601010800/A1195345.dat'}}]);
console.log(JSON.stringify({{error,a:c.audio.A1.length,l:c.logs.length,skipped:c.skipped.length}}));""")
    assert "多份 VDR" in result["error"]
    assert (result["a"], result["l"], result["skipped"]) == (1, 1, 1)


@needs_node
@pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not installed")
def test_g726_decoder_matches_ffmpeg(tmp_path):
    coded = tmp_path / "syn.g726"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=8000:duration=2",
                    "-f", "lavfi", "-i", "anoisesrc=r=8000:d=2:a=0.3", "-filter_complex", "amix=inputs=2",
                    "-c:a", "g726", "-code_size", "3", "-f", "g726", str(coded)], check=True)
    ref = subprocess.run([FFMPEG, "-v", "error", "-f", "g726", "-code_size", "3", "-ar", "8000", "-i", str(coded),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    (tmp_path / "ref.raw").write_bytes(ref)
    result = run_node(f"""
const fs=require('fs');const {{decode}}=require('{SRC}/g726.js');
const js=decode(new Uint8Array(fs.readFileSync('{coded.as_posix()}')),3);
const r=fs.readFileSync('{(tmp_path / "ref.raw").as_posix()}');const ref=new Int16Array(r.buffer,r.byteOffset,r.length/2);
let diff=0;for(let i=0;i<Math.min(js.length,ref.length);i++)if(js[i]!==ref[i])diff++;
console.log(JSON.stringify({{n:js.length,ref:ref.length,diff}}));""")
    assert result == {"n": 16000, "ref": 16000, "diff": 0}


@needs_node
def test_jcy_audio_record_log_and_ais_parsing():
    result = run_node(f"""
const V=require('{SRC}/vdr.js');
const payload=new Uint8Array(315000).fill(0x55);
const rec=new Uint8Array(32+payload.length+2);rec.set(Buffer.from('VoyageDtRec ~'));
const n=payload.length+2;rec[26]=n>>>24;rec[27]=(n>>16)&255;rec[28]=(n>>8)&255;rec[29]=n&255;rec[30]=13;rec[31]=10;
rec.set(payload,32);rec[rec.length-2]=13;rec[rec.length-1]=10;
const a=V.parseAudioRecord(rec);
let bad='';try{{const b=rec.slice();b[29]^=1;V.parseAudioRecord(b)}}catch(e){{bad=e.message}}
const cls=V.classify([{{path:'X/Audio/202601010800/A1195345.dat'}},{{path:'X/Log/202601010800/L0190000.log'}},{{path:'X/readme.txt'}}]);
const s='$VRHDT,84.2,T*'+(()=>{{let x=0;for(const c of 'VRHDT,84.2,T')x^=c.charCodeAt(0);return x.toString(16).toUpperCase().padStart(2,'0')}})();
const ais='!AIVDM,1,1,,A,1>pf7h@00o8VMOP<ovH2nRB00000,0*62';
const mk=(off,str)=>[0xff,0xff,off>>8,off&255,0,1,...Buffer.from(str),13,10];
const log=new Uint8Array([...mk(256,s),...mk(512,ais)]);
const recs=V.parseLog(log,Date.UTC(2026,0,1,8,19,0));
const nav=V.navSeries(recs);const dec=V.decodeAis(recs);
console.log(JSON.stringify({{payload:a.payload.length,rate:V.inferRate(a.payload.length),bad,
 audio:cls.audio.A1[0].start,log:cls.logs[0].start,skipped:cls.skipped.length,recs:recs.length,hdg:nav.series[0].hdg,
 navUtc:nav.series[0].utc,ais:Object.keys(dec.positions)}}));""")
    assert result["payload"] == 315000 and result["rate"] == {"rate": 14000, "exact": True, "common": True}
    assert "長度欄" in result["bad"]
    assert result["audio"] == 1767255593450 and result["log"] == 1767255540000 and result["skipped"] == 1
    assert result["recs"] == 2 and result["hdg"] == 84.2 and result["navUtc"] == 1767255541
    assert result["ais"] == ["999000001"]


@needs_node
def test_gatehouse_csv_dedupes_converts_time_and_drops_outliers():
    result = run_node(f"""
const G=require('{SRC}/gatehouse.js');
const head='Base station time stamp,MMSI,Ship name,Latitude,Longitude,Course over ground,Speed over ground,AIS message ID,IMO,Callsign,Heading,Position accuracy';
const rows=[head,
 '01 Jan 2026 16:07:02 CST,999000001,,23.53625,120.25008,73.0,5.5,1,,,70,0',
 '01 Jan 2026 16:07:02 CST,999000001,,23.53625,120.25008,73.0,5.5,1,,,70,0',
 '01 Jan 2026 16:07:12 CST,999000001,,23.53640,120.25040,73.0,5.5,1,,,511,0',
 '01 Jan 2026 16:07:22 CST,999000001,,23.93640,120.25040,73.0,5.5,1,,,70,0',
 '01 Jan 2026 16:07:32 CST,999000001,,23.53660,120.25080,73.0,5.5,1,,,70,0',
 'bad,999000001,,,,,,,,,,'].join('\\r\\n');
const r=G.merge([G.parseCsvText(rows)]);const t=r.tracks['999000001'];
console.log(JSON.stringify({{n:t.length,first:t[0][0],hdg:t[1][5],skipped:r.skipped,outliers:r.outliers,
 iso:G.parseTime('2026-01-01T16:07:02+08:00',8)}}));""")
    assert result == {"n": 3, "first": 1767254822000, "hdg": None, "skipped": 1, "outliers": 1, "iso": 1767254822000}


@needs_node
def test_outlier_filter_keeps_the_track_after_a_bad_first_fix_or_a_bad_pair():
    result = run_node(f"""
const G=require('{SRC}/gatehouse.js');
const t0=Date.UTC(2026,0,1,8,0,0),good=i=>[t0+i*10000,22.5+i*0.0001,120.25,5,0,null];
const track=bad=>{{const a=Array.from({{length:12}},(_,i)=>good(i));for(const i of bad)a[i]=[a[i][0],22.68,120.25,5,0,null];return a;}};
const run=bad=>{{const r=G.merge([{{tracks:{{'999000001':track(bad)}},names:{{}},rows:12,skipped:0}}]);
 return {{kept:r.tracks['999000001'].map(p=>Math.round((p[0]-t0)/10000)),outliers:r.outliers}};}};
console.log(JSON.stringify({{first:run([0]),pair:run([5,6]),spike:run([7]),last:run([11])}}));""")
    # A fix about 20 km off passes the 50 km median test; it must not take the real track with it.
    assert result["first"] == {"kept": list(range(1, 12)), "outliers": 1}
    assert result["pair"] == {"kept": [i for i in range(12) if i not in (5, 6)], "outliers": 2}
    assert result["spike"] == {"kept": [i for i in range(12) if i != 7], "outliers": 1}
    assert result["last"] == {"kept": list(range(11)), "outliers": 1}
