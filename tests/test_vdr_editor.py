"""Synthetic editor contracts; no case files, browser services or Python at end user."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parents[1] / "scripts/vdr_review/src").as_posix()
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")


def run_node(code):
    result = subprocess.run(
        [NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return json.loads(result.stdout)


def test_file_session_reuses_handle_and_only_success_clears_dirty():
    result = run_node(f"""
const E=require('{SRC}/editor.js');
(async()=>{{
 const calls=[],handle={{name:'唯一主檔.json'}};
 let response={{saved:false,cancelled:true}},bad=false;
 const s=E.createFileSession(async o=>{{calls.push(o.handle);if(bad)throw Error('disk');return response;}});
 s.changed();const cancelled=await s.save('case',{{}}),afterCancel=s.dirty();
 response={{saved:false,downloaded:true}};await s.save('case',{{}});const afterDownload=s.dirty();
 response={{saved:true,handle}};await s.save('case',{{}});const afterSave=s.dirty();
 s.changed();await s.save('case',{{}});const reused=calls.at(-1)===handle;
 s.changed();bad=true;let error='';try{{await s.save('case',{{}})}}catch(e){{error=e.message;}}
 console.log(JSON.stringify({{cancelled,afterCancel,afterDownload,afterSave,reused,error,afterError:s.dirty()}}));
}})();
""")
    assert result == {
        "cancelled": {"saved": False, "cancelled": True},
        "afterCancel": True,
        "afterDownload": True,
        "afterSave": False,
        "reused": True,
        "error": "disk",
        "afterError": True,
    }


def test_file_session_does_not_hide_edits_made_during_async_write_or_replace():
    result = run_node(f"""
const E=require('{SRC}/editor.js');
(async()=>{{
 let finish;const old={{name:'old'}},newer={{name:'new'}};
 const s=E.createFileSession(()=>new Promise(resolve=>finish=resolve));
 s.changed();const first=s.save('case',{{}}),busy=await s.save('case',{{}});
 s.changed();finish({{saved:true,handle:old}});await first;
 const laterDirty=s.dirty(),reused=s.handle()===old;
 const next=s.save('case',{{}});s.replace(newer);finish({{saved:true,handle:old}});
 const superseded=await next;
 console.log(JSON.stringify({{busy,laterDirty,reused,superseded:superseded.superseded,
  afterReplaceDirty:s.dirty(),preservedNewHandle:s.handle()===newer}}));
}})();
""")
    assert result == {
        "busy": {"saved": False, "busy": True},
        "laterDirty": True,
        "reused": True,
        "superseded": True,
        "afterReplaceDirty": False,
        "preservedNewHandle": True,
    }


@pytest.mark.parametrize("field,value", [("text", "new"), ("spk", "B"), ("start", 1), ("end", 4)])
def test_editing_reviewed_words_speaker_or_timing_requires_rechecking(field, value):
    result = run_node(f"""
const E=require('{SRC}/editor.js');
const previous={{A1:[{{id:1,start:0,end:3,text:'old',spk:'A',st:1}}]}};
const state=JSON.parse(JSON.stringify(previous));state.A1[0][{json.dumps(field)}]={json.dumps(value)};
const changed=E.invalidateReviewedChanges(previous,state);
console.log(JSON.stringify({{status:state.A1[0].st,changed:changed.length,previous:previous.A1[0].st}}));
""")
    assert result == {"status": 0, "changed": 1, "previous": 1}


def test_setting_review_status_without_content_edits_is_not_invalidated():
    result = run_node(f"""
const E=require('{SRC}/editor.js');
const previous={{A1:[{{id:1,start:0,end:3,text:'old',spk:'A',st:0}}]}};
const state=JSON.parse(JSON.stringify(previous));state.A1[0].st=1;
E.invalidateReviewedChanges(previous,state);
console.log(JSON.stringify(state.A1[0].st));
""")
    assert result == 1


@pytest.mark.parametrize("field,value", [("text", "文字修正"), ("spk", "B"), ("st", 1)])
def test_pending_timing_is_not_resolved_by_text_speaker_or_review_status(field, value):
    result = run_node(f"""
