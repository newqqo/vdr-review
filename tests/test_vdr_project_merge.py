"""Pure, synthetic source-bound progress merges; no case files or browser I/O."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROJECT = (ROOT / "scripts" / "vdr_review" / "src" / "project.js").as_posix()
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")
PRELUDE = """
const P=require(PROJECT_PATH),hash='a'.repeat(64);
function cue(id,start,end,text='synthetic',more={}){
 return {id,start,end,text,spk:'A',st:0,orig:'source',origNo:null,ovl:false,...more};
}
function project(cues=[],more={}){
 return {version:4,recording_id:hash,t0_utc:'2026-10-22T00:00:00.000Z',tz_hours:8,
 state:{A1:cues},uid:Math.max(0,...cues.map(c=>c.id))+1,spks:['A'],spkColor:{A:'#123456'},
 extra:{events:'current settings',other:'',fixed:false,time_offset_s:2.3,time_offset_basis:'synthetic'},...more};
}
const errorOf=f=>{try{f();return ''}catch(e){return e.message}};
""".replace("PROJECT_PATH", json.dumps(PROJECT))


def run_js(code):
    result = subprocess.run([NODE, "-e", PRELUDE + code], check=True, capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    return json.loads(result.stdout)


def test_front_middle_and_late_progress_merge_without_changing_current_metadata():
    result = run_js("""
const a=project([cue(7,0,2,'front'),cue(2,7000,7002,'middle')],{uid:90,tool:'current',
 saved_at:'keep this stamp',custom:{keep:['all','values']}});
const b=project([cue(7,14000,14002,'late')],{uid:1000,tz_hours:-4,tool:'incoming',extra:{events:'replace?'}});
const aBefore=JSON.stringify(a),bBefore=JSON.stringify(b),r=P.mergeProjects(a,b);
const settings=({state,uid,spks,spkColor,import_history,...rest})=>rest;
console.log(JSON.stringify({r,unchanged:aBefore===JSON.stringify(a)&&bBefore===JSON.stringify(b),
 settings:JSON.stringify(settings(a))===JSON.stringify(settings(r.project))}));
""")
    assert result["unchanged"] and result["settings"]
    merged = result["r"]
    assert merged["stats"] == {"added": 1, "duplicates": 0, "conflicts": 0}
    assert [(c["id"], c["text"]) for c in merged["project"]["state"]["A1"]] == [(7, "front"), (2, "middle"), (90, "late")]
    assert merged["project"]["uid"] == 91 and merged["idMap"] == {"7": 90}


def test_different_t0_uses_source_clock_not_either_display_correction():
    result = run_js("""
const a=project([cue(1,0,2,'front')]);
const b=project([cue(1,-1,1,'overlap'),cue(3,60,61,'later')],{
 t0_utc:'2026-10-22T01:00:00.500Z',tz_hours:-8,
 extra:{time_offset_s:-5,time_offset_basis:'different clock'}});
console.log(JSON.stringify(P.mergeProjects(a,b)));
""")
    assert result["timeShiftSeconds"] == 3600.5
    assert [(c["start"], c["end"]) for c in result["project"]["state"]["A1"]] == [(0, 2), (3599.5, 3601.5), (3660.5, 3661.5)]
    assert result["project"]["extra"]["time_offset_s"] == 2.3


def test_nonidentical_overlap_retains_both_and_reports_pair_ids_and_intersection():
    result = run_js("""
const a=project([cue(10,0,10,'current'),cue(11,6,9,'also current')]);
const b=project([cue(10,5,12,'incoming')]);
console.log(JSON.stringify(P.mergeProjects(a,b)));
""")
    assert result["stats"] == {"added": 1, "duplicates": 0, "conflicts": 2}
    assert [c["text"] for c in result["project"]["state"]["A1"]] == ["current", "incoming", "also current"]
    assert result["conflicts"] == [
        {"channel": "A1", "currentId": 10, "incomingId": 12, "sourceIncomingId": 10, "start": 5, "end": 10},
        {"channel": "A1", "currentId": 11, "incomingId": 12, "sourceIncomingId": 10, "start": 6, "end": 9},
    ]


def test_precise_duplicates_ignore_id_and_object_key_order_and_repeat_import_is_idempotent():
    result = run_js("""
