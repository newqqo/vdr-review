"""Chunked enhanced-audio provenance and byte-level replay; synthetic PCM only."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "vdr_review" / "src"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")


def run_export(duration, rate=8000, enhanced=True, silent=False):
    app = (SRC / "app.js").read_text(encoding="utf-8")
    start = app.index("  let exportBusy = false;")
    end = app.index("  async function bundleEntries(ch)", start)
    script = r"""
const crypto=require('node:crypto');
const DSP=require(SRC+'/dsp.js'), VDRMedia=require(SRC+'/media.js');
const sourceStart=Date.UTC(2026,0,1,23,59,30,125),rate=RATE,duration=DURATION;
const total=Math.round(rate*duration), source=Int16Array.from({length:total},(_,i)=>
 SILENT?0:Math.round((i<60*rate?1000:16000)*Math.sin(2*Math.PI*997*i/rate)+
 100*Math.sin(2*Math.PI*50*i/rate)));
const sha=b=>crypto.createHash('sha256').update(Buffer.from(b)).digest('hex');
const originalHash=sha(Buffer.from(source.buffer));
const sha256=async b=>sha(Buffer.from(await b.arrayBuffer()));
const buttons={}, $=id=>buttons[id]||(buttons[id]={disabled:false});
let pauses=0,destroyed=0,read=[];
const au={pause(){pauses++}},text=()=>{},decodeChannel=()=>{throw Error('Unexpected decoder')};
const uiError=key=>new Error(key),NAME='SYNTHETIC',BUILD_INFO='synthetic-test',TZ=0,OFS=0,OFS_BASIS='';
const S={identity:'a'.repeat(64),cls:{audio:{A1:[]}},cover:[sourceStart,sourceStart+duration*1000]};
let exportHistory=[];const saveExtra=()=>{},fmtDT=t=>new Date(t).toISOString();
const manifest=()=>({recording_id:S.identity});
const VDRStream={createStore(){return {
 channels:{A1:{rate,sources:[],decoder:[],qa:{gaps:[],overlaps:[],bad:[],indexedOnly:false,pending_records:0}}},
 async readRange(ch,a,b){read.push([a,b]);return {pcm:source.slice(Math.round(a*rate),Math.round(b*rate))}},
 stats(){return {cacheBytes:0}},destroy(){destroyed++}
}}};
APP_EXPORT
// Independent replay reads the serialized recipe rather than recalculating gain
// or calling clipPCM. This catches lost segments and local-vs-output offsets.
function replay(processing){
 const output=new Int16Array(processing.total_samples);
 function biquad(x,high,f,q,state){
  const w=2*Math.PI*f/processing.sample_rate,cs=Math.cos(w),al=Math.sin(w)/(2*q);
  const b0=(high?1+cs:1-cs)/2,b1=high?-(1+cs):1-cs,a0=1+al,a1=-2*cs,a2=1-al;
  let {x1,x2,y1,y2}=state;
  for(let i=0;i<x.length;i++){
   const v=x[i],y=(b0*v+b1*x1+b0*x2-a1*y1-a2*y2)/a0;
   x2=x1;x1=v;y2=y1;y1=y;x[i]=y;
  }
 }
 for(const segment of processing.segments){
  const x=Float32Array.from(source.subarray(segment.output_sample_start,segment.output_sample_end),v=>v/32768);
  biquad(x,true,segment.highpass_hz,segment.filter_q,segment.filter_state_reset);
  biquad(x,true,segment.highpass_hz,segment.filter_q,segment.filter_state_reset);
  biquad(x,false,segment.lowpass_hz,segment.filter_q,segment.filter_state_reset);
  biquad(x,false,segment.lowpass_hz,segment.filter_q,segment.filter_state_reset);
  output.set(Int16Array.from(x,v=>Math.round(Math.max(-1,Math.min(1,v*segment.gain))*32767)),segment.output_sample_start);
 }
 return output;
}
(async()=>{
 try{
  const entries=await audioEntries('A1',sourceStart,sourceStart+duration*1000,ENHANCED);
  const actual=Buffer.from(await entries[0].data.arrayBuffer());
  const alignment=JSON.parse(entries[1].data),receipt=JSON.parse(entries[2].data);
  const oldParts=[Buffer.from(DSP.wavHeader(total,rate))];
  for(let i=0;i<source.length;i+=rate*60){
   const chunk=source.slice(i,Math.min(total,i+rate*60));
   const old=ENHANCED?VDRMedia.clipPCM(chunk,rate,0,chunk.length/rate,true).pcm:chunk;
   oldParts.push(Buffer.from(old.buffer,old.byteOffset,old.byteLength));
  }
  const old=Buffer.concat(oldParts),p=receipt.processing;
  const replayed=ENHANCED?replay(p):source;
  const replayWav=Buffer.concat([Buffer.from(DSP.wavHeader(total,rate)),Buffer.from(replayed.buffer)]);
  console.log(JSON.stringify({alignment,receipt,oldSha256:sha(old),actualSha256:sha(actual),
   replaySha256:sha(replayWav),bytes:actual.length,wavSamples:actual.readUInt32LE(40)/2,
   originalUnchanged:originalHash===sha(Buffer.from(source.buffer)),read,pauses,destroyed,
   historyProcessing:exportHistory[0].processing,buttons: Object.values(buttons).map(b=>b.disabled)}));
 }catch(error){console.log(JSON.stringify({error:error.message,pauses,destroyed,read,
  originalUnchanged:originalHash===sha(Buffer.from(source.buffer))}));}
})();
"""
    script = (script.replace("SRC", json.dumps(SRC.as_posix()))
              .replace("DURATION", str(duration)).replace("RATE", str(rate))
              .replace("ENHANCED", "true" if enhanced else "false")
              .replace("SILENT", "true" if silent else "false")
              .replace("APP_EXPORT", app[start:end]))
    proc = subprocess.run([NODE, "-e", script], check=True, capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    return json.loads(proc.stdout)


@pytest.mark.parametrize("rate", [8000, 14000, 16000])
@pytest.mark.parametrize("duration", [90, 120])
def test_unequal_level_enhanced_export_records_all_segments_and_replays_exact_wav(duration, rate):
    result = run_export(duration, rate)
    assert "error" not in result, result
    processing = result["receipt"]["processing"]
    assert processing == result["alignment"]["processing"] == result["historyProcessing"]
    assert processing["algorithm_version"] == "vdr-enhanced-listening-v1"
    assert processing["sample_rate"] == rate and processing["total_samples"] == rate * duration
    assert processing["segment_max_samples"] == rate * 60
    first, last = processing["segments"]
    assert (first["output_sample_start"], first["output_sample_end"]) == (0, rate * 60)
    assert (last["output_sample_start"], last["output_sample_end"]) == (rate * 60, rate * duration)
    assert (first["output_start_s"], first["output_end_s"]) == (0, 60)
    assert (last["output_start_s"], last["output_end_s"]) == (60, duration)
    assert first["source_start_utc"] == "2026-01-01T23:59:30.125Z"
    assert first["source_end_utc"] == last["source_start_utc"] == "2026-01-02T00:00:30.125Z"
    assert last["source_end_utc"] == {90: "2026-01-02T00:01:00.125Z", 120: "2026-01-02T00:01:30.125Z"}[duration]
    assert first["gain"] == 20 and 0 < last["gain"] < first["gain"]
    assert "gain" not in processing  # No misleading last-segment scalar at the top.
    for segment in processing["segments"]:
        assert segment["input_sample_start"] == 0
        assert segment["input_sample_end"] == segment["output_sample_end"] - segment["output_sample_start"]
        assert segment["filter_state_reset"] == {
            "applies_to": "each of the four biquad stages", "x1": 0, "x2": 0, "y1": 0, "y2": 0}
        assert segment["highpass_hz"] == 300 and segment["lowpass_hz"] == 3400
    assert result["actualSha256"] == result["oldSha256"] == result["replaySha256"]
    assert result["actualSha256"] == result["receipt"]["output_wav_sha256"] == result["alignment"]["audio_sha256"]
    assert result["wavSamples"] == rate * duration and result["bytes"] == 44 + rate * duration * 2
    assert result["originalUnchanged"] and result["pauses"] == result["destroyed"] == 1
    assert result["read"] == [[0, 60], [60, duration]]
    assert result["buttons"] and not any(result["buttons"])


@pytest.mark.parametrize("duration", [0.25, 60])
def test_one_segment_boundaries_are_complete_without_changing_audio(duration):
    result = run_export(duration)
    processing = result["receipt"]["processing"]
    assert len(processing["segments"]) == 1
    assert processing["segments"][0]["output_sample_start"] == 0
    assert processing["segments"][0]["output_sample_end"] == processing["total_samples"] == round(8000 * duration)
    assert result["actualSha256"] == result["oldSha256"] == result["replaySha256"]


def test_silent_two_segment_enhancement_preserves_gain_one_and_exact_bytes():
    result = run_export(90, silent=True)
    assert [segment["gain"] for segment in result["receipt"]["processing"]["segments"]] == [1, 1]
    assert result["actualSha256"] == result["oldSha256"] == result["replaySha256"]
    assert result["originalUnchanged"]


def test_decoded_original_export_keeps_legacy_processing_shape_and_samples():
    result = run_export(90, enhanced=False)
    assert result["receipt"]["processing"] == result["alignment"]["processing"] == {"kind": "decoded_original", "gain": 1}
    assert result["actualSha256"] == result["oldSha256"] == result["replaySha256"]
    assert result["originalUnchanged"]


def test_enhancement_limit_rejects_before_reading_or_mutating_audio():
    result = run_export(120.001)
    assert result == {"error": "exportEnhancedLimit", "pauses": 0, "destroyed": 0,
                      "read": [], "originalUnchanged": True}


def test_processing_metadata_rejects_incomplete_or_discontinuous_segments_and_detaches_recipe():
    script = "const M=require(" + json.dumps((SRC / "media.js").as_posix()) + r""");
