"""Generic SRT round trips use synthetic text and source-bound receipts only."""

import json
import shutil

import pytest

from test_vdr_editor import HARNESS, SRC, run_node

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

RECEIPT = {
    "kind": "vdr-transcription-export",
    "version": 1,
    "recording_id": "a" * 64,
    "channel": "A1",
    "source_start_utc": "2026-01-01T00:10:00.000Z",
    "source_end_utc": "2026-01-01T02:10:00.000Z",
    "display_tz_hours": 8,
    "corrected_start_utc": "2026-01-01T00:10:30.000Z",
    "time_correction": {"offset_s": 30, "basis": "synthetic independent clock check"},
    "audio_file": "A1_synthetic_excerpt.wav",
    "audio_sha256": "b" * 64,
}


def pure(code):
    return run_node(
        "const E=require(" + json.dumps(SRC + "/editor.js") + ");"
        "const receipt=" + json.dumps(RECEIPT) + ";\n" + code
    )


def ui(code, before=""):
    harness = HARNESS.replace("const editor=Editor.init(ctx);", before + "\nconst editor=Editor.init(ctx);")
    helper = r"""
function field(id,node=$('imSrtBtn').adjacent){
 if(node.id===id)return node;for(const c of node.children){const f=field(id,c);if(f)return f;}
}
async function openSrt(text='1\n00:00:01,125 --> 00:00:02,250\nsynthetic words',name='synthetic.srt'){
 const bytes=new TextEncoder().encode(text);
 await $('imSrt').onchange({target:{files:[{name,size:bytes.byteLength,arrayBuffer:async()=>bytes.buffer}],value:'selected'}});
}
const receipt=RECEIPT;
""".replace("RECEIPT", json.dumps(RECEIPT))
    return run_node("const SRC=" + json.dumps(SRC) + ";\n" + harness + helper + code)


def test_parse_srt_keeps_fractional_times_multiline_original_and_long_hours():
    result = pure(r"""
console.log(JSON.stringify(E.parseSrt('\uFEFF1\r\n02:59:59,1 --> 03:00:01,025\r\n第一行\r\n第二行\r\n\r\n\r\n2\r\n03:59:59.125 --> 04:00:00.250\r\nlast')));
""")
    assert result == [
        {"s": 10799.1, "e": 10801.025, "text": "第一行\n第二行", "no": "1"},
        {"s": 14399.125, "e": 14400.25, "text": "last", "no": "2"},
    ]


def test_srt_file_hash_identifies_original_bom_and_newline_bytes_not_normalized_text():
    result = pure(r"""
(async()=>{
 const crypto=require('node:crypto'),text='\uFEFF1\r\n00:00:01,000 --> 00:00:02,000\r\n原文 / 日本語\r\n';
 const bytes=new TextEncoder().encode(text),file={size:bytes.byteLength,arrayBuffer:async()=>bytes.buffer,
  text(){throw Error('Do not hash re-encoded File.text()');}};
 const original=await E.readSrtFile(file),normalized=await E.readSrtFile(new Blob([text.replace(/^\uFEFF/,'').replace(/\r\n/g,'\n')]));
 console.log(JSON.stringify({original,normalized,expected:crypto.createHash('sha256').update(bytes).digest('hex'),
  bytes:bytes.byteLength,sameCues:JSON.stringify(original.got)===JSON.stringify(normalized.got)}));
})();
""")
    assert result["original"]["srt_sha256"] == result["expected"]
    assert result["original"]["srt_bytes"] == result["bytes"]
    assert result["original"]["srt_sha256"] != result["normalized"]["srt_sha256"]
    assert result["sameCues"]
    assert result["original"]["got"][0]["text"] == "原文 / 日本語"


