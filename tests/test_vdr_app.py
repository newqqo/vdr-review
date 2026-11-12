"""Source identity and delivery regressions; synthetic data only."""
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
sys.path.insert(0, str(ROOT / "scripts" / "vdr_review"))
from build import main  # noqa: E402


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_recording_identity_covers_middle_audio_and_all_logs_but_not_root_name():
    source = (ROOT / "scripts/vdr_review/src/app.js").read_text(encoding="utf-8")
    start = source.index("async function recordingIdentity(")
    end = source.index("\n$('loadbtn').onclick", start)
    code = """
const crypto=require('node:crypto');
const sha256=async data=>crypto.createHash('sha256').update(Buffer.from(data)).digest('hex');
function file(path,text,start=0,codec='g726'){
 const b=Buffer.from(text);return {path,start,codec,size:b.length,file:{size:b.length,
 arrayBuffer:async()=>b.buffer.slice(b.byteOffset,b.byteOffset+b.byteLength)}};
}
function recording(root='copy',middle='middle',log='navigation'){
 return {audio:{A1:[file(root+'/Audio/202610230900/A1000000.dat','first'),
 file(root+'/Audio/202610230900/A1010000.dat',middle,60000),
 file(root+'/Audio/202610230900/A1020000.dat','last',120000)]},
 logs:[file(root+'/Log/202610230900/L0000000.log',log,0,'log')]};
}
""" + source[start:end] + """
(async()=>{
 const progress=[],a=recording(),base=await recordingIdentity(a,(n,t)=>progress.push([n,t]));
 const renamed=await recordingIdentity(recording('different-copy-nameLog'),()=>{});
 const changed=await recordingIdentity(recording('copy','MIDDLE'),()=>{});
 const navChanged=await recordingIdentity(recording('copy','middle','NAVIGATION'),()=>{});
 const reversed=recording();reversed.audio.A1.reverse();
 const order=await recordingIdentity(reversed,()=>{});
 console.log(JSON.stringify({base,renamed,changed,navChanged,order,progress,
 allHashes:[...a.audio.A1,...a.logs].every(f=>/^[a-f0-9]{64}$/.test(f.sha256))}));
})();
"""
    proc = subprocess.run([NODE, "-e", code], check=True, capture_output=True,
                          text=True, encoding="utf-8")
    data = json.loads(proc.stdout)
    assert data["base"] == data["renamed"] == data["order"]
    assert data["base"] != data["changed"]
    assert data["base"] != data["navChanged"]
    assert data["allHashes"]
    assert data["progress"] == [[1, 4], [2, 4], [3, 4], [4, 4]]


