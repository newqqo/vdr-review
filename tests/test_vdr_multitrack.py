"""Synthetic synchronized activity-waveform UI; no source audio or network use."""

import json
import shutil

import pytest

from test_vdr_editor import HARNESS, SRC, run_node

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def in_multitrack(code, setup=""):
    before = r"""
ctx.channels=['A1','A2','A3','C1'];ctx.dur=600;
const changes=[],requests=[],known={},audioEvents={},pauses=[];
ctx.channelLabels={A1:'A1 駕駛臺',A2:'A2 左翼',A3:'A3 右翼',C1:'C1 VHF'};
for(const ch of ctx.channels){ctx.ENV[ch]=new Uint8Array(2400);known[ch]=new Uint8Array(2400);}
ctx.envKnown=ch=>known[ch];ctx.pending=()=>10;
ctx.requestWaveforms=(channels,a,b)=>{requests.push({channels,a,b});return Promise.resolve();};
Element.prototype.setAttribute=function(k,v){(this.attrs||(this.attrs={}))[k]=v;};
Element.prototype.dispatchEvent=function(e){if(this===sel)changes.push(this.value);if(this.events[e.type])this.events[e.type](e);};
Element.prototype.getContext=function(){
 if(this.graphics)return this.graphics;const operations=[];this.operations=operations;
 this.graphics=new Proxy({}, {get:(o,k)=>o[k]||((...args)=>operations.push({method:k,args,fill:o.fillStyle}))});return this.graphics;
};
document.body.classList.contains=k=>k==='editing';
au.addEventListener=(name,fn)=>(audioEvents[name]||(audioEvents[name]=[])).push(fn);
function audioEvent(name){for(const fn of audioEvents[name]||[])fn();}
let audioTime=0;
Object.defineProperty(au,'currentTime',{get:()=>audioTime,set:value=>{audioTime=value;audioEvent('seeked');audioEvent('timeupdate');}});
au.play=()=>{au.paused=false;audioEvent('play');return Promise.resolve();};
au.pause=()=>{pauses.push(audioTime);au.paused=true;audioEvent('pause');};
""" + setup
    harness = HARNESS.replace("const editor=Editor.init(ctx);", before + "\nconst editor=Editor.init(ctx);")
    helpers = r"""
const rows=()=> $('edwaveRows').children;
const row=ch=>rows().find(r=>r.dataset.channel===ch);
const label=ch=>row(ch).children[0],canvas=ch=>row(ch).children[1];
const range=()=>[+$('edwaveRows').dataset.start,+$('edwaveRows').dataset.end];
const clickWave=(ch,x)=>canvas(ch).events.click({clientX:x,target:canvas(ch)});
const settle=async()=>{await Promise.resolve();await Promise.resolve();};
function selectCue(id){$('edlist').events.focusin({target:{closest:()=>({dataset:{id:String(id)}})}});editor.refreshMedia();}
"""
    return run_node("const SRC=" + json.dumps(SRC) + ";\n" + harness + helpers + code)


def test_four_tracks_show_same_range_cursor_and_explicit_active_channel_before_playback():
    result = in_multitrack(r"""
(async()=>{
 await settle();console.log(JSON.stringify({channels:rows().map(r=>r.dataset.channel),
  ranges:rows().map(r=>[r.children[1].dataset.start,r.children[1].dataset.end]),
  cursors:rows().map(r=>r.children[1].dataset.cursor),active:rows().filter(r=>r.dataset.active==='true').map(r=>r.dataset.channel),
  requests,paused:au.paused,firstCanvas:canvas('A1').id,label:label('A2').textContent}));
})();
""")
    assert result["channels"] == ["A1", "A2", "A3", "C1"]
    assert result["ranges"] == [["0", "60"]] * 4 and result["cursors"] == ["0"] * 4
    assert result["active"] == ["A1"] and result["paused"]
    assert result["requests"] == [{"channels": result["channels"], "a": 0, "b": 60}]
    assert result["firstCanvas"] == "edwave" and result["label"] == "A2 左翼"


def test_six_tracks_are_created_without_fixed_four_track_assumptions():
    result = in_multitrack(r"""
(async()=>{await settle();console.log(JSON.stringify({channels:rows().map(r=>r.dataset.channel),requested:requests[0].channels}));})();
""", "ctx.channels.push('A4','C2');for(const ch of ['A4','C2']){ctx.ENV[ch]=new Uint8Array(2400);known[ch]=new Uint8Array(2400);}")
    assert result["channels"] == ["A1", "A2", "A3", "C1", "A4", "C2"]
    assert result["requested"] == result["channels"]


@pytest.mark.parametrize("playing", [False, True])
def test_label_switch_keeps_time_viewport_and_playing_state(playing):
    result = in_multitrack(r"""
(async()=>{
 await settle();au.currentTime=25;const before=range();label('A2').onclick();await settle();
 console.log(JSON.stringify({selected:sel.value,time:au.currentTime,paused:au.paused,before,after:range(),changes,
 active:rows().filter(r=>r.dataset.active==='true').map(r=>r.dataset.channel),pauses}));
})();
""", "au.paused=" + ("false" if playing else "true") + ";")
    assert result["selected"] == "A2" and result["time"] == 25
    assert result["paused"] is (not playing) and result["pauses"] == []
    assert result["before"] == result["after"] == [0, 60]
    assert result["changes"] == ["A2"] and result["active"] == ["A2"]


