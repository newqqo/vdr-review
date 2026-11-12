"""Synthetic format guards only; these tests do not certify JRC model support."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "vdr_review" / "src").as_posix()
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")

HELPERS = """
function adts(rate=16000, channels=1, profile=2) {
  const index={16000:8,8000:11,12000:9}[rate];
  return Uint8Array.from([0xff,0xf1,((profile-1)<<6)|(index<<2)|(channels>>2),
    (channels&3)<<6,1,0x1f,0xfc,0]);
}
function aac({channel='A1',stamp='2026102309123456',payload=adts()}={}) {
  const b=new Uint8Array(80+payload.length), view=new DataView(b.buffer);
  b.set(Buffer.from('VoyageDtRec '+stamp));
  b.set([0xff,0xff,channel[0]==='A'?1:2,0],44);
  view.setUint32(48,payload.length,false); b.set(Buffer.from(stamp),52);
  b.set(Buffer.from(channel),68); b.set(payload,72); b.set([0,0,13,10,0,0,13,10],b.length-8);
  return b;
}
function g726(length=315000) {
  const b=new Uint8Array(length+34); b.set(Buffer.from('VoyageDtRec ~'));
  new DataView(b.buffer).setUint32(26,length+2,false); b.set([13,10],30); b.set([13,10],b.length-2);
  return b;
}
function entry(path,bytes) { return {path,size:bytes.length,file:{size:bytes.length,arrayBuffer:async()=>bytes.slice().buffer}}; }
function failure(fn) { try { fn(); return null; } catch(e) { return e.message; } }
async function failureAsync(fn) { try { await fn(); return null; } catch(e) { return e.message; } }
"""


def run_node(code):
    result = subprocess.run(
        [NODE, "-e", f"const V=require('{SRC}/vdr.js');\n{HELPERS}\n{code}"],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    return json.loads(result.stdout)


def test_aac_container_uses_record_timestamp_and_keeps_payload():
    result = run_node("""
const source=aac(), r=V.readAudioRecord(source,'A1');
console.log(JSON.stringify({codec:r.codec,channel:r.channel,rate:r.rate,frames:r.frames,
  samples:r.samples,duration:r.duration,start:r.start,stamp:r.recordedTimestamp,
  payload:[...r.payload],unmodified:source.length===88}));
""")
    assert result == {
        "codec": "aac", "channel": "A1", "rate": 16000, "frames": 1,
        "samples": 1024, "duration": .064, "start": 1792746754560,
        "stamp": "2026102309123456", "payload": [255, 241, 96, 64, 1, 31, 252, 0],
        "unmodified": True,
    }


def test_aac_vhf_rate_and_multiple_frame_duration():
    result = run_node("""
const p=new Uint8Array([...adts(8000),...adts(8000)]),r=V.readAudioRecord(aac({channel:'C2',payload:p}),'C2');
console.log(JSON.stringify({rate:r.rate,frames:r.frames,samples:r.samples,duration:r.duration}));
""")
    assert result == {"rate": 8000, "frames": 2, "samples": 2048, "duration": .256}


@pytest.mark.parametrize("mutation", [
    "b=b.slice(0,-1)",
    "b[48]=1",
    "b[b.length-1]=0",
    "b[27]=49",  # container timestamps disagree
    "b[69]=50",  # recorded A2 disagrees with expected A1
    "b[46]=2",  # VHF record type with microphone channel
    "b[70]=1",
    "b[74]=(b[74]&0xc3)|(9<<2)",  # unobserved MIC 12 kHz
    "b[75]=0x80",  # two channels rather than mono
    "b[77]=0xff",  # impossible ADTS length
    "b[78]|=1",  # multiple raw data blocks
    "b[74]&=0x3f",  # unvalidated AAC Main profile, not LC
    "b[72]=0xfe",
])
def test_aac_rejects_truncation_inconsistent_metadata_and_unknown_layout(mutation):
    result = run_node(f"""
let b=aac(); {mutation};
console.log(JSON.stringify({{error:failure(()=>V.readAudioRecord(b,'A1'))}}));
""")
    assert result["error"]


@pytest.mark.parametrize("stamp", [
    "2026023009123456", "2026102324123456", "2026102309603456", "2026102309126056",
])
def test_aac_dates_are_rejected_instead_of_normalized(stamp):
    result = run_node(f"console.log(JSON.stringify({{error:failure(()=>V.readAudioRecord(aac({{stamp:'{stamp}'}}),'A1'))}}));")
    assert "日期或時間無效" in result["error"]


def test_g726_strict_channel_rate_and_minute_length_not_length_voting():
    result = run_node("""
