"""Progress import previews, merges and explicit replacements use synthetic cases."""

import json

import pytest

from test_vdr_editor import HARNESS, SRC, run_node


def ui(code, setup=""):
    harness = HARNESS.replace("const editor=Editor.init(ctx);", setup + "\nconst editor=Editor.init(ctx);")
    helpers = r"""
function front(){const d=doc();d.state.A1=[{...cue,text:'前段已核'}];return d;}
function back(){const d=doc();d.t0_utc='2026-01-01T00:00:10.000Z';d.state.A1=[{...cue,text:'公司後段',st:0}];return d;}
function snapshot(){return JSON.stringify({state:editor.state(),extra,dirty:editor.dirty(),cache:storage.get('bound-key')});}
function merge(){return $('projectImportMerge').onclick();}
function zeroProgress(count=1){const d=doc();d.version=2;delete d.recording_id;delete d.t0_utc;d.t0_utc_s=0;
 d.state.A1=Array.from({length:count},(_,i)=>({...cue,id:i+1,start:20+i,end:20+i,text:'零長度原文'+i,st:i?0:1}));d.uid=count+1;return d;}
async function importZero(){const d=zeroProgress();await openImport(d);$('projectLegacyDate').value='2026-01-01';$('projectLegacyDate').onchange();
 $('projectLegacyConfirm').checked=true;$('projectLegacyZero').checked=true;$('projectLegacyZero').onchange();$('projectLegacyPrepare').onclick();merge();return d;}
function selectId(id){$('edlist').events.focusin({target:{closest:()=>({dataset:{id:String(id)}})}});}
"""
    return run_node("const SRC=" + json.dumps(SRC) + ";\n" + harness + helpers + code)


def test_dynamic_panel_is_real_registered_dom_and_preview_cancel_are_read_only():
    result = ui(r"""
(async()=>{
 await load(front());const before=snapshot();await openImport(back(),'公司後段.json');
 const preview={same:before===snapshot(),hidden:$('projectImportPanel').hidden,name:$('projectImportFilename').textContent,
  summary:$('projectImportSummary').textContent,mergeDisabled:$('projectImportMerge').disabled,
  replaceTag:$('projectImportReplace').parentNode.tagName,replaceMenuOpen:!!$('projectImportReplace').parentNode.open,
  realPanel:$('edpanel').querySelector('.edhead').adjacent===$('projectImportPanel')};
 $('projectImportCancel').onclick();merge();
 console.log(JSON.stringify({preview,cancelSame:before===snapshot(),hidden:$('projectImportPanel').hidden,alerts}));
})();
""")
    assert result["preview"] == {
        "same": True, "hidden": False, "name": "公司後段.json", "summary": "新增 1 句 · 重複 0 句",
        "mergeDisabled": False, "replaceTag": "DETAILS", "replaceMenuOpen": False, "realPanel": True,
    }
    assert result["cancelSame"] and result["hidden"] and result["alerts"] == []


def test_primary_merge_keeps_front_review_and_current_file_handle_not_incoming_handle():
    result = ui(r"""
(async()=>{
 await load(front());let picks=0,writes=0,foreignWrites=0;
 const currentHandle={name:'目前主檔.json',createWritable:async()=>({write:async()=>writes++,close:async()=>{}})};
 global.showSaveFilePicker=async()=>{picks++;return currentHandle;};await $('saveP').onclick();
 const incoming=back();incoming.extra={...extra,events:'只在公司副檔',time_offset_s:30,time_offset_basis:'公司校時'};
 const incomingHandle={name:'公司後段.json',getFile:async()=>({name:'公司後段.json',text:async()=>JSON.stringify(incoming)}),
  createWritable:async()=>({write:async()=>foreignWrites++,close:async()=>{}})};
 window.showOpenFilePicker=async()=>[incomingHandle];await $('loadP').onclick();const beforeCount=editor.state().A1.length;
 merge();const dirtyAfterMerge=editor.dirty();await $('saveP').onclick();
 console.log(JSON.stringify({beforeCount,cues:editor.state().A1,picks,writes,foreignWrites,dirtyAfterMerge,dirty:editor.dirty(),extra}));
})();
""")
    assert result["beforeCount"] == 1 and result["dirtyAfterMerge"] and not result["dirty"]
    assert [(c["text"], c["start"], c["end"], c["st"]) for c in result["cues"]] == [
        ("前段已核", 0, 2, 1), ("公司後段", 10, 12, 0),
    ]
    assert len({c["id"] for c in result["cues"]}) == 2
    assert result["picks"] == 1 and result["writes"] == 2 and result["foreignWrites"] == 0
    assert result["extra"]["events"] == "" and result["extra"]["time_offset_s"] == 0


