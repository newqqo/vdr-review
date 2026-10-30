"""Public-content check: synthetic trees only, never real case terms."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vdr_public_check", ROOT / "scripts/vdr_review/check_public.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def tree(path, files):
    for name, data in files.items():
        file = path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def rules(findings):
    return [(item.path, item.line, item.rule) for item in findings]


@pytest.mark.parametrize("name", [
    "README.md", "LICENSE", ".github/workflows/vdr-ci.yml", "scripts/vdr_review/build.py", "scripts/vdr_review/VERSION",
    "scripts/vdr_review/src/app.js", "scripts/vdr_review/vendor/leaflet/leaflet.js", "tests/test_vdr_app.py"])
def test_allowlist_accepts_tool_source(name):
    assert CHECK.is_allowed(name)


@pytest.mark.parametrize("name", [
    "local/sensitive_patterns.txt", "HANDOFF.local.md", "output/VDR.html", "data/case.json",
    "scripts/vdr_review/src/cases/a.js", "tests/fixtures/test_vdr_x.py", "tests/conftest.py",
    "scripts/vdr_review/vendor/leaflet/extra.js", "sample.wav"])
def test_allowlist_rejects_case_output_local_and_nested_files(name):
    assert not CHECK.is_allowed(name)


def test_clean_synthetic_tree_has_no_findings(tmp_path):
    files = {"README.md": "Synthetic VDR 合成資料 https://example.com/a\nnewqqo@users.noreply.github.com\n",
             "tests/test_vdr_url.py": "'https://example.com@tiles.invalid.test/x'\nC:/ffmpeg/bin/ffmpeg.exe\n"}
    root = tree(tmp_path, files)
    assert CHECK.check(root, files) == []


def test_generic_rules_report_paths_emails_and_credentials_with_line_numbers(tmp_path):
    # Split literals so this test file itself passes the check it exercises.
    files = {"README.md": "\n".join([
        "ok", "C:\\" + "Users\\someone\\case", "/ho" + "me/someone/case", "person" + "@agency.invalid-domain.org",
        "-----BEGIN OPENSSH " + "PRIVATE KEY-----", "ghp_" + "a" * 36, "AKIA" + "A" * 16])}
    root = tree(tmp_path, files)
    assert rules(CHECK.check(root, files)) == [
        ("README.md", 2, "local path"), ("README.md", 3, "local path"), ("README.md", 4, "e-mail address"),
        ("README.md", 5, "private key"), ("README.md", 6, "access token"), ("README.md", 7, "access token")]


def test_disallowed_missing_binary_and_large_files_fail_closed(tmp_path, monkeypatch):
    files = {"output/private.wav": b"RIFF", "README.md": b"\xff\xfe\0", "LICENSE": "a\0b",
             "AGENTS.md": "x" * (CHECK.MAX_BYTES + 1)}
    root = tree(tmp_path, files)
    names = [*files, "CLAUDE.md"]
    assert rules(CHECK.check(root, names)) == [
        ("output/private.wav", None, "not in the public allowlist"), ("README.md", None, "not UTF-8 text"),
        ("LICENSE", None, "binary content"), ("AGENTS.md", None, f"larger than {CHECK.MAX_BYTES} bytes"),
        ("CLAUDE.md", None, "listed by git but missing from the working tree")]
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "LICENSE" or original(self))
    assert rules(CHECK.check(root, ["LICENSE"])) == [("LICENSE", None, "symbolic link")]


def test_local_patterns_are_case_insensitive_and_skip_comments(tmp_path):
    patterns = tree(tmp_path / "p", {"local/sensitive_patterns.txt": "# comment\n\n  synthetic vessel \n(?-i:XY\\b)\n"})
    local = CHECK.load_patterns(patterns / CHECK.LOCAL_PATTERNS)
    assert [label for label, _ in local] == ["local pattern (line 3)", "local pattern (line 4)"]
    files = {"README.md": "SYNTHETIC VESSEL\nxy XY\n"}
    root = tree(tmp_path / "r", files)
    assert [(item.line, item.rule, item.text) for item in CHECK.check(root, files, local)] == [
        (1, "local pattern (line 3)", "SYNTHETIC VESSEL"), (2, "local pattern (line 4)", "XY")]
    assert CHECK.load_patterns(tmp_path / "missing.txt") == []


@pytest.mark.parametrize("line, message", [("[unclosed", "invalid pattern"), ("a*", "pattern matches empty text")])
def test_bad_local_pattern_is_rejected_with_its_line(tmp_path, line, message):
    file = tree(tmp_path, {"p.txt": "# header\n" + line + "\n"}) / "p.txt"
    with pytest.raises(ValueError, match="p.txt:2: " + message):
        CHECK.load_patterns(file)


def test_main_fails_on_findings_and_reports_scope(tmp_path, monkeypatch, capsys):
    root = tree(tmp_path, {"README.md": "clean\n", "data/case.json": "{}"})
    monkeypatch.setattr(CHECK, "candidate_files", lambda path: ["README.md"])
    assert CHECK.main(["--root", str(root)]) == 0
    assert "1 files checked, no local patterns (generic checks only), 0 findings" in capsys.readouterr().out
    tree(root, {"local/sensitive_patterns.txt": "clean\n"})
    monkeypatch.setattr(CHECK, "candidate_files", lambda path: ["README.md", "data/case.json"])
    assert CHECK.main(["--root", str(root)]) == 1
    out = capsys.readouterr().out
    assert "data/case.json: not in the public allowlist" in out and "README.md:1: local pattern (line 1): clean" in out
    assert "2 files checked, 1 local patterns, 2 findings" in out
    with pytest.raises(SystemExit):
        CHECK.main(["--root", str(root), "--patterns", str(tmp_path / "missing.txt")])