const mic=V.readAudioRecord(g726(),'A1'),vhf=V.readAudioRecord(g726(180000),'C1');
console.log(JSON.stringify({mic:[mic.codec,mic.rate,mic.duration,mic.samples],vhf:[vhf.rate,vhf.duration],
  errors:[()=>V.readAudioRecord(g726(360000),'A1'),()=>V.readAudioRecord(g726(180000),'A1'),
    ()=>V.readAudioRecord(g726(),'C1'),()=>V.readAudioRecord(g726(314999),'A1'),
    ()=>V.readAudioRecord(g726(),'A0'),()=>V.readAudioRecord(g726()),
    ()=>V.readAudioRecord(new Uint8Array(20),'A1')].map(f=>failure(f))}));
""")
    assert result["mic"] == ["g726", 14000, 60, 840000]
    assert result["vhf"] == [8000, 60]
    assert all(result["errors"])


def test_g726_payload_is_not_mistaken_for_aac_marker():
    result = run_node("""
const b=g726();b[44]=255;b[45]=255;
console.log(JSON.stringify({codec:V.readAudioRecord(b,'A1').codec}));
""")
    assert result == {"codec": "g726"}


def test_async_index_supports_both_layouts_without_using_aac_folder_time():
    result = run_node("""
(async()=>{
  const modern=await V.indexEntries([entry('Ship/Internal_Storage/audio/202610221200/A1_record.dat',aac())]);
  const old=await V.indexEntries([entry('Ship/Audio/202610230900/A1123456.dat',g726()),
    {path:'Ship/Log/202610230900/L0120000.log'}, {path:'Ship/readme.txt'}]);
  const a=modern.audio.A1[0], b=old.audio.A1[0];
  console.log(JSON.stringify({modern:[a.start,a.codec,a.rate,a.duration,a.frames],
    old:[b.start,b.codec,b.rate,b.duration],logs:old.logs.length,skipped:old.skipped}));
})().catch(e=>{console.error(e);process.exitCode=1});
""")
    assert result["modern"] == [1792746754560, "aac", 16000, .064, 1]
    assert result["old"] == [1792746754560, "g726", 14000, 60]
    assert result["logs"] == 1 and result["skipped"] == ["Ship/readme.txt"]


def test_async_index_rejects_mixed_roots_bad_lengths_and_mislabelled_containers():
    result = run_node("""
(async()=>{
  const mixed=await failureAsync(()=>V.indexEntries([
    entry('Ship1/Audio/202610230900/A1123456.dat',g726()),
    entry('Ship2/Internal_Storage/audio/202610230900/A1_record.dat',aac())]));
  const size=await failureAsync(()=>V.indexEntries([entry('Ship/Audio/202610230900/A1123456.dat',g726(360000))]));
  const mismatch=await failureAsync(()=>V.indexEntries([entry('Ship/Internal_Storage/audio/202610230900/A2_record.dat',aac())]));
  const layout=await failureAsync(()=>V.indexEntries([entry('Ship/Internal_Storage/audio/202610230900/A1_record.dat',g726())]));
  console.log(JSON.stringify({mixed,size,mismatch,layout}));
})().catch(e=>{console.error(e);process.exitCode=1});
""")
    assert "多份 VDR" in result["mixed"]
    assert "60 秒" in result["size"]
    assert "聲道" in result["mismatch"]
    assert "容器不符" in result["layout"]


@pytest.mark.parametrize("path", [
    "S/Audio/202602300900/A1123456.dat", "S/Audio/202610232400/A1123456.dat",
    "S/Audio/202610230960/A1123456.dat", "S/Audio/202610230900/A1603456.dat",
    "S/Audio/202610230900/A1126056.dat", "S/Log/202602300900/L0120000.log",
])
def test_legacy_path_dates_are_rejected_instead_of_normalized(path):
    result = run_node(f"console.log(JSON.stringify({{error:failure(()=>V.classify([{{path:'{path}'}}]))}}));")
    assert result["error"]


def test_navigation_drops_invalid_status_ranges_and_nonfinite_values():
    result = run_node("""