const a=project([cue(9,0,2)]),b=project([{ovl:false,origNo:null,orig:'source',st:0,spk:'A',text:'synthetic',end:2,start:0,id:1},cue(2,3,4,'new')]);
const first=P.mergeProjects(a,b),again=P.mergeProjects(first.project,b);
console.log(JSON.stringify({first,again,same:JSON.stringify(first.project)===JSON.stringify(again.project)}));
""")
    assert result["first"]["stats"] == {"added": 1, "duplicates": 1, "conflicts": 0}
    assert result["again"]["stats"] == {"added": 0, "duplicates": 2, "conflicts": 0}
    assert result["same"]
    assert result["first"]["idMap"] == {"1": 9, "2": 10}


@pytest.mark.parametrize("field,value", [("text", "different"), ("spk", "B"), ("st", 1),
                                         ("orig", "different original"), ("ovl", True),
                                         ("origNo", 7)])
def test_differences_in_any_investigation_content_are_preserved_not_deduplicated(field, value):
    result = run_js(f"""
const a=project([cue(1,0,2)]),b=project([cue(1,0,2,'synthetic',{{[{json.dumps(field)}]:{json.dumps(value)}}})]);
console.log(JSON.stringify(P.mergeProjects(a,b)));
""")
    assert result["stats"] == {"added": 1, "duplicates": 0, "conflicts": 1}
    assert len(result["project"]["state"]["A1"]) == 2


def test_id_collisions_and_forward_repetition_references_are_remapped_globally():
    result = run_js("""
const a=project([cue(1,0,2,'current')],{state:{A1:[cue(1,0,2,'current')],C1:[cue(2,3,4,'current other')]},uid:50});
const b=project([],{state:{A1:[cue(1,20,22,'reply',{repOf:3,ovl:true})],C1:[cue(3,10,12,'source')]},uid:4});
const first=P.mergeProjects(a,b),again=P.mergeProjects(first.project,b);
console.log(JSON.stringify({first,again}));
""")
    first = result["first"]
    assert first["idMap"] == {"3": 50, "1": 51}
    reply = first["project"]["state"]["A1"][-1]
    assert reply["repOf"] == 50 and reply["ovl"] is True and reply["id"] == 51
    assert first["project"]["state"]["A1"][0]["id"] == 1
    assert first["project"]["state"]["C1"][0]["id"] == 2
    assert result["again"]["stats"] == {"added": 0, "duplicates": 2, "conflicts": 0}
    assert result["again"]["project"] == first["project"]


def test_repetition_of_a_duplicate_points_to_existing_current_id():
    result = run_js("""
const a=project([cue(80,0,2,'source')],{uid:99});
const b=project([cue(1,0,2,'source'),cue(2,5,6,'reply',{repOf:1})]);
console.log(JSON.stringify(P.mergeProjects(a,b)));
""")
    assert result["stats"] == {"added": 1, "duplicates": 1, "conflicts": 0}
    assert result["idMap"] == {"1": 80, "2": 99}
    assert result["project"]["state"]["A1"][-1]["repOf"] == 80


def test_speakers_and_colors_are_union_with_current_values_and_order_preferred():
    result = run_js("""
const a=project([],{uid:40,spks:['B','A'],spkColor:{A:'#111',B:'#222'}});
const b=project([],{spks:['A','C','C'],spkColor:{A:'#abc',C:'#333'}});
console.log(JSON.stringify(P.mergeProjects(a,b)));
""")
    assert result["project"]["spks"] == ["B", "A", "C"]
    assert result["project"]["spkColor"] == {"A": "#111", "B": "#222", "C": "#333"}
    assert result["project"]["uid"] == 40


def test_touching_intervals_different_channels_and_incoming_own_overlaps_are_not_cross_file_conflicts():
    result = run_js("""
const a=project([cue(1,0,2)]);
const b=project([],{state:{A1:[cue(2,2,4,'touch'),cue(3,3,5,'internal overlap')],C1:[cue(4,0,2)]},uid:5});
console.log(JSON.stringify(P.mergeProjects(a,b)));
""")
    assert result["stats"] == {"added": 3, "duplicates": 0, "conflicts": 0}
    assert len(result["project"]["state"]["C1"]) == 1


def test_source_version_invalid_content_and_dangling_or_cyclic_references_reject_without_mutation():
    result = run_js("""