const E=require('{SRC}/editor.js');
const marker={{reason:'legacy_zero_duration',original_start:20,original_end:20,placeholder_seconds:0.1,origin_t0_utc:'2026-01-01T00:00:00.000Z'}};
const previous={{A1:[{{id:1,start:20,end:20.1,text:'原文',spk:'A',st:0,timing_review:marker}}]}};
const state=JSON.parse(JSON.stringify(previous));state.A1[0][{json.dumps(field)}]={json.dumps(value)};
E.invalidateReviewedChanges(previous,state);
console.log(JSON.stringify({{cue:state.A1[0],markerSame:JSON.stringify(state.A1[0].timing_review)===JSON.stringify(marker)}}));
""")
    assert result["markerSame"] and result["cue"]["st"] == 0
    assert "timing_origin" not in result["cue"]


@pytest.mark.parametrize("field,value", [("start", 19.8), ("end", 22)])
def test_only_time_edits_resolve_pending_timing_and_leave_an_origin_record(field, value):
    result = run_node(f"""
const E=require('{SRC}/editor.js');
const marker={{reason:'legacy_zero_duration',original_start:20,original_end:20,placeholder_seconds:0.1,origin_t0_utc:'2026-01-01T00:00:00.000Z'}};
const previous={{A1:[{{id:1,start:20,end:20.1,text:'原文',spk:'A',st:0,timing_review:marker}}]}};
const state=JSON.parse(JSON.stringify(previous));state.A1[0][{json.dumps(field)}]={json.dumps(value)};
const changed=E.invalidateReviewedChanges(previous,state);
console.log(JSON.stringify({{cue:state.A1[0],count:changed.length,marker}}));
""")
    assert result["count"] == 1 and result["cue"]["st"] == 0 and "timing_review" not in result["cue"]
    assert result["cue"]["timing_origin"] == {**result["marker"], "resolution": "time_adjusted_in_editor"}


# A tiny DOM implements only the editor's display/event surface. It lets the real
# import/save/bundle handlers run with the real project validator and ZIP writer.
HARNESS = r"""
global.VDRI18n=require(SRC+'/i18n.js');
require(SRC+'/i18n_core.js');
require(SRC+'/i18n_ui.js');
require(SRC+'/i18n_editor.js');
require(SRC+'/i18n_app.js');
global.ReviewState=require(SRC+'/review_state.js');
global.VDRProject=require(SRC+'/project.js');
const Editor=require(SRC+'/editor.js'),elements={},listeners={},alerts=[],downloads=[];
const storage=new Map();global.localStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)};
global.setTimeout=()=>0;
class Element {
 constructor(id=''){this.id=id;this.value='';this.children=[];this.dataset={};this.style={setProperty(){}};
  this.classList={contains:()=>false,toggle(){}};this.clientWidth=300;this.clientHeight=100;this.scrollTop=0;
  this.scrollHeight=20;this.selectionStart=0;this.selectionEnd=0;this.tagName='DIV';this.hidden=false;this.events={};}
 set id(value){if(this._id&&elements[this._id]===this)delete elements[this._id];this._id=value;if(value)elements[value]=this;}
 get id(){return this._id||'';}
 set innerHTML(value){this.html=value;this.children=[];}
 get innerHTML(){return this.html||'';}
 setAttribute(name,value){(this.attributes||(this.attributes={}))[name]=String(value);}
 addEventListener(name,fn){this.events[name]=fn;}
 appendChild(el){this.children.push(el);el.parentNode=this;return el;}
 prepend(el){this.children.unshift(el);el.parentNode=this;}
 replaceChildren(...nodes){this.children=[];for(const node of nodes)this.appendChild(node);}
 insertAdjacentElement(_,el){this.adjacent=el;}
 querySelector(selector){if(selector==='.tx')return this.tx||(this.tx=new Element());if(selector==='.edhead')return this.head||(this.head=new Element());return null;}
 querySelectorAll(){return [];}
 getContext(){return new Proxy({}, {get:(o,k)=>o[k]||(()=>{})});}
 getBoundingClientRect(){return {left:0,top:0,bottom:100,width:300,height:100};}
 scrollIntoView(){} focus(){} setSelectionRange(){} click(){downloads.push(this.download);}
}
global.document={getElementById:id=>elements[id]||(elements[id]=new Element(id)),
 createElement:tag=>{const n=new Element();n.tagName=tag.toUpperCase();return n;},
 createTextNode:text=>{const n=new Element();n.nodeType=3;n.nodeValue=n.textContent=text;return n;},
 querySelectorAll:()=>[],addEventListener:(k,fn)=>listeners[k]=fn,
 body:new Element(),activeElement:{tagName:'BODY'}};
