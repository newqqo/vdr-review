"""All-channel activity navigation is bounded and never labels unknown audio quiet."""

import json
import shutil

import pytest

from test_vdr_editor import SRC, run_node
from test_vdr_multitrack import in_multitrack

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

KNOWN = "for(const ch of ctx.channels)known[ch].fill(1);ctx.pending=()=>0;"


def test_activity_window_clips_overlaps_excludes_touching_or_invalid_and_preserves_input():
    result = run_node("const E=require(" + json.dumps(SRC + "/editor.js") + r""");
const segments=[[-2,2],[4,7],[8,12],[12,14],[1,1],[3,2],['4',6],null];
const before=JSON.stringify(segments),out=E.activityWindow(segments,2,12);
console.log(JSON.stringify({out,unchanged:before===JSON.stringify(segments),invalid:E.activityWindow(segments,12,2)}));
""")
    assert result == {
        "out": {"segments": [
            {"start": 4, "end": 7, "visibleStart": 4, "visibleEnd": 7},
            {"start": 8, "end": 12, "visibleStart": 8, "visibleEnd": 12},
        ], "total": 2, "omitted": 0},
        "unchanged": True,
        "invalid": {"segments": [], "total": 0, "omitted": 0},
    }


def test_activity_window_clips_edges_and_caps_ten_thousand_segments_to_forty():
    result = run_node("const E=require(" + json.dumps(SRC + "/editor.js") + r""");
const many=Array.from({length:10000},(_,i)=>[i,i+0.5]),out=E.activityWindow(many,0,10000);
console.log(JSON.stringify({count:out.segments.length,total:out.total,omitted:out.omitted,
 edge:E.activityWindow([[-2,3],[8,12]],0,10)}));
""")
    assert result["count"] == 40 and result["total"] == 10000 and result["omitted"] == 9960
    assert result["edge"] == {"segments": [
        {"start": -2, "end": 3, "visibleStart": 0, "visibleEnd": 3},
        {"start": 8, "end": 12, "visibleStart": 8, "visibleEnd": 10},
    ], "total": 2, "omitted": 0}


def test_all_four_channels_have_activity_chips_and_top_bands_before_any_playback():
    result = in_multitrack(r"""
(async()=>{
 await settle();const views=[];
 for(const ch of ctx.channels){if(sel.value!==ch)label(ch).onclick();editor.refreshMedia();
  views.push({channel:sel.value,data:{...$('vhfseg').dataset},html:$('vhfseg').innerHTML,strip:{...$('edstrip').dataset}});}
 console.log(JSON.stringify({views,paused:au.paused,time:au.currentTime}));
})();
""", KNOWN + "ctx.VHF.A1=[[5,7],[20,23]];ctx.VHF.A2=[[10,12]];ctx.VHF.A3=[[15,16]];ctx.VHF.C1=[[10,12],[40,43]];")
    assert result["paused"] and result["time"] == 0
    for view, count in zip(result["views"], [2, 1, 1, 2]):
        channel = view["channel"]
        assert view["data"]["channel"] == channel
        assert view["data"]["shown"] == str(count)
        assert view["data"]["start"] == "0" and view["data"]["end"] == "60"
        assert view["html"].count("<button ") == count
        assert "聲音活動段 " + channel in view["html"] and "非確定發話" in view["html"]
        assert 'data-channel="' + channel + '"' in view["html"]
        assert view["strip"]["activityChannel"] == channel
        assert view["strip"]["activityStart"] == "-30" and view["strip"]["activityEnd"] == "30"
        assert int(view["strip"]["activityCount"]) == (1 if channel == "C1" else count)


def test_visible_scope_clips_chip_start_and_limits_dom_with_explicit_overflow():
    result = in_multitrack(r"""
(async()=>{
 await settle();au.currentTime=200;editor.refreshMedia();
 console.log(JSON.stringify({range:range(),data:{...$('vhfseg').dataset},html:$('vhfseg').innerHTML}));
})();
""", KNOWN + "ctx.VHF.A1=[[180,190],...Array.from({length:60},(_,i)=>[190+i*0.5,190.25+i*0.5]),[248,250]];")
    assert result["range"] == [188, 248]
    assert result["data"]["total"] == "61" and result["data"]["shown"] == "40"
    assert result["html"].count("<button ") == 40
    assert 'data-t="188" data-start="180" data-end="190"' in result["html"]
    assert 'data-t="248"' not in result["html"]
    assert "另有 21 段未列出" in result["html"] and "僅目前可視範圍" in result["html"]