const a=project([cue(1,0,2,'keep')]),before=JSON.stringify(a),errors=[];
for(const mutate of [b=>b.recording_id='b'.repeat(64),b=>b.version=3,b=>b.version=5,
 b=>b.state.A1[0].st=5,b=>b.state.A1[0].end=0,b=>b.state.A1[0].repOf=99,
 b=>b.state.A1[0].repOf=1,b=>{b.state.A1=[cue(1,0,2,'a',{repOf:2}),cue(2,3,4,'b',{repOf:1})];b.uid=3}]){
 const b=project([cue(1,0,2)]);mutate(b);const previous=JSON.stringify(b);
 errors.push({error:errorOf(()=>P.mergeProjects(a,b)),unchanged:previous===JSON.stringify(b)});
}
console.log(JSON.stringify({errors,unchanged:before===JSON.stringify(a),invalidCurrent:errorOf(()=>P.mergeProjects({...a,uid:0},project()))}));
""")
    assert result["unchanged"] and result["invalidCurrent"]
    assert all(item["error"] and item["unchanged"] for item in result["errors"])
    assert "覆誦" in result["errors"][-1]["error"]


def test_existing_current_dangling_reference_and_all_custom_fields_remain_untouched():
    result = run_js("""
const a=project([cue(10,0,2,'keep',{repOf:777,custom:{flag:true,list:[1,2]}})],{uid:40});
const b=project([cue(1,3,4,'new')]),before=JSON.stringify(a.state.A1[0]),r=P.mergeProjects(a,b);
const same=before===JSON.stringify(r.project.state.A1[0]);r.project.state.A1[0].custom.list[0]=99;
console.log(JSON.stringify({same,detached:a.state.A1[0].custom.list[0]===1}));
""")
    assert result == {"same": True, "detached": True}


def test_excessive_conflict_pairs_fail_atomically_instead_of_allocating_unbounded_results():
    result = run_js("""
const a=project(Array.from({length:101},(_,i)=>cue(i+1,0,10,'current '+i)));
const b=project(Array.from({length:100},(_,i)=>cue(i+1,0,10,'incoming '+i)));
const before=JSON.stringify(a),error=errorOf(()=>P.mergeProjects(a,b));
console.log(JSON.stringify({error,unchanged:before===JSON.stringify(a)}));
""")
    assert "衝突過多" in result["error"] and result["unchanged"]


def test_v2_conversion_requires_explicit_calendar_day_and_preserves_synthetic_cues():
    result = run_js("""
const legacy={version:2,t0_utc_s:37193.45,state:{A1:[cue(1,95,99,'old progress')]},uid:2,
 spks:['A'],spkColor:{A:'#123456'},extra:{events:'old settings'}};
const before=JSON.stringify(legacy),r=P.migrateLegacyV2(legacy,hash,'2026-10-23');
console.log(JSON.stringify({r,unchanged:before===JSON.stringify(legacy),
 direct:errorOf(()=>P.validateImport(legacy,hash,{allowLegacy:true})),
 directMerge:errorOf(()=>P.mergeProjects(project(),legacy))}));
""")
    converted = result["r"]
    assert result["unchanged"] and result["direct"] and result["directMerge"]
    assert converted["version"] == 4 and converted["recording_id"] == "a" * 64
    assert converted["t0_utc"] == "2026-10-23T10:19:53.450Z"
    assert converted["state"]["A1"][0]["start"] == 95
    assert converted["state"]["A1"][0]["end"] == 99
    assert converted["migration"]["from_version"] == 2
    assert converted["migration"]["source_binding"] == "explicit_user_confirmation"
    assert converted["migration"]["confirmed_utc_date"] == "2026-10-23"
    assert converted["migration"]["original_t0_utc_s"] == 37193.45


def test_v2_calendar_validation_seconds_bounds_source_and_cue_validation_are_strict():
    result = run_js("""
const v2=()=>({version:2,t0_utc_s:0,state:{A1:[cue(1,0,1)]},uid:2});
const dates=['2026-02-29','2026-04-31','2026-13-01','2026-00-01','2026-01-00',
 '2026-1-01','2026-01-01T00:00:00Z','',undefined];
