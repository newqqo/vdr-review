"""Incremental export hashes and ZIPs use bounded chunks; synthetic bytes only."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROJECT = (ROOT / 'scripts/vdr_review/src/project.js').as_posix()
NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(not NODE, reason='node unavailable')


def test_incremental_sha_matches_native_for_padding_and_arbitrary_chunk_boundaries():
    code = f"""
const P=require('{PROJECT}'),crypto=require('node:crypto');
for(const n of [0,1,55,56,63,64,65,127,128,129,1000,100000,17000003]){{
 const b=Buffer.alloc(n);for(let i=0;i<n;i++)b[i]=(i*73+19)&255;
 const h=new P.Sha256();for(let p=0;p<n;p+=1337)h.update(b.subarray(p,p+1337));
 if(h.hex()!==crypto.createHash('sha256').update(b).digest('hex'))throw Error('mismatch '+n);
}}
(async()=>{{
 const bytes=Buffer.alloc(17*1024*1024+7,47),blob=new Blob([bytes]);
 // A large Blob's arrayBuffer is deliberately unusable; hashing must stream it.
 blob.arrayBuffer=()=>{{throw Error('whole Blob allocation')}};
 const hash=await P.sha256(blob);if(hash!==crypto.createHash('sha256').update(bytes).digest('hex'))throw Error('Blob mismatch');
 const z=await P.zipEntries([{{name:'audio.wav',data:blob}}]);
 if(z.size!==blob.size+116)throw Error('ZIP size');
 console.log(JSON.stringify({{hash,zipBytes:z.size}}));
}})().catch(e=>{{console.error(e);process.exit(1)}});
"""
    run = subprocess.run([NODE, '-e', code], capture_output=True, text=True,
                         encoding='utf-8', check=True)
    assert len(json.loads(run.stdout)['hash']) == 64


def test_fractional_correction_survives_export_fields_without_time_shift():
    source = (ROOT / 'scripts/vdr_review/src/app.js').read_text(encoding='utf-8')
    formatter = source[source.index('function fmtDT('):source.index('\nfunction parseDT(')]
    review = (ROOT / 'scripts/vdr_review/src/review_state.js').as_posix()
    code = f"""
const R=require('{review}'),pad=n=>String(n).padStart(2,'0'),TZ=28800;
{formatter}
const start=Date.parse('2026-01-05T13:00:51.020Z'),offset=2300;
const text=fmtDT(start+offset),raw=R.parseDateTime(text,TZ)-offset;
if(raw!==start)throw Error('fraction lost '+text);
console.log(JSON.stringify({{text,raw,start}}));
"""
    run = subprocess.run([NODE, '-e', code], capture_output=True, text=True,
                         encoding='utf-8', check=True)
    result = json.loads(run.stdout)
    assert result['text'] == '2026-01-05 21:00:53.320'