def test_unknown_pending_gap_and_analyzed_empty_states_do_not_assert_silence():
    result = in_multitrack(r"""
(async()=>{
 await settle();const unknown={data:{...$('vhfseg').dataset},html:$('vhfseg').innerHTML};
 known.A1.fill(1);ctx.pending=()=>0;editor.refreshMedia();
 const analyzed={data:{...$('vhfseg').dataset},html:$('vhfseg').innerHTML};
 known.A1.fill(0);ctx.gaps=ch=>ch==='A1'?[[0,60]]:[];editor.refreshMedia();
 console.log(JSON.stringify({unknown,analyzed,gap:{data:{...$('vhfseg').dataset},html:$('vhfseg').innerHTML}}));
})();
""")
    assert int(result["unknown"]["data"]["unknown"]) > 0 and result["unknown"]["data"]["analyzed"] == "0"
    assert "仍待分析" in result["unknown"]["html"] and "不能視為無聲" in result["unknown"]["html"]
    assert "已分析部分未找到" not in result["unknown"]["html"]
    assert result["analyzed"]["data"]["unknown"] == "0" and int(result["analyzed"]["data"]["analyzed"]) > 0
    assert "不代表完全無聲" in result["analyzed"]["html"]
    assert result["gap"]["data"]["unknown"] == result["gap"]["data"]["analyzed"] == "0"
    assert result["gap"]["data"]["gaps"] == "1" and "缺段（見斜線）" in result["gap"]["html"]
    assert "已分析部分未找到" not in result["gap"]["html"]


def test_activity_chip_seeks_current_channel_and_cancels_old_sentence_stop_bound():
    result = in_multitrack(r"""
(async()=>{
 await settle();editor.state().A1.push({...cue,start:20,end:22});editor.changed();selectCue(1);
 $('edlist').events.click({target:{closest:s=>s==='.er'?{dataset:{id:'1'}}:s==='[data-a=play]'?{}:null}});
 const before=au.currentTime;
 await $('vhfseg').events.click({target:{closest:()=>({dataset:{t:'23.5',channel:'A1'}})}});
 au.currentTime=23.6;
 const after={time:au.currentTime,paused:au.paused,channel:sel.value,changes:changes.slice(),pauses:pauses.slice()};
 await $('vhfseg').events.click({target:{closest:()=>({dataset:{t:'100',channel:'C1'}})}});
 console.log(JSON.stringify({before,after,ignoredTime:au.currentTime}));
})();
""", KNOWN + "ctx.VHF.A1=[[23.5,24]];")
    assert result["before"] == 19.85
    assert result["after"] == {"time": 23.6, "paused": False, "channel": "A1", "changes": [], "pauses": []}
    assert result["ignoredTime"] == 23.6


def test_activity_chip_play_rejection_is_shown_instead_of_unhandled():
    result = in_multitrack(r"""
(async()=>{
 await settle();au.play=()=>Promise.reject(Error('synthetic playback blocked'));
 await $('vhfseg').events.click({target:{closest:()=>({dataset:{t:'8',channel:'A1'}})}});
 console.log(JSON.stringify({time:au.currentTime,message:$('edsave').textContent,channel:sel.value}));
})();
""", KNOWN + "ctx.VHF.A1=[[8,10]];")
    assert result["time"] == 7.5 and result["channel"] == "A1"
    assert "活動段播放未完成" in result["message"] and "synthetic playback blocked" in result["message"]


def test_time_correction_invalidates_activity_clock_cache_without_moving_raw_seek():
    result = in_multitrack(r"""
(async()=>{
 await settle();const before=$('vhfseg').innerHTML;offset=30;editor.render();
 const after=$('vhfseg').innerHTML;
 await $('vhfseg').events.click({target:{closest:()=>({dataset:{t:'10',channel:'A1'}})}});
 console.log(JSON.stringify({before,after,time:au.currentTime}));
})();
""", KNOWN + "let offset=0;ctx.ofs=()=>offset;ctx.VHF.A1=[[10,12]];")
    assert "08:00:10" in result["before"] and "08:00:40" in result["after"]
    assert result["before"] != result["after"] and result["time"] == 9.5
