"""Portable CI tests: no ambient pytest config or real case input is needed."""
import importlib.util
import os
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vdr_test_runner", ROOT / "scripts/vdr_review/run_tests.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def fixture_root(tmp_path):
    (tmp_path / "scripts/vdr_review/src").mkdir(parents=True)
    (tmp_path / "scripts/vdr_review/src/app.html").write_text("synthetic", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    return tmp_path


def test_runner_selects_only_vdr_tests_from_explicit_root(tmp_path):
    root = fixture_root(tmp_path)
    for name in ("test_vdr_z.py", "test_archive_print.py", "test_vdr_a.py"):
        (root / "tests" / name).touch()
    assert [path.name for path in RUNNER.test_paths(root)] == ["test_vdr_a.py", "test_vdr_z.py"]


def test_runner_rejects_wrong_or_empty_root(tmp_path):
    with pytest.raises(ValueError, match="not a VDR source root"):
        RUNNER.test_paths(tmp_path)
    fixture_root(tmp_path)
    with pytest.raises(ValueError, match="no VDR tests"):
        RUNNER.test_paths(tmp_path)


@pytest.mark.parametrize("missing", ["node", "ffmpeg"])
def test_runner_requires_node_and_ffmpeg_instead_of_allowing_skips(monkeypatch, missing):
    monkeypatch.setattr(RUNNER.shutil, "which", lambda name: None if name == missing else name)
    monkeypatch.setattr(RUNNER.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="version", stderr=""))
    with pytest.raises(ValueError, match=missing):
        RUNNER.check_tools()


def test_runner_rejects_broken_reference_executable(monkeypatch):
    monkeypatch.setattr(RUNNER.shutil, "which", lambda name: name)

    def broken(*args, **kwargs):
        raise RUNNER.subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(RUNNER.subprocess, "run", broken)
    with pytest.raises(ValueError, match="could not run: node"):
        RUNNER.check_tools()


@pytest.mark.parametrize("hook", ["pytest_collectreport", "pytest_runtest_logreport"])
def test_any_test_or_collection_skip_fails_otherwise_green_gate(hook):
    plugin = RUNNER.NoSkippedTests()
    getattr(plugin, hook)(SimpleNamespace(skipped=True, nodeid="synthetic::skipped"))
    session = SimpleNamespace(exitstatus=0)
    plugin.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1


def test_skip_gate_preserves_existing_error_status():
    plugin = RUNNER.NoSkippedTests()
    plugin.pytest_runtest_logreport(SimpleNamespace(skipped=True, nodeid="synthetic::skipped"))
    session = SimpleNamespace(exitstatus=2)
    plugin.pytest_sessionfinish(session, 2)
    assert session.exitstatus == 2


def test_runner_ignores_parent_config_plugins_and_addopts_then_restores_environment(tmp_path, monkeypatch):
    root = fixture_root(tmp_path)
    (root / "tests/test_vdr_synthetic.py").touch()
    (root / "pyproject.toml").write_text("intentionally not valid pytest config", encoding="utf-8")
    monkeypatch.setattr(RUNNER, "check_tools", lambda: None)
    monkeypatch.setenv("PYTEST_ADDOPTS", "--bad-ambient-option")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "old-value")
    monkeypatch.setenv("PYTEST_PLUGINS", "not_an_installed_plugin")
    captured = {}

    def fake_pytest_main(command, plugins):
        captured["command"] = command
        assert os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
        assert "PYTEST_ADDOPTS" not in os.environ
        assert "PYTEST_PLUGINS" not in os.environ
        assert "--noconftest" in command
        config = Path(command[command.index("-c") + 1])
        assert config.read_text(encoding="utf-8") == "[pytest]\n"
        assert isinstance(plugins[0], RUNNER.NoSkippedTests)
        return 0

    monkeypatch.setattr(pytest, "main", fake_pytest_main)
    assert RUNNER.main(["--root", str(root), "--basetemp", str(tmp_path / "pytest-tmp")]) == 0
    assert captured["command"][-1] == str(root / "tests/test_vdr_synthetic.py")
    assert os.environ["PYTEST_ADDOPTS"] == "--bad-ambient-option"
    assert os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "old-value"
    assert os.environ["PYTEST_PLUGINS"] == "not_an_installed_plugin"


def test_ci_checks_public_content_then_builds_and_tests_with_minimal_dependencies():
    workflow = (ROOT / ".github/workflows/vdr-ci.yml").read_text(encoding="utf-8")
    assert "requirements-test.txt" in workflow
    assert "requirements.lock" not in workflow
    assert "--no-deps -e" not in workflow
    assert "actions/setup-node@" in workflow and "ffmpeg -version" in workflow
    check, build, test = (workflow.index(step) for step in ("check_public.py", "build.py --out", "run_tests.py --root ."))
    assert check < build < test
    assert workflow.count("run_tests.py --root .") == 1
    # Docs, licence and ignore-rule changes must also pass the public check.
    assert "paths:" not in workflow
    assert "verify_longform.py" not in workflow and "deploy-pages" not in workflow


