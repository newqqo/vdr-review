"""Four-language core diagnostics; synthetic inputs only, no case data or network."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts/vdr_review/src"
NODE = shutil.which("node")
LOCALES = ["zh-TW", "en", "ja", "zh-CN"]
CORE_MODULES = ["vdr", "review_state", "project", "media", "stream", "gatehouse", "worker", "dsp"]
pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")


def run_core(code):
    prelude = "const SRC=" + json.dumps(SRC.as_posix()) + ";\n"
    result = subprocess.run([NODE, "-e", prelude + code], check=True, capture_output=True,
                            text=True, encoding="utf-8", timeout=90)
    return json.loads(result.stdout)


def catalogs():
    return run_core("""
const fs=require('node:fs'),vm=require('node:vm'),catalog={};
vm.runInNewContext(fs.readFileSync(SRC+'/i18n_core.js','utf8'),{
 VDRI18n:{addMessages:(locale,messages)=>{catalog[locale]=messages;}}
});
console.log(JSON.stringify(catalog));
""")


def test_core_catalog_has_four_identical_key_and_parameter_sets():
    dictionaries = catalogs()
    assert set(dictionaries) == set(LOCALES)
    keys = set(dictionaries["en"])
    assert len(keys) >= 140
    for locale, entries in dictionaries.items():
        assert set(entries) == keys
        for key, text in entries.items():
            assert key.startswith("core.") and isinstance(text, str) and text
            assert sorted(re.findall(r"\{([a-zA-Z][a-zA-Z0-9_]*)\}", text)) == sorted(
                re.findall(r"\{([a-zA-Z][a-zA-Z0-9_]*)\}", dictionaries["en"][key]))
            if locale == "en":
                assert not re.search(r"[\u3400-\u9fff]", text)
    assert "Meeting Ink" not in json.dumps(dictionaries)


def test_every_referenced_core_key_exists_and_diagnostics_have_no_chinese_fallbacks():
    keys = set(catalogs()["en"])
    for name in CORE_MODULES:
        source = (SRC / (name + ".js")).read_text(encoding="utf-8")
        for key in re.findall(r"(?:ui|coreError|fail|label)\('([^']+)'", source):
            assert "core." + key in keys, (name, key)
        for line in source.splitlines():
            if re.search(r"throw new Error|\bfail\(|\bstatus\(", line) and not line.lstrip().startswith("//"):
                assert not re.search(r"[\u3400-\u9fff]", line), (name, line)


def test_commonjs_core_modules_self_register_without_a_global_i18n_dependency():
    result = run_core("""
const modules=['vdr','review_state','project','media','stream','gatehouse','dsp'];
const loaded=modules.map(name=>!!require(SRC+'/'+name+'.js'));
const I=require(SRC+'/i18n_core.js');
console.log(JSON.stringify({loaded,same:I===require(SRC+'/i18n.js'),
 locale:I.getLocale(),locales:I.availableLocales(),global:!!global.VDRI18n,
 defaultError:(()=>{try{require(SRC+'/vdr.js').readAudioRecord(new Uint8Array(0),'C1')}catch(e){return e.message}})()}));
""")
    assert result["loaded"] == [True] * 7
    assert result["same"] and not result["global"]
    assert result["locale"] == "zh-TW" and set(result["locales"]) == set(LOCALES)
    assert result["defaultError"] == "錄音檔過短或已截斷"


@pytest.mark.parametrize("locale", LOCALES)
def test_errors_follow_current_locale_and_interpolate_evidence_values_literally(locale):
    result = run_core("""
const I=require(SRC+'/i18n_core.js'),V=require(SRC+'/vdr.js'),P=require(SRC+'/project.js'),
 M=require(SRC+'/media.js'),G=require(SRC+'/gatehouse.js'),S=require(SRC+'/stream.js');