const rate=8000,start=Date.UTC(2026,0,1),p=M.clipPCM(new Int16Array(80),rate,0,.01,true).processing;
const segment={start:0,end:80,processing:p};
const receipt=M.enhancedProcessing(rate,start,80,[segment]);
p.gain=17;segment.end=40;
const invalid=[
 ()=>M.enhancedProcessing(0,start,80,[]),
 ()=>M.enhancedProcessing(rate,NaN,80,[]),
 ()=>M.enhancedProcessing(rate,start,80,[]),
 ()=>M.enhancedProcessing(rate,start,80,[{start:1,end:80,processing:p}]),
 ()=>M.enhancedProcessing(rate,start,80,[{start:0,end:40,processing:{...p,sample_end:40}}]),
 ()=>M.enhancedProcessing(rate,start,80,[{start:0,end:81,processing:{...p,sample_end:81}}]),
 ()=>M.enhancedProcessing(rate,start,80,[{start:0,end:80,processing:{...p,gain:Infinity}}]),
 ()=>M.enhancedProcessing(rate,start,80,[{start:0,end:80,processing:{...p,kind:'decoded_original'}}]),
 ()=>M.enhancedProcessing(rate,start,rate*61,[{start:0,end:rate*61,processing:{...p,sample_end:rate*61}}])
].map(fn=>{try{fn();return false}catch(e){return e instanceof TypeError}});
console.log(JSON.stringify({invalid,detached:receipt.segments[0].gain===1&&receipt.segments[0].output_sample_end===80}));
"""
    proc = subprocess.run([NODE, "-e", script], check=True, capture_output=True,
                          text=True, encoding="utf-8", timeout=30)
    result = json.loads(proc.stdout)
    assert all(result["invalid"]) and result["detached"]