def release_workflow():
    workflow = (ROOT / ".github/workflows/vdr-release.yml").read_text(encoding="utf-8")
    head, _, body = workflow.partition("\njobs:\n")
    parts = re.split(r"^  (build|deploy|release):\n", body, flags=re.M)
    return workflow, head, dict(zip(parts[1::2], parts[2::2]))


def test_ci_only_builds_and_tests_and_never_publishes():
    workflow = (ROOT / ".github/workflows/vdr-ci.yml").read_text(encoding="utf-8")
    assert "branches: [main]" in workflow and "pull_request:" in workflow and "tags:" not in workflow
    assert "contents: read" in workflow
    for forbidden in ("contents: write", "pages: write", "id-token", "upload-pages-artifact", "gh release", "--site"):
        assert forbidden not in workflow, forbidden


def test_release_starts_only_from_a_tag_or_by_hand():
    _, head, jobs = release_workflow()
    assert 'tags: ["v*"]' in head and "workflow_dispatch:" in head
    assert "branches:" not in head and "pull_request" not in head
    assert set(jobs) == {"build", "deploy", "release"}


def test_release_jobs_hold_only_the_permissions_they_need():
    _, head, jobs = release_workflow()
    granted = {name: set(re.findall(r"^      ([a-z-]+): (?:read|write)$", job, flags=re.M)) for name, job in jobs.items()}
    assert "permissions: {}" in head
    assert granted == {"build": {"contents"}, "deploy": {"pages", "id-token"}, "release": {"contents"}}
    assert "contents: read" in jobs["build"] and "contents: write" in jobs["release"]
    assert "persist-credentials: false" in jobs["build"]


def test_release_publishes_only_after_the_tag_check_public_check_and_tests_pass():
    workflow, _, jobs = release_workflow()
    steps = ("--check-tag", "check_public.py", "run_tests.py --root .", "build.py --site _site", "upload-pages-artifact@", "upload-artifact@")
    positions = [jobs["build"].index(step) for step in steps]
    assert positions == sorted(positions)
    assert workflow.count("run_tests.py --root .") == 1 and workflow.count("--site") == 1
    assert "needs: build" in jobs["deploy"] and "deploy-pages@" in jobs["deploy"]
    assert "name: github-pages" in jobs["deploy"]
    assert "needs: deploy" in jobs["release"] and "gh release create" in jobs["release"]
    for bypass in ("continue-on-error", "always()", "|| true", "if:"):
        assert bypass not in workflow, bypass


def test_release_attaches_the_deployed_files_under_ascii_names():
    workflow, _, jobs = release_workflow()
    assert workflow.isascii()
    assert "name: release-files" in jobs["build"] and "name: release-files" in jobs["release"]
    assert "cp site/vdr-review.html site/version.json assets/" in jobs["release"]
    assert "cp site/*.zip assets/vdr-review-colleague.zip" in jobs["release"]
    assert "--verify-tag" in jobs["release"] and "--clobber" in jobs["release"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "`vdr-review.html`" in readme and "`vdr-review-colleague.zip`" in readme


def test_release_tag_is_never_expanded_into_a_shell_script():
    workflow, head, _ = release_workflow()
    assert workflow.count("inputs.tag") == 1 and "RELEASE_TAG: ${{ inputs.tag || github.ref_name }}" in head
    assert set(re.findall(r"\$\{\{\s*(.*?)\s*\}\}", workflow)) == {
        "inputs.tag || github.ref_name", "env.RELEASE_TAG", "runner.temp", "steps.deployment.outputs.page_url", "github.token"}
    assert re.findall(r".*\$\{\{ env\.RELEASE_TAG \}\}.*", workflow) == ["          ref: refs/tags/${{ env.RELEASE_TAG }}"]


def test_release_uses_the_same_toolchain_as_ci_and_no_build_cache():
    ci = (ROOT / ".github/workflows/vdr-ci.yml").read_text(encoding="utf-8")
    workflow, _, _ = release_workflow()
    for pattern in (r'python-version: ".*"', r'node-version: ".*"', r"actions/checkout@\S+", r"actions/setup-python@\S+", r"actions/setup-node@\S+"):
        assert re.findall(pattern, ci) == re.findall(pattern, workflow), pattern
    assert "sudo apt-get install --yes --no-install-recommends ffmpeg" in workflow
    assert not re.search(r"^\s*cache:", workflow, flags=re.M)
