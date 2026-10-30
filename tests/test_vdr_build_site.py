"""Version source, build identity and Pages site output; synthetic data only."""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "vdr_review" / "src"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="node is not installed")
sys.path.insert(0, str(ROOT / "scripts" / "vdr_review"))
import build as BUILD  # noqa: E402

VERSION = (ROOT / "scripts/vdr_review/VERSION").read_text(encoding="utf-8").strip()


def embedded_build_info(html):
    return json.loads(re.search(r"const BUILD_INFO=(\{.*?\});\n", html).group(1))


def run_node(code):
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


def test_version_file_is_plain_semver():
    assert BUILD.SEMVER.fullmatch(VERSION)
    assert BUILD.read_version() == VERSION


@pytest.mark.parametrize("value", ["", "1.0", "v1.0.0", "1.0.0-rc.1", "01.0.0", "1.0.0.0", "1.0.x"])
def test_malformed_version_stops_the_build(tmp_path, monkeypatch, value):
    file = tmp_path / "VERSION"
    file.write_text(value + "\n", encoding="utf-8")
    monkeypatch.setattr(BUILD, "VERSION_FILE", file)
    with pytest.raises(ValueError, match="X.Y.Z"):
        BUILD.build()


def test_build_info_is_an_object_embedded_in_the_html():
    info = BUILD.build_info()
    assert set(info) == {"version", "commit", "source_digest", "built_at"}
    assert info["version"] == VERSION
    assert re.fullmatch(r"[0-9a-f]{7,}|unknown", info["commit"])
    assert re.fullmatch(r"[0-9a-f]{12}", info["source_digest"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", info["built_at"])
    assert embedded_build_info(BUILD.build(info)) == info


@needs_node
def test_about_line_shows_version_commit_and_utc_build_time():
    source = (SRC / "app.js").read_text(encoding="utf-8")
    line = next(line for line in source.splitlines() if "$('buildinfo').textContent" in line)
    # The About page is readable before any recording loads, so set this at startup.
    assert line.startswith("$(") and source.index(line) < source.index("async function startMain(")
    code = f"""
const BUILD_INFO={{version:'1.2.3',commit:'abc1234',source_digest:'d',built_at:'2026-01-02T03:04:05Z'}};
const box={{}},$=()=>box;
{line}
console.log(JSON.stringify(box.textContent));"""
    assert run_node(code) == "v1.2.3 · git abc1234 · 2026-01-02 03:04 UTC"


@needs_node
def test_case_file_keeps_the_build_object_and_still_accepts_an_older_string():
    code = f"""
const P=require({json.dumps((SRC / "project.js").as_posix())});
const base={{version:4,recording_id:'a'.repeat(64),t0_utc:'2026-01-01T00:00:00.000Z',state:{{}}}};
const info={{version:'1.0.0',commit:'abc1234',source_digest:'d'.repeat(12),built_at:'2026-01-01T00:00:00Z'}};
console.log(JSON.stringify({{info,
 current:P.createProject({{...base,tool_build:info}}).tool_build,
 older:P.createProject({{...base,tool_build:'2026-01-01 00:00 git abc1234 source dddddd (local build)'}}).tool_build}}));"""
    result = run_node(code)
    assert result["current"] == result["info"]
    assert result["older"].endswith("(local build)")


def test_site_is_complete_and_version_json_matches_the_html(tmp_path, monkeypatch):
    default = tmp_path / "default" / "tool.html"
    monkeypatch.setattr(BUILD, "DEFAULT_OUT", default)
    site = tmp_path / "_site"
    assert BUILD.main(["--site", str(site)]) == 0
    assert {path.name for path in site.iterdir()} == {"index.html", "vdr-review.html", "version.json", "VDR核聽工具_同事版.zip"}
    payload = (site / "index.html").read_bytes()
    assert payload == (site / "vdr-review.html").read_bytes()
    assert json.loads((site / "version.json").read_text(encoding="utf-8")) == {
        "version": VERSION, "released": embedded_build_info(payload.decode("utf-8"))["built_at"][:10],
        "html": "vdr-review.html", "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
        "notes_url": f"https://github.com/newqqo/vdr-review/releases/tag/v{VERSION}"}
    with zipfile.ZipFile(site / "VDR核聽工具_同事版.zip") as archive:
        assert archive.testzip() is None
        assert archive.read("開啟VDR核聽工具.html") == payload
    assert not default.parent.exists()


def test_site_can_be_rebuilt_but_refuses_files_it_did_not_create(tmp_path, capsys):
    site = tmp_path / "_site"
    assert BUILD.main(["--site", str(site)]) == 0
    assert BUILD.main(["--site", str(site)]) == 0
    manifest = json.loads((site / "version.json").read_text(encoding="utf-8"))
    assert manifest["sha256"] == hashlib.sha256((site / "vdr-review.html").read_bytes()).hexdigest()
    (site / "index.html").unlink()
    (site / "private.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        BUILD.main(["--site", str(site)])
    assert "private.json" in capsys.readouterr().err
    assert not (site / "index.html").exists()


def test_release_tag_must_match_version_and_the_check_builds_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(BUILD, "DEFAULT_OUT", tmp_path / "output" / "tool.html")
    major, minor, patch = VERSION.split(".")
    assert BUILD.main(["--check-tag", f"v{VERSION}"]) == 0
    for tag in (VERSION, f"v{int(major) + 1}.{minor}.{patch}", f"v{major}.{minor}", f"v{VERSION}-rc.1", "", f"refs/tags/v{VERSION}"):
        with pytest.raises(SystemExit) as stopped:
            BUILD.main(["--check-tag", tag])
        assert stopped.value.code == 2
        assert "does not match VERSION" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("extra", [["--out", "x.html"], ["--package", "x.zip"]])
def test_site_cannot_be_combined_with_other_outputs(tmp_path, monkeypatch, extra):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        BUILD.main(["--site", "_site", *extra])
    assert list(tmp_path.iterdir()) == []