@pytest.mark.parametrize("version,expected_picks,expected_opened_writes", [(2, 1, 0), (3, 1, 0), (4, 0, 1)])
def test_replace_saves_converted_legacy_progress_to_a_new_file_not_over_the_opened_one(
        version, expected_picks, expected_opened_writes):
    result = ui(r"""
(async()=>{
 await load(front());let picks=0,openedWrites=0,saved=null;
 const opened=doc();opened.version=%d;opened.state.A1=[{...cue,start:20,end:22,text:'開啟的進度'}];
 if(opened.version===2){delete opened.recording_id;delete opened.t0_utc;opened.t0_utc_s=0;}
 if(opened.version===3)delete opened.recording_id;
 const openedHandle={name:'開啟的進度.json',getFile:async()=>({name:'開啟的進度.json',text:async()=>JSON.stringify(opened)}),
  createWritable:async()=>({write:async()=>openedWrites++,close:async()=>{}})};
 window.showOpenFilePicker=async()=>[openedHandle];await $('loadP').onclick();
 if(opened.version===2){$('projectLegacyDate').value='2026-01-01';$('projectLegacyDate').onchange();
  $('projectLegacyConfirm').checked=true;$('projectLegacyPrepare').onclick();}
 $('projectImportReplace').onclick();
 global.showSaveFilePicker=async()=>{picks++;return {name:'另存主檔.json',createWritable:async()=>({
  write:async b=>{saved=JSON.parse(await b.text());},close:async()=>{}})};};
 await $('saveP').onclick();
 console.log(JSON.stringify({picks,openedWrites,text:editor.state().A1.map(c=>c.text),savedVersion:saved&&saved.version}));
})();
""" % version)
    assert result["text"] == ["開啟的進度"]
    assert result["picks"] == expected_picks and result["openedWrites"] == expected_opened_writes
    assert result["savedVersion"] == (4 if expected_picks else None)


def test_repeated_merge_deduplicates_without_changing_already_saved_case():
    result = ui(r"""
(async()=>{
 await load(front());await openImport(back());merge();
 global.showSaveFilePicker=async()=>({name:'case.json',createWritable:async()=>({write:async()=>{},close:async()=>{}})});
 await $('saveP').onclick();const before=snapshot();await openImport(back());const preview=$('projectImportSummary').textContent;
 merge();console.log(JSON.stringify({preview,same:before===snapshot(),count:editor.state().A1.length,dirty:editor.dirty(),hidden:$('projectImportPanel').hidden}));
})();
""")
    assert result == {"preview": "新增 0 句 · 重複 1 句", "same": True, "count": 2, "dirty": False, "hidden": True}


def test_stale_preview_recomputes_and_requires_second_merge_without_overwriting_new_edits():
    result = ui(r"""
(async()=>{
 await load(front());await openImport(back());editor.state().A1[0].text='前段最新修正';editor.changed();
 const before=snapshot();merge();const first={same:before===snapshot(),count:editor.state().A1.length,
  summary:$('projectImportSummary').textContent,hidden:$('projectImportPanel').hidden};
 merge();console.log(JSON.stringify({first,cues:editor.state().A1,hidden:$('projectImportPanel').hidden,dirty:editor.dirty()}));
})();
""")
    assert result["first"]["same"] and result["first"]["count"] == 1 and not result["first"]["hidden"]
    assert "目前內容已更新，請再確認" in result["first"]["summary"]
    assert [(c["text"], c["st"]) for c in result["cues"]] == [("前段最新修正", 0), ("公司後段", 0)]
    assert result["hidden"] and result["dirty"]


