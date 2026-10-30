"""UI-only translation foundations; no audio, case evidence, browser or network."""

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts/vdr_review/src/i18n.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")


def run_i18n(code):
    script = "const I=require(" + json.dumps(MODULE.as_posix()) + ");\n" + code
    proc = subprocess.run([NODE, "-e", script], check=True, capture_output=True, text=True, encoding="utf-8")
    return json.loads(proc.stdout)


def test_default_is_deterministic_traditional_chinese_with_english_fallback_and_named_keys():
    result = run_i18n("""
console.log(JSON.stringify({locale:I.getLocale(),default:I.DEFAULT_LOCALE,fallback:I.FALLBACK_LOCALE,
 locales:I.availableLocales(),play:I.t('action.play'),playing:I.t('playback.playing'),
 current:I.t('playback.currentPosition'),srt:I.t('srt.title'),unknown:I.t('unknown.key')}));
""")
    assert result == {"locale": "zh-TW", "default": "zh-TW", "fallback": "en", "locales": ["en", "ja", "zh-CN", "zh-TW"],
                      "play": "播放", "playing": "▶ 播放中", "current": "⏸ 目前位置", "srt": "匯出音檔／匯入 SRT", "unknown": "unknown.key"}


@pytest.mark.parametrize("locale,normalized", [("en", "en"), ("en-GB", "en-GB"), ("EN_us", "en-US"), ("fr-FR", "fr-FR")])
def test_english_or_unsupported_locale_uses_english_not_a_guessed_user_language(locale, normalized):
    result = run_i18n("const T=I.create({locale:" + json.dumps(locale) + "});console.log(JSON.stringify({locale:T.getLocale(),play:T.t('action.play')}));")
    assert result == {"locale": normalized, "play": "Play"}


def test_unbound_translation_function_tracks_locale_without_this_or_affecting_other_instances():
    result = run_i18n("""
const a=I.create(),b=I.create(),t=a.t;const before=t('action.save');
a.setLocale('en');const english=t('action.save');a.setLocale('zh_tw');
console.log(JSON.stringify({before,english,after:t('action.save'),other:b.t('action.save'),global:I.t('action.save'),
 override:t('action.save',{},'en'),locale:a.getLocale()}));
""")
    assert result == {"before": "儲存案件", "english": "Save case", "after": "儲存案件", "other": "儲存案件",
                      "global": "儲存案件", "override": "Save case", "locale": "zh-TW"}


def test_partial_extension_uses_exact_locale_then_language_then_english_and_unknown_key():
    result = run_i18n("""
const T=I.create({locale:'fr-FR',messages:{fr:{'action.play':'Lire'},'fr-FR':{'common.cancel':'Annuler'}}});
T.addMessages('en',{'extension.help':'Help {topic}'});
console.log(JSON.stringify({play:T.t('action.play'),cancel:T.t('common.cancel'),pause:T.t('action.pause'),
 help:T.t('extension.help',{topic:'SRT'}),unknown:T.t('extension.none',{name:'do not insert'}),
 known:T.has('action.pause'),unknownHas:T.has('extension.none'),locales:T.availableLocales(),global:I.has('extension.help')}));
""")
    assert result == {"play": "Lire", "cancel": "Annuler", "pause": "Pause", "help": "Help SRT", "unknown": "extension.none",
                      "known": True, "unknownHas": False, "locales": ["en", "fr", "fr-FR", "ja", "zh-CN", "zh-TW"], "global": False}


def test_explicit_fallback_locale_can_be_extended_but_english_remains_final_fallback():
    result = run_i18n("""
const T=I.create({locale:'zz',fallbackLocale:'fr',messages:{fr:{'action.play':'Lire'}}});
console.log(JSON.stringify({play:T.t('action.play'),pause:T.t('action.pause'),missing:T.t('missing.key')}));
""")
    assert result == {"play": "Lire", "pause": "Pause", "missing": "missing.key"}


def test_named_interpolation_is_literal_nonrecursive_and_keeps_missing_or_object_values_visible():
    result = run_i18n("""
const T=I.create();T.addMessages('zh-TW',{'extension.line':'{name}/{name}/{count}/{flag}/{missing}/{cue}'});
const text=T.t('extension.line',{name:'$&{count}<img>',count:0,flag:false,cue:{text:'not stringified'}});
console.log(JSON.stringify({text,progress:T.t('review.progress',{channel:'A1',done:2,total:8}),
 imported:T.t('srt.imported',{count:3,channel:'C1'},'en')}));
""")
    assert result == {"text": "$&{count}<img>/$&{count}<img>/0/false/{missing}/{cue}",
                      "progress": "A1　已核 2／8", "imported": "Imported 3 sentences into C1"}


def test_ui_translation_never_mutates_original_cue_speaker_or_project_fields():
    result = run_i18n("""
const cue=Object.freeze({id:1,text:'播放 / Play / <b>原文</b>',orig:'未核',spk:'Delete',start:10,end:12,st:1});
const project=Object.freeze({version:4,recording_id:'a'.repeat(64),t0_utc:'2026-10-23T00:00:00.000Z',
 state:Object.freeze({A1:Object.freeze([cue])}),audio_file:'Play_原音.wav'});
const before=JSON.stringify(project),T=I.create({locale:'en'});
T.t('srt.imported',{count:1,channel:'A1',project,cue});T.t('review.unassignedSpeaker',{text:cue.text,spk:cue.spk});
console.log(JSON.stringify({same:JSON.stringify(project)===before,cue,keys:Object.keys(project),locale:T.getLocale()}));
""")
    assert result["same"] and result["locale"] == "en"
    assert result["cue"] == {"id": 1, "text": "播放 / Play / <b>原文</b>", "orig": "未核", "spk": "Delete", "start": 10, "end": 12, "st": 1}
    assert result["keys"] == ["version", "recording_id", "t0_utc", "state", "audio_file"]


