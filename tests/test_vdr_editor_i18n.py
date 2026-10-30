"""Four-language editor UI, using synthetic state only; no evidence or browser."""

import json
import re
from pathlib import Path

import pytest

from test_vdr_editor import SRC, run_node
from test_vdr_workbench import in_row_workbench


def in_catalog(code):
    return run_node("const I=require(" + json.dumps(SRC + "/i18n.js") + ");\n"
                    "const catalogs=require(" + json.dumps(SRC + "/i18n_editor.js") + ");\n" + code)


def test_editor_catalogs_have_identical_named_keys_and_interpolation_parameters():
    result = in_catalog(r"""
const locales=['zh-TW','en','ja','zh-CN'],keys=Object.keys(catalogs['zh-TW']).sort(),params=s=>[...s.matchAll(/\{([a-z][a-z0-9_]*)\}/gi)].map(m=>m[1]).sort();
console.log(JSON.stringify({keys,locales:Object.keys(catalogs),frozen:Object.values(catalogs).every(Object.isFrozen),
 mismatches:keys.flatMap(key=>locales.filter(locale=>JSON.stringify(params(catalogs[locale][key]))!==JSON.stringify(params(catalogs['zh-TW'][key]))).map(locale=>({key,locale}))),
 keyParity:locales.every(locale=>JSON.stringify(Object.keys(catalogs[locale]).sort())===JSON.stringify(keys)),
 registered:locales.every(locale=>keys.every(key=>I.t(key,{},locale)===catalogs[locale][key]))}));
""")
    assert result["locales"] == ["zh-TW", "en", "ja", "zh-CN"]
    assert len(result["keys"]) >= 180
    assert result["keyParity"] and result["registered"] and result["frozen"]
    assert result["mismatches"] == []


def test_every_literal_editor_translation_key_is_registered_in_all_four_languages():
    source = Path(SRC, "editor.js").read_text(encoding="utf-8")
    keys = set(re.findall(r"(?:tr|editorError)\('([A-Za-z][A-Za-z0-9]+)'", source))
    keys |= set(re.findall(r"staticText\([^,()]+,'([A-Za-z][A-Za-z0-9]+)'\)", source))
    result = in_catalog("console.log(JSON.stringify(Object.keys(catalogs['zh-TW'])));")
    registered = {key.removeprefix("editor.") for key in result}
    assert keys <= registered


@pytest.mark.parametrize("locale,needle", [("zh-TW", "整份未匯入"), ("en", "Nothing was imported"),
                                             ("ja", "インポートしていません"), ("zh-CN", "整份未导入")])
def test_commonjs_parser_errors_are_localized_and_never_rewrite_valid_subtitle_content(locale, needle):
    result = run_node("const E=require(" + json.dumps(SRC + "/editor.js") + ");\n"
                      "const I=require(" + json.dumps(SRC + "/i18n.js") + ");\n"
                      "I.setLocale(" + json.dumps(locale) + ");\n" + r"""
const source='1\n00:00:01,000 --> 00:00:02,000\n船長 / 左满舵 / 面舵 / Play';
let error;try{E.parseSrt('1\n00:00:02,000 --> 00:00:01,000\n不要改');}catch(e){error={message:e.message,key:e.i18nKey,params:e.i18nParams};}
console.log(JSON.stringify({error,parsed:E.parseSrt(source),source}));
""")
    assert needle in result["error"]["message"]
    assert result["error"]["key"] == "editor.srtTimeInvalid"
    assert result["error"]["params"] == {"number": 1}
    assert result["parsed"] == [{"s": 1, "e": 2, "text": "船長 / 左满舵 / 面舵 / Play", "no": "1"}]


def test_live_error_metadata_retranslates_nested_labels_without_reexecuting_validation():
    result = run_node("const E=require(" + json.dumps(SRC + "/editor.js") + ");\n"
                      "const I=require(" + json.dumps(SRC + "/i18n.js") + ");\n" + r"""
const receipt={kind:'vdr-transcription-export',version:1,recording_id:'a'.repeat(64),channel:'A1',source_start_utc:'bad'};
let error;try{E.validateTranscriptionReceipt(receipt,'a'.repeat(64),['A1']);}catch(e){error=e;}
const values={};for(const locale of ['zh-TW','en','ja','zh-CN']){I.setLocale(locale);values[locale]=I.errorText(error);}
console.log(JSON.stringify({values,key:error.i18nKey,paramKeys:error.i18nParamKeys,original:error.message}));
""")
    assert result["key"] == "editor.utcZRequired"
    assert result["paramKeys"] == {"label": "editor.sourceStart"}
    assert result["values"]["en"].startswith("Source audio start requires")
    assert "原音の開始" in result["values"]["ja"]
    assert "原音起点" in result["values"]["zh-CN"]
    assert result["original"] == result["values"]["zh-TW"]


