"""GateHouse import rejects bad dates/positions and AIS unavailable sentinels."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATEHOUSE = (ROOT / "scripts" / "vdr_review" / "src" / "gatehouse.js").as_posix()
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")


def run_js(code):
    prefix = "const G=require(" + json.dumps(GATEHOUSE) + ");\n"
    result = subprocess.run([NODE, "-e", prefix + code], check=True, capture_output=True,
                            text=True, encoding="utf-8")
    return json.loads(result.stdout)


def test_gatehouse_dates_reject_rollover_leap_seconds_and_bad_offsets():
    result = run_js("""
const invalid=['2026-02-29T08:00:00Z','2026-02-30T08:00:00Z','2026-04-31T08:00:00Z',
'2026-00-01T08:00:00Z','2026-13-01T08:00:00Z','2026-01-00T08:00:00Z',
'2026-01-01T24:00:00Z','2026-01-01T08:60:00Z','2026-01-01T08:00:60Z',
'2026-01-01T08:00:00+08:60','2026-01-01T08:00:00+14:01','2026-01-01T08:00:00+24:00',
'2026-01-01T08:00:00-15:00','30 Feb 2026 08:00:00 CST','01 Foo 2026 08:00:00 CST',
'01 Jan 2026 08:00:00 WTF','01 Jan 2026 24:00:00 CST','31 Apr 2026 08:00:00 UTC'];
console.log(JSON.stringify({invalid:invalid.map(s=>G.parseTime(s,8)),
badDefault:[NaN,Infinity,-Infinity,15,8.00001].map(z=>G.parseTime('2026-01-01 08:00:00',z))}));
""")
    assert result["invalid"] == [None] * 18
    assert result["badDefault"] == [None] * 5


def test_gatehouse_valid_time_formats_and_real_leap_day_are_preserved():
    result = run_js("""
const strings=['2026-01-01T16:07:02+08:00','2026-01-01T16:07:02+0800',
'2026/1/1 16:07:02','01 Jan 2026 16:07:02 CST','01 Jan 2026 08:07:02 UTC','2026-01-01T08:07:02Z'];
console.log(JSON.stringify({same:strings.map(s=>G.parseTime(s,8)),
leap:G.parseTime('2024-02-29T00:00:00Z',8),fraction:G.parseTime('2026-01-01T08:07:02.123456Z',8),
india:G.parseTime('2026-01-01T13:37:02+05:30',8),explicit:G.parseTime('2026-01-01T08:07:02Z',NaN)}));
""")
    assert result["same"] == [1767254822000] * 6
    assert result["leap"] == 1709164800000
    assert result["fraction"] == 1767254822123
    assert result["india"] == result["explicit"] == 1767254822000


def test_csv_invalid_positions_and_times_are_skipped_not_plotted():
    result = run_js("""
const head='Timestamp,MMSI,Latitude,Longitude,SOG,COG,Heading';
const values=[['2026-01-01T08:00:00Z','23.5','120.2'],
['2026-02-30T08:00:00Z','23.5','120.2'],['2026-01-01T08:00:00Z','NaN','120.2'],
['2026-01-01T08:00:00Z','23.5','NaN'],['2026-01-01T08:00:00Z','Infinity','120.2'],
['2026-01-01T08:00:00Z','23.5','-Infinity'],['2026-01-01T08:00:00Z','91','120.2'],
['2026-01-01T08:00:00Z','23.5','181'],['2026-01-01T08:00:00Z','   ','120.2'],
['2026-01-01T08:00:00Z','23.5','garbage'],['2026-01-01T08:00:00Z','0x23','120.2']];
const csv=[head,...values.map(([t,lat,lon])=>[t,'999000001',lat,lon,1,2,3].join(','))].join('\\n');
const r=G.parseCsvText(csv),points=Object.values(r.tracks).flat();
console.log(JSON.stringify({rows:r.rows,skipped:r.skipped,points,finite:points.every(p=>p.every(x=>x===null||Number.isFinite(x)))}));
""")
    assert result["rows"] == 1 and result["skipped"] == 10 and result["finite"]
    assert result["points"][0][1:] == [23.5, 120.2, 1, 2, 3]


def test_csv_ais_unavailable_motion_values_become_null_without_losing_positions():
    result = run_js("""
const head='Timestamp,MMSI,Latitude,Longitude,SOG,COG,Heading';
const fields=[['102.3','360','511'],['Infinity','NaN','Infinity'],['-1','-1','-1'],
['','  ','bad'],['102.2','359.9','359'],['0','0','0']];
const csv=[head,...fields.map((a,i)=>[`2026-01-01T08:00:0${i}Z`,'999000001',23.5,120.2,...a].join(','))].join('\\n');
const result=G.merge([G.parseCsvText(csv)]),points=result.tracks['999000001'];
console.log(JSON.stringify({points,rows:result.rows,skipped:result.skipped,
finite:points.every(p=>p.every(x=>x===null||Number.isFinite(x)))}));
""")
    assert result["rows"] == 6 and result["skipped"] == 0 and result["finite"]
    assert [p[3:] for p in result["points"][:4]] == [[None, None, None]] * 4
    assert result["points"][4][3:] == [102.2, 359.9, 359]
    assert result["points"][5][3:] == [0, 0, 0]


def test_csv_track_keys_cannot_inherit_object_members():
    result = run_js("""
const csv='Timestamp,MMSI,Latitude,Longitude,Ship name\\n2026-01-01T08:00:00Z,constructor,23.5,120.2,synthetic';
const parsed=G.parseCsvText(csv),merged=G.merge([parsed]);
console.log(JSON.stringify({count:merged.tracks.constructor.length,name:merged.names.constructor,rows:merged.rows}));
""")
    assert result == {"count": 1, "name": "synthetic", "rows": 1}