def test_merge_preview_accounts_for_composer_created_after_preview_before_any_commit():
    result = ui(r"""
(async()=>{
 await load(front());await openImport(back());au.currentTime=5;
 $('edcompose').value='預覽後聽打';$('edcompose').events.input({target:$('edcompose')});
 merge();const first={count:editor.state().A1.length,summary:$('projectImportSummary').textContent,hidden:$('projectImportPanel').hidden};
 merge();console.log(JSON.stringify({first,cues:editor.state().A1,value:$('edcompose').value}));
})();
""")
    assert result["first"]["count"] == 2 and not result["first"]["hidden"]
    assert "目前內容已更新" in result["first"]["summary"]
    assert [(c["text"], c["start"], c["end"]) for c in result["cues"]] == [
        ("前段已核", 0, 2), ("預覽後聽打", 5, 7), ("公司後段", 10, 12),
    ]
    assert result["value"] == ""


def test_overlapping_progress_is_previewed_as_plain_text_and_both_versions_survive():
    result = ui(r"""
(async()=>{
 await load(front());const incoming=doc();incoming.state.A1[0]={...cue,text:'<img onerror=alert(1)>',start:1,end:3};
 await openImport(incoming);const conflict=$('projectImportConflictList').children[0],before=editor.state().A1[0].text;
 const preview={summary:$('projectImportSummary').textContent,hidden:$('projectImportConflicts').hidden,
  closed:!$('projectImportConflicts').open,text:conflict.textContent,html:conflict.innerHTML};merge();
 console.log(JSON.stringify({before,preview,cues:editor.state().A1,alerts}));
})();
""")
    assert result["before"] == "前段已核"
    assert result["preview"]["summary"] == "新增 1 句 · 重複 0 句 · 重疊 1 處"
    assert not result["preview"]["hidden"] and result["preview"]["closed"]
    assert "<img onerror=alert(1)>" in result["preview"]["text"] and result["preview"]["html"] == ""
    assert [c["text"] for c in result["cues"]] == ["前段已核", "<img onerror=alert(1)>"]
    assert result["alerts"] == []


def test_foreign_source_or_malformed_progress_only_alerts_and_cannot_mutate_current_case():
    result = ui(r"""
(async()=>{
 await load(front());const before=snapshot(),foreign=back();foreign.recording_id='b'.repeat(64);
 await openImport(foreign);const first={same:before===snapshot(),hidden:$('projectImportPanel').hidden};
 const bad=back();bad.state.A1[0].st=99;await openImport(bad);merge();$('projectImportReplace').onclick();
 console.log(JSON.stringify({first,same:before===snapshot(),hidden:$('projectImportPanel').hidden,alerts}));
})();
""")
    assert result["first"] == {"same": True, "hidden": True}
    assert result["same"] and result["hidden"] and len(result["alerts"]) == 2
    assert "錄音來源不同" in result["alerts"][0] and "核聽狀態無效" in result["alerts"][1]


def test_replace_remains_explicit_and_declining_confirm_retains_preview_and_case():
    result = ui(r"""
(async()=>{
 await load(front());let prompts=[];global.confirm=message=>{prompts.push(message);return false;};
 await openImport(back());const before=snapshot(),noPromptYet=prompts.length===0;
 $('projectImportReplace').onclick();const declined={same:before===snapshot(),hidden:$('projectImportPanel').hidden};
 global.confirm=message=>{prompts.push(message);return true;};$('projectImportReplace').onclick();
 console.log(JSON.stringify({noPromptYet,declined,prompts,cues:editor.state().A1,hidden:$('projectImportPanel').hidden,dirty:editor.dirty()}));
})();
""")
    assert result["noPromptYet"] and result["declined"] == {"same": True, "hidden": False}
    assert len(result["prompts"]) == 2 and all("確定取代目前全部聽打內容" in prompt for prompt in result["prompts"])
    assert [c["text"] for c in result["cues"]] == ["公司後段"]
    assert result["cues"][0]["start"] == 10 and result["hidden"] and not result["dirty"]