@pytest.mark.parametrize("playing", [False, True])
def test_click_same_x_on_different_tracks_selects_same_time_without_mixing_or_autoplay(playing):
    result = in_multitrack(r"""
(async()=>{
 await settle();clickWave('A3',150);const first=au.currentTime;clickWave('C1',150);await settle();
 console.log(JSON.stringify({first,last:au.currentTime,selected:sel.value,paused:au.paused,changes,pauses,
 cursor:rows().map(r=>r.children[1].dataset.cursor)}));
})();
""", "au.paused=" + ("false" if playing else "true") + ";")
    assert result["first"] == result["last"] == 30
    assert result["selected"] == "C1" and result["paused"] is (not playing)
    assert result["changes"] == ["A3", "C1"] and result["pauses"] == []
    assert result["cursor"] == ["30"] * 4


def test_selecting_far_sentence_while_paused_frames_its_boundaries_without_seeking_then_label_preserves_frame():
    result = in_multitrack(r"""
(async()=>{
 await settle();editor.state().A1.push({...cue,start:100,end:104});editor.changed();selectCue(1);
 const framed=range();editor.refreshMedia();const repaint=range();label('A2').onclick();
 const switched=range();au.currentTime=200;const sought=range();
 console.log(JSON.stringify({framed,repaint,switched,sought,time:au.currentTime,paused:au.paused,
  shared:rows().map(r=>[r.children[1].dataset.start,r.children[1].dataset.end])}));
})();
""")
    assert result["framed"] == result["repaint"] == result["switched"] == [98, 106]
    assert result["sought"] == [188, 248] and result["time"] == 200 and result["paused"]
    assert result["shared"] == [["188", "248"]] * 4


def test_only_active_track_can_drag_selected_sentence_and_edit_resets_review_status():
    result = in_multitrack(r"""
(async()=>{
 await settle();editor.state().A1.push({...cue,start:100,end:104});editor.changed();selectCue(1);
 const inactive=canvas('A2');inactive.events.mousedown({clientX:75,target:inactive,preventDefault(){}});
 listeners.mousemove({clientX:94});listeners.mouseup();const ignored=editor.state().A1[0].start;
 const activeCanvas=canvas('A1');activeCanvas.events.mousedown({clientX:75,target:activeCanvas,preventDefault(){}});
 listeners.mousemove({clientX:93.75});listeners.mouseup();
 console.log(JSON.stringify({ignored,start:editor.state().A1[0].start,status:editor.state().A1[0].st,
  time:au.currentTime,selected:sel.value,range:range(),dirty:editor.dirty()}));
})();
""")
    assert result == {"ignored": 100, "start": 100.5, "status": 0, "time": 0,
                      "selected": "A1", "range": [98, 106], "dirty": True}


def test_visible_range_requests_are_deduplicated_and_obsolete_completion_cannot_clear_new_status():
    result = in_multitrack(r"""
(async()=>{
 editor.refreshMedia();editor.refreshMedia();const initial=requests.length;
 au.currentTime=200;au.currentTime=400;const afterSeek=requests.length;
 deferred[0].resolve();await settle();const stillWorking=$('edwaveStatus').textContent;
 deferred[2].resolve();await settle();const done=$('edwaveStatus').textContent;
 editor.refreshMedia();console.log(JSON.stringify({initial,afterSeek,final:requests.length,range:range(),stillWorking,done}));
})();
""", "const deferred=[];ctx.requestWaveforms=(channels,a,b)=>{requests.push({channels,a,b});return new Promise((resolve,reject)=>deferred.push({resolve,reject}));};")
    assert result["initial"] == 1 and result["afterSeek"] == result["final"] == 3
    assert result["range"] == [388, 448]
    assert "正在建立" in result["stillWorking"] and "已處理" in result["done"]


def test_zoom_and_resize_share_range_and_decode_failure_is_visible_not_silence():
    result = in_multitrack(r"""
(async()=>{
 await settle();$('edwaveScale').value='300';$('edwaveScale').onchange();await settle();listeners.resize();
 console.log(JSON.stringify({range:range(),shared:rows().map(r=>[r.children[1].dataset.start,r.children[1].dataset.end]),
  status:$('edwaveStatus').textContent,unknown:rows().map(r=>+r.dataset.unknown),requests:requests.length}));
})();
""", "ctx.requestWaveforms=(channels,a,b)=>{requests.push({channels,a,b});return Promise.reject(Error('synthetic codec failure'));};")
    assert result["range"] == [0, 300] and result["shared"] == [["0", "300"]] * 4
    assert "synthetic codec failure" in result["status"] and all(result["unknown"])
    assert result["requests"] == 2


def test_known_gaps_are_painted_after_unknown_and_keep_hatched_missing_record_label():
    result = in_multitrack(r"""
(async()=>{
 await settle();const c=canvas('A1'),texts=c.operations.filter(o=>o.method==='fillText').map(o=>o.args[0]);
 console.log(JSON.stringify({texts,gaps:row('A1').dataset.gaps,unknown:row('A1').dataset.unknown}));
})();
""", "ctx.gaps=ch=>ch==='A1'?[[10,40]]:[];")
    assert "尚未分析" in result["texts"] and "缺段" in result["texts"]
    assert result["texts"].index("尚未分析") < result["texts"].index("缺段")
    assert result["gaps"] == "1" and int(result["unknown"]) > 0


def test_wave_helpers_clamp_edges_without_negative_time_or_mismatched_x_mapping():
    result = run_node("const E=require(" + json.dumps(SRC + "/editor.js") + r""");
console.log(JSON.stringify({start:E.waveWindow(0,60,600),end:E.waveWindow(599,60,600),short:E.waveWindow(0,60,20),
 left:E.waveTimeAt([98,106],-50,0,300,600),middle:E.waveTimeAt([98,106],150,0,300,600),right:E.waveTimeAt([98,106],400,0,300,600)}));
""")
    assert result == {"start": [0, 60], "end": [540, 600], "short": [0, 20],
                      "left": 98, "middle": 102, "right": 106}
