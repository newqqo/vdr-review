"""Bounded long-form VDR decoding and global-time playback without a browser."""
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
global.crypto=require('node:crypto').webcrypto;
global.VDR=require('{SRC}/vdr.js');
global.DSP=require('{SRC}/dsp.js');
const S=require('{SRC}/stream.js');
const crypto=require('node:crypto');
const epoch=Date.UTC(2026,0,5,20);
function legacy(value=1,rate=8000){{
 const b=Buffer.alloc(rate*60*3/8+34,value);
 b.write('VoyageDtRec ',0,'ascii'); b.writeUInt32BE(b.length-32,26);
 b[30]=13;b[31]=10;b[b.length-2]=13;b[b.length-1]=10;
 return b;
}}
function record(n,start=n*60,value=n+1,channel='C1'){{
 const rate=channel[0]==='A'?14000:8000,b=legacy(value,rate),bytes=Uint8Array.from(b);
 return {{start:epoch+start*1000,path:'root/Audio/202601052000/'+channel+n+'.dat',
 duration:60,rate,codec:'g726',size:b.length,
 sha256:crypto.createHash('sha256').update(b).digest('hex'),
 file:{{size:b.length,arrayBuffer:async()=>bytes.slice().buffer}}}};
}}
const decoder=async(ch,job)=>({{pcm:new Int16Array(job.total).fill(new Uint8Array(job.parts[0].payload)[0]).buffer}});
function store(files,duration,options={{}}){{return S.createStore({{audio:{{C1:files}},W0:epoch,W1:epoch+duration*1000,decodeG726:decoder,...options}})}}
async function failure(fn){{try{{await fn();return null;}}catch(e){{return e.message;}}}}
class FakeAudio extends EventTarget{{
 constructor(){{super();this.currentTime=0;this.playbackRate=1;this.volume=1;this.muted=false;this.readyState=0;this.paused=true;this.src='';this.plays=0;}}
 load(){{this.currentTime=0;if(this.src)queueMicrotask(()=>{{this.readyState=1;this.dispatchEvent(new Event('loadedmetadata'));}})}}
 play(){{this.paused=false;this.plays++;this.dispatchEvent(new Event('playing'));return Promise.resolve()}}
 pause(){{this.paused=true}}
 removeAttribute(name){{if(name==='src')this.src=''}}
 fire(type){{this.dispatchEvent(new Event(type))}}
}}
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
(async()=>{{
{code}
}})().catch(e=>{{console.error(e);process.exitCode=1}});
"""
    result = subprocess.run([NODE, "-e", script], check=True, capture_output=True,
                            text=True, encoding="utf-8", timeout=90)
    return json.loads(result.stdout)


def test_four_hour_index_holds_no_whole_channel_pcm_and_random_reads_are_bounded():
    result = run_node("""
const files=Array.from({length:240},(_,n)=>record(n));
const st=store(files,4*3600,{maxCacheBytes:2*8000*60*2});
const initial=st.stats(), lengths=[];
for(const at of [0,3600,7200,4*3600-60]){const r=await st.readRange('C1',at,at+60);lengths.push(r.pcm.length)}
const over=await failure(()=>st.readRange('C1',0,4*3600));
console.log(JSON.stringify({initial,after:st.stats(),lengths,over,
 wholePCM:!!st.channels.C1.pcm,sources:st.channels.C1.sources.length,
 env:st.channels.C1.env.length,qa:st.channels.C1.qa}));
""")
    assert result["initial"]["cacheBytes"] == 0
    assert result["initial"]["decodedRecords"] == 0
    assert result["after"]["decodedRecords"] == 4
    assert result["after"]["peakCacheBytes"] <= 2 * 8000 * 60 * 2
    assert result["after"]["cacheRecords"] == 2
    assert result["after"]["maxReadSeconds"] == 60
    assert result["lengths"] == [480000] * 4
    assert result["sources"] == 240 and not result["wholePCM"]
    assert result["env"] == 4 * 3600 * 20
    assert result["qa"]["indexedOnly"]
    assert result["qa"]["pending_records"] == 236
    assert "120" in result["over"]


def test_overlap_precedence_and_real_gap_samples_are_exact():
    result = run_node("""