const sentences=[
 '$GPGGA,091234,2230.0,N,12015.0,E,0,08,1,0,M,0,M,,',
 '$GNGNS,091234,2230.0,N,12015.0,E,NN,08,1,0,0,,',
 '$GPRMC,091234,V,2230.0,N,12015.0,E,5,40,231026,,,A',
 '$GPRMC,091234,A,2230.0,N,12015.0,E,5,40,231026,,,N',
 '$GPVTG,40,T,,M,5,N,,K,N', '$VRTHS,60,V', '$VRHDT,360,T',
 '$VRHDT,Infinity,T', '$VRHDT,NaN,T', '$VRVBW,4,0,V,0,0,V',
 '$GPGGA,091234,2261.0,N,12015.0,E,1,08,1,0,M,0,M,,',
 '$GPGGA,091234,2230.0,Q,12015.0,E,1,08,1,0,M,0,M,,',
 '$GPGGA,091234,9130.0,N,12015.0,E,1,08,1,0,M,0,M,,',
 '$GPRMC,091234,A,2230.0,N,18115.0,E,-1,361,231026,,,A',
 '$GPZDA,091234,30,02,2026,00,00', '$VRHDT,60,T*ZZ'];
const bad=V.navSeries(sentences.map((s,i)=>({t:i*1000,s})));
const good=V.navSeries([
 {t:0,s:'$GPGGA,091234,2230.0,N,12015.0,E,1,08,1,0,M,0,M,,'},
 {t:0,s:'$VRTHS,60,A'}, {t:0,s:'$GPVTG,40,T,,M,5,N,,K,A'},
 {t:0,s:'$VRVBW,-2,0,A,0,0,V'}, {t:Infinity,s:'$VRHDT,80,T'}]);
console.log(JSON.stringify({bad,good}));
""")
    assert result["bad"] == {"series": [], "zdaOffsets": []}
    assert result["good"] == {"series": [{"utc": 0, "lat": 22.5, "lon": 120.25,
                                            "hdg": 60, "cog": 40, "sog": 5, "stw": -2}],
                               "zdaOffsets": []}


def test_ais_unavailable_speed_course_heading_stay_null_not_normal_values():
    result = run_node("""
let b='0'.repeat(168);
function set(at,n,v) { b=b.slice(0,at)+v.toString(2).padStart(n,'0')+b.slice(at+n); }
set(0,6,1);set(8,30,999000001);set(50,10,1023);set(61,28,Math.round(120.25*600000));
set(89,27,Math.round(22.5*600000));set(116,12,3600);set(128,9,511);
let payload='';for(let i=0;i<b.length;i+=6){let n=parseInt(b.slice(i,i+6),2);payload+=String.fromCharCode(n+48+(n>39?8:0));}
const r=V.decodeAis([{t:0,s:'!AIVDM,1,1,,A,'+payload+',0'},
  {t:Infinity,s:'!AIVDM,1,1,,A,'+payload+',0'}, {t:0,s:'!AIVDM,1,1,,A,INVALID~,0'}]);
console.log(JSON.stringify(r));
""")
    assert result["positions"] == {"999000001": [[0, 22.5, 120.25, None, None, None, False]]}


STAMPED_LOG = """
// Layout observed on L_ log files: a 44-byte container header, then records of
// FF FF, 01 00, 4-byte sentence length, 16-digit UTC time, 2-byte port, 6 unused bytes,
// the sentence with CRLF, and 00 00 CR LF; the file ends with one more 00 00 CR LF.
function stampedLog(lines,{stamp='2026102309000000',port=3}={}) {
  const parts=[],text=s=>Buffer.from(s,'latin1');
  lines.forEach((line,i)=>{
    const body=text(line+'\\r\\n'),head=new Uint8Array(32),view=new DataView(head.buffer);
    head.set([0xff,0xff,1,0]);view.setUint32(4,body.length,false);
    head.set(text(stamp.slice(0,14)+String(i+1).padStart(2,'0')),8);view.setUint16(24,port,false);
    parts.push(head,body,Uint8Array.from([0,0,13,10]));
  });
  const records=Buffer.concat(parts),b=new Uint8Array(48+records.length),view=new DataView(b.buffer);
  b.set(text('VoyageDtRec '+stamp));b.set([1,0],28);view.setUint32(32,records.length,false);b.set([13,10],42);
  b.set(records,44);b.set([0,0,13,10],b.length-4);
  return b;
}
function key(fn) { try { fn(); return null; } catch(e) { return e.i18nKey || e.message; } }
"""


def test_stamped_log_records_keep_their_own_utc_time_port_and_sentence():
    result = run_node(STAMPED_LOG + """
