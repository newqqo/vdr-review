"""Synthetic standalone-browser persistence and ZIP contracts; never reads case data."""
import base64
import io
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROJECT = (ROOT / "scripts" / "vdr_review" / "src" / "project.js").as_posix()
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")
PRELUDE = """
const P=require(PROJECT_PATH);
const hash='a'.repeat(64);
const fixture=()=>({version:4,recording_id:hash,t0_utc:'2026-10-22T10:43:00.000Z',tz_hours:8,
state:{A1:[{id:1,start:-1,end:2,spk:'測試講者',text:'synthetic <text>',st:0,ovl:false,orig:'synthetic',origNo:null}]},
uid:2,spks:['測試講者'],spkColor:{'測試講者':'#277a7b'},extra:{events:'',other:'',fixed:false,time_offset_s:0,time_offset_basis:'',own_dims:[null,1,2,3],other_dims:[null,null,null,null]}});
const errorOf=(f)=>{try{f();return ''}catch(e){return e.message}};
""".replace("PROJECT_PATH", json.dumps(PROJECT))


def run_js(code):
    result = subprocess.run([NODE, "-e", PRELUDE + code], check=True, capture_output=True,
                            text=True, encoding="utf-8")
    return json.loads(result.stdout)


def test_project_validation_is_source_bound_and_detached():
    result = run_js("""
const input=fixture(),before=JSON.stringify(input),out=P.validateImport(input,hash);
out.state.A1[0].text='changed';out.extra.own_dims[1]=99;
console.log(JSON.stringify({unchanged:JSON.stringify(input)===before,version:out.version,
badBinding:errorOf(()=>P.validateImport(input,'b'.repeat(64))),badCurrent:errorOf(()=>P.validateImport(input,null)),
badFuture:errorOf(()=>P.validateImport({...input,version:5},hash)),badDate:errorOf(()=>P.validateImport({...input,t0_utc:'2026-02-30T10:43:00Z'},hash)),
noZone:errorOf(()=>P.validateImport({...input,t0_utc:'2026-10-22T10:43:00'},hash))}));
""")
    assert result["unchanged"] and result["version"] == 4
    assert all(result[k] for k in ("badBinding", "badCurrent", "badFuture", "badDate", "noZone"))


def test_invalid_import_never_normalizes_malformed_cues_or_unsafe_settings():
    result = run_js("""
const changes=[c=>c.id=0,c=>c.id=1.5,c=>c.start=NaN,c=>c.end=c.start,c=>c.end=Infinity,
c=>c.st='1',c=>c.st=4,c=>c.text={},c=>c.spk=[],c=>c.ovl='yes',c=>c.repOf=-1];
const errors=changes.map(fn=>{const d=fixture();fn(d.state.A1[0]);return errorOf(()=>P.validateImport(d,hash))});
const duplicate=fixture();duplicate.state.C1=[{...duplicate.state.A1[0]}];
const unsafe=JSON.parse(JSON.stringify(fixture()));unsafe.state=JSON.parse('{"__proto__":[]}');
const badColors=fixture();badColors.spkColor={'a':'red;background:url(test)'};
const badExtra=fixture();badExtra.extra.fixed='false';
console.log(JSON.stringify({errors,duplicate:errorOf(()=>P.validateImport(duplicate,hash)),
unsafe:errorOf(()=>P.validateImport(unsafe,hash)),badColors:errorOf(()=>P.validateImport(badColors,hash)),
badExtra:errorOf(()=>P.validateImport(badExtra,hash))}));
""")
    assert all(result["errors"])
    assert all(result[k] for k in ("duplicate", "unsafe", "badColors", "badExtra"))


def test_legacy_progress_requires_explicit_confirmation_and_remains_unchanged():
    result = run_js("""
const d=fixture();d.version=3;delete d.recording_id;const before=JSON.stringify(d);
const denied=errorOf(()=>P.validateImport(d,hash)),out=P.validateImport(d,hash,{allowLegacy:true});
console.log(JSON.stringify({denied,unchanged:before===JSON.stringify(d),out,
v2:errorOf(()=>P.validateImport({...d,version:2},hash,{allowLegacy:true}))}));
""")
    assert result["denied"] and result["v2"] and result["unchanged"]
    assert result["out"]["version"] == 4 and result["out"]["recording_id"] == "a" * 64
    assert result["out"]["migration"]["source_binding"] == "explicit_user_confirmation"