def test_colleague_package_only_contains_local_html_and_instructions(tmp_path):
    html = tmp_path / "tool.html"
    package = tmp_path / "colleague.zip"
    assert main(["--out", str(html), "--package", str(package)]) == 0
    with zipfile.ZipFile(package) as zf:
        assert zf.testzip() is None
        assert set(zf.namelist()) == {"開啟VDR核聽工具.html", "使用說明.txt"}
        assert zf.read("開啟VDR核聽工具.html") == html.read_bytes()
        assert "不需 Python" in zf.read("使用說明.txt").decode("utf-8-sig")
    text = html.read_text(encoding="utf-8")
    assert "VDRProject" in text and "VDRMedia" in text
    assert "evtdate" in text and "exBundle" in text
    assert "tileConsent" not in text
    assert "const baseName = tr('map.photo')" in text
    assert "layers: [BASES[baseName]]" in text
    assert "https://wmts.nlsc.gov.tw/wmts/PHOTO2/default/GoogleMapsCompatible/{z}/{y}/{x}" in text
    assert 'value="8" min="-12" max="14"' in text


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_four_hour_multichannel_window_is_indexed_without_whole_pcm():
    stream = (ROOT / "scripts/vdr_review/src/stream.js").as_posix()
    code = f"""
global.DSP={{ENV_RATE:20}};
const M=require('{stream}'),audio={{}};
for(let i=1;i<=6;i++)audio['A'+i]=Array.from({{length:240}},(_,m)=>
 ({{start:m*60000,duration:60,rate:14000,codec:'g726',path:'A'+i+'/'+m,file:{{size:1}}}}));
const s=M.createStore({{audio,W0:0,W1:14400000}});
console.log(JSON.stringify({{duration:s.duration,channels:Object.keys(s.channels).length,
 hasPcm:Object.values(s.channels).some(c=>'pcm' in c||'blob' in c),stats:s.stats()}}));
"""
    proc = subprocess.run([NODE, "-e", code], check=True, capture_output=True,
                          text=True, encoding="utf-8")
    result = json.loads(proc.stdout)
    assert result["duration"] == 14400 and result["channels"] == 6
    assert result["hasPcm"] is False
    assert result["stats"]["cacheBytes"] == 0


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_visible_waveform_requests_deduplicate_and_cancel_obsolete_views():
    source = (ROOT / "scripts/vdr_review/src/app.js").read_text(encoding="utf-8")
    start = source.index("  let waveformRequest = null;")
    end = source.index("  function setCh()", start)
    code = """
const CHS={A1:{},A2:{}},DUR=120,calls=[],listeners={};
const window={addEventListener:(type,fn)=>listeners[type]=fn};
const store={ensureEnvelopes:(channels,start,end,options)=>{
 calls.push({channels,start,end,signal:options.signal});return Promise.resolve({});
}};
""" + source[start:end] + """
(async()=>{
 const first=requestWaveforms(['A2','A1','A1','invalid'],-10,20);
 const same=requestWaveforms(['A1','A2'],0,20);
 const second=requestWaveforms(['A2'],60,200);
 const invalid=await requestWaveforms(['invalid'],0,30);
 const beforeUnload=calls.map(c=>c.signal.aborted);
 listeners.pagehide();
 console.log(JSON.stringify({same:first===same,different:first!==second,invalid,
  beforeUnload,afterUnload:calls.map(c=>c.signal.aborted),
  ranges:calls.map(c=>({channels:c.channels,start:c.start,end:c.end}))}));
})();
"""
    proc = subprocess.run([NODE, "-e", code], check=True, capture_output=True,
                          text=True, encoding="utf-8")
    result = json.loads(proc.stdout)
    assert result["same"] and result["different"]
    assert result["invalid"] is None
    assert result["beforeUnload"] == [True, False]
    assert result["afterUnload"] == [True, True]
    assert result["ranges"] == [
        {"channels": ["A1", "A2"], "start": 0, "end": 20},
        {"channels": ["A2"], "start": 60, "end": 120},
    ]


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_activity_candidates_include_mic_channels_and_only_refresh_changed_channel():
    source = (ROOT / "scripts/vdr_review/src/app.js").read_text(encoding="utf-8")
    start = source.index("  function refreshActivity(ch) {")
    end = source.index("  refreshActivity();", start)
    code = """
const CHS={},VHF={},calls=[];
for(const name of ['A1','A2','A3','C1'])
 CHS[name]={env:{name},envKnown:{name,known:true}};
const DSP={segments:(env,threshold,gap,minDur,known)=>{
 calls.push({name:env.name,threshold,gap,minDur,known:known===CHS[env.name].envKnown});
 return [[calls.length,calls.length+1]];
}};
""" + source[start:end] + """
refreshActivity();
const initial=Object.keys(VHF),before=Object.fromEntries(Object.entries(VHF));
refreshActivity('A2');refreshActivity('missing');
console.log(JSON.stringify({initial,calls,changed:Object.keys(VHF).filter(k=>VHF[k]!==before[k])}));
"""
    proc = subprocess.run([NODE, "-e", code], check=True, capture_output=True,
                          text=True, encoding="utf-8")
    result = json.loads(proc.stdout)
    assert result["initial"] == ["A1", "A2", "A3", "C1"]
    assert [c["name"] for c in result["calls"]] == ["A1", "A2", "A3", "C1", "A2"]
    assert all(c["known"] and c["threshold"] == 64 and c["gap"] == 1.5
               and c["minDur"] == 0.5 for c in result["calls"])
    assert result["changed"] == ["A2"]


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_ais_files_are_read_once_and_parsed_again_when_the_zone_changes():
    source = (ROOT / "scripts/vdr_review/src/app.js").read_text(encoding="utf-8")
    start = source.index("$('tz').addEventListener('change'")
    end = source.index("// ---------- 4.", start)
    code = """
const crypto=require('node:crypto');
const AISCSV=require(""" + json.dumps((ROOT / "scripts/vdr_review/src/gatehouse.js").as_posix()) + """);
const sha256=async data=>crypto.createHash('sha256').update(Buffer.from(data)).digest('hex');
const nodes={},$=id=>nodes[id]||(nodes[id]={value:'',disabled:false,hidden:false,addEventListener(type,fn){this['on'+type]=fn;}});
const S={csv:null,csvNames:[],csvSources:[],canLoad:true};
let TZ=0;const html=(id,render)=>{$(id).html=render();},text=()=>{},esc=String,tr=k=>k,errHTML=String,errorText=e=>e.stack;
const parseDT=()=>null,fmtDT=String,setWindowFromEvent=()=>{};
""" + source[start:end] + """
(async()=>{
 const csv='MMSI,Timestamp,Latitude,Longitude\\n999000001,2026-01-01 08:00:00,22.5,120.25\\n';
 const bytes=Buffer.from(csv);let reads=0;
 const file={name:'track.csv',size:bytes.length,
  arrayBuffer:async()=>{reads++;return bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength);},
  text:async()=>{reads++;return csv;},stream:()=>{throw new Error('unexpected stream read');}};
 $('tz').value='0';await $('fcsv').onchange({target:{files:[file]}});
 const atZero=S.csv?.tracks['999000001'][0][0];
 $('tz').value='8';$('tz').onchange();
 const atEight=S.csv?.tracks['999000001'][0][0];
 console.log(JSON.stringify({reads,atZero,atEight,summary:$('csvsum').html,hash:S.csvSources[0]?.sha256,expected:await sha256(bytes)}));
})();
"""
    proc = subprocess.run([NODE, "-e", code], check=True, capture_output=True, text=True, encoding="utf-8")
    data = json.loads(proc.stdout)
    # The recorded hash must describe the very bytes that were parsed.
    assert data["reads"] == 1 and data["hash"] == data["expected"]
    # Zone-less timestamps follow the zone field, also after the file was imported.
    assert data["atZero"] == 1767254400000 and data["atEight"] == 1767254400000 - 8 * 3600000
    assert "csvSummary" in data["summary"]


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_ais_source_choice_keeps_vdr_and_gatehouse_tracks_apart():
    source = (ROOT / "scripts/vdr_review/src/app.js").read_text(encoding="utf-8")
    start = source.index("function aisTrack(")
    end = source.index(chr(10) + "}" + chr(10), start) + 3
    code = source[start:end] + """
const v=[[0,1,1]],g=[[5,2,2]],sources={vdr:{'1':v,'2':v},gatehouse:{'1':g,'3':g}};
const pick=(m,mode)=>{const t=aisTrack(sources,m,mode);return t===v?'vdr':t===g?'gatehouse':t;};
console.log(JSON.stringify(Object.fromEntries(['1','2','3','4'].map(m=>[m,['vdr-first','vdr','gatehouse'].map(mode=>pick(m,mode))]))));
"""
    proc = subprocess.run([NODE, "-e", code], check=True, capture_output=True, text=True, encoding="utf-8")
    # VDR shares the recording's clock, so it wins by default; the two are never merged.
    assert json.loads(proc.stdout) == {
        "1": ["vdr", "vdr", "gatehouse"], "2": ["vdr", "vdr", None],
        "3": ["gatehouse", None, "gatehouse"], "4": [None, None, None]}
    main = source[source.index("async function startMain("):]
    assert ".concat(inWin)" not in main and "aisTrack(aisSources" in main
    assert "ais_source: $('aisSource').value" in main