const st=store([record(0,0,11),record(1,30,22)],120);
const r=await st.readRange('C1',0,120), x=r.pcm;
console.log(JSON.stringify({samples:[x[0],x[30*8000-1],x[30*8000],x[90*8000-1],x[90*8000],x[x.length-1]],
 range:r.qa,qa:st.channels.C1.qa,decoded:st.channels.C1.decoder,
 verified:st.channels.C1.sources.map(s=>s.verified)}));
""")
    assert result["samples"] == [11, 11, 22, 22, 0, 0]
    assert result["range"]["gaps"] == [[90, 120]]
    assert result["range"]["overlaps"] == [[30, 60]]
    assert result["qa"]["gaps"] == [[90, 120]]
    assert not result["qa"]["indexedOnly"]
    assert [r["offset"] for r in result["decoded"]] == [0, 240000]
    assert all(result["verified"])


def test_sample_aligned_range_edges_and_cached_reads_preserve_pcm():
    result = run_node("""
const st=store([record(0,0,17)],60);
const a=await st.readRange('C1',.0002,.0009);
a.pcm[0]=999;
const b=await st.readRange('C1',.0002,.0009);
console.log(JSON.stringify({a:{start:a.start,end:a.end,n:a.pcm.length},b:[...b.pcm],stats:st.stats()}));
""")
    assert result["a"] == {"start": 2 / 8000, "end": 7 / 8000, "n": 5}
    assert result["b"] == [17] * 5
    assert result["stats"]["decodedRecords"] == 1
    assert result["stats"]["cacheHits"] == 1


def test_waveform_overlap_precedence_does_not_depend_on_seek_decode_order():
    result = run_node("""
global.DSP={...global.DSP,envelope:(pcm,rate)=>new Uint8Array(Math.floor(pcm.length/rate*20)).fill(pcm[0])};
const st=store([record(0,0,11),record(1,30,22)],90,{maxCacheBytes:0});
await st.readRange('C1',60,70);await st.readRange('C1',0,10);
console.log(JSON.stringify({bins:[st.channels.C1.env[0],st.channels.C1.env[599],st.channels.C1.env[600],st.channels.C1.env[1200]],
 known:[st.channels.C1.envKnown[0],st.channels.C1.envKnown[600]],stats:st.stats()}));
""")
    assert result["bins"] == [11, 11, 22, 22]
    assert result["known"] == [1, 1]
    assert result["stats"]["cacheBytes"] == result["stats"]["peakCacheBytes"] == 0


def test_sha_mismatch_is_fatal_and_never_relabelled_as_missing_audio():
    result = run_node("""
const f=record(0);f.sha256='0'.repeat(64);
const st=store([f],60);
const error=await failure(()=>st.readRange('C1',0,10));
console.log(JSON.stringify({error,stats:st.stats(),bad:st.channels.C1.qa.bad}));
""")
    assert "SHA-256" in result["error"]
    assert result["stats"]["decodedRecords"] == 0
    assert result["bad"] == []


def test_bad_record_is_reported_as_rejected_with_a_zero_filled_gap():
    result = run_node("""
const f=record(0), bytes=legacy(1);bytes[0]=0;
f.sha256=crypto.createHash('sha256').update(bytes).digest('hex');
f.file.arrayBuffer=async()=>Uint8Array.from(bytes).buffer;
const st=store([f],60),r=await st.readRange('C1',0,10);
console.log(JSON.stringify({silent:r.pcm.every(v=>v===0),qa:st.channels.C1.qa,range:r.qa}));
""")
    assert result["silent"]
    assert result["qa"]["gaps"] == [[0, 60]]
    assert result["range"]["gaps"] == [[0, 10]]
    assert len(result["qa"]["bad"]) == 1
    assert not result["qa"]["indexedOnly"]


def test_aac_decoder_delay_padding_is_not_reported_as_recorded_sound():
    result = run_node("""