let bits='0'.repeat(168);
function set(at,n,v) { bits=bits.slice(0,at)+v.toString(2).padStart(n,'0')+bits.slice(at+n); }
set(0,6,1);set(8,30,999000001);set(50,10,55);set(61,28,Math.round(120.26*600000));set(89,27,Math.round(22.51*600000));
set(116,12,730);set(128,9,73);
let payload='';for(let i=0;i<bits.length;i+=6){const n=parseInt(bits.slice(i,i+6),2);payload+=String.fromCharCode(n+48+(n>39?8:0));}
const log=stampedLog(['$GPGGA,090000.00,2230.0000,N,12015.0000,E,1,08,1.0,5.0,M,,M,,','$HEHDT,84.2,T','!AIVDM,1,1,,A,'+payload+',0']);
const recs=V.parseLog(log,0,{format:'stamped',path:'L_000000.log'});
console.log(JSON.stringify({recs,nav:V.navSeries(recs).series,ais:V.decodeAis(recs).positions}));
""")
    assert [(r["t"], r["port"]) for r in result["recs"]] == [
        (1792746000010, 3), (1792746000020, 3), (1792746000030, 3)]
    assert result["recs"][1]["s"] == "$HEHDT,84.2,T"
    assert result["nav"] == [{"utc": 1792746000, "lat": 22.5, "lon": 120.25, "hdg": 84.2}]
    assert result["ais"] == {"999000001": [[1792746000030, 22.51, 120.26, 5.5, 73, 73, False]]}


def test_index_reads_stamped_logs_from_the_internal_storage_layout():
    result = run_node(STAMPED_LOG + """
(async()=>{
  const modern=await V.indexEntries([entry('Ship/Internal_Storage/audio/202610230900/A1_record.dat',aac()),
    entry('Ship/Internal_Storage/log/202610230900/L_120000.log',stampedLog(['$HEHDT,1,T']))]);
  const old=V.classify([{path:'Ship/Log/202610230900/L0120000.log'}]);
  console.log(JSON.stringify({modern:modern.logs.map(l=>[l.start,l.format]),unsupported:modern.unsupportedLogs,
    old:old.logs.map(l=>[l.start,l.format])}));
})().catch(e=>{console.error(e);process.exitCode=1});
""")
    assert result["modern"] == [[1792746720000, "stamped"]] and result["unsupported"] == []
    assert result["old"] == [[1792746720000, "offset"]]


def test_stamped_log_stops_on_framing_it_does_not_recognize_instead_of_guessing():
    result = run_node(STAMPED_LOG + """
const good=stampedLog(['$HEHDT,1,T','$HEHDT,2,T']),cut=(b,f)=>{const c=b.slice();f(c);return c;};
const cases={
  ok:good,
  magic:cut(good,b=>b[0]=0x57),
  total:cut(good,b=>new DataView(b.buffer).setUint32(32,1,false)),
  marker:cut(good,b=>b[44]=0),
  length:cut(good,b=>new DataView(b.buffer).setUint32(48,3,false)),
  stamp:cut(good,b=>b.set(Buffer.from('2026023009000001'),52)),
  suffix:cut(good,b=>b[44+32+12]=1),
  trailer:cut(good,b=>b[b.length-1]=0),
  truncated:good.slice(0,good.length-10),
};
const out={};for(const [k,b] of Object.entries(cases))out[k]=key(()=>V.parseLog(b,0,{format:'stamped',path:'L_000000.log'}).length);
console.log(JSON.stringify(out));
""")
    assert result.pop("ok") is None
    assert set(result.values()) == {"core.vdr.logFormat"}, result


def test_index_reports_progress_while_reading_aac_records():
    # Reading thousands of records from a disc takes minutes; the page needs a count to show.
    result = run_node("""
(async()=>{
  const seen=[];
  const entries=[0,1,2].map(i=>entry('Ship/Internal_Storage/audio/202610230900/A1_'+i+'.dat',aac({stamp:'20261023090'+i+'00000'})));
  await V.indexEntries(entries,(done,total)=>seen.push([done,total]));
  console.log(JSON.stringify(seen));
})().catch(e=>{console.error(e);process.exitCode=1});
""")
    assert result == [[1, 3], [2, 3], [3, 3]]
