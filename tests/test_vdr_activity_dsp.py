"""Envelope activity segmentation distinguishes quiet audio from unknown bins."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DSP = (ROOT / "scripts" / "vdr_review" / "src" / "dsp.js").as_posix()
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")


def run_node(code):
    result = subprocess.run(
        [NODE, "-e", f"const D=require('{DSP}');\n{code}"],
        check=True, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    return json.loads(result.stdout)


def test_short_known_quiet_gap_bridges_but_unknown_gap_does_not():
    result = run_node("""
const env=Uint8Array.from([...Array(20).fill(180),...Array(10).fill(0),...Array(20).fill(180)]);
const known=new Uint8Array(env.length).fill(1);
const quiet=D.segments(env,64,1.5,.5,known);
known.fill(0,20,30);
console.log(JSON.stringify({quiet,unknown:D.segments(env,64,1.5,.5,known),legacy:D.segments(env)}));
""")
    assert result["quiet"] == [[0, 2.5]]
    assert result["legacy"] == result["quiet"]
    assert result["unknown"] == [[0, 1], [1.5, 2.5]]


def test_single_unknown_bin_immediately_splits_even_when_envelope_is_loud():
    result = run_node("""
const env=new Uint8Array(41).fill(255),known=new Uint8Array(41).fill(1);
known[20]=0;
console.log(JSON.stringify(D.segments(env,64,1.5,.5,known)));
""")
    assert result == [[0, 1], [1.05, 2.05]]


def test_unknown_loud_bins_neither_start_nor_extend_a_segment():
    result = run_node("""
const env=new Uint8Array(60).fill(255),known=new Uint8Array(60);
known.fill(1,20,40);
console.log(JSON.stringify(D.segments(env,64,1.5,.5,known)));
""")
    assert result == [[1, 2]]


def test_short_segments_either_side_of_unknown_are_not_joined_to_pass_minimum():
    result = run_node("""
const env=new Uint8Array(17).fill(200),known=new Uint8Array(17).fill(1);
known[8]=0;
console.log(JSON.stringify({masked:D.segments(env,64,1.5,.5,known),legacy:D.segments(env)}));
""")
    assert result["masked"] == []
    assert result["legacy"] == [[0, 0.85]]


def test_known_values_at_or_below_threshold_are_not_sound_activity():
    result = run_node("""
const known=new Uint8Array(80).fill(1);
console.log(JSON.stringify([0,1,63,64].map(value=>D.segments(new Uint8Array(80).fill(value),64,1.5,.5,known))));
""")
    assert result == [[], [], [], []]


def test_empty_and_short_masks_conservatively_leave_unmarked_bins_unknown():
    result = run_node("""
console.log(JSON.stringify({
 empty:D.segments(new Uint8Array(),64,1.5,.5,new Uint8Array()),
 unknown:D.segments(new Uint8Array(40).fill(255),64,1.5,.5,new Uint8Array()),
 short:D.segments(new Uint8Array(40).fill(255),64,1.5,.5,new Uint8Array(20).fill(1))
}));
""")
    assert result == {"empty": [], "unknown": [], "short": [[0, 1]]}


def test_legacy_results_unchanged_without_mask_or_with_all_known_bins():
    result = run_node("""
function oldSegments(env,th=64,gap=1.5,minDur=.5){
 const segs=[];let st=-1,last=-1;
 for(let k=0;k<=env.length;k++){
  const on=k<env.length&&env[k]>th;
  if(on){if(st<0)st=k;last=k;}
  else if(st>=0&&(k===env.length||(k-last)/D.ENV_RATE>gap)){
   if((last+1-st)/D.ENV_RATE>=minDur)segs.push([st/D.ENV_RATE,(last+1)/D.ENV_RATE]);
   st=-1;
  }
 }
 return segs;
}
let seed=19,checked=0;
for(const length of [0,1,9,20,71,1000]){
 const env=Uint8Array.from({length},()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed>>>24});
 for(const [th,gap,minDur] of [[64,1.5,.5],[128,.05,0],[220,0,.1],[0,5,1]]){
  const expected=JSON.stringify(oldSegments(env,th,gap,minDur));
  for(const known of [undefined,null,new Uint8Array(length).fill(1)]){
   if(JSON.stringify(D.segments(env,th,gap,minDur,known))!==expected)throw new Error('legacy mismatch');
   checked++;
  }
 }
}
console.log(JSON.stringify(checked));
""")
    assert result == 72


def test_envelope_only_scan_preserves_inputs_and_allocates_no_pcm_arrays():
    result = run_node("""
const env=new Uint8Array(4*3600*D.ENV_RATE),known=new Uint8Array(env.length).fill(1);
env.fill(180,3600*D.ENV_RATE,3601*D.ENV_RATE);
const before=Buffer.from(env),knownBefore=Buffer.from(known);
// Segment detection must consume the small envelope, not allocate decoded audio.
for(const name of ['Int16Array','Float32Array','Float64Array','ArrayBuffer']){
 global[name]=function(){throw new Error('unexpected PCM allocation: '+name)};
}
const segments=D.segments(env,64,1.5,.5,known);
console.log(JSON.stringify({segments,unchanged:Buffer.from(env).equals(before)&&Buffer.from(known).equals(knownBefore)}));
""")
    assert result == {"segments": [[3600, 3601]], "unchanged": True}