global.window={addEventListener:(k,fn)=>listeners[k]=fn};
global.confirm=()=>true;global.alert=m=>alerts.push(m);global.prompt=()=>null;
global.URL={createObjectURL:()=> 'blob:test',revokeObjectURL(){}};
const $=document.getElementById;
$('edflt').value='all';$('edrate').value='1';
const au={currentTime:0,paused:true,addEventListener(){},play(){},pause(){}},sel=new Element();sel.value='A1';
let extra={events:'',other:'',fixed:false,time_offset_s:0,time_offset_basis:'',own_dims:[null,null,null,null],other_dims:[null,null,null,null]};
const ctx={au,sel,T0:1767225600,LT:28800,ENV:{},ENVR:4,VHF:{},KEY:'bound-key',NAME:'SYNTHETIC',channels:['A1'],
 recordingId:'a'.repeat(64),dur:60,extra:{get:()=>extra,set:x=>{extra=x}},defaultExtra:extra,
 bundleEntries:async()=>[{name:'A1_原音.wav',data:new Uint8Array([1,2])},{name:'A1_來源清單.json',data:'{}'}],
 exportClip:async()=>{},exportWav:async()=>{}};
const editor=Editor.init(ctx);
const cue={id:1,start:0,end:2,text:'existing',spk:'A',st:1,orig:'',origNo:null,ovl:false};
function doc(){return {version:4,recording_id:'a'.repeat(64),t0_utc:'2026-01-01T00:00:00.000Z',tz_hours:8,
 state:{A1:[{...cue,text:'loaded'}]},spks:['A'],spkColor:{A:'#123456'},uid:2,extra};}
function openImport(data,name='案件.json'){return $('imJson').onchange({target:{files:[{name,text:async()=>JSON.stringify(data)}],value:'x'}});}
async function load(data){$('projectImportCancel').onclick();const failures=alerts.length;await openImport(data);
 if(alerts.length===failures&&!$('projectImportPanel').hidden&&!$('projectImportReplace').disabled)$('projectImportReplace').onclick();}
"""


def in_editor(code):
    return run_node("const SRC=" + json.dumps(SRC) + ";\n" + HARNESS + code)


def test_import_is_transactional_source_bound_and_preserves_in_memory_recovery():
    result = in_editor(r"""
(async()=>{
 editor.state().A1.push({...cue});editor.changed();
 const before=JSON.stringify(editor.state()),bad=doc();bad.recording_id='b'.repeat(64);
 await load(bad);const unchanged=before===JSON.stringify(editor.state());
 const malformed=doc();malformed.state.A1[0].st=99;await load(malformed);
 const stillUnchanged=before===JSON.stringify(editor.state());
 const good=doc();good.t0_utc='2026-01-01T00:00:10.000Z';await load(good);
 const loaded=editor.state().A1[0],loadedDirty=editor.dirty(),recover=$('loadP').adjacent;
 recover.onclick();
 console.log(JSON.stringify({unchanged,stillUnchanged,errors:alerts.length,loadedText:loaded.text,loadedStart:loaded.start,
  loadedDirty,recovered:editor.state().A1[0].text,recoveredDirty:editor.dirty(),recoveryVisible:!recover.hidden}));
})();
""")
    assert result == {
        "unchanged": True,
        "stillUnchanged": True,
        "errors": 2,
        "loadedText": "loaded",
        "loadedStart": 10,
        "loadedDirty": False,
        "recovered": "existing",
        "recoveredDirty": True,
        "recoveryVisible": True,
    }


def test_editor_real_save_handler_uses_same_file_and_never_marks_cancel_as_saved():
    result = in_editor(r"""
(async()=>{
 editor.state().A1.push({...cue});editor.changed();
 let picks=0,writes=0,closes=0,cancel=true;
 const handle={name:'my-case.json',createWritable:async()=>({write:async b=>{JSON.parse(await b.text());writes++;},close:async()=>closes++})};
 global.showSaveFilePicker=async()=>{picks++;if(cancel)throw Object.assign(Error(),{name:'AbortError'});return handle;};
 await $('saveP').onclick();const cancelDirty=editor.dirty();cancel=false;
 await $('saveP').onclick();const savedDirty=editor.dirty();
 editor.state().A1[0].text='edited';editor.changed();await $('saveP').onclick();
 console.log(JSON.stringify({picks,writes,closes,cancelDirty,savedDirty,dirty:editor.dirty(),status:editor.state().A1[0].st}));
})();
""")
    assert result == {
        "picks": 2, "writes": 2, "closes": 2, "cancelDirty": True,
        "savedDirty": False, "dirty": False, "status": 0,
    }


def test_editor_bundle_collects_one_archive_without_clearing_unsaved_changes():
    result = in_editor(r"""