def test_exported_dictionaries_are_frozen_and_extension_copies_caller_dictionary():
    result = run_i18n("""
const input={'extension.name':'First'},T=I.create();T.addMessages('en',input);input['extension.name']='Changed';
console.log(JSON.stringify({apiFrozen:Object.isFrozen(I),outerFrozen:Object.isFrozen(I.messages),
 innerFrozen:Object.values(I.messages).every(Object.isFrozen),value:T.t('extension.name',{},'en'),
 global:I.has('extension.name')}));
""")
    assert result == {"apiFrozen": True, "outerFrozen": True, "innerFrozen": True, "value": "First", "global": False}


def test_core_language_keys_and_placeholders_match_and_do_not_embed_transcription_brand():
    dictionaries = run_i18n("console.log(JSON.stringify(I.messages));")
    assert set(dictionaries) == {"zh-TW", "en", "ja", "zh-CN"}
    assert all(set(values) == set(dictionaries["en"]) for values in dictionaries.values())
    required = {"action.confirmDeleteCue", "action.chooseCue", "action.playCue", "action.resume", "action.delete",
                "playback.playing", "playback.currentPosition", "playback.playingReplayLabel", "playback.playCueLabel",
                "review.reviewed", "review.unreviewed", "review.partial", "review.unclear", "review.unassignedSpeaker",
                "workbench.inputPlaceholder", "workbench.inputTitle", "srt.title", "srt.import", "srt.alignment"}
    assert required <= set(dictionaries["en"])
    for values in dictionaries.values():
        for key, value in values.items():
            assert isinstance(value, str) and value and dictionaries["en"][key]
            assert sorted(re.findall(r"\{([a-zA-Z][a-zA-Z0-9_]*)\}", value)) == sorted(re.findall(r"\{([a-zA-Z][a-zA-Z0-9_]*)\}", dictionaries["en"][key]))
    assert "Meeting Ink" not in json.dumps(dictionaries)


def test_malformed_or_prototype_like_dictionary_updates_fail_before_installing_any_entry():
    result = run_i18n("""
const T=I.create(),before=T.t('action.play'),invalid=[null,[],{'action.play':3},
 {'action.play':'Wrong','bad..key':'bad'},JSON.parse('{"action.play":"Wrong","__proto__":"bad"}'),{'constructor':'bad'}];
const rejected=invalid.map(messages=>{try{T.addMessages('fr',messages);return false;}catch(e){return e instanceof TypeError;}});
console.log(JSON.stringify({rejected,unchanged:T.t('action.play')===before,locales:T.availableLocales(),polluted:{}.bad||null}));
""")
    assert result == {"rejected": [True] * 6, "unchanged": True, "locales": ["en", "ja", "zh-CN", "zh-TW"], "polluted": None}


def test_invalid_locale_changes_are_rejected_without_changing_current_language():
    result = run_i18n("""
const T=I.create(),rejected=['','../en','__proto__',null,12].map(name=>{
 try{T.setLocale(name);return false;}catch(e){return e instanceof TypeError;}});
console.log(JSON.stringify({rejected,locale:T.getLocale(),play:T.t('action.play')}));
""")
    assert result == {"rejected": [True] * 5, "locale": "zh-TW", "play": "播放"}


def test_browser_export_has_no_dom_storage_navigator_or_network_side_effects():
    result = run_i18n("""
const fs=require('node:fs'),vm=require('node:vm'),sandbox={self:{}};
for(const name of ['document','localStorage','sessionStorage','navigator','fetch','XMLHttpRequest','WebSocket'])
 Object.defineProperty(sandbox,name,{get(){throw Error('Unexpected global access: '+name);}});
vm.runInNewContext(fs.readFileSync(require.resolve(""" + json.dumps(MODULE.as_posix()) + """),'utf8'),sandbox);
console.log(JSON.stringify({exported:typeof sandbox.self.VDRI18n.t,locale:sandbox.self.VDRI18n.getLocale(),
 play:sandbox.self.VDRI18n.t('action.play'),keys:Object.keys(sandbox.self)}));
""")
    assert result == {"exported": "function", "locale": "zh-TW", "play": "播放", "keys": ["VDRI18n"]}


def test_builder_loads_ui_dictionary_first_and_core_dictionary_in_worker():
    path = ROOT / "scripts/vdr_review/build.py"
    spec = importlib.util.spec_from_file_location("vdr_i18n_build_test", path)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    assert builder.MAIN_MODULES[0] == "i18n.js" and builder.MAIN_MODULES.count("i18n.js") == 1
    assert builder.WORKER_MODULES[:2] == ("i18n.js", "i18n_core.js")
    output = builder.build()
    assert output.index("root.VDRI18n=api") < output.index("function createFileSession(")
    source = path.read_text(encoding="utf-8")
    assert "任意支援 SRT 的轉錄工具" in source and "Meeting Ink" not in source
