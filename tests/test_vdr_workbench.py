"""Simple listen/type workbench contracts; synthetic audio, no evidence or network."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from test_vdr_editor import SRC
from test_vdr_multitrack import in_multitrack
from test_vdr_stream import run_node as run_player_node


class WorkbenchMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.nodes = {}
        self.elements = []
        self.duplicates = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        identity = attrs.get("id")
        node = {
            "tag": tag, "attrs": attrs,
            "details": [a.get("id") for t, a in self.stack if t == "details"],
            "ancestor_ids": [a["id"] for _, a in self.stack if a.get("id")],
            "ancestor_classes": [c for _, a in self.stack for c in a.get("class", "").split()],
            "parent": self.stack[-1] if self.stack else None,
        }
        self.elements.append(node)
        if identity:
            if identity in self.nodes:
                self.duplicates.append(identity)
            self.nodes[identity] = node
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def in_workbench(code, setup=""):
    return in_multitrack(r"""
const composer=$('edcompose');
function type(value){composer.value=value;composer.events.input({target:composer});}
function key(props={}){const event={target:composer,key:'Enter',prevented:false,stopped:false,
 preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;},...props};
 composer.events.keydown(event);return event;}
""" + code, r"""
document.body.classList.contains=()=>false;
const offStates=[];$('edtool').classList.toggle=(name,value)=>{if(name==='off')offStates.push(value);};
""" + setup)


def in_row_workbench(code, setup=""):
    """Local DOM extension for real delegated row handlers, not shared mocks."""
    return in_workbench(r"""
const subtitleRow=id=>$('edlist').children.find(r=>+r.dataset.id===id);
const subtitleText=id=>subtitleRow(id).querySelector('.tx');
async function rowFixture(){const data=doc();data.state.A1=[
 {...cue,id:1,start:10,end:12,text:'第一句'},
 {...cue,id:2,start:30,end:35,text:'第二句'},
 {...cue,id:3,start:10.5,end:13,text:'重疊句'}];data.uid=4;await load(data);await settle();}
function rowEvent(name,target,props={}){const e={target,button:0,pointerId:7,clientX:10,clientY:10,detail:1,
 preventDefault(){this.defaultPrevented=true;},stopPropagation(){this.stopped=true;},...props};
 const handler=$('edlist').events[name];if(handler)handler(e);return e;}
function clickSubtitle(id,{alreadyFocused=false,selectedText='',kind='text',drag=false}={}){
 const r=subtitleRow(id),target=kind==='text'?r.querySelector('.tx'):kind==='card'?r:r.querySelector(kind);
 document.activeElement=alreadyFocused?target:{tagName:'BODY'};
 selectionText='';if(target.tagName==='TEXTAREA'){target.selectionStart=0;target.selectionEnd=0;}
 rowEvent('pointerdown',target);
 if(target.tagName==='TEXTAREA'){document.activeElement=target;rowEvent('focusin',target);}
 if(drag)rowEvent('pointermove',target,{clientX:50,clientY:10});
 selectionText=selectedText;if(target.tagName==='TEXTAREA'&&selectedText)target.selectionEnd=selectedText.length;
 rowEvent('click',target);return target;
}
function cueIndicators(){return $('edlist').children.filter(r=>r.dataset.id).map(r=>{
 const badge=r.querySelector('.cue-playstate');return {id:+r.dataset.id,active:r.classList.contains('act'),
  playing:r.classList.contains('is-playing'),current:r.classList.contains('cur'),data:r.dataset.playing,
  badge:badge?.textContent,hidden:badge?.hidden};});}
""" + code, r"""
let selectionText='';window.getSelection=()=>({isCollapsed:!selectionText,toString:()=>selectionText});
const scrollCalls=[],outerScrollCalls=[];
const originalCreate=document.createElement,baseQuery=Element.prototype.querySelector;
function tracked(el){if(el._rowTracked)return el;el._rowTracked=true;
 el.classList={contains:k=>(el.className||'').split(/\s+/).includes(k),
  toggle(k,on){const set=new Set((el.className||'').split(/\s+/).filter(Boolean));
   if(on===undefined)on=!set.has(k);if(on)set.add(k);else set.delete(k);el.className=[...set].join(' ');return on;},
  add(...names){for(const k of names)this.toggle(k,true);},remove(...names){for(const k of names)this.toggle(k,false);}};
 return el;
}
document.createElement=tag=>tracked(originalCreate(tag));
Element.prototype.matches=function(selector){return selector.split(',').some(s=>{s=s.trim();
 if(s.startsWith('.'))return this.classList.contains(s.slice(1));if(s==='[data-a=play]')return this.dataset.a==='play';
 if(s==='[data-a=acc]')return this.dataset.a==='acc';return this.tagName===s.toUpperCase();});};