def test_v2_requires_valid_full_date_and_manual_source_check_before_preview_and_merge():
    result = ui(r"""
(async()=>{
 await load(front());const old=doc();old.version=2;delete old.recording_id;delete old.t0_utc;
 old.t0_utc_s=3600;old.state.A1[0].text='舊版中後段';const before=snapshot();await openImport(old);
 const initial={legacyHidden:$('projectLegacy').hidden,disabled:$('projectImportMerge').disabled,same:before===snapshot()};
 $('projectLegacyPrepare').onclick();const unchecked=$('projectImportSummary').textContent;
 $('projectLegacyDate').value='';$('projectLegacyDate').onchange();$('projectLegacyConfirm').checked=true;$('projectLegacyPrepare').onclick();
 const invalid={same:before===snapshot(),disabled:$('projectImportMerge').disabled,message:$('projectImportSummary').textContent};
 $('projectLegacyDate').value='2026-01-02';$('projectLegacyDate').onchange();const resetCheck=$('projectLegacyConfirm').checked;
 merge();const beforeCheck=before===snapshot();$('projectLegacyConfirm').checked=true;$('projectLegacyConfirm').onchange();
 $('projectLegacyPrepare').onclick();const ready={disabled:$('projectImportMerge').disabled,summary:$('projectImportSummary').textContent,time:$('projectLegacyTime').textContent};
 merge();console.log(JSON.stringify({initial,unchecked,invalid,resetCheck,beforeCheck,ready,cues:editor.state().A1,alerts}));
})();
""")
    assert result["initial"] == {"legacyHidden": False, "disabled": True, "same": True}
    assert "勾選" in result["unchecked"]
    assert result["invalid"]["same"] and result["invalid"]["disabled"]
    assert result["invalid"]["message"] and not result["resetCheck"] and result["beforeCheck"]
    assert not result["ready"]["disabled"] and "新增 1 句" in result["ready"]["summary"]
    assert "2026-01-02 09:00:00" in result["ready"]["time"]
    assert [(c["text"], c["start"], c["end"]) for c in result["cues"]] == [
        ("前段已核", 0, 2), ("舊版中後段", 90000, 90002),
    ]
    assert result["alerts"] == []


def test_v2_changing_date_or_unchecking_source_invalidates_prepared_preview():
    result = ui(r"""
(async()=>{
 await load(front());const old=doc();old.version=2;delete old.recording_id;delete old.t0_utc;old.t0_utc_s=0;
 await openImport(old);$('projectLegacyDate').value='2026-01-01';$('projectLegacyDate').onchange();
 $('projectLegacyConfirm').checked=true;$('projectLegacyPrepare').onclick();const prepared=!$('projectImportMerge').disabled;
 $('projectLegacyConfirm').checked=false;$('projectLegacyConfirm').onchange();const before=snapshot();merge();
 const unchecked={same:before===snapshot(),disabled:$('projectImportMerge').disabled};
 $('projectLegacyConfirm').checked=true;$('projectLegacyPrepare').onclick();$('projectLegacyDate').value='2026-01-02';$('projectLegacyDate').onchange();merge();
 console.log(JSON.stringify({prepared,unchecked,same:before===snapshot(),disabled:$('projectImportMerge').disabled,checked:$('projectLegacyConfirm').checked}));
})();
""")
    assert result["prepared"] and result["unchecked"] == {"same": True, "disabled": True}
    assert result["same"] and result["disabled"] and not result["checked"]