def test_switching_language_during_composition_keeps_native_targets_caret_audio_and_case_bytes():
    result = in_row_workbench(r"""
(async()=>{
 await settle();au.currentTime=12;composer.events.compositionstart();type('船長：左滿舵 / 日本語');
 const cueId=editor.state().A1[0].id,rowBefore=subtitleRow(cueId),textBefore=subtitleText(cueId);
 document.activeElement=composer;composer.selectionStart=2;composer.selectionEnd=6;
 $('ts').value='time input in progress';$('te').value='end input in progress';$('edlist').scrollTop=93;
 const before=JSON.stringify(editor.state()),cacheBefore=storage.get(ctx.KEY),dirty=editor.dirty(),requestCount=requests.length;
 const labels=[];for(const locale of ['en','ja','zh-CN','zh-TW']){VDRI18n.setLocale(locale);labels.push({locale,apply:$('srtImportApply').textContent,progress:$('edprog').textContent});}
 const enter=key();
 console.log(JSON.stringify({sameRow:rowBefore===subtitleRow(cueId),sameTextarea:textBefore===subtitleText(cueId),
  sameState:before===JSON.stringify(editor.state()),sameCache:cacheBefore===storage.get(ctx.KEY),sameDirty:dirty===editor.dirty(),
  sameFocus:document.activeElement===composer,caret:[composer.selectionStart,composer.selectionEnd],text:composer.value,
  ts:$('ts').value,te:$('te').value,scroll:$('edlist').scrollTop,time:au.currentTime,paused:au.paused,
  playCalls,pauses,requestsBefore:requestCount,requestsAfter:requests.length,enterPrevented:enter.prevented,labels}));
})();
""")
    assert all(result[key] for key in ["sameRow", "sameTextarea", "sameState", "sameCache", "sameDirty", "sameFocus"])
    assert result["caret"] == [2, 6] and result["text"] == "船長：左滿舵 / 日本語"
    assert result["ts"] == "time input in progress" and result["te"] == "end input in progress"
    assert result["scroll"] == 93 and result["time"] == 12 and result["paused"]
    assert result["playCalls"] == 0 and result["pauses"] == [] and not result["enterPrevented"]
    assert result["requestsBefore"] == result["requestsAfter"]
    assert [item["apply"] for item in result["labels"]] == ["Confirm alignment and import", "時刻合わせを確認してインポート", "确认对齐并导入", "確認對齊並匯入"]


def test_playing_and_paused_badges_retranslate_without_replaying_or_replacing_rows():
    result = in_row_workbench(r"""
(async()=>{
 await rowFixture();au.currentTime=11;await au.play();const rowBefore=subtitleRow(1),textBefore=subtitleText(1);
 document.activeElement=textBefore;textBefore.selectionStart=1;textBefore.selectionEnd=2;
 const values=[];for(const locale of ['en','ja','zh-CN']){VDRI18n.setLocale(locale);values.push({locale,badge:rowBefore.querySelector('.cue-playstate').textContent,button:$('edpp').textContent,key:$('edpp').dataset.i18n});}
 au.pause();VDRI18n.setLocale('en');
 console.log(JSON.stringify({values,pausedBadge:rowBefore.querySelector('.cue-playstate').textContent,pausedButton:$('edpp').textContent,
  pausedKey:$('edpp').dataset.i18n,sameRow:rowBefore===subtitleRow(1),sameTextarea:textBefore===subtitleText(1),
  focused:document.activeElement===textBefore,caret:[textBefore.selectionStart,textBefore.selectionEnd],time:au.currentTime,playCalls}));
})();
""")
    assert [item["badge"] for item in result["values"]] == ["▶ Playing", "▶ 再生中", "▶ 播放中"]
    assert [item["key"] for item in result["values"]] == ["ui.pause"] * 3
    assert result["pausedBadge"] == "⏸ Current position" and result["pausedButton"] == "▶ Resume"
    assert result["pausedKey"] == "ui.resume" and result["sameRow"] and result["sameTextarea"] and result["focused"]
    assert result["caret"] == [1, 2] and result["time"] == 11 and result["playCalls"] == 1


def test_srt_alignment_error_and_disabled_guard_survive_language_switch_with_manual_input_intact():
    result = in_row_workbench(r"""
(async()=>{
 const bytes=new TextEncoder().encode('1\n00:00:01,000 --> 00:00:02,000\n原文を保持');
 await $('imSrt').onchange({target:{files:[{name:'原始_字幕.srt',size:bytes.byteLength,arrayBuffer:async()=>bytes.buffer}],value:'x'}});
 $('srtLocalStart').value='08:00';$('srtLocalStart').oninput();const before=JSON.stringify(editor.state()),errorBefore=$('srtImportError').textContent;
 const values=[];for(const locale of ['en','ja','zh-CN']){VDRI18n.setLocale(locale);values.push({locale,error:$('srtImportError').textContent,disabled:$('srtImportApply').disabled,
  date:$('srtLocalStart').value,origin:$('srtImportOrigin').value,channel:$('srtImportChannel').value});}
 console.log(JSON.stringify({values,errorBefore,same:before===JSON.stringify(editor.state()),hidden:$('srtImportPanel').hidden}));
})();
""")
    assert result["same"] and not result["hidden"] and "不會猜日期" in result["errorBefore"]
    assert all(item["disabled"] and item["date"] == "08:00" and item["origin"] == "manual" and item["channel"] == "A1" for item in result["values"])
    assert "Enter the full date" in result["values"][0]["error"]
    assert "日付は推定しません" in result["values"][1]["error"]
    assert "不会猜日期" in result["values"][2]["error"]