I.setLocale(LOCALE);
const error=fn=>{try{fn();return null;}catch(e){return e.message;}};
const channel='X$&{channel}<原始>';
console.log(JSON.stringify({
 short:error(()=>V.readAudioRecord(new Uint8Array(0),'C1')),
 shortExpected:I.t('core.vdr.short'),
 channel:error(()=>V.readAudioRecord(new Uint8Array(34),channel)),
 channelExpected:I.t('core.vdr.channel',{channel}),
 project:error(()=>P.validateImport(null,'a'.repeat(64))),
 projectExpected:I.t('core.validation.format',{label:I.t('core.label.project')}),
 date:error(()=>M.eventWindow([0,86400000],'','12:00',20,0)),
 dateExpected:I.t('core.media.dateRequired'),
 csv:error(()=>G.parseCsvText('')),
 csvExpected:I.t('core.gatehouse.empty'),
 stream:error(()=>S.createStore({audio:{},W0:0,W1:0})),
 streamExpected:I.t('core.stream.time')
}));
""".replace("LOCALE", json.dumps(locale)))
    for key in ["short", "channel", "project", "date", "csv", "stream"]:
        assert result[key] == result[key + "Expected"]
        assert not result[key].startswith("core.")
    assert "X$&{channel}<原始>" in result["channel"]


def test_language_changes_never_change_project_content_or_migration_provenance():
    result = run_core("""
const I=require(SRC+'/i18n_core.js'),P=require(SRC+'/project.js'),hash='a'.repeat(64);
const cue={id:7,start:1,end:2,text:'原文 / original / 原文',spk:'Speaker 講者',st:0,orig:'未核',ovl:false};
const input={version:4,recording_id:hash,t0_utc:'2026-10-23T00:00:00.000Z',state:{A1:[cue]},
 uid:8,spks:[cue.spk],spkColor:{},extra:{events:'原文事件',time_offset_s:0}};
const before=JSON.stringify(input),outputs=[],migrations=[];
for(const locale of ['zh-TW','en','ja','zh-CN']){
 I.setLocale(locale);outputs.push(JSON.stringify(P.createProject(input)));
 migrations.push(JSON.stringify(P.migrateLegacyV2({version:2,t0_utc_s:60,state:{A1:[cue]},uid:8},hash,'2026-10-23')));
}
console.log(JSON.stringify({unchanged:JSON.stringify(input)===before,
 projectsEqual:new Set(outputs).size===1,migrationsEqual:new Set(migrations).size===1,
 cue:JSON.parse(outputs[0]).state.A1[0]}));
""")
    assert result["unchanged"] and result["projectsEqual"] and result["migrationsEqual"]
    assert result["cue"]["text"] == "原文 / original / 原文"
    assert result["cue"]["spk"] == "Speaker 講者"


def test_error_metadata_retranslates_existing_errors_and_nested_field_labels_without_revalidation():
    result = run_core("""
const I=require(SRC+'/i18n_core.js'),V=require(SRC+'/vdr.js'),P=require(SRC+'/project.js'),
 M=require(SRC+'/media.js'),G=require(SRC+'/gatehouse.js'),S=require(SRC+'/stream.js');
I.setLocale('zh-TW');
const caught=fn=>{try{fn();}catch(e){return e;}};
const errors=[caught(()=>V.readAudioRecord(new Uint8Array(0),'C1')),
 caught(()=>P.validateImport(null,'a'.repeat(64))),
 caught(()=>P.createProject({version:4,recording_id:'a'.repeat(64),t0_utc:'2026-10-23T00:00:00.000Z',
 state:{A1:[]},extra:{events:{}}})),
 caught(()=>M.eventWindow([0,86400000],'','12:00',20,0)),
 caught(()=>G.parseCsvText('')),caught(()=>S.createStore({audio:{},W0:0,W1:0}))];
const before=errors.map(e=>e.message),metadata=errors.map(e=>({key:e.i18nKey,params:e.i18nParams,paramKeys:e.i18nParamKeys}));
I.setLocale('ja');const translated=errors.map(e=>I.errorText(e));
const expected=[I.t('core.vdr.short'),
 I.t('core.validation.format',{label:I.t('core.label.project')}),
 I.t('core.validation.text',{label:I.t('core.label.extraField',{field:'events'})}),
 I.t('core.media.dateRequired'),I.t('core.gatehouse.empty'),I.t('core.stream.time')];
console.log(JSON.stringify({before,metadata,translated,expected,names:errors.map(e=>e.name),
 messagesUnchanged:JSON.stringify(before)===JSON.stringify(errors.map(e=>e.message)),
 external:I.errorText(new Error('raw external 原文')),invalidKey:I.errorText({message:'raw',i18nKey:'core.notFound'})}));
""")
    assert result["translated"] == result["expected"]
    assert result["names"] == ["Error"] * 6 and result["messagesUnchanged"]
    assert all(item["key"].startswith("core.") for item in result["metadata"])
    assert result["metadata"][1]["paramKeys"] == {"label": "core.label.project"}
    assert result["metadata"][2]["paramKeys"] == {"label": "core.label.extraField"}
    assert result["metadata"][2]["params"]["field"] == "events"
    assert result["external"] == "raw external 原文" and result["invalidKey"] == "raw"


def test_browser_core_catalog_registration_needs_no_dom_storage_or_network():
    result = run_core("""