def test_fourteen_v2_zero_duration_notes_require_separate_opt_in_and_preserve_originals_as_unreviewed():
    result = ui(r"""
(async()=>{
 await load(front());const old=zeroProgress(14),original=JSON.stringify(old),before=snapshot();await openImport(old);
 const initial={hidden:$('projectLegacyZeroLabel').hidden,checked:!!$('projectLegacyZero').checked};
 $('projectLegacyDate').value='2026-01-01';$('projectLegacyDate').onchange();$('projectLegacyConfirm').checked=true;
 $('projectLegacyPrepare').onclick();merge();const denied={same:before===snapshot(),disabled:$('projectImportMerge').disabled,message:$('projectImportSummary').textContent};
 $('projectLegacyZero').checked=true;$('projectLegacyZero').onchange();const stillDisabled=$('projectImportMerge').disabled;merge();
 const beforePrepare=before===snapshot();$('projectLegacyPrepare').onclick();const ready=!$('projectImportMerge').disabled;merge();
 console.log(JSON.stringify({initial,denied,stillDisabled,beforePrepare,ready,cues:editor.state().A1,sourceUnchanged:original===JSON.stringify(old)}));
})();
""")
    assert result["initial"] == {"hidden": False, "checked": False}
    assert result["denied"]["same"] and result["denied"]["disabled"] and "零長度" in result["denied"]["message"]
    assert result["stillDisabled"] and result["beforePrepare"] and result["ready"] and result["sourceUnchanged"]
    assert len(result["cues"]) == 15
    assert result["cues"][0]["text"] == "前段已核" and result["cues"][0]["st"] == 1
    for i, cue in enumerate(result["cues"][1:]):
        assert cue["text"] == "零長度原文" + str(i) and cue["st"] == 0
        assert cue["start"] == 20 + i and abs(cue["end"] - cue["start"] - 0.1) < 1e-8
        assert cue["timing_review"] == {
            "reason": "legacy_zero_duration", "original_start": 20 + i, "original_end": 20 + i,
            "placeholder_seconds": 0.1, "origin_t0_utc": "2026-01-01T00:00:00.000Z",
        }


def test_zero_duration_opt_in_is_hidden_for_normal_legacy_and_invalidates_preview_when_changed():
    result = ui(r"""
(async()=>{
 await importZero();await openImport(zeroProgress());$('projectLegacyDate').value='2026-01-01';$('projectLegacyDate').onchange();
 $('projectLegacyConfirm').checked=true;$('projectLegacyZero').checked=true;$('projectLegacyPrepare').onclick();
 const prepared=!$('projectImportMerge').disabled,before=snapshot();$('projectLegacyZero').checked=false;$('projectLegacyZero').onchange();merge();
 const unchecked={same:before===snapshot(),disabled:$('projectImportMerge').disabled};
 const normal=zeroProgress();normal.state.A1[0].end=22;await openImport(normal);
 console.log(JSON.stringify({prepared,unchecked,normalHidden:$('projectLegacyZeroLabel').hidden,normalChecked:!!$('projectLegacyZero').checked}));
})();
""")
    assert result["prepared"] and result["unchecked"] == {"same": True, "disabled": True}
    assert result["normalHidden"] and not result["normalChecked"]


def test_timing_review_survives_text_edit_and_blocks_status_buttons_and_alt_enter():
    result = ui(r"""
(async()=>{
 await importZero();const c=editor.state().A1[0],marker=JSON.stringify(c.timing_review);selectId(c.id);
 const row=$('edlist').children.find(r=>+r.dataset.id===c.id),rowHasFlag=row.innerHTML.includes('>時間待校</span>');
 $('edlist').events.input({target:{value:'原文人工修正',classList:{contains:()=>true},style:{},scrollHeight:20,
  closest:()=>({dataset:{id:String(c.id)}})}});
 const afterText={text:c.text,markerSame:marker===JSON.stringify(c.timing_review),st:c.st};const blocked=[];
 for(const st of [1,2,3]){$('edtool').events.click({target:{closest:()=>({dataset:{st:String(st)}})}});blocked.push(c.st);}
 listeners.keydown({target:{tagName:'TEXTAREA',closest:()=>null},key:'Enter',altKey:true,preventDefault(){}});
 console.log(JSON.stringify({rowHasFlag,afterText,blocked,afterAlt:c.st,message:$('edsave').textContent,origin:c.timing_origin||null}));
})();
""")
    assert result["rowHasFlag"]
    assert result["afterText"] == {"text": "原文人工修正", "markerSame": True, "st": 0}
    assert result["blocked"] == [0, 0, 0] and result["afterAlt"] == 0 and result["origin"] is None
    assert "先調整本句起迄時間" in result["message"]