def test_project_preview_retranslates_without_importing_or_staling_then_merge_keeps_both_parts():
    result = in_row_workbench(r"""
(async()=>{
 editor.state().A1.push({...cue,text:'前半已核',spk:'船長'});editor.changed();
 const incoming=doc();incoming.state.A1=[{...cue,id:8,start:30,end:32,text:'後半公司進度',spk:'操船者'}];incoming.uid=9;
 await openImport(incoming);const before=JSON.stringify(editor.state()),cacheBefore=storage.get(ctx.KEY);
 const values=[];for(const locale of ['en','ja','zh-CN']){VDRI18n.setLocale(locale);values.push({locale,summary:$('projectImportSummary').textContent,merge:$('projectImportMerge').textContent,disabled:$('projectImportMerge').disabled});}
 const same=before===JSON.stringify(editor.state())&&cacheBefore===storage.get(ctx.KEY);$('projectImportMerge').onclick();
 console.log(JSON.stringify({same,values,texts:editor.state().A1.map(c=>c.text),speakers:editor.state().A1.map(c=>c.spk),panelHidden:$('projectImportPanel').hidden,dirty:editor.dirty()}));
})();
""")
    assert result["same"] and all(not item["disabled"] for item in result["values"])
    assert result["values"][0]["summary"] == "1 new · 0 duplicates"
    assert result["values"][1]["summary"] == "追加 1 件 · 重複 0 件"
    assert result["values"][2]["summary"] == "新增 1 句 · 重复 0 句"
    assert result["texts"] == ["前半已核", "後半公司進度"] and result["speakers"] == ["船長", "操船者"]
    assert result["panelHidden"] and result["dirty"]


@pytest.mark.parametrize("locale,first,pilot", [("zh-TW", "船長", "引水人"), ("en", "Master", "Pilot"),
                                                ("ja", "船長", "水先人"), ("zh-CN", "船长", "引航员")])
def test_new_case_starter_roles_follow_initial_language_but_never_rename_on_switch(locale, first, pilot):
    result = in_row_workbench(r"""
(async()=>{
 editor.changed();const before=JSON.parse(storage.get(ctx.KEY)).spks;
 VDRI18n.setLocale('ja');VDRI18n.setLocale('en');editor.changed();const after=JSON.parse(storage.get(ctx.KEY)).spks;
 console.log(JSON.stringify({before,after}));
})();
""", "VDRI18n.setLocale(" + json.dumps(locale) + ");")
    assert result["before"][:2] == [first, pilot]
    assert result["after"] == result["before"]


def test_english_handover_has_localized_notes_and_headers_but_exact_original_data():
    result = in_row_workbench(r"""
(async()=>{
 const data=doc();data.spks=['船長／Captain'];data.state.A1[0]={...cue,text:'=原文 / 面舵 / 左满舵',orig:'原音文言',spk:'船長／Captain'};await load(data);
 VDRI18n.setLocale('en');let entries;const original=VDRProject.zipEntries;VDRProject.zipEntries=async list=>{entries=list;return original(list);};
 await $('exBundle').onclick();const saved=JSON.parse(entries[0].data);
 console.log(JSON.stringify({names:entries.map(e=>e.name),readme:entries[3].data,csv:entries[1].data,srt:entries[2].data,
  text:saved.state.A1[0].text,orig:saved.state.A1[0].orig,speaker:saved.state.A1[0].spk,spks:saved.spks,recording:saved.recording_id,t0:saved.t0_utc}));
})();
""")
    assert result["names"][:4] == ["SYNTHETIC_review_case.vdr.json", "all_channels_review.csv", "A1_current_range.srt", "handover_notes.txt"]
    assert "Maritime audio-review handover" in result["readme"] and "Original VDR evidence is not included" in result["readme"]
    assert '"Channel","No.","Playback start"' in result["csv"] and '"\'=原文 / 面舵 / 左满舵"' in result["csv"]
    assert result["text"] == "=原文 / 面舵 / 左满舵" and result["orig"] == "原音文言"
    assert result["speaker"] == "船長／Captain" and result["spks"] == ["船長／Captain"]
    assert "船長／Captain：=原文 / 面舵 / 左满舵" in result["srt"]
    assert result["recording"] == "a" * 64 and result["t0"] == "2026-01-01T00:00:00.000Z"
