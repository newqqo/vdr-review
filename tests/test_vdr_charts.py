"""Chart layers from user-chosen shapefile ZIPs; synthetic data only."""
import base64
import json
import shutil
import subprocess
import sys
import zipfile
import io
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "vdr_review" / "src").as_posix()
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")
sys.path.insert(0, str(ROOT / "scripts" / "vdr_review"))
import synthetic_charts as SC  # noqa: E402


def parse(zips: dict) -> dict:
    """zips: {file name: ZIP bytes} -> {file name: layers or {"error": key}} parsed by the real module."""
    payload = json.dumps({name: base64.b64encode(data).decode() for name, data in zips.items()})
    code = f"""
const C=require({json.dumps(SRC)}+'/charts.js'),input={payload};
(async()=>{{const out={{}};
 for(const [name,b64] of Object.entries(input)){{
  try{{out[name]=await C.parseChartZip(new Uint8Array(Buffer.from(b64,'base64')),name);}}
  catch(e){{out[name]={{error:e.i18nKey,message:e.message}};}}
 }}
 console.log(JSON.stringify(out));}})();"""
    run = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(run.stdout)


def test_points_lines_and_areas_are_read_with_names_numbers_and_dates():
    layers = parse({"charts.zip": SC.chart_zip(SC.sample_layers())})["charts.zip"]
    by_name = {layer["name"]: layer for layer in layers}
    assert set(by_name) == {"light_P_1150918", "DEPCNT_L_20260918", "channel_A_1150918"}
    lights = by_name["light_P_1150918"]
    # The deleted table row hides its shape too, so rows and shapes stay matched.
    assert (lights["kind"], lights["geometry"], lights["date"], len(lights["features"])) == ("lights", "point", "2026-09-18", 2)
    assert lights["features"][0] == {"g": [22.53, 120.3], "p": {"NOBJNM": "合成燈塔甲", "OBJNAM": "SYNTHETIC Light A"}}
    depth = by_name["DEPCNT_L_20260918"]
    assert (depth["kind"], depth["geometry"], depth["date"], depth["points"]) == ("depth", "line", "2026-09-18", 7)
    assert [f["p"]["VALDCO"] for f in depth["features"]] == [5, 10]
    assert depth["features"][1]["g"] == [[[22.5, 120.29], [22.5, 120.3]], [[22.5, 120.32], [22.51, 120.33]]]
    fairway = by_name["channel_A_1150918"]
    # Without a .cpg the table is read as UTF-8 when valid, otherwise as Big5.
    assert (fairway["kind"], fairway["geometry"]) == ("channels", "polygon")
    assert fairway["features"][0]["p"] == {"NOBJNM": "合成航道", "RESTRN": "anchoring prohibited"}


def test_wrong_files_are_explained_instead_of_drawn():
    projected = {"light_P_1": {"shp": SC.shp(SC.POINT, [(170000.0, 2540000.0)]), "dbf": SC.dbf([("OBJNAM", "C", 10)], [("X",)])}}
    projected_prj = {"light_P_2": {"shp": SC.shp(SC.POINT, [(120.3, 22.5)]),
                                   "prj": 'PROJCS["TWD97_TM2",GEOGCS["GCS_TWD_1997"]]'}}
    unsupported = {"patch_1": {"shp": SC.shp(SC.POINT, [(120.3, 22.5)])[:32] + (31).to_bytes(4, "little") + SC.shp(SC.POINT, [(120.3, 22.5)])[36:]}}
    broken = {"light_P_3": {"shp": SC.shp(SC.LINE, [[[(120.3, 22.5), (120.4, 22.6)]]])[:-8]}}
    kml = io.BytesIO()
    with zipfile.ZipFile(kml, "w") as zf:
        zf.writestr("light_kml_1150918.kmz", b"PK")
    other = io.BytesIO()
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("readme.txt", "synthetic")
    result = parse({"projected.zip": SC.chart_zip(projected), "prj.zip": SC.chart_zip(projected_prj),
                    "type.zip": SC.chart_zip(unsupported), "broken.zip": SC.chart_zip(broken),
                    "kml.zip": kml.getvalue(), "other.zip": other.getvalue(), "not-a-zip.zip": b"synthetic"})
    assert {name: r["error"] for name, r in result.items()} == {
        "projected.zip": "core.charts.projection", "prj.zip": "core.charts.projection", "type.zip": "core.charts.shapeType",
        "broken.zip": "core.charts.shp", "kml.zip": "core.charts.useShp", "other.zip": "core.charts.noShapefile",
        "not-a-zip.zip": "core.gatehouse.zip"}
    assert "TWD97" in result["projected.zip"]["message"] and "SHP" in result["kml.zip"]["message"]


def test_zip_reader_handles_a_slice_of_a_larger_buffer():
    data = SC.chart_zip(SC.sample_layers())
    padded = base64.b64encode(b"\0" * 13 + data + b"\0" * 7).decode()
    code = f"""
const G=require({json.dumps(SRC)}+'/gatehouse.js'),all=new Uint8Array(Buffer.from('{padded}','base64'));
G.unzipEntries(all.subarray(13,all.length-7),n=>n.endsWith('.cpg')).then(e=>console.log(JSON.stringify(e.map(x=>[x.name,Buffer.from(x.bytes).toString()]))));"""
    run = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", check=True)
    assert json.loads(run.stdout) == [["light_P_1150918.cpg", "UTF-8"], ["DEPCNT_L_20260918.cpg", "UTF-8"]]


def test_data_date_and_kind_come_from_the_file_name():
    code = f"""
const C=require({json.dumps(SRC)}+'/charts.js');
console.log(JSON.stringify({{roc:C.dataDate('light_P_1150918'),greg:C.dataDate('x_20261231'),bad:C.dataDate('x_1151340'),none:C.dataDate('x'),
 kinds:['light_P_1','WRECKS_A_1','DEPCNT_L_1','channel_L_1','buoys_P_1'].map(C.kindOf)}}));"""
    run = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", check=True)
    assert json.loads(run.stdout) == {"roc": "2026-09-18", "greg": "2026-12-31", "bad": None, "none": None,
                                      "kinds": ["lights", "wrecks", "depth", "channels", "other"]}


def test_chart_layers_are_built_in_and_offered_before_and_after_loading():
    html = (ROOT / "scripts/vdr_review/src/app.html").read_text(encoding="utf-8")
    app = (ROOT / "scripts/vdr_review/src/app.js").read_text(encoding="utf-8")
    build = (ROOT / "scripts/vdr_review/build.py").read_text(encoding="utf-8")
    assert '"gatehouse.js", "charts.js"' in build
    loadpane = html[html.index('<section id="loadpane"'):html.index("</section>")]
    assert 'id="fchart"' in loadpane and 'id="fchartAdd"' in html and 'id="fchartAdd"' not in loadpane
    # Charts sit under the tracks on the shared canvas and are listed in the layer control.
    assert "S.addCharts(S.charts);" in app and "l.bringToBack()" in app and "layerControl.addOverlay(g, entry.text())" in app