const f=record(0);f.codec='aac';f.duration=4;
global.VDR={readAudioRecord:()=>({payload:new Uint8Array(10),codec:'aac',rate:8000,
 samples:32000,duration:4,start:epoch})};
global.VDRMedia={decodeAAC:async()=>({pcm:new Int16Array(32000).fill(4).buffer,
 decoded:[{samples:30976,declared_samples:32000,decoder_samples:30976}]})};
const st=store([f],4),r=await st.readRange('C1',0,4);
console.log(JSON.stringify({edge:[r.pcm[30975],r.pcm[30976],r.pcm[31999]],
 gaps:st.channels.C1.qa.gaps,receipt:st.channels.C1.decoder[0],range:r.qa}));
""")
    assert result["edge"] == [4, 0, 0]
    assert result["gaps"] == [[30976 / 8000, 4]]
    assert result["range"]["gaps"] == result["gaps"]
    assert result["receipt"]["samples"] == 30976
    assert result["receipt"]["declared_samples"] == 32000


def test_ranges_serialize_decoders_and_cancel_queued_stale_seeks():
    result = run_node("""
let active=0,peak=0,calls=0;
const slow=async(ch,job)=>{active++;peak=Math.max(peak,active);calls++;await new Promise(r=>setTimeout(r,10));active--;return decoder(ch,job)};
const st=store([record(0),record(1),record(2)],180,{decodeG726:slow});
const controller=new AbortController();
const a=st.readRange('C1',0,60);
const b=st.readRange('C1',60,120,{signal:controller.signal}).catch(e=>e.name);
const c=st.readRange('C1',120,180);
controller.abort();
await a;const canceled=await b;await c;
console.log(JSON.stringify({peak,calls,canceled,stats:st.stats()}));
""")
    assert result["peak"] == result["stats"]["peakActiveDecoders"] == 1
    assert result["calls"] == 2
    assert result["canceled"] == "AbortError"


def test_store_destroy_clears_cache_and_prevents_future_reads():
    result = run_node("""
const st=store([record(0)],60);await st.readRange('C1',0,10);st.destroy();
console.log(JSON.stringify({stats:st.stats(),error:await failure(()=>st.readRange('C1',0,10))}));
""")
    assert result["stats"]["cacheBytes"] == 0
    assert result["error"]


def test_player_starts_paused_seeks_and_switches_channels_on_global_timeline():
    result = run_node("""
const st=store([record(0),record(1),record(2)],180);
// A second channel's metadata/readRange maps to the same synthetic recording.
st.channels.C2=st.channels.C1;
const read=st.readRange;st.readRange=(ch,...args)=>read('C1',...args);
const native=new FakeAudio(),p=S.createPlayer({element:native,store:st,channel:'C1'});
await p.ready;const initial={paused:p.paused,plays:native.plays,time:p.currentTime,duration:p.duration};
await p.prepare(75.5);const sought={time:p.currentTime,native:native.currentTime};
await p.setChannel('C2');const switched={time:p.currentTime,channel:p.channel,paused:p.paused};
await p.play();const playing=!p.paused;
native.currentTime=59.999;native.fire('timeupdate');native.fire('ended');
for(let i=0;i<10&&p.seeking;i++)await tick();
const boundary={time:p.currentTime,paused:p.paused,native:native.currentTime};
p.pause();p.destroy();
console.log(JSON.stringify({initial,sought,switched,playing,boundary}));
""")
    assert result["initial"] == {"paused": True, "plays": 0, "time": 0, "duration": 180}
    assert result["sought"] == {"time": 75.5, "native": 15.5}
    assert result["switched"] == {"time": 75.5, "channel": "C2", "paused": True}
    assert result["playing"]
    assert result["boundary"] == {"time": 120, "paused": False, "native": 0}


def test_player_rapid_seeks_only_apply_latest_result_and_pause_stays_paused():
    result = run_node("""