def test_srt_file_byte_limit_is_checked_before_hashing_and_after_reading():
    result = pure(r"""
(async()=>{
 const P=require(SRC+'/project.js'),original=P.sha256;let reads=0,hashes=0,errors=[];
 P.sha256=async()=>{hashes++;return 'a'.repeat(64);};
 try{for(const file of [{size:20000001,arrayBuffer:async()=>{reads++;return new ArrayBuffer(1);}},
   {size:1,arrayBuffer:async()=>{reads++;return new ArrayBuffer(20000001);}}]){
  try{await E.readSrtFile(file);}catch(e){errors.push(e.i18nKey);}
 }}finally{P.sha256=original;}
 console.log(JSON.stringify({reads,hashes,errors}));
})();
""".replace("SRC+'/project.js'", json.dumps(SRC + "/project.js")))
    assert result == {"reads": 1, "hashes": 0, "errors": ["editor.srtInvalid"] * 2}


def test_srt_hash_failure_keeps_existing_case_and_cache_without_opening_import_preview():
    result = ui(r"""
(async()=>{
 editor.state().A1.push({...cue});editor.changed();
 const before=JSON.stringify(editor.state()),cache=storage.get(ctx.KEY),dirty=editor.dirty();
 VDRProject.sha256=async()=>{throw Error('synthetic hash failure');};
 await openSrt();
 console.log(JSON.stringify({sameState:before===JSON.stringify(editor.state()),sameCache:cache===storage.get(ctx.KEY),
  sameDirty:dirty===editor.dirty(),hidden:$('imSrtBtn').adjacent.hidden,alerts}));
})();
""")
    assert result["sameState"] and result["sameCache"] and result["sameDirty"] and result["hidden"]
    assert len(result["alerts"]) == 1 and "synthetic hash failure" in result["alerts"][0]


def test_cancelling_during_srt_hashing_cannot_reopen_preview_or_import_anything():
    result = ui(r"""
(async()=>{
 editor.state().A1.push({...cue});editor.changed();
 const before=JSON.stringify(editor.state()),cache=storage.get(ctx.KEY);let finish;
 VDRProject.sha256=()=>new Promise(resolve=>{finish=resolve;});
 const operation=openSrt();while(!finish)await Promise.resolve();
 field('srtImportCancel').onclick();finish('b'.repeat(64));await operation;
 console.log(JSON.stringify({sameState:before===JSON.stringify(editor.state()),sameCache:cache===storage.get(ctx.KEY),
  hidden:$('imSrtBtn').adjacent.hidden,alerts}));
})();
""")
    assert result == {"sameState": True, "sameCache": True, "hidden": True, "alerts": []}


def test_new_srt_selection_supersedes_old_hash_and_stale_read_errors():
    result = ui(r"""
(async()=>{
 const hash=VDRProject.sha256;let finish,first=true;
 VDRProject.sha256=bytes=>{if(first){first=false;return new Promise(resolve=>{finish=resolve;});}return hash(bytes);};
 const older=openSrt(undefined,'older.srt');while(!finish)await Promise.resolve();
 await openSrt(undefined,'newer.srt');const before=field('srtImportPreview').textContent;
 finish('c'.repeat(64));await older;const newerKept=before===field('srtImportPreview').textContent;
 let reject;const stale=$('imSrt').onchange({target:{files:[{name:'stale.srt',arrayBuffer:()=>new Promise((_,no)=>{reject=no;})}],value:'x'}});
 await openSrt(undefined,'latest.srt');reject(Error('stale read failure'));await stale;
 field('srtImportApply').onclick();
 console.log(JSON.stringify({newerKept,source:editor.state().A1[0].srt_source,alerts}));
})();
""")
    assert result["newerKept"] and result["alerts"] == []
    assert result["source"]["file"] == "latest.srt"
    assert result["source"]["srt_sha256"] not in ["b" * 64, "c" * 64]