Element.prototype.closest=function(selector){for(let el=this;el;el=el.parentNode)if(el.matches?.(selector))return el;return null;};
Element.prototype.querySelector=function(selector){
 if(this.id==='edlist'){
  const found=/^\.er(?:\[data-id=["']?(\d+)["']?\])?(\.act)?(?: (.*))?$/.exec(selector);
  if(found){const r=this.children.find(n=>n.dataset.id&&(!found[1]||+n.dataset.id===+found[1])&&(!found[2]||n.classList.contains('act')));
   return found[3]?r?.querySelector(found[3])||null:r||null;}
 }
 if(selector==='.tx'){const t=tracked(baseQuery.call(this,selector));t.tagName='TEXTAREA';t.className='tx';t.parentNode=this;return t;}
 if(selector==='.cue-playstate'){
  if(!this._badge){const b=tracked(new Element());b.className='cue-playstate';b.tagName='SPAN';b.parentNode=this;
   const match=/<span[^>]*class="cue-playstate"[^>]*>([^<]*)<\/span>/.exec(this.innerHTML);
   b.textContent=match?match[1]:'';b.hidden=match?/\bhidden\b/.test(match[0].split('>')[0]):true;this._badge=b;}
  return this._badge;
 }
 if(selector==='.etime'||selector==='[data-a=play]'||selector==='[data-a=acc]'){
  const action=selector==='[data-a=acc]'?'acc':'play',key='_button_'+action;
  if(!this[key]){const b=tracked(new Element());b.tagName='BUTTON';b.className=action==='play'?'etime':'esug';b.dataset.a=action;
   b.parentNode=this;b.firstChild={nodeValue:'▶ 00:10'};this[key]=b;}return this[key];
 }
 return baseQuery.call(this,selector);
};
Element.prototype.querySelectorAll=function(selector){if(this.id==='edlist'){
 const rs=this.children.filter(r=>r.dataset.id);
 if(selector==='.er')return rs;if(selector==='.etime')return rs.map(r=>r.querySelector('.etime'));
 if(selector==='.cue-playstate')return rs.map(r=>r.querySelector('.cue-playstate'));
 }return [];};
Element.prototype.scrollIntoView=function(options){outerScrollCalls.push({id:this.id,cue:this.dataset.id,options});};
Element.prototype.scrollTo=function(options){scrollCalls.push({id:this.id,...options});this.scrollTop=options.top;};
Element.prototype.getBoundingClientRect=function(){if(this.id==='edlist')return {left:0,top:100,bottom:300,width:300,height:200};
 if(this.parentNode?.id==='edlist'){const i=this.parentNode.children.indexOf(this),top=100+i*120-this.parentNode.scrollTop;
  return {left:0,top,bottom:top+80,width:300,height:80};}
 return {left:0,top:0,bottom:100,width:300,height:100};};
Object.defineProperty(Element.prototype,'offsetTop',{get(){return this.parentNode?.id==='edlist'?this.parentNode.children.indexOf(this)*120:0;}});
Object.defineProperty(Element.prototype,'offsetHeight',{get(){return this.parentNode?.id==='edlist'?80:this.clientHeight;}});
$('edlist').clientHeight=200;$('edlist').scrollHeight=1000;
const realPlay=au.play;let playCalls=0;au.play=()=>{playCalls++;return realPlay();};
""" + setup)


def test_primary_map_composer_and_save_are_visible_without_modes_or_consent():
    source = Path(SRC, "app.html").read_text(encoding="utf-8")
    markup = WorkbenchMarkup()
    markup.feed(source)
    assert markup.duplicates == []
    for identity in ["map", "edcompose", "edcomposeAdd", "saveP", "loadP", "playbtn", "seek"]:
        assert markup.nodes[identity]["details"] == []
        assert "hidden" not in markup.nodes[identity]["attrs"]
    for identity in ["transcriptionFlow", "navigationDetails", "reviewOptions", "deliveryOptions", "keyboardHelp", "dataDetails", "about"]:
        assert markup.nodes[identity]["tag"] == "details"
        assert "open" not in markup.nodes[identity]["attrs"]
    assert "navigationDetails" in markup.nodes["cv"]["details"]
    assert "navigationDetails" in markup.nodes["events"]["details"]
    assert "reviewOptions" in markup.nodes["editmode"]["details"]
    assert "transcriptionFlow" in markup.nodes["imSrtBtn"]["details"]
    assert not {"showAux", "tileConsent", "tips", "tipsok"}.intersection(markup.nodes)
    assert "body:not(.auxiliary)" not in source
    assert "#edtool.off{display:none}" in source
    assert "@media (max-width:1000px)" in source and "grid-template-columns:1fr" in source


def test_open_waveform_block_lives_under_map_not_above_subtitle_list():
    source = Path(SRC, "app.html").read_text(encoding="utf-8")
    markup = WorkbenchMarkup()
    markup.feed(source)
    wave = markup.nodes["waveDetails"]
    assert wave["tag"] == "details" and "open" in wave["attrs"]
    assert wave["parent"][1]["id"] == "navigationPane"
    assert "edpanel" not in wave["ancestor_ids"]
    for identity in ["edwaves", "edwaveRows", "edwave", "edwaveScale", "vhfseg"]:
        assert "navigationPane" in markup.nodes[identity]["ancestor_ids"]
        assert markup.nodes[identity]["details"] == ["waveDetails"]
        assert "edhead" not in markup.nodes[identity]["ancestor_classes"]
    # Keep the map first, then the initially open four/six-channel waveform slot.
    order = list(markup.nodes)
    assert order.index("map") < order.index("waveDetails") < order.index("navigationDetails")


def test_delete_button_is_a_direct_nonadvanced_selected_sentence_control():
    source = Path(SRC, "app.html").read_text(encoding="utf-8")
    markup = WorkbenchMarkup()
    markup.feed(source)
    buttons = [node for node in markup.elements if node["attrs"].get("data-a") == "del"]
    assert len(buttons) == 1
    button = buttons[0]
    assert button["tag"] == "button" and button["parent"][1]["id"] == "edtool"
    assert "adv" not in button["attrs"].get("class", "").split() and "adv" not in button["ancestor_classes"]
    assert button["details"] == [] and "hidden" not in button["attrs"]
    assert button["attrs"]["data-i18n"] == "action.delete" and "Alt+Delete" in button["attrs"]["title"]


def test_primary_runtime_transcription_labels_are_not_tied_to_a_specific_service():
    for name in ["app.html", "app.js", "editor.js"]:
        source = Path(SRC, name).read_text(encoding="utf-8")
        assert not re.search(r"meeting\s*ink", source, re.IGNORECASE), name
    markup = WorkbenchMarkup()
    markup.feed(Path(SRC, "app.html").read_text(encoding="utf-8"))
    assert markup.nodes["imSrtBtn"]["attrs"]["data-i18n"] == "srt.import"
    assert markup.nodes["imSrtBtn"]["details"] == ["transcriptionFlow"]


def test_subtitle_list_has_independent_height_between_header_and_composer_footer():
    source = Path(SRC, "app.html").read_text(encoding="utf-8")
    markup = WorkbenchMarkup()
    markup.feed(source)
    subtitles = markup.nodes["edlist"]
    assert subtitles["parent"][1]["id"] == "edpanel"
    assert not {"edhead", "edfoot"}.intersection(subtitles["ancestor_classes"])
    assert subtitles["attrs"]["tabindex"] == "0" and subtitles["attrs"]["aria-label"] == "字幕清單"
    for identity in ["edcomposer", "deliveryOptions"]:
        node = markup.nodes[identity]
        assert "edpanel" in node["ancestor_ids"] and "edfoot" in node["ancestor_classes"]
        assert "edhead" not in node["ancestor_classes"]
        assert node["parent"][1]["class"] == "edfoot"
    assert "edhead" in markup.nodes["edtool"]["ancestor_classes"]
    assert "edfoot" not in markup.nodes["edtool"]["ancestor_classes"]
    order = list(markup.nodes)
    assert order.index("edtool") < order.index("edlist") < order.index("edcomposer") < order.index("keyboardHelp")

    def rules(selector):
        blocks = re.findall(re.escape(selector) + r"\s*\{([^{}]*)\}", source)
        return [dict(part.strip().split(":", 1) for part in block.split(";") if ":" in part) for block in blocks]

    panel = next(rule for rule in rules("#edpanel") if rule.get("display") == "flex")
    assert panel["flex-direction"] == "column"
    assert not {"height", "max-height"}.intersection(panel)
    assert panel.get("overflow") != "hidden"
    assert rules(".edhead")[0]["flex"] == rules(".edfoot")[0]["flex"] == "none"
    subtitle_rules = rules("#edlist")
    base = subtitle_rules[0]
    assert base["flex"] == "none" and base["box-sizing"] == "border-box"
    assert base["overflow-y"] == "auto" and base["overscroll-behavior"] == "contain"
    assert base["min-height"] == "320px"
    assert re.fullmatch(r"clamp\(320px,\s*calc\(100vh\s*-\s*430px\),\s*700px\)", base["height"])
    assert all(rule.get("flex", "none") == "none" for rule in subtitle_rules)
    assert any(rule.get("height") == "clamp(360px,55vh,640px)" for rule in subtitle_rules)
    short_height = re.search(r"@media\s*\(max-height:\s*600px\)\s*\{#edpanel\{([^}]*)\}#edlist\{([^}]*)\}\}", source)
    assert short_height and "position:relative" in short_height[1] and "top:0" in short_height[1]
    assert "height:360px" in short_height[2]


@pytest.mark.parametrize("playing", [False, True])
def test_waveform_fold_redraw_preserves_live_composer_cues_channel_playback_and_cache(playing):
    result = in_workbench(r"""
(async()=>{
 await settle();await load(doc());au.currentTime=25;PLAY
 type('收合仍保留的聽打');await settle();
 const savedRows=rows().slice(),savedCanvases=savedRows.map(r=>r.children[1]);
 const snapshot=()=>JSON.stringify({state:editor.state(),extra,cache:[...storage.entries()],dirty:editor.dirty(),
  text:composer.value,channel:sel.value,time:au.currentTime,paused:au.paused,range:range(),changes,pauses});
 const before=snapshot(),requestsBefore=requests.length,paintBefore=savedCanvases.map(c=>c.operations.length);
 $('waveDetails').open=false;$('waveDetails').events.toggle();
 const closed={same:before===snapshot(),paint:savedCanvases.map(c=>c.operations.length)};
 for(const c of savedCanvases){c.clientWidth=420;c.clientHeight=44;}
 $('waveDetails').open=true;$('waveDetails').events.toggle();await settle();
 console.log(JSON.stringify({closed,paintBefore,same:before===snapshot(),
  reusedRows:rows().every((r,i)=>r===savedRows[i]&&r.children[1]===savedCanvases[i]),
  painted:savedCanvases.map((c,i)=>c.operations.length>paintBefore[i]),
  dimensions:savedCanvases.map(c=>[c.width,c.height]),requestsBefore,requestsAfter:requests.length,
  channels:rows().map(r=>r.dataset.channel),cues:editor.state().A1,text:composer.value,paused:au.paused}));
})();
""".replace("PLAY", "await au.play();" if playing else ""))
    assert result["closed"]["same"] and result["closed"]["paint"] == result["paintBefore"]
    assert result["same"] and result["reusedRows"] and all(result["painted"])
    assert result["dimensions"] == [[840, 88]] * 4
    assert result["requestsBefore"] == result["requestsAfter"]
    assert result["channels"] == ["A1", "A2", "A3", "C1"]
    assert [(c["text"], c["st"]) for c in result["cues"]] == [("loaded", 1), ("收合仍保留的聽打", 0)]
    assert result["text"] == "收合仍保留的聽打" and result["paused"] is (not playing)


@pytest.mark.parametrize("kind", ["text", "card"])
def test_first_click_of_subtitle_seeks_and_plays_continuously_without_editing_progress(kind):
    result = in_row_workbench(r"""
(async()=>{
 await rowFixture();const before=JSON.stringify({state:editor.state(),cache:[...storage.entries()],dirty:editor.dirty()});
 clickSubtitle(1,{kind:KIND});await settle();const jumped={time:au.currentTime,paused:au.paused,playCalls};
 audioTime=14;audioEvent('timeupdate');await settle();
 console.log(JSON.stringify({jumped,paused:au.paused,time:au.currentTime,pauses,
  same:before===JSON.stringify({state:editor.state(),cache:[...storage.entries()],dirty:editor.dirty()}),
  active:cueIndicators().filter(c=>c.active).map(c=>c.id)}));
})();
""".replace("KIND", json.dumps(kind)))
    assert result["jumped"] == {"time": 9.85, "paused": False, "playCalls": 1}
    assert not result["paused"] and result["time"] == 14 and result["pauses"] == []
    assert result["same"] and result["active"] == [1]


def test_row_replay_clears_an_old_sentence_end_stop_and_zero_start_does_not_go_negative():
    result = in_row_workbench(r"""
(async()=>{
 await rowFixture();clickSubtitle(1,{kind:'[data-a=play]'});const boundedCalls=playCalls;
 clickSubtitle(2,{kind:'card'});await settle();const jumped=au.currentTime;
 audioTime=36;audioEvent('timeupdate');await settle();const continued=!au.paused;
 const data=doc();await load(data);clickSubtitle(1,{kind:'text'});await settle();
 console.log(JSON.stringify({boundedCalls,jumped,continued,pauses,start:au.currentTime,paused:au.paused,playCalls}));
})();
""")
    assert result["boundedCalls"] == 1 and result["jumped"] == 29.85 and result["continued"]
    assert result["pauses"] == [] and result["start"] == 0 and not result["paused"] and result["playCalls"] == 3


def test_reclick_in_focused_textarea_moves_cursor_without_replaying_or_seeking():
    result = in_row_workbench(r"""
(async()=>{
 await rowFixture();clickSubtitle(1);await settle();audioTime=11.7;audioEvent('timeupdate');
 const before={time:au.currentTime,playCalls};clickSubtitle(1,{alreadyFocused:true});await settle();
 console.log(JSON.stringify({before,time:au.currentTime,playCalls,paused:au.paused,pauses}));
})();
""")
    assert result["before"] == {"time": 11.7, "playCalls": 1}
    assert result["time"] == 11.7 and result["playCalls"] == 1 and not result["paused"] and result["pauses"] == []


@pytest.mark.parametrize("selected_text,drag", [("選取字句", False), ("", True)])
def test_selecting_text_or_pointer_drag_does_not_start_subtitle_playback(selected_text, drag):
    result = in_row_workbench(r"""
(async()=>{
 await rowFixture();au.currentTime=50;const before=JSON.stringify(editor.state());
 clickSubtitle(1,{selectedText:TEXT,drag:DRAG});await settle();
 console.log(JSON.stringify({time:au.currentTime,paused:au.paused,playCalls,same:before===JSON.stringify(editor.state()),pauses}));
})();
""".replace("TEXT", json.dumps(selected_text)).replace("DRAG", json.dumps(drag)))
    assert result == {"time": 50, "paused": True, "playCalls": 0, "same": True, "pauses": []}


def test_tab_style_focusin_selects_a_subtitle_without_playing():
    result = in_row_workbench(r"""
(async()=>{
 await rowFixture();au.currentTime=50;const target=subtitleText(1);document.activeElement=target;
 rowEvent('focusin',target);await settle();
 console.log(JSON.stringify({time:au.currentTime,paused:au.paused,playCalls,active:cueIndicators().filter(c=>c.active).map(c=>c.id)}));
})();
""")
    assert result == {"time": 50, "paused": True, "playCalls": 0, "active": [1]}


@pytest.mark.parametrize("kind", ["text", "card"])
def test_showing_advanced_tools_does_not_disable_first_click_subtitle_playback(kind):
    result = in_row_workbench(r"""
(async()=>{
 await rowFixture();au.currentTime=50;clickSubtitle(1,{kind:KIND});await settle();
 console.log(JSON.stringify({time:au.currentTime,paused:au.paused,playCalls,active:cueIndicators().filter(c=>c.active).map(c=>c.id)}));
})();
""".replace("KIND", json.dumps(kind)), "document.body.classList.contains=k=>k==='editing';")
    assert result == {"time": 9.85, "paused": False, "playCalls": 1, "active": [1]}


def test_sentence_play_button_runs_once_and_keeps_its_bounded_end_in_advanced_mode():
    result = in_row_workbench(r"""
(async()=>{
 await rowFixture();clickSubtitle(1,{kind:'[data-a=play]'});await settle();
 const first={time:au.currentTime,paused:au.paused,playCalls};audioTime=12.1;audioEvent('timeupdate');await settle();
 console.log(JSON.stringify({first,time:au.currentTime,paused:au.paused,pauses,playCalls}));
})();
""", "document.body.classList.contains=k=>k==='editing';")
    assert result["first"] == {"time": 9.85, "paused": False, "playCalls": 1}
    assert result["paused"] and result["time"] == 12.1 and result["pauses"] == [12.1] and result["playCalls"] == 1


def test_bounded_loop_remains_bound_to_played_cue_after_focusing_a_different_row():
    result = in_row_workbench(r"""
(async()=>{
 await rowFixture();$('edloop').checked=true;clickSubtitle(1,{kind:'[data-a=play]'});await settle();
 rowEvent('focusin',subtitleText(2));const stateBefore=JSON.stringify(editor.state());
 audioTime=12.1;audioEvent('timeupdate');await settle();
 const first={time:au.currentTime,paused:au.paused,active:cueIndicators().filter(c=>c.active).map(c=>c.id)};
 audioTime=12.2;audioEvent('timeupdate');await settle();
 console.log(JSON.stringify({first,time:au.currentTime,paused:au.paused,playCalls,pauses,
  same:stateBefore===JSON.stringify(editor.state()),active:cueIndicators().filter(c=>c.active).map(c=>c.id)}));
})();
""")
    assert result["first"] == {"time": 9.85, "paused": False, "active": [2]}
    assert result["time"] == 9.85 and not result["paused"] and result["playCalls"] == 1 and result["pauses"] == []
    assert result["same"] and result["active"] == [2]


def test_speaker_suggestion_button_is_not_also_a_row_play_command():
    result = in_row_workbench(r"""
(async()=>{
 const data=doc();data.state.A1[0]={...cue,start:10,end:12,spk:''};await load(data);au.currentTime=50;
 $('imSug').onchange({target:{files:[{text:async()=>JSON.stringify({suggestions:{A1:{1:{spk:'引水人',score:0.9,margin:0.4,confident:true}}}})}],value:'x'}});await settle();
 clickSubtitle(1,{kind:'[data-a=acc]'});await settle();
 console.log(JSON.stringify({time:au.currentTime,paused:au.paused,playCalls,spk:editor.state().A1[0].spk,pauses}));
})();
""")
    assert result == {"time": 50, "paused": True, "playCalls": 0, "spk": "引水人", "pauses": []}


def test_playing_badges_follow_all_overlaps_pause_and_gap_independently_from_selected_row():
    result = in_row_workbench(r"""
(async()=>{
 await rowFixture();au.currentTime=10.75;await au.play();
 rowEvent('focusin',subtitleText(2));const playing=cueIndicators(),playingIds=$('edlist').dataset.playingCues;
 au.pause();const paused=cueIndicators(),pausedIds=$('edlist').dataset.playingCues,currentIds=$('edlist').dataset.currentCues;
 await au.play();audioTime=20;audioEvent('timeupdate');await settle();
 console.log(JSON.stringify({playing,playingIds,paused,pausedIds,currentIds,gap:cueIndicators(),
  gapPlaying:$('edlist').dataset.playingCues,gapCurrent:$('edlist').dataset.currentCues}));
})();
""")
    assert result["playingIds"] == result["currentIds"] == "1,3" and result["pausedIds"] == ""
    for row in result["playing"]:
        assert row["active"] is (row["id"] == 2)
        assert row["playing"] is (row["id"] in [1, 3]) and row["data"] == str(row["playing"]).lower()
        if row["playing"]:
            assert not row["hidden"] and row["badge"] == "▶ 播放中"
    for row in result["paused"]:
        assert not row["playing"] and row["current"] is (row["id"] in [1, 3])
        if row["current"]:
            assert not row["hidden"] and row["badge"] == "⏸ 目前位置"
    assert result["gapPlaying"] == result["gapCurrent"] == ""
    assert all(not row["playing"] and not row["current"] and row["hidden"] for row in result["gap"])
    assert [row["id"] for row in result["gap"] if row["active"]] == [2]


def test_playback_highlight_and_visible_badge_are_distinct_from_selection_outline():
    source = Path(SRC, "app.html").read_text(encoding="utf-8")
    playing = re.search(r"\.er\.is-playing\s*\{([^{}]*)\}", source)
    selected = re.search(r"\.er\.act\s*\{([^{}]*)\}", source)
    badge = re.search(r"\.cue-playstate\s*\{([^{}]*)\}", source)
    assert playing and selected and badge
    assert all(property_name in playing[1] for property_name in ["background:", "border-color:", "box-shadow:"])
    assert "outline:" in selected[1] and selected[1] != playing[1]
    assert "display:inline-block" in badge[1] and "font-weight:700" in badge[1]
    assert re.search(r"\.cue-playstate\[hidden\]\s*\{\s*display:none\s*;?\s*\}", source)
    assert 'data-i18n="workbench.showAdvanced">顯示進階工具</span></label>' in source


def test_follow_playback_and_keyboard_next_scroll_only_subtitle_container():
    result = in_row_workbench(r"""
(async()=>{
 await rowFixture();rowEvent('focusin',subtitleText(1));$('edfollow').checked=true;
 document.activeElement={tagName:'BODY'};outerScrollCalls.length=0;scrollCalls.length=0;
 await au.play();audioTime=31;audioEvent('timeupdate');await settle();const following=scrollCalls.slice();
 scrollCalls.length=0;const before=JSON.stringify(editor.state());
 const order=$('edlist').children.filter(r=>r.dataset.id).map(r=>+r.dataset.id),nextId=order[order.indexOf(1)+1];
 listeners.keydown({target:{tagName:'BODY',closest:()=>null},key:'ArrowDown',altKey:true,preventDefault(){}});
 const next=editor.state().A1.find(c=>c.id===nextId);
 console.log(JSON.stringify({following,navigation:scrollCalls,outer:outerScrollCalls,scrollTop:$('edlist').scrollTop,
  same:before===JSON.stringify(editor.state()),time:au.currentTime,expectedTime:next.start-0.15,
  active:cueIndicators().filter(c=>c.active).map(c=>c.id),nextId}));
})();
""")
    assert result["following"] and result["navigation"] and result["outer"] == []
    assert all(call["id"] == "edlist" for call in result["following"] + result["navigation"])
    assert result["same"] and result["time"] == result["expectedTime"] and result["active"] == [result["nextId"]]


def test_sentence_end_can_be_dragged_without_showing_advanced_tools_and_only_invalidates_that_cue():
    result = in_workbench(r"""
(async()=>{
 const data=doc();data.state.A1=[{...cue,start:10,end:14,text:'人工修正文',orig:'原始逐字稿'},
  {...cue,id:2,start:30,end:33,text:'其他已核句',orig:'另一原文'}];data.uid=3;await load(data);selectCue(1);
 const other=JSON.stringify(editor.state().A1[1]),activeCanvas=canvas('A1'),before=range();
 activeCanvas.events.mousedown({clientX:225,target:activeCanvas,preventDefault(){}});
 listeners.mousemove({clientX:187.5});listeners.mouseup();
 console.log(JSON.stringify({before,cue:editor.state().A1[0],otherSame:other===JSON.stringify(editor.state().A1[1]),
  dirty:editor.dirty(),time:au.currentTime,paused:au.paused,advanced:document.body.classList.contains('editing')}));
})();
""")
    assert result["before"] == [8, 16] and not result["advanced"]
    assert result["cue"] == {
        "id": 1, "start": 10, "end": 13, "text": "人工修正文", "spk": "A", "st": 0,
        "orig": "原始逐字稿", "origNo": None, "ovl": False,
    }
    assert result["otherSame"] and result["dirty"] and result["time"] == 0 and result["paused"]


def test_alt_w_splits_from_known_waveform_without_enabling_advanced_tools():
    result = in_workbench(r"""
(async()=>{
 const data=doc();data.state.A1=[{...cue,start:10,end:20,text:'第一句。第二句。',orig:'完整原始逐字稿'},
  {...cue,id:2,start:30,end:33,text:'其他已核句'}];data.uid=3;await load(data);selectCue(1);
 const other=JSON.stringify(editor.state().A1[1]);
 listeners.keydown({target:{tagName:'TEXTAREA',closest:()=>null},key:'w',altKey:true,preventDefault(){}});
 console.log(JSON.stringify({cues:editor.state().A1,otherSame:other===JSON.stringify(editor.state().A1.find(c=>c.id===2)),
  dirty:editor.dirty(),advanced:document.body.classList.contains('editing'),message:$('edsave').textContent}));
})();
""", "known.A1.fill(1);ctx.pending=()=>0;ctx.ENV.A1.fill(180,44,48);ctx.ENV.A1.fill(180,64,68);")
    assert not result["advanced"] and result["dirty"] and result["otherSame"]
    split = [c for c in result["cues"] if c["id"] != 2]
    assert len(split) == 2 and [(c["text"], c["st"]) for c in split] == [("第一句。", 0), ("第二句。", 0)]
    assert all(c["orig"] == "完整原始逐字稿" for c in split)
    assert [(c["start"], c["end"]) for c in split] == [(10.9, 12.15), (15.9, 17.15)]
    assert "依波形拆成 2 句" in result["message"]


def test_physical_alt_w_and_bracket_shortcuts_work_with_non_latin_key_values_without_tools_mode():
    result = in_workbench(r"""
(async()=>{
 const data=doc();data.state.A1=[{...cue,start:10,end:20,text:'第一句。第二句。',orig:'原始全文'}];await load(data);selectCue(1);
 let prevented=0;listeners.keydown({target:{tagName:'TEXTAREA',closest:()=>null},key:'ц',code:'KeyW',altKey:true,preventDefault(){prevented++;}});
 const split=editor.state().A1.map(c=>({...c}));au.currentTime=11.5;
 listeners.keydown({target:{tagName:'TEXTAREA',closest:()=>null},key:'」',code:'BracketRight',altKey:true,preventDefault(){prevented++;}});
 console.log(JSON.stringify({split,cues:editor.state().A1,prevented,advanced:document.body.classList.contains('editing')}));
})();
""", "known.A1.fill(1);ctx.pending=()=>0;ctx.ENV.A1.fill(180,44,48);ctx.ENV.A1.fill(180,64,68);")
    assert result["prevented"] == 2 and not result["advanced"]
    assert len(result["split"]) == 2 and result["split"][0]["end"] == 12.15
    assert result["cues"][0]["end"] == 11.5 and result["cues"][0]["st"] == 0
    assert result["cues"][0]["orig"] == result["cues"][1]["orig"] == "原始全文"
    assert result["cues"][1] == result["split"][1]


@pytest.mark.parametrize("ime_event", [{"isComposing": True}, {"keyCode": 229}])
def test_ime_key_events_never_trigger_physical_alt_actions(ime_event):
    result = in_workbench(r"""
(async()=>{
 const data=doc();data.state.A1[0]={...cue,start:10,end:14,text:'第一句。第二句。'};await load(data);selectCue(1);au.currentTime=12;
 const before=JSON.stringify({state:editor.state(),cache:[...storage.entries()],dirty:editor.dirty()});let prevented=0;
 for(const [key,code] of [['ц','KeyW'],['」','BracketRight'],['Delete','Delete']])listeners.keydown({
  target:{tagName:'TEXTAREA',closest:()=>null},key,code,altKey:true,preventDefault(){prevented++;},...IME});
 console.log(JSON.stringify({same:before===JSON.stringify({state:editor.state(),cache:[...storage.entries()],dirty:editor.dirty()}),prevented,
  advanced:document.body.classList.contains('editing')}));
})();
""".replace("IME", json.dumps(ime_event)))
    assert result == {"same": True, "prevented": 0, "advanced": False}


def test_alt_delete_is_available_without_tools_mode_and_physical_alt_z_undo_restores_exact_cue():
    result = in_workbench(r"""
(async()=>{
 const data=doc();data.state.A1=[{...cue,start:10,end:14,text:'刪除後可復原',orig:'原始字幕'},
  {...cue,id:2,start:30,end:33,text:'保留句'}];data.uid=3;await load(data);selectCue(1);
 const before=JSON.stringify(editor.state()),prompts=[];global.confirm=message=>{prompts.push(message);return true;};let prevented=0;
 listeners.keydown({target:{tagName:'TEXTAREA',closest:()=>null},key:'Delete',code:'Delete',altKey:true,preventDefault(){prevented++;}});
 const afterDelete=editor.state().A1.map(c=>({...c}));
 listeners.keydown({target:{tagName:'TEXTAREA',closest:()=>null},key:'я',code:'KeyZ',altKey:true,preventDefault(){prevented++;}});
 console.log(JSON.stringify({afterDelete,same:before===JSON.stringify(editor.state()),prompts,prevented,
  advanced:document.body.classList.contains('editing')}));
})();
""")
    assert [cue["text"] for cue in result["afterDelete"]] == ["保留句"]
    assert result["same"] and result["prompts"] == ["刪除這一句？"] and result["prevented"] == 2 and not result["advanced"]


def test_first_input_is_real_unreviewed_channel_bound_cue_without_advanced_mode():
    result = in_workbench(r"""
(async()=>{
 await settle();au.currentTime=12;type('左舵十度');
 const cached=JSON.parse(storage.get('bound-key'));
 console.log(JSON.stringify({cue:editor.state().A1[0],cached:cached.state.A1[0],dirty:editor.dirty(),
  label:$('edcomposeTime').textContent,data:{...$('edcomposer').dataset},toolHidden:offStates.at(-1),paused:au.paused}));
})();
""")
    assert result["cue"] == result["cached"]
    assert result["cue"]["text"] == "左舵十度" and result["cue"]["st"] == 0
    assert result["cue"]["start"] == 12 and result["cue"]["end"] == 14
    assert result["dirty"] and result["paused"] and result["toolHidden"]
    assert result["data"] == {"channel": "A1", "start": "12", "end": "14"}
    assert result["label"] == "A1 · 08:00:12 → 08:00:14"


def test_enter_completes_shift_enter_keeps_multiline_and_ime_enter_is_not_hijacked():
    result = in_workbench(r"""
(async()=>{
 await settle();au.currentTime=15;type('第一行\n第二行');
 const shift=key({shiftKey:true}),before=editor.state().A1.length;
 composer.events.compositionstart();type('第一行\n第二行引');
 const ime=key({isComposing:true,keyCode:229});listeners.keydown(ime);
 const during={value:composer.value,length:editor.state().A1.length,paused:au.paused,disabled:$('edcomposeAdd').disabled};
 composer.events.compositionend();const enter=key();
 console.log(JSON.stringify({shiftPrevented:shift.prevented,imePrevented:ime.prevented,before,during,
  value:composer.value,text:editor.state().A1[0].text,count:editor.state().A1.length,enterPrevented:enter.prevented,stopped:enter.stopped}));
})();
""")
    assert not result["shiftPrevented"] and not result["imePrevented"]
    assert result["during"] == {"value": "第一行\n第二行引", "length": 1, "paused": True, "disabled": True}
    assert result["before"] == result["count"] == 1 and result["value"] == ""
    assert result["text"] == "第一行\n第二行引" and result["enterPrevented"] and result["stopped"]


def test_global_text_shortcuts_leave_composer_alone_but_escape_can_pause():
    result = in_workbench(r"""
(async()=>{
 await settle();type('核聽中');const enter=key({ctrlKey:true});
 listeners.keydown({target:composer,key:'Enter',altKey:true,preventDefault(){throw Error('hijacked Alt+Enter');}});
 listeners.keydown({target:composer,key:'w',altKey:true,preventDefault(){throw Error('hijacked Alt+W');}});
 listeners.keydown({target:composer,key:'Escape',preventDefault(){}});
 const playing=!au.paused;listeners.keydown({target:composer,key:'Escape',preventDefault(){}});
 console.log(JSON.stringify({count:editor.state().A1.length,status:editor.state().A1[0].st,playing,paused:au.paused,pauses}));
})();
""")
    assert result["count"] == 1 and result["status"] == 0
    assert result["playing"] and result["paused"] and result["pauses"] == [0]


def test_physical_alt_p_toggles_playback_from_composer_with_non_latin_key_without_finishing_text():
    result = in_workbench(r"""
(async()=>{
 await settle();au.currentTime=9;type('仍在輸入的聽打');const before=JSON.stringify(editor.state());let prevented=0;
 const event=()=>({target:composer,key:'з',code:'KeyP',altKey:true,preventDefault(){prevented++;}});
 listeners.keydown(event());const playing=!au.paused;listeners.keydown(event());
 console.log(JSON.stringify({playing,paused:au.paused,prevented,text:composer.value,same:before===JSON.stringify(editor.state()),pauses}));
})();
""")
    assert result == {"playing": True, "paused": True, "prevented": 2, "text": "仍在輸入的聽打", "same": True, "pauses": [9]}


def test_completing_during_playback_uses_last_playback_time_without_pausing():
    result = in_workbench(r"""
(async()=>{
 await settle();au.currentTime=10;await au.play();type('請備便');
 audioTime=17;audioEvent('timeupdate');await settle();$('edcomposeAdd').onclick();
 console.log(JSON.stringify({cue:editor.state().A1[0],paused:au.paused,pauses,value:composer.value}));
})();
""")
    assert result["cue"]["start"] == 10 and result["cue"]["end"] == 17
    assert not result["paused"] and result["pauses"] == [] and result["value"] == ""


def test_playing_same_chunk_seek_timeupdate_before_seeked_does_not_extend_to_target():
    result = in_workbench(r"""
(async()=>{
 await settle();au.currentTime=50;await au.play();type('舵正');
 audioTime=51;audioEvent('timeupdate');await settle();
 audioTime=70;audioEvent('timeupdate');audioEvent('seeked');await settle();
 console.log(JSON.stringify({cue:editor.state().A1[0],time:au.currentTime,value:composer.value,paused:au.paused}));
})();
""")
    assert result["cue"]["start"] == 50 and result["cue"]["end"] == 51
    assert result["time"] == 70 and result["value"] == "" and not result["paused"]


def test_paused_seek_and_channel_switch_keep_old_channel_and_timestamp():
    result = in_workbench(r"""
(async()=>{
 await settle();au.currentTime=40;type('A1記錄');label('C1').onclick();await settle();
 au.currentTime=50;type('C1記錄');au.currentTime=70;await settle();
 console.log(JSON.stringify({a:editor.state().A1[0],c:editor.state().C1[0],selected:sel.value,
  value:composer.value,data:{...$('edcomposer').dataset},changes,paused:au.paused}));
})();
""")
    assert result["a"]["text"] == "A1記錄" and result["a"]["start"] == 40 and result["a"]["end"] == 42
    assert result["c"]["text"] == "C1記錄" and result["c"]["start"] == 50 and result["c"]["end"] == 52
    assert result["selected"] == "C1" and result["changes"] == ["C1"] and result["paused"]
    assert result["value"] == "" and result["data"] == {"channel": "C1", "start": "70", "end": ""}


def test_save_without_enter_captures_current_cue_and_reopen_preserves_it():
    result = in_workbench(r"""
(async()=>{
 await settle();au.currentTime=12;type('無須先按新增');await $('saveP').onclick();
 const saved=written[0],dirty=editor.dirty(),cleared=composer.value;
 const loaded=JSON.parse(JSON.stringify(saved));loaded.state.A1[0].text='重開後';await load(loaded);
 console.log(JSON.stringify({saved:saved.state.A1[0],dirty,cleared,reopened:editor.state().A1[0],count:written.length}));
})();
""", "const written=[];VDRProject.saveProject=async args=>{written.push(JSON.parse(JSON.stringify(args.data)));return {saved:true,handle:{name:'case.vdr.json'}};};")
    assert result["saved"]["text"] == "無須先按新增" and result["saved"]["start"] == 12 and result["saved"]["end"] == 14
    assert not result["dirty"] and result["cleared"] == "" and result["count"] == 1
    assert result["reopened"] == {**result["saved"], "text": "重開後"}


def test_undo_removes_one_composer_cue_and_existing_row_edits_still_invalidate_review():
    result = in_workbench(r"""
(async()=>{
 await settle();editor.state().A1.push({...cue,id:100,text:'既有',st:1});editor.changed();
 au.currentTime=20;type('新增');$('edundo').onclick();
 const afterUndo=JSON.parse(JSON.stringify(editor.state().A1));
 $('edlist').events.input({target:{value:'既有修正',classList:{contains:()=>true},style:{},scrollHeight:20,
  closest:()=>({dataset:{id:'100'}})}});
 console.log(JSON.stringify({afterUndo,edited:editor.state().A1[0],value:composer.value}));
})();
""")
    assert len(result["afterUndo"]) == 1 and result["afterUndo"][0]["text"] == "既有" and result["afterUndo"][0]["st"] == 1
    assert result["edited"]["text"] == "既有修正" and result["edited"]["st"] == 0
    assert result["value"] == ""


def test_ime_blur_defers_completion_until_composition_end_without_losing_final_text():
    result = in_workbench(r"""
(async()=>{
 await settle();au.currentTime=9;composer.events.compositionstart();type('引');composer.events.blur();
 const before=composer.value;composer.value='引水人';composer.events.compositionend();
 console.log(JSON.stringify({before,value:composer.value,cue:editor.state().A1[0],count:editor.state().A1.length}));
})();
""")
    assert result["before"] == "引" and result["value"] == "" and result["count"] == 1
    assert result["cue"]["text"] == "引水人" and result["cue"]["start"] == 9 and result["cue"]["end"] == 11


def test_ime_inputs_and_blur_on_own_new_row_keep_the_original_textarea_target_alive():
    result = in_row_workbench(r"""
(async()=>{
 await settle();au.currentTime=9;document.activeElement=composer;composer.events.compositionstart();type('引');
 const originalRow=subtitleRow(1),target=subtitleText(1),writesBefore=htmlWrites.length;
 type('引水');type('引水人');
 const during={sameRow:subtitleRow(1)===originalRow,sameText:subtitleText(1)===target,text:target.value};
 rowEvent('pointerdown',target);composer.events.blur();composer.value='引水人，左滿舵';composer.events.compositionend();
 const afterEnd={sameRow:subtitleRow(1)===originalRow,sameText:subtitleText(1)===target,
  connected:originalRow.parentNode===$('edlist')&&target.parentNode===originalRow,text:target.value};
 document.activeElement=target;rowEvent('focusin',target);rowEvent('click',target);await settle();
 console.log(JSON.stringify({during,afterEnd,sameFinal:subtitleText(1)===target,writes:htmlWrites.slice(writesBefore),
  cue:editor.state().A1[0],cached:JSON.parse(storage.get('bound-key')).state.A1[0],value:composer.value,playCalls,time:au.currentTime}));
})();
""", r"""
// Replacing innerHTML must destroy old native targets; the base shared mock
// intentionally lacks that behavior, so this regression installs it locally.
const htmlProperty=Object.getOwnPropertyDescriptor(Element.prototype,'innerHTML'),htmlWrites=[];
Object.defineProperty(Element.prototype,'innerHTML',{get:htmlProperty.get,set(value){
 if(this.id==='edlist'||this.dataset.id)htmlWrites.push({id:this.id,cue:this.dataset.id||null});
 if(this.tx)this.tx.parentNode=null;delete this.tx;delete this._badge;delete this._button_play;delete this._button_acc;
 for(const child of this.children)child.parentNode=null;htmlProperty.set.call(this,value);
}});
""")
    assert result["during"] == {"sameRow": True, "sameText": True, "text": "引水人"}
    assert result["afterEnd"] == {"sameRow": True, "sameText": True, "connected": True, "text": "引水人，左滿舵"}
    assert result["sameFinal"] and result["writes"] == [] and result["value"] == ""
    assert result["cue"]["text"] == "引水人，左滿舵" and result["cue"]["start"] == 9 and result["cue"]["end"] == 11
    assert result["cached"] == result["cue"] and result["playCalls"] == 1 and result["time"] == 8.85


def test_corrected_clock_can_cross_midnight_without_changing_raw_cue_seconds():
    result = in_workbench(r"""
(async()=>{
 await settle();au.currentTime=5;type('跨日記錄');const label=$('edcomposeTime').textContent,title=$('edcomposeTime').title;
 key();console.log(JSON.stringify({label,title,cue:editor.state().A1[0]}));
})();
""", "ctx.T0=Date.parse('2026-10-23T15:59:50Z')/1000;ctx.ofs=()=>30;")
    assert result["label"] == "A1 · 00:00:25 → 00:00:27" and "2026-10-24" in result["title"]
    assert result["cue"]["start"] == 5 and result["cue"]["end"] == 7


def test_auto_buffer_seek_events_leave_composer_text_and_channel_open():
    result = in_workbench(r"""
(async()=>{
 await settle();au.currentTime=58;await au.play();type('跨緩衝繼續輸入');
 audioTime=59.8;audioEvent('timeupdate');await settle();
 for(const fn of audioEvents.seeking||[])fn({type:'seeking',reason:'buffer'});
 audioTime=60;audioEvent('timeupdate');
 for(const fn of audioEvents.seeked||[])fn({type:'seeked',reason:'buffer'});
 await settle();const preserved=composer.value;type('跨緩衝繼續輸入完成');
 audioTime=63;audioEvent('timeupdate');await settle();key();
 console.log(JSON.stringify({preserved,cue:editor.state().A1[0],count:editor.state().A1.length,paused:au.paused}));
})();
""")
    assert result["preserved"] == "跨緩衝繼續輸入" and result["count"] == 1 and not result["paused"]
    assert result["cue"]["text"] == "跨緩衝繼續輸入完成" and result["cue"]["start"] == 58 and result["cue"]["end"] == 63


def test_player_seek_reason_distinguishes_user_channel_and_automatic_buffer():
    result = run_player_node("""
const st=store([record(0),record(1),record(2)],180);st.channels.C2=st.channels.C1;
const read=st.readRange;st.readRange=(ch,...args)=>read('C1',...args);
const native=new FakeAudio(),p=S.createPlayer({element:native,store:st,channel:'C1'}),events=[];
await p.ready;
function changed(e){events.push({type:e.type,reason:e.reason,target:e.target===p,current:e.currentTarget===p,bound:this===p});}
p.addEventListener('seeking',changed);p.addEventListener('seeked',changed);
await p.prepare(10);const same=events.splice(0);
await p.prepare(75);const across=events.splice(0);
await p.setChannel('C2');const channel=events.splice(0);
await p.play();native.currentTime=59.9;native.fire('timeupdate');native.fire('ended');
for(let i=0;i<20&&p.seeking;i++)await tick();const buffer=events.splice(0);
native.fire('seeked');const forwarded=events.splice(0);
console.log(JSON.stringify({same,across,channel,buffer,forwarded,paused:p.paused,time:p.currentTime}));p.destroy();
""")
    assert [(e["type"], e["reason"]) for e in result["same"]] == [("seeked", "user")]
    for name, reason in [("across", "user"), ("channel", "channel"), ("buffer", "buffer")]:
        assert [(e["type"], e["reason"]) for e in result[name]] == [("seeking", reason), ("seeked", reason)]
    assert [(e["type"], e["reason"]) for e in result["forwarded"]] == [("seeked", "buffer")]
    assert all(e["target"] and e["current"] and e["bound"] for name in ["same", "across", "channel", "buffer", "forwarded"] for e in result[name])
    assert not result["paused"] and result["time"] == 120


def test_player_seek_event_listener_once_remove_and_rapid_seek_latest_reason_are_preserved():
    result = run_player_node("""
const st=store([record(0),record(1),record(2),record(3)],240,
 {decodeG726:async(ch,job)=>{await new Promise(r=>setTimeout(r,5));return decoder(ch,job)}});
st.channels.C2=st.channels.C1;const read=st.readRange;st.readRange=(ch,...args)=>read('C1',...args);
const native=new FakeAudio(),p=S.createPlayer({element:native,store:st,channel:'C1'});
await p.ready;let once=0,removed=0;const events=[],fn=()=>removed++;
p.addEventListener('seeked',()=>once++,{once:true});p.addEventListener('seeked',fn);p.removeEventListener('seeked',fn);
p.addEventListener('seeked',e=>events.push({reason:e.reason,time:p.currentTime}));
await Promise.all([p.prepare(65),p.setChannel('C2'),p.prepare(185.25)]);
console.log(JSON.stringify({once,removed,events,time:p.currentTime,paused:p.paused}));p.destroy();
""")
    assert result["once"] == 1 and result["removed"] == 0
    assert result["events"] == [{"reason": "user", "time": 185.25}]
    assert result["time"] == 185.25 and result["paused"]