const dateErrors=dates.map(d=>errorOf(()=>P.migrateLegacyV2(v2(),hash,d)));
const secondErrors=[-1,86400,Infinity,NaN,'3600',null].map(s=>{
 const b=v2();b.t0_utc_s=s;return errorOf(()=>P.migrateLegacyV2(b,hash,'2026-10-23'));
});
const sourceErrors=[errorOf(()=>P.migrateLegacyV2(v2(),'invalid','2026-10-23')),
 errorOf(()=>P.migrateLegacyV2({...v2(),recording_id:'b'.repeat(64)},hash,'2026-10-23')),
 errorOf(()=>P.migrateLegacyV2({...v2(),version:3},hash,'2026-10-23')),
 errorOf(()=>P.migrateLegacyV2({...v2(),state:{A1:[cue(1,1,0)]}},hash,'2026-10-23'))];
const leap=P.migrateLegacyV2({...v2(),t0_utc_s:86399.999},hash,'2028-02-29');
console.log(JSON.stringify({dateErrors,secondErrors,sourceErrors,leap:leap.t0_utc}));
""")
    assert all(result["dateErrors"]) and all(result["secondErrors"]) and all(result["sourceErrors"])
    assert result["leap"] == "2028-02-29T23:59:59.999Z"


def test_merge_appends_migration_audit_without_changing_prior_history_or_repeating_it():
    result = run_js("""
const oldHistory={t0_utc:'2026-10-21T00:00:00.000Z',added:1,custom:{keep:true}};
const a=project([cue(10,0,2,'existing')],{import_history:[oldHistory],uid:30});
const v2={version:2,t0_utc_s:3600,state:{A1:[cue(1,-3600,-3598,'existing'),cue(2,-3599,-3597,'incoming')]},uid:3};
const incoming=P.migrateLegacyV2(v2,hash,'2026-10-22');
const r=P.mergeProjects(a,incoming),again=P.mergeProjects(r.project,incoming);
const before=JSON.stringify(a.import_history);r.project.import_history[0].custom.keep=false;
console.log(JSON.stringify({r,again,detached:before===JSON.stringify(a.import_history)}));
""")
    assert result["detached"]
    merged = result["r"]
    assert merged["stats"] == {"added": 1, "duplicates": 1, "conflicts": 1}
    history = result["again"]["project"]["import_history"]
    assert len(history) == 2 and history[0]["custom"]["keep"] is True
    assert history[1] == {
        "t0_utc": "2026-10-22T01:00:00.000Z", "timeShiftSeconds": 3600,
        "added": 1, "duplicates": 1, "conflicts": 1,
        "migration": merged["project"]["import_history"][1]["migration"],
    }
    assert history[1]["migration"]["original_t0_utc_s"] == 3600
    assert result["again"]["stats"] == {"added": 0, "duplicates": 2, "conflicts": 0}


def test_history_limit_and_invalid_history_reject_atomically_but_duplicate_only_at_limit_is_allowed():
    result = run_js("""
const a=project([cue(1,0,2)],{import_history:Array.from({length:1000},(_,i)=>({batch:i}))});
const before=JSON.stringify(a),error=errorOf(()=>P.mergeProjects(a,project([cue(1,3,4,'new')])));
const same=P.mergeProjects(a,project([cue(9,0,2)]));
const invalid=[{},'history',[null],Array.from({length:1001},()=>({}))].map(h=>
 errorOf(()=>P.mergeProjects({...a,import_history:h},project())));
console.log(JSON.stringify({error,unchanged:before===JSON.stringify(a),same,invalid}));
""")
    assert "1000" in result["error"] and result["unchanged"]
    assert all(result["invalid"])
    assert result["same"]["stats"] == {"added": 0, "duplicates": 1, "conflicts": 0}
    assert len(result["same"]["project"]["import_history"]) == 1000


def test_zero_duration_v2_requires_literal_opt_in_and_retains_text_ids_and_original_clock():
    result = run_js("""
const legacy={version:2,t0_utc_s:54000,state:{A1:[cue(8,95,95,'keep all words',{st:1})],
 A2:[cue(10,96.7,96.7,'second zero')],C1:[cue(12,100,101,'normal',{st:1})]},uid:13};