const st=store([record(0),record(1),record(2),record(3)],240,{decodeG726:async(ch,job)=>{await new Promise(r=>setTimeout(r,5));return decoder(ch,job)}});
const native=new FakeAudio(),p=S.createPlayer({element:native,store:st,channel:'C1'});
await p.ready;
const a=p.prepare(65),b=p.prepare(125),c=p.prepare(185.25);
await Promise.all([a,b,c]);
console.log(JSON.stringify({time:p.currentTime,native:native.currentTime,paused:p.paused,seeking:p.seeking,
 decoded:st.stats().decodedRecords,plays:native.plays}));p.destroy();
""")
    assert result["time"] == 185.25
    assert result["native"] == 5.25
    assert result["paused"] and not result["seeking"]
    assert result["plays"] == 0
    assert result["decoded"] == 2


def test_player_runtime_media_error_pauses_and_reports_without_losing_position():
    result = run_node("""
const st=store([record(0),record(1)],120),native=new FakeAudio(),messages=[];
const p=S.createPlayer({element:native,store:st,channel:'C1',status:m=>messages.push(m)});
await p.ready;await p.prepare(75.25);await p.play();
native.error={code:3};native.fire('error');
const failed={time:p.currentTime,paused:p.paused,seeking:p.seeking,error:p.error.message,message:messages.at(-1)};
native.error=null;await p.prepare(75.25);
const retry={time:p.currentTime,paused:p.paused,error:p.error};
console.log(JSON.stringify({failed,retry}));p.destroy();
""")
    assert result["failed"]["time"] == 75.25
    assert result["failed"]["paused"] and not result["failed"]["seeking"]
    assert "音訊錯誤 3" in result["failed"]["error"]
    assert result["failed"]["message"] == result["failed"]["error"]
    assert result["retry"] == {"time": 75.25, "paused": True, "error": None}


def test_six_visible_tracks_get_envelopes_without_allocating_a_range_pcm():
    result = run_node("""
const names=['A1','A2','A3','A4','C1','C2'],audio={};
for(const ch of names)audio[ch]=Array.from({length:5},(_,n)=>record(n,n*60,n+1,ch));
const RealInt16Array=global.Int16Array;let maxAllocation=0;
global.Int16Array=class extends RealInt16Array{constructor(value,...args){super(value,...args);if(typeof value==='number')maxAllocation=Math.max(maxAllocation,value)}};
const st=store([],300,{audio,maxCacheBytes:2*8000*60*2});
const summary=await st.ensureEnvelopes(names,30,90);
console.log(JSON.stringify({summary,stats:st.stats(),maxAllocation,tracks:Object.fromEntries(names.map(ch=>
 [ch,{known:st.channels[ch].envKnown.slice(0,2400).every(v=>v===1),laterUnknown:st.channels[ch].envKnown.slice(2400).every(v=>v===0),
  pending:st.channels[ch].qa.pending_records,ownsPCM:!!st.channels[ch].pcm}]))}));
""")
    assert result["summary"]["records"] == result["summary"]["decodedRecords"] == 12
    assert result["summary"]["reusedRecords"] == result["summary"]["rejectedRecords"] == 0
    assert result["maxAllocation"] == 14000 * 60
    assert result["stats"]["maxReadSeconds"] == 0
    assert result["stats"]["peakActiveDecoders"] == 1
    assert result["stats"]["peakCacheBytes"] <= result["stats"]["maxCacheBytes"]
    assert result["stats"]["envelopeDecodedRecords"] == 12
    for track in result["tracks"].values():
        assert track["known"] and track["laterUnknown"] and not track["ownsPCM"]
        assert track["pending"] == 3


def test_envelopes_survive_pcm_eviction_without_redecoding_or_rereading_sources():
    result = run_node("""