def test_adjusting_time_clears_pending_flag_preserves_origin_then_allows_review():
    result = ui(r"""
(async()=>{
 await importZero();const c=editor.state().A1[0],marker=JSON.parse(JSON.stringify(c.timing_review));selectId(c.id);
 $('te').value='00:22.00';$('te').events.change({target:$('te')});
 const adjusted={start:c.start,end:c.end,st:c.st,hasPending:Object.hasOwn(c,'timing_review'),origin:c.timing_origin};
 $('edtool').events.click({target:{closest:()=>({dataset:{st:'1'}})}});
 console.log(JSON.stringify({marker,adjusted,reviewed:c.st,cached:JSON.parse(storage.get('bound-key')).state.A1[0]}));
})();
""")
    assert result["adjusted"] == {
        "start": 20, "end": 22, "st": 0, "hasPending": False,
        "origin": {**result["marker"], "resolution": "time_adjusted_in_editor"},
    }
    assert result["reviewed"] == 1 and result["cached"]["st"] == 1
    assert "timing_review" not in result["cached"] and result["cached"]["timing_origin"] == result["adjusted"]["origin"]


def test_pending_timing_is_explicit_in_srt_csv_and_case_export_not_a_fake_review():
    result = ui(r"""
(async()=>{
 await importZero();let entries;VDRProject.zipEntries=async list=>{entries=list;return new Blob(['synthetic']);};
 await $('exBundle').onclick();const saved=JSON.parse(entries[0].data);
 console.log(JSON.stringify({srt:entries[2].data,csv:entries[1].data,cue:saved.state.A1[0],dirty:editor.dirty()}));
})();
""")
    assert "零長度原文0〔時間待校〕" in result["srt"] and "00:00:20,000 --> 00:00:20,100" in result["srt"]
    assert "時間待校（舊檔零長度，0.1秒暫置）" in result["csv"]
    assert result["cue"]["text"] == "零長度原文0" and result["cue"]["st"] == 0 and result["cue"]["timing_review"]
    assert result["dirty"]


def test_v4_merge_history_preserves_source_t0_and_fractional_time_shift_not_display_correction():
    result = ui(r"""
(async()=>{
 await load(front());const incoming=back();incoming.t0_utc='2026-01-01T00:00:10.250Z';
 incoming.state.A1[0].start=1;incoming.state.A1[0].end=3;
 incoming.extra={...extra,time_offset_s:600,time_offset_basis:'副檔顯示校時'};
 const before=snapshot();await openImport(incoming);const previewUnchanged=before===snapshot();merge();
 const cached=JSON.parse(storage.get('bound-key')),history=cached.import_history;
 await openImport(incoming);merge();const repeated=JSON.parse(storage.get('bound-key'));
 console.log(JSON.stringify({previewUnchanged,t0:cached.t0_utc,history,cues:cached.state.A1,
  offset:cached.extra.time_offset_s,repeatedHistory:repeated.import_history,alerts}));
})();
""")
    assert result["previewUnchanged"] and result["alerts"] == []
    assert result["t0"] == "2026-01-01T00:00:00.000Z" and result["offset"] == 0
    assert [(c["start"], c["end"]) for c in result["cues"]] == [(0, 2), (11.25, 13.25)]
    assert result["history"] == [{
        "t0_utc": "2026-01-01T00:00:10.250Z", "timeShiftSeconds": 10.25,
        "added": 1, "duplicates": 0, "conflicts": 0,
    }]
    assert result["repeatedHistory"] == result["history"]