def test_srt_provenance_is_retained_through_save_reopen_and_project_merge():
    result = ui(r"""
(async()=>{
 const raw='\uFEFF1\r\n00:00:01,125 --> 00:00:02,250\r\n原始字幕\r\n';
 const bytes=new TextEncoder().encode(raw),hash=await VDRProject.sha256(bytes),started=Date.now();
 await openSrt(raw,'A1_synthetic_excerpt.srt');field('srtImportApply').onclick();
 const source=JSON.parse(JSON.stringify(editor.state().A1[0].srt_source)),finished=Date.now();
 let saved;global.showSaveFilePicker=async()=>({name:'case.json',createWritable:async()=>({
  write:async b=>{saved=JSON.parse(await b.text());},close:async()=>{}})});
 await $('saveP').onclick();const savedSource=saved.state.A1[0].srt_source;
 editor.state().A1[0].text='temporary edit';editor.changed();await load(saved);
 const reopened=editor.state().A1[0];
 const empty=doc();empty.state.A1=[];empty.uid=1;
 const merged=VDRProject.mergeProjects(empty,saved).project.state.A1[0];
 console.log(JSON.stringify({source,hash,bytes:bytes.byteLength,started,finished,savedSource,
  reopened,merged,unchanged:JSON.stringify(savedSource)===JSON.stringify(source)}));
})();
""", "ctx.exports=()=>[" + json.dumps(RECEIPT) + "];")
    source = result["source"]
    assert source["srt_sha256"] == result["hash"] and source["srt_bytes"] == result["bytes"]
    assert source["audio_sha256"] == "b" * 64 and source["method"] == "export_receipt"
    assert source["zero_source_utc"] == RECEIPT["source_start_utc"]
    assert source["imported_at_utc"].endswith("Z")
    from datetime import datetime

    imported = datetime.fromisoformat(source["imported_at_utc"].replace("Z", "+00:00")).timestamp() * 1000
    assert result["started"] <= imported <= result["finished"]
    assert result["unchanged"] and result["savedSource"] == source
    assert result["reopened"]["srt_source"] == source and result["merged"]["srt_source"] == source
    assert result["reopened"]["orig"] == result["merged"]["orig"] == "原始字幕"
    assert result["reopened"]["text"] == result["merged"]["text"] == "原始字幕"


def test_old_projects_without_srt_hash_remain_loadable_and_mergeable():
    result = ui(r"""
(async()=>{
 const old=doc(),origin={file:'legacy.srt',method:'manual_local_time',zero_source_utc:old.t0_utc};
 old.state.A1[0].srt_source=origin;await load(old);
 const loaded=editor.state().A1[0],base=doc();base.state.A1=[];base.uid=1;
 const merged=VDRProject.mergeProjects(base,old).project.state.A1[0];
 console.log(JSON.stringify({loaded:loaded.srt_source,merged:merged.srt_source,alerts}));
})();
""")
    assert result["loaded"] == result["merged"] == {
        "file": "legacy.srt", "method": "manual_local_time", "zero_source_utc": "2026-01-01T00:00:00.000Z"
    }
    assert result["alerts"] == []


@pytest.mark.parametrize("suffix", [
    "not an SRT block", "2\n00:00:02,000 --> invalid\nwords",
    "2\n00:60:00,000 --> 01:01:00,000\nwords",
    "2\n00:00:02,000 --> 00:00:02,000\nwords",
    "2\n00:00:03,000 --> 00:00:02,000\nwords",
    "2\n00:00:02,000 --> 00:00:03,000\nwords\u0000",
])
def test_malformed_block_rejects_whole_srt_instead_of_silently_skipping(suffix):
    text = "1\n00:00:00,000 --> 00:00:01,000\nvalid\n\n" + suffix
    result = pure("let error='';try{E.parseSrt(" + json.dumps(text) + ")}catch(e){error=e.message;}console.log(JSON.stringify({error}));")
    assert result["error"]