const files=[record(0),record(1)],st=store(files,120,{maxCacheBytes:0});
const first=await st.ensureEnvelopes('C1',0,120);
for(const f of files)f.file.arrayBuffer=async()=>{throw Error('waveform reread evicted audio')};
const second=await st.ensureEnvelopes(['C1','C1'],-10,130);
console.log(JSON.stringify({first,second,stats:st.stats(),known:st.channels.C1.envKnown.every(v=>v===1)}));
""")
    assert result["first"]["decodedRecords"] == 2
    assert result["second"]["channels"] == ["C1"]
    assert result["second"]["start"] == 0 and result["second"]["end"] == 120
    assert result["second"]["decodedRecords"] == 0
    assert result["second"]["reusedRecords"] == 2
    assert result["stats"]["decodedRecords"] == 2
    assert result["stats"]["cacheBytes"] == 0 and result["known"]


def test_envelope_jobs_yield_between_records_so_playback_can_enter_queue():
    result = run_node("""
const audio={C1:Array.from({length:4},(_,n)=>record(n)),C2:Array.from({length:4},(_,n)=>record(n,n*60,n+1,'C2'))};
const order=[];let playback=null,st;
st=store([],240,{audio,decodeG726:async(ch,job)=>{order.push(ch+':'+job.parts[0].name.split('/').at(-1));return decoder(ch,job)},
 onUpdate:()=>{if(!playback)playback=st.readRange('C1',180,181)}});
const waves=await st.ensureEnvelopes(null,0,120);await playback;
console.log(JSON.stringify({order,waves,stats:st.stats()}));
""")
    assert result["order"][:3] == ["C1:C10.dat", "C1:C13.dat", "C2:C20.dat"]
    assert result["waves"]["decodedRecords"] == 4
    assert result["stats"]["peakActiveDecoders"] == 1


def test_abort_stops_a_stale_multitrack_scan_after_its_current_record():
    result = run_node("""
const controller=new AbortController(),audio={C1:[record(0),record(1)],C2:[record(0,0,1,'C2'),record(1,60,2,'C2')]};
const st=store([],120,{audio,onUpdate:()=>controller.abort()});
let error;try{await st.ensureEnvelopes(null,0,120,{signal:controller.signal})}catch(e){error=e.name}
console.log(JSON.stringify({error,stats:st.stats(),counts:Object.fromEntries(Object.entries(st.channels).map(([ch,c])=>[ch,c.qa.pending_records]))}));
""")
    assert result["error"] == "AbortError"
    assert result["stats"]["decodedRecords"] == 1
    assert result["stats"]["envelopeDecodedRecords"] == 1
    assert result["stats"]["envelopeCancelledRequests"] == 1
    assert result["stats"]["activeEnvelopeRequests"] == 0
    assert result["counts"] == {"C1": 1, "C2": 2}


def test_a_queued_envelope_job_checks_abort_before_touching_its_source():
    result = run_node("""
const audio={C1:[record(0)],C2:[record(0,0,1,'C2')]};
let calls=0;const st=store([],60,{audio,decodeG726:async(ch,job)=>{calls++;await new Promise(r=>setTimeout(r,10));return decoder(ch,job)}});
const playback=st.readRange('C1',0,10),controller=new AbortController();
const scan=st.ensureEnvelopes('C2',0,60,{signal:controller.signal}).catch(e=>e.name);controller.abort();
await playback;console.log(JSON.stringify({error:await scan,calls,stats:st.stats(),pending:st.channels.C2.qa.pending_records}));
""")
    assert result["error"] == "AbortError" and result["calls"] == 1
    assert result["pending"] == 1
    assert result["stats"]["envelopeDecodedRecords"] == 0


def test_envelope_scan_keeps_sha_mismatches_fatal_and_reports_rejected_gaps():
    result = run_node("""