def test_save_reuses_file_handle_and_only_reports_saved_after_close():
    result = run_js("""
(async()=>{let picks=0,opens=0,closes=0,writes=[];
const handle={createWritable:async()=>{opens++;return {write:async b=>writes.push(await b.text()),close:async()=>{closes++}}}};
global.showSaveFilePicker=async options=>{picks++;return handle};
const first=await P.saveProject({name:'synthetic.vdr.json',data:fixture()});
const second=await P.saveProject({name:'synthetic.vdr.json',data:fixture(),handle:first.handle});
console.log(JSON.stringify({picks,opens,closes,writes:writes.map(JSON.parse),same:first.handle===second.handle,saved:second.saved,mode:second.mode}));
})().catch(e=>{console.error(e);process.exit(1)});
""")
    assert result["picks"] == 1 and result["opens"] == result["closes"] == 2
    assert result["same"] and result["saved"] and result["mode"] == "file"
    assert all(d["version"] == 4 for d in result["writes"])


def test_cancel_and_write_failure_never_claim_saved():
    result = run_js("""
(async()=>{let aborts=0,closed=0;
global.showSaveFilePicker=async()=>{throw Object.assign(new Error('cancel'),{name:'AbortError'})};
const cancel=await P.saveProject({data:fixture()});let writeError='',closeError='';
try{await P.saveProject({data:fixture(),handle:{createWritable:async()=>({write:async()=>{throw Error('disk full')},close:async()=>{closed++},abort:async()=>{aborts++}})}})}catch(e){writeError=e.message}
try{await P.saveProject({data:fixture(),handle:{createWritable:async()=>({write:async()=>{},close:async()=>{throw Error('close failed')},abort:async()=>{aborts++}})}})}catch(e){closeError=e.message}
console.log(JSON.stringify({cancel,aborts,closed,writeError,closeError}));
})().catch(e=>{console.error(e);process.exit(1)});
""")
    assert result["cancel"] == {"saved": False, "cancelled": True}
    assert result["writeError"] == "disk full" and result["closeError"] == "close failed"
    assert result["aborts"] == 2 and result["closed"] == 0


def test_download_fallback_is_explicitly_not_same_file_save():
    result = run_js("""
(async()=>{let clicked=0;delete global.showSaveFilePicker;
global.document={createElement:()=>({click:()=>clicked++})};
global.URL={createObjectURL:()=> 'blob:test',revokeObjectURL:()=>{}};global.setTimeout=()=>{};
const saved=await P.saveProject({name:'test.json',data:fixture()});
console.log(JSON.stringify({clicked,saved}));})().catch(e=>{console.error(e);process.exit(1)});
""")
    assert result["clicked"] == 1
    assert result["saved"]["saved"] is False and result["saved"]["downloaded"] is True
    assert result["saved"]["mode"] == "download" and result["saved"]["message"]


def test_zip_unicode_crc_determinism_and_binary_readback():
    result = run_js("""
(async()=>{const entries=[{name:'核聽/說明.txt',data:'synthetic 測試'},{name:'audio/A1.wav',data:new Uint8Array([0,1,127,128,255])},{name:'empty.txt',data:new Blob([])}];
const a=Buffer.from(await (await P.zipEntries(entries)).arrayBuffer()),b=Buffer.from(await (await P.zipEntries(entries)).arrayBuffer());
console.log(JSON.stringify({zip:a.toString('base64'),same:a.equals(b),crc:P.crc32(new TextEncoder().encode('123456789'))}));
})().catch(e=>{console.error(e);process.exit(1)});
""")
    assert result["same"] and result["crc"] == 0xCBF43926
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(result["zip"]))) as archive:
        assert archive.testzip() is None
        assert archive.read("核聽/說明.txt").decode() == "synthetic 測試"
        assert archive.read("audio/A1.wav") == bytes([0, 1, 127, 128, 255])
        assert archive.read("empty.txt") == b""
        assert all(i.flag_bits & 0x800 for i in archive.infolist())
        assert all(i.compress_type == zipfile.ZIP_STORED for i in archive.infolist())
        assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in archive.infolist())


def test_zip_rejects_unsafe_and_colliding_portable_paths():
    result = run_js("""
(async()=>{const names=['../escape.txt','/absolute.txt','C:/drive.txt','a\\\\b.txt','a//b.txt','a/./b.txt','NUL.txt','a/CON','a/trailing.','a/b?.txt'];
const errors=[];for(const name of names){try{await P.zipEntries([{name,data:''}]);errors.push('')}catch(e){errors.push(e.message)}}
let duplicate='';try{await P.zipEntries([{name:'A.txt',data:''},{name:'a.txt',data:''}])}catch(e){duplicate=e.message}
console.log(JSON.stringify({errors,duplicate}));})().catch(e=>{console.error(e);process.exit(1)});
""")
    assert all(result["errors"]) and result["duplicate"]


def test_sha256_requires_real_binary_input_and_digest():
    result = run_js("""
(async()=>{const hash=await P.sha256('abc');let invalid='';try{await P.sha256(null)}catch(e){invalid=e.message}
console.log(JSON.stringify({hash,invalid}));})().catch(e=>{console.error(e);process.exit(1)});
""")
    assert result["hash"] == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert result["invalid"]