def test_receipt_uses_raw_source_time_even_when_current_correction_changed():
    result = pure(r"""
const got=E.parseSrt('1\n00:00:01,125 --> 00:00:02,250\nhello');
const base={recordingId:'a'.repeat(64),channels:['A1','C1'],channel:'A1',t0:1767225600,displayTzHours:8};
const first=E.srtAlignment(got,{...base,receipt,offsetSeconds:30});
const correctedAgain=E.srtAlignment(got,{...base,receipt,offsetSeconds:-12});
const manual=E.srtAlignment(got,{...base,localStart:'2026-01-01T08:10:30.000',offsetSeconds:30});
console.log(JSON.stringify({shift:first.shift,start:first.start,end:first.end,changedShift:correctedAgain.shift,
 manualShift:manual.shift,sourceZero:first.zero_utc}));
""")
    assert result == {"shift": 600, "start": 601.125, "end": 602.25,
                      "changedShift": 600, "manualShift": 600, "sourceZero": RECEIPT["source_start_utc"]}


@pytest.mark.parametrize("change", [
    "receipt.version=2", "receipt.kind='anything'", "receipt.recording_id='c'.repeat(64)",
    "receipt.channel='C1'", "receipt.source_start_utc='2026-02-30T00:00:00Z'",
    "receipt.source_end_utc=receipt.source_start_utc", "receipt.source_start_utc='00:10:00'",
    "receipt.corrected_start_utc='2026-01-01T00:10:31Z'", "receipt.audio_sha256='bad'",
    "receipt.time_correction.offset_s=NaN", "receipt.time_correction.basis=''",
    "receipt.display_tz_hours=15",
])
def test_receipts_reject_foreign_source_wrong_channel_version_and_invalid_times(change):
    result = pure(change + r""";
let error='';try{E.validateTranscriptionReceipt(receipt,'a'.repeat(64),['A1']);}catch(e){error=e.message;}
console.log(JSON.stringify({error}));
""")
    assert result["error"]


def test_receipt_channel_mismatch_and_srt_longer_than_export_are_rejected():
    result = pure(r"""
const got=E.parseSrt('1\n00:00:01,000 --> 00:00:02,000\nhello');
const base={recordingId:'a'.repeat(64),channels:['A1','C1'],channel:'A1',t0:1767225600,displayTzHours:8,offsetSeconds:0,receipt};
let channel='',duration='';try{E.srtAlignment(got,{...base,channel:'C1'});}catch(e){channel=e.message;}
try{E.srtAlignment([{s:7199,e:7201}],base);}catch(e){duration=e.message;}
console.log(JSON.stringify({channel,duration}));
""")
    assert result["channel"] and result["duration"]


def test_manual_alignment_requires_complete_valid_date_and_handles_midnight_and_milliseconds():
    result = pure(r"""
const got=[{s:3599.875,e:3601.125}],base={channels:['A1'],channel:'A1',t0:1767225600,displayTzHours:8,offsetSeconds:0};
const a=E.srtAlignment(got,{...base,localStart:'2026-01-01T23:30:00.125'});
let errors=0;for(const localStart of ['23:30:00','2026-02-30T23:30:00','2026-01-01T24:00:00','2026-01-01T23:61:00']){
 try{E.srtAlignment(got,{...base,localStart});}catch(e){errors++;}}
console.log(JSON.stringify({zero:a.zero_utc,shift:a.shift,start:a.start,end:a.end,errors}));
""")
    assert result == {"zero": "2026-01-01T15:30:00.125Z", "shift": 55800.125,
                      "start": 59400, "end": 59401.25, "errors": 4}


def test_manual_alignment_accepts_datetime_local_native_normalized_minute_value():
    result = pure(r"""
const base={channels:['A1'],channel:'A1',t0:1767225600,displayTzHours:8,offsetSeconds:30};
const cues=[{s:1.125,e:2.250}],values=['2026-01-01T08:10','2026-01-01T08:10:00','2026-01-01T08:10:00.000'];
console.log(JSON.stringify(values.map(localStart=>{
 const a=E.srtAlignment(cues,{...base,localStart});return {zero:a.zero_utc,shift:a.shift,start:a.start,end:a.end};
})));
""")
    expected = {"zero": "2026-01-01T00:09:30.000Z", "shift": 570, "start": 571.125, "end": 572.25}
    assert result == [expected, expected, expected]