const fs=require('node:fs'),vm=require('node:vm'),context={};
for(const key of ['document','localStorage','fetch','navigator','XMLHttpRequest'])
 Object.defineProperty(context,key,{get(){throw Error('Unexpected access: '+key)}});
vm.createContext(context);
for(const file of ['i18n','i18n_core','vdr','project','media','stream','gatehouse'])
 vm.runInContext(fs.readFileSync(SRC+'/'+file+'.js','utf8'),context);
console.log(vm.runInContext(`VDRI18n.setLocale('ja');JSON.stringify({
 message:(()=>{try{VDR.readAudioRecord(new Uint8Array(0),'C1')}catch(e){return e.message}})(),
 expected:VDRI18n.t('core.vdr.short'),locales:VDRI18n.availableLocales()})`,context));
""")
    assert result["message"] == result["expected"] and "録音" in result["message"]
    assert set(result["locales"]) == set(LOCALES)


def test_worker_accepts_locale_without_changing_decode_messages_or_breaking_on_bad_locale():
    result = run_core("""
const fs=require('node:fs'),vm=require('node:vm'),sent=[],context={};
context.self=context;context.postMessage=value=>sent.push(value);
context.G726={decode:()=>new Int16Array(2)};context.DSP={envelope:()=>new Uint8Array(1)};
vm.createContext(context);
for(const file of ['i18n','i18n_core','worker'])vm.runInContext(fs.readFileSync(SRC+'/'+file+'.js','utf8'),context);
const call=locale=>context.onmessage({data:{ch:'C1',rate:8000,codeSize:3,total:2,parts:[],locale}});
call('ja');const accepted=context.VDRI18n.getLocale();call('../bad');call(undefined);
console.log(JSON.stringify({accepted,retained:context.VDRI18n.getLocale(),types:sent.map(x=>x.type),
 channels:sent.map(x=>x.ch),lengths:sent.map(x=>x.pcm.byteLength)}));
""")
    assert result == {"accepted": "ja", "retained": "ja", "types": ["done"] * 3,
                      "channels": ["C1"] * 3, "lengths": [4] * 3}


def test_player_buffering_status_carries_translation_metadata_without_changing_callback_text_contract():
    from test_vdr_stream import run_node

    result = run_node("""
const I=require('""" + SRC.as_posix() + """/i18n_core.js');I.setLocale('en');
const messages=[],st=store([record(0)],60),element=new FakeAudio();
const player=S.createPlayer({element,store:st,channel:'C1',status:(text,metadata)=>messages.push({text,metadata})});
await player.ready;
const before=player.currentTime;I.setLocale('ja');await player.setChannel('C1');
console.log(JSON.stringify({first:messages[0],last:messages.at(-1),before,after:player.currentTime,paused:player.paused}));
player.destroy();
""")
    assert result["first"] == {"text": "Preparing audio segment…", "metadata": {"key": "core.stream.preparing", "params": {}}}
    assert result["last"] == {"text": "", "metadata": None}
    assert result["before"] == result["after"] == 0 and result["paused"]


def test_player_failure_status_retains_core_error_metadata_for_live_translation():
    from test_vdr_stream import run_node

    result = run_node("""
const I=require('""" + SRC.as_posix() + """/i18n_core.js');I.setLocale('en');
const messages=[],st=store([record(0)],60),element=new FakeAudio();
const player=S.createPlayer({element,store:st,channel:'C1',status:(text,metadata)=>messages.push({text,metadata})});
await player.ready;element.error={code:4};element.fire('error');
const failure=messages.at(-1);I.setLocale('ja');
const changed=I.errorText({i18nKey:failure.metadata.key,i18nParams:failure.metadata.params,i18nParamKeys:failure.metadata.paramKeys});
console.log(JSON.stringify({failure,changed,expected:I.t('core.stream.interruptedCode',{code:4}),
 name:player.error.name,key:player.error.i18nKey,paused:player.paused}));player.destroy();
""")
    assert result["failure"]["metadata"] == {"key": "core.stream.interruptedCode", "params": {"code": 4}}
    assert result["changed"] == result["expected"] and result["name"] == "Error"
    assert result["key"] == "core.stream.interruptedCode" and result["paused"]