(async()=>{
 editor.state().A1.push({...cue});editor.changed();
 let entries;const original=VDRProject.zipEntries;
 VDRProject.zipEntries=async e=>{entries=e;return original(e);};
 await $('exBundle').onclick();
 let warned=false;listeners.beforeunload({preventDefault(){warned=true;}});
 console.log(JSON.stringify({names:entries.map(e=>e.name),downloads,dirty:editor.dirty(),warned,
  version:JSON.parse(entries[0].data).version,recording:JSON.parse(entries[0].data).recording_id}));
})();
""")
    assert result["names"] == [
        "SYNTHETIC_核聽案件.vdr.json", "全部聲道核聽表.csv", "A1_本時段字幕.srt",
        "交接說明.txt", "A1_原音.wav", "A1_來源清單.json",
    ]
    assert result["downloads"] == ["SYNTHETIC_核聽交接成果.zip"]
    assert result["dirty"] and result["warned"]
    assert result["version"] == 4 and result["recording"] == "a" * 64


def test_legacy_import_requires_confirmation_and_retains_migration_in_saved_project():
    result = in_editor(r"""
(async()=>{
 editor.state().A1.push({...cue});editor.changed();
 const old=doc();old.version=3;delete old.recording_id;
 let confirms=0;global.confirm=()=>{confirms++;return false;};
 await load(old);const rejectedText=editor.state().A1[0].text;
 global.confirm=()=>{confirms++;return true;};await load(old);
 const importedDirty=editor.dirty();let saved;
 global.showSaveFilePicker=async()=>({name:'converted.json',createWritable:async()=>({
  write:async b=>{saved=JSON.parse(await b.text());},close:async()=>{}})});
 await $('saveP').onclick();
 console.log(JSON.stringify({rejectedText,confirms,importedDirty,imported:editor.state().A1[0].text,
  migration:saved.migration,version:saved.version,recording:saved.recording_id}));
})();
""")
    assert result["rejectedText"] == "existing" and result["confirms"] == 3
    assert result["importedDirty"] and result["imported"] == "loaded"
    assert result["migration"]["from_version"] == 3
    assert result["migration"]["source_binding"] == "explicit_user_confirmation"
    assert result["version"] == 4 and result["recording"] == "a" * 64


def test_srt_import_requires_full_date_and_has_no_nearest_day_guess():
    result = in_editor(r"""
(async()=>{
 const srt='1\n00:00:01,1 --> 00:00:02,12\nhello';
 const bytes=new TextEncoder().encode(srt);
 await $('imSrt').onchange({target:{files:[{name:'syn.srt',size:bytes.byteLength,arrayBuffer:async()=>bytes.buffer}],value:'x'}});
 const panel=$('imSrtBtn').adjacent;
 function field(id,node=panel){if(node.id===id)return node;for(const c of node.children){const n=field(id,c);if(n)return n;}}
 const date=field('srtLocalStart'),apply=field('srtImportApply');
 date.value='08:00:00';date.oninput();apply.onclick();
 const rejected=editor.state().A1.length===0&&apply.disabled&&!!field('srtImportError').textContent;
 date.value='2026-01-02T08:00:00';date.oninput();apply.onclick();
 const c=editor.state().A1[0];
 console.log(JSON.stringify({rejected,alerts:alerts.length,start:c.start,end:c.end,status:c.st,dirty:editor.dirty(),panelHidden:panel.hidden}));
})();
""")
    assert result == {"rejected": True, "alerts": 0, "start": 86401.1, "end": 86402.12, "status": 0, "dirty": True, "panelHidden": True}


def test_csv_has_full_dates_and_escapes_spreadsheet_formulas_without_altering_main_file():
    result = in_editor(r"""
(async()=>{
 editor.state().A1.push({...cue,text:'=HYPERLINK("x")',start:86400,end:86402});editor.changed();
 let entries;const original=VDRProject.zipEntries;VDRProject.zipEntries=async e=>{entries=e;return original(e);};
 await $('exBundle').onclick();
 console.log(JSON.stringify({csv:entries[1].data,original:JSON.parse(entries[0].data).state.A1[0].text}));
})();
""")
    assert "2026-01-02 08:00:00.000 UTC+8" in result["csv"]
    assert "未載入此時段，未檢查" in result["csv"]
    assert '"\'=HYPERLINK(""x"")"' in result["csv"]
    assert result["original"] == '=HYPERLINK("x")'