def test_inline_manual_import_enables_apply_for_native_minute_value_but_never_time_only():
    result = ui(r"""
(async()=>{
 await openSrt();const date=field('srtLocalStart'),apply=field('srtImportApply');
 date.value='08:10';date.oninput();const timeOnlyRejected=apply.disabled;
 date.value='2026-01-01T08:10';date.oninput();const minuteAccepted=!apply.disabled;
 apply.onclick();const c=editor.state().A1[0];
 console.log(JSON.stringify({timeOnlyRejected,minuteAccepted,start:c.start,end:c.end,hidden:$('imSrtBtn').adjacent.hidden}));
})();
""")
    assert result == {"timeOnlyRejected": True, "minuteAccepted": True, "start": 601.125,
                      "end": 602.25, "hidden": True}


def test_inline_import_prefers_matching_audio_receipt_preserves_existing_and_stores_provenance():
    result = ui(r"""
(async()=>{
 editor.state().A1.push({...cue,id:100});editor.changed();
 await openSrt(undefined,'A1_synthetic_excerpt.srt');
 const origin=field('srtImportOrigin').value,preview=field('srtImportPreview').textContent;
 field('srtImportApply').onclick();const imported=editor.state().A1[1];
 console.log(JSON.stringify({origin,preview,existing:editor.state().A1[0].text,start:imported.start,end:imported.end,
  st:imported.st,provenance:imported.srt_source,dirty:editor.dirty(),hidden:$('imSrtBtn').adjacent.hidden}));
})();
""", "ctx.ofs=()=>30;ctx.exports=()=>[" + json.dumps(RECEIPT) + "];")
    assert result["origin"] == "receipt:0" and "A1_synthetic_excerpt.wav" in result["preview"]
    assert result["existing"] == "existing" and result["st"] == 0
    assert result["start"] == 601.125 and result["end"] == 602.25
    assert result["provenance"]["zero_source_utc"] == RECEIPT["source_start_utc"]
    assert result["provenance"]["audio_sha256"] == "b" * 64
    assert result["dirty"] and result["hidden"]


def test_inline_cancel_and_malformed_srt_never_mutate_existing_cues():
    result = ui(r"""
(async()=>{
 editor.state().A1.push({...cue,id:100});editor.changed();const before=JSON.stringify(editor.state());
 await openSrt();field('srtImportCancel').onclick();const cancelled=before===JSON.stringify(editor.state());
 await openSrt('1\n00:00:00,000 --> 00:00:01,000\nvalid\n\nnot srt');
 const bad=before===JSON.stringify(editor.state());
 await openSrt();global.confirm=()=>false;field('srtImportApply').onclick();
 console.log(JSON.stringify({cancelled,bad,declined:before===JSON.stringify(editor.state()),alerts:alerts.length,
  stillOpen:!$('imSrtBtn').adjacent.hidden}));
})();
""")
    assert result == {"cancelled": True, "bad": True, "declined": True, "alerts": 1, "stillOpen": True}


def test_uploaded_receipt_rejects_foreign_then_allows_valid_segment_and_save_reopen():
    result = ui(r"""
(async()=>{
 editor.state().A1.push({...cue,id:100});editor.changed();const before=JSON.stringify(editor.state());await openSrt();
 async function loadReceipt(value){await field('srtReceiptFile').onchange({target:{files:[{name:'對照單.json',text:async()=>JSON.stringify(value)}],value:'x'}});}
 await loadReceipt({...receipt,recording_id:'c'.repeat(64)});
 const foreignRejected=field('srtImportApply').disabled&&before===JSON.stringify(editor.state());
 await loadReceipt(receipt);field('srtImportApply').onclick();
 let saved;global.showSaveFilePicker=async()=>({name:'case.json',createWritable:async()=>({
  write:async b=>{saved=JSON.parse(await b.text());},close:async()=>{}})});
 await $('saveP').onclick();const savedImport=saved.state.A1[1];
 editor.state().A1[1].text='unsaved change';editor.changed();await load(saved);
 console.log(JSON.stringify({foreignRejected,start:savedImport.start,provenance:savedImport.srt_source,
  reopened:editor.state().A1[1].text,dirty:editor.dirty()}));
})();
""")
    assert result["foreignRejected"] and result["start"] == 601.125
    assert result["provenance"]["method"] == "export_receipt"
    assert result["reopened"] == "synthetic words" and not result["dirty"]