const before=JSON.stringify(legacy),denied=[undefined,{},false,{allowZeroDuration:1},{allowZeroDuration:'true'}]
 .map(options=>errorOf(()=>P.migrateLegacyV2(legacy,hash,'2026-10-23',options)));
const r=P.migrateLegacyV2(legacy,hash,'2026-10-23',{allowZeroDuration:true});
console.log(JSON.stringify({denied,r,unchanged:before===JSON.stringify(legacy)}));
""")
    assert all(result["denied"]) and result["unchanged"]
    project = result["r"]
    first = project["state"]["A1"][0]
    assert (first["id"], first["text"], first["start"], first["end"], first["st"]) == (8, "keep all words", 95, 95.1, 0)
    assert first["timing_review"] == {
        "reason": "legacy_zero_duration", "original_start": 95, "original_end": 95,
        "placeholder_seconds": 0.1, "origin_t0_utc": "2026-10-23T15:00:00.000Z",
    }
    assert project["migration"]["zero_duration_cues"] == {"count": 2, "ids": [8, 10]}
    assert project["state"]["C1"][0]["st"] == 1
    assert "timing_review" not in project["state"]["C1"][0]


def test_zero_duration_opt_in_does_not_accept_reversed_nonfinite_or_invalid_status():
    result = run_js("""
const errors=[];
for(const c of [cue(1,2,1),cue(1,NaN,NaN),cue(1,Infinity,Infinity),cue(1,3,3,'bad',{st:7}),
 cue(1,'3','3')]){
 const b={version:2,t0_utc_s:0,state:{A1:[c]},uid:2};
 errors.push(errorOf(()=>P.migrateLegacyV2(b,hash,'2026-10-23',{allowZeroDuration:true})));
}
console.log(JSON.stringify(errors));
""")
    assert all(result)


def test_timing_review_is_strict_and_cannot_claim_checked_or_other_status():
    result = run_js("""
const good=P.migrateLegacyV2({version:2,t0_utc_s:0,state:{A1:[cue(1,10,10)]},uid:2},hash,
 '2026-10-23',{allowZeroDuration:true}),errors=[];
const mutations=[c=>c.st=1,c=>c.st=2,c=>c.st=3,c=>c.timing_review=null,
 c=>c.timing_review.reason='other',c=>c.timing_review.placeholder_seconds=1,
 c=>c.timing_review.original_end=11,c=>c.timing_review.original_start='10',
 c=>c.timing_review.origin_t0_utc='2026-02-30T00:00:00.000Z',
 c=>delete c.timing_review.origin_t0_utc,c=>c.timing_review.extra=true,
 c=>c.end=12];
for(const mutate of mutations){const p=structuredClone(good);mutate(p.state.A1[0]);errors.push(errorOf(()=>P.createProject(p)));}
console.log(JSON.stringify({errors,good:P.createProject(good)}));
""")
    assert all(result["errors"])
    assert result["good"]["state"]["A1"][0]["st"] == 0


def test_merge_shifts_zero_duration_placeholder_but_preserves_origin_and_repeat_import_deduplicates():
    result = run_js("""
const a=project([cue(20,0,2,'current')]);
const legacy={version:2,t0_utc_s:3600,state:{A1:[cue(1,10,10,'zero',{st:1})]},uid:2};
const incoming=P.migrateLegacyV2(legacy,hash,'2026-10-22',{allowZeroDuration:true});
const first=P.mergeProjects(a,incoming),again=P.mergeProjects(first.project,incoming);
console.log(JSON.stringify({incoming,first,again}));
""")
    original = result["incoming"]["state"]["A1"][0]
    merged = result["first"]["project"]["state"]["A1"][-1]
    assert (merged["start"], merged["end"], merged["st"]) == (3610, 3610.1, 0)
    assert merged["timing_review"] == original["timing_review"]
    assert merged["timing_review"]["original_start"] == 10
    assert merged["timing_review"]["origin_t0_utc"] == "2026-10-22T01:00:00.000Z"
    assert result["again"]["stats"] == {"added": 0, "duplicates": 1, "conflicts": 0}
    assert result["again"]["project"] == result["first"]["project"]
    history = result["first"]["project"]["import_history"][0]
    assert history["migration"]["zero_duration_cues"] == {"count": 1, "ids": [1]}