const changed=record(0);changed.sha256='0'.repeat(64);const st=store([changed],60);
const error=await failure(()=>st.ensureEnvelopes('C1',0,60));
const bad=record(0),bytes=legacy(1);bytes[0]=0;bad.sha256=crypto.createHash('sha256').update(bytes).digest('hex');
bad.file.arrayBuffer=async()=>Uint8Array.from(bytes).buffer;
const st2=store([bad],120),first=await st2.ensureEnvelopes('C1',0,120),second=await st2.ensureEnvelopes('C1',0,120);
console.log(JSON.stringify({error,hashBad:st.channels.C1.qa.bad,first,second,qa:st2.channels.C1.qa,
 known:st2.channels.C1.envKnown.some(v=>v===1),stats:st2.stats()}));
""")
    assert "SHA-256" in result["error"] and result["hashBad"] == []
    assert result["first"]["rejectedRecords"] == result["second"]["rejectedRecords"] == 1
    assert result["qa"]["gaps"] == [[0, 120]]
    assert len(result["qa"]["bad"]) == 1 and not result["known"]
    assert result["stats"]["decodedRecords"] == 0


def test_aac_record_that_repeats_the_previous_frame_uses_the_fully_decoded_copy():
    # Observed on a real export: each AAC minute record repeats the last whole frame(s) of the previous
    # record so it can decode on its own. Its first frame decodes without history and is distorted
    # (marked -1 here); the previous record holds a correct copy of the same audio.
    result = run_node("""
const rate=8000,F=470,step=(F-1)*1024;
function aacRecord(i){
 const startMs=epoch+Math.round(i*step/rate*100)*10,d=new Date(startMs),p=(n)=>String(n).padStart(2,'0');
 const stamp=d.getUTCFullYear()+p(d.getUTCMonth()+1)+p(d.getUTCDate())+p(d.getUTCHours())+p(d.getUTCMinutes())+p(d.getUTCSeconds())+p(Math.floor(d.getUTCMilliseconds()/10));
 const frame=[0xff,0xf1,108,64,1,0x1f,0xfc,0],b=new Uint8Array(80+F*8),view=new DataView(b.buffer);
 b.set(Buffer.from('VoyageDtRec '+stamp));b.set([0xff,0xff,2,0],44);view.setUint32(48,F*8,false);
 b.set(Buffer.from(stamp),52);b.set(Buffer.from('C1'),68);for(let k=0;k<F;k++)b.set(frame,72+k*8);b.set([0,0,13,10,0,0,13,10],b.length-8);
 return {start:startMs,path:'root/Internal_Storage/audio/202601052000/C1_'+i+'.dat',duration:F*1024/rate,rate,codec:'aac',frames:F,
  size:b.length,sha256:crypto.createHash('sha256').update(b).digest('hex'),file:{size:b.length,arrayBuffer:async()=>b.slice().buffer}};
}
global.VDRMedia={decodeAAC:async(parts,r,total)=>{const i=+parts[0].name.split('C1_')[1].split('.')[0],pcm=new Int16Array(total);
 for(let j=0;j<total;j++)pcm[j]=j<1024?-1:(i*step+j)%30000;return {pcm:pcm.buffer,decoded:[{samples:total,decoder_samples:total}]};}};
const files=[aacRecord(0),aacRecord(1)],W1=epoch+(step+F*1024)/rate*1000;
const st=S.createStore({audio:{C1:files},W0:epoch,W1});
const v=(await st.readRange('C1',0,120)).pcm;let cold=0,jumps=0;
for(let j=1025;j<v.length;j++){if(v[j]===-1)cold++;else if(v[j-1]!==-1&&v[j]!==(v[j-1]+1)%30000)jumps++;}
const c=st.channels.C1;
console.log(JSON.stringify({cold,jumps,overlaps:c.qa.overlaps,gaps:c.qa.gaps,repeated:c.qa.repeated_lead_records,
 lead:c.sources.map(x=>x.lead_duplicate_samples||0),envKnown:Array.from(c.envKnown.subarray(1100,1300)).every(Boolean)}));
""")
    assert result["cold"] == 0 and result["jumps"] == 0, result
    assert result["overlaps"] == [] and result["gaps"] == [] and result["repeated"] == 1
    # The waveform has no hole where the repeated frame was dropped.
    assert result["lead"] == [0, 1024] and result["envKnown"]