def test_full_channel_export_awaits_failure_and_reenables_button_without_false_success():
    result = ui(r"""
(async()=>{
 let finish;ctx.exportWav=()=>new Promise((resolve,reject)=>{finish=reject;});
 const operation=$('exWav').onclick();const disabledWhileWriting=$('exWav').disabled;
 finish(Error('synthetic disk failure'));await operation;
 console.log(JSON.stringify({disabledWhileWriting,enabled:!$('exWav').disabled,status:$('edsave').textContent}));
})();
""")
    assert result["disabledWhileWriting"] and result["enabled"]
    assert "未完成" in result["status"] and "synthetic disk failure" in result["status"]


def test_overlap_count_handles_unordered_overlapping_old_cues_without_counting_touching_edges():
    result = pure(r"""
const old=[{start:10,end:20},{start:0,end:2},{start:15,end:30},{start:2,end:4}];
console.log(JSON.stringify(E.countSrtOverlaps([{s:4,e:10},{s:3,e:10},{s:20,e:21},{s:30,e:31},{s:-1,e:0}],old,0)));
""")
    assert result == 2


def test_alignment_handles_large_subtitle_array_without_spreading_arguments_to_math():
    result = pure(r"""
const cues=Array.from({length:150000},(_,i)=>({s:i/100,e:i/100+0.005}));
const a=E.srtAlignment(cues,{channels:['A1'],channel:'A1',t0:1767225600,displayTzHours:8,
 offsetSeconds:0,localStart:'2026-01-01T08:00:00.000'});
console.log(JSON.stringify({start:a.start,end:a.end}));
""")
    assert result["start"] == 0
    assert result["end"] == pytest.approx(1499.995, abs=1e-10)


def test_pending_media_does_not_claim_silence_or_full_audio_verification_in_csv():
    result = ui(r"""
(async()=>{
 editor.state().A1.push({...cue});editor.changed();editor.refreshMedia();
 let entries;const zip=VDRProject.zipEntries;VDRProject.zipEntries=async e=>{entries=e;return zip(e);};
 await $('exBundle').onclick();
 console.log(JSON.stringify({vhf:$('vhfseg').innerHTML,csv:entries[1].data}));
})();
""", "ctx.pending=()=>9;ctx.envKnown=()=>new Uint8Array(240);ctx.VHF.A1=[];")
    assert "其餘錄音按需建立波形" in result["vhf"]
    assert "本時段未偵測到發話" not in result["vhf"]
    assert "依索引未見缺漏；尚有錄音未解碼檢查" in result["csv"]


def test_uploaded_receipt_must_match_explicit_target_channel():
    result = ui(r"""
(async()=>{
 await openSrt();field('srtImportChannel').value='C1';field('srtImportChannel').onchange();
 await field('srtReceiptFile').onchange({target:{files:[{name:'receipt.json',text:async()=>JSON.stringify(receipt)}],value:'x'}});
 console.log(JSON.stringify({disabled:field('srtImportApply').disabled,error:field('srtImportError').textContent,
  channel:field('srtImportChannel').value,count:editor.state().C1.length}));
})();
""", "ctx.channels.push('C1');")
    assert result["disabled"] and "目前選定 C1" in result["error"]
    assert result["channel"] == "C1" and result["count"] == 0