def test_v2_merge_history_keeps_confirmed_source_day_and_nonzero_utc_day_seconds():
    result = ui(r"""
(async()=>{
 await load(front());const incoming=doc();incoming.version=2;delete incoming.recording_id;delete incoming.t0_utc;
 incoming.t0_utc_s=3600.125;incoming.state.A1[0]={...cue,start:5,end:7,text:'次日公司段'};
 await openImport(incoming);$('projectLegacyDate').value='2026-01-02';$('projectLegacyDate').onchange();
 $('projectLegacyConfirm').checked=true;$('projectLegacyPrepare').onclick();merge();
 const cached=JSON.parse(storage.get('bound-key'));
 console.log(JSON.stringify({t0:cached.t0_utc,cues:cached.state.A1,history:cached.import_history,alerts}));
})();
""")
    assert result["alerts"] == [] and result["t0"] == "2026-01-01T00:00:00.000Z"
    assert [(c["start"], c["end"]) for c in result["cues"]] == [(0, 2), (90005.125, 90007.125)]
    assert len(result["history"]) == 1
    entry = result["history"][0]
    assert entry["t0_utc"] == "2026-01-02T01:00:00.125Z" and entry["timeShiftSeconds"] == 90000.125
    assert (entry["added"], entry["duplicates"], entry["conflicts"]) == (1, 0, 0)
    assert entry["migration"]["from_version"] == 2
    assert entry["migration"]["confirmed_utc_date"] == "2026-01-02"
    assert entry["migration"]["original_t0_utc_s"] == 3600.125


@pytest.mark.parametrize("action", ["dup", "auto"])
def test_pending_timing_cannot_be_recreated_without_flag_then_marked_reviewed(action):
    result = ui(r"""
(async()=>{
 await importZero();const c=editor.state().A1[0];c.text='第一句。第二句。';editor.changed();selectId(c.id);
 const before=snapshot();$('edtool').events.click({target:{closest:()=>({dataset:{a:ACTION}})}});
 const blocked={same:before===snapshot(),message:$('edsave').textContent};
 $('edtool').events.click({target:{closest:()=>({dataset:{st:'1'}})}});
 listeners.keydown({target:{tagName:'TEXTAREA',closest:()=>null},key:'Enter',altKey:true,preventDefault(){}});
 console.log(JSON.stringify({blocked,cues:editor.state().A1,cached:JSON.parse(storage.get('bound-key')).state.A1,
  message:$('edsave').textContent}));
})();
""".replace("ACTION", json.dumps(action)), setup="document.body.classList.contains=k=>k==='editing';")
    assert result["blocked"]["same"] and "先調整時間待校句子的起迄" in result["blocked"]["message"]
    assert len(result["cues"]) == 1 and result["cached"] == result["cues"]
    assert result["cues"][0]["text"] == "第一句。第二句。" and result["cues"][0]["st"] == 0
    assert result["cues"][0]["timing_review"]["reason"] == "legacy_zero_duration"
    assert "先調整本句起迄時間" in result["message"]


@pytest.mark.parametrize("action,selected", [("merge", 1), ("mergeP", 100)])
def test_merging_normal_neighbor_cannot_absorb_pending_timing_and_drop_its_flag(action, selected):
    result = ui(r"""
(async()=>{
 await load(front());await importZero();
 editor.state().A1.push({...cue,id:100,start:30,end:32,text:'右側正常句',st:1});editor.changed();selectId(SELECTED);
 const before=snapshot();$('edtool').events.click({target:{closest:()=>({dataset:{a:ACTION}})}});
 const blocked={same:before===snapshot(),message:$('edsave').textContent};
 const pending=editor.state().A1.find(c=>c.timing_review);selectId(pending.id);
 $('edtool').events.click({target:{closest:()=>({dataset:{st:'1'}})}});
 console.log(JSON.stringify({blocked,cues:editor.state().A1,cached:JSON.parse(storage.get('bound-key')).state.A1,
  message:$('edsave').textContent}));
})();
""".replace("ACTION", json.dumps(action)).replace("SELECTED", str(selected)),
                setup="document.body.classList.contains=k=>k==='editing';")
    assert result["blocked"]["same"] and "先調整時間待校句子的起迄" in result["blocked"]["message"]
    assert result["cached"] == result["cues"] and len(result["cues"]) == 3
    assert [(c["text"], c["st"]) for c in result["cues"]] == [
        ("前段已核", 1), ("零長度原文0", 0), ("右側正常句", 1),
    ]
    assert result["cues"][1]["timing_review"]["original_start"] == 20
    assert "先調整本句起迄時間" in result["message"]
