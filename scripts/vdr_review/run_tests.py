"""Run only portable VDR tests, with Node/FFmpeg and no skipped-test success.

Use --root for a checkout or any copy of the source tree. This does not run
opt-in browser harnesses or export a four-hour WAV.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


DEFAULT_ROOT = Path(__file__).resolve().parents[2]


def test_paths(root: Path) -> list[Path]:
    """Select VDR files explicitly, never unrelated tests placed beside them."""
    if not (root / "scripts" / "vdr_review" / "src" / "app.html").is_file():
        raise ValueError(f"not a VDR source root: {root}")
    paths = sorted((root / "tests").glob("test_vdr_*.py"))
    if not paths:
        raise ValueError(f"no VDR tests found under {root / 'tests'}")
    return paths


def check_tools() -> None:
    """Fail before pytest collection instead of passing a codec-skipped suite."""
    for name, flag in (("node", "--version"), ("ffmpeg", "-version")):
        executable = shutil.which(name)
        if not executable:
            raise ValueError(f"required test tool is not on PATH: {name}")
        try:
            result = subprocess.run([executable, flag], capture_output=True,
                                    text=True, check=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError(f"required test tool could not run: {name}: {exc}") from exc
        lines = (result.stdout or result.stderr).splitlines()
        if not lines:
            raise ValueError(f"required test tool returned no version: {name}")
        print(f"{name}: {lines[0]}", flush=True)


class NoSkippedTests:
    """A green portable gate means every selected test actually ran."""

    def __init__(self) -> None:
        self.skipped: set[str] = set()

    def pytest_collectreport(self, report) -> None:
        if report.skipped:
            self.skipped.add(report.nodeid)

    def pytest_runtest_logreport(self, report) -> None:
        if report.skipped:
            self.skipped.add(report.nodeid)

    def pytest_sessionfinish(self, session, exitstatus) -> None:
        if self.skipped and int(exitstatus) == 0:
            session.exitstatus = 1

    def pytest_terminal_summary(self, terminalreporter) -> None:
        if self.skipped:
            terminalreporter.write_sep("=", "VDR gate failed: skipped tests are not validation", red=True)
            for nodeid in sorted(self.skipped):
                terminalreporter.write_line(nodeid)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="checkout or extracted VDR source root")
    parser.add_argument("--basetemp", type=Path, help="pytest temporary directory")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        paths = test_paths(root)
        check_tools()
    except ValueError as exc:
        parser.error(str(exc))
    try:
        import pytest
    except ImportError:
        parser.error("install scripts/vdr_review/requirements-test.txt with this Python first")

    # A parent repository's pytest config, plugins and addopts are not part of
    # this portable gate. The transient config is generated, not source input.
    previous_autoload = os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD")
    previous_addopts = os.environ.pop("PYTEST_ADDOPTS", None)
    previous_plugins = os.environ.pop("PYTEST_PLUGINS", None)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    try:
        temp_parent = args.basetemp.resolve().parent if args.basetemp else None
        if temp_parent:
            temp_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="vdr-pytest-", dir=temp_parent) as directory:
            config = Path(directory) / "pytest.ini"
            config.write_text("[pytest]\n", encoding="utf-8")
            command = ["-c", str(config), "--rootdir", str(root), "-q", "--strict-markers", "--noconftest"]
            if args.basetemp:
                command.extend(("--basetemp", str(args.basetemp.resolve())))
            command.extend(str(path) for path in paths)
            return int(pytest.main(command, plugins=[NoSkippedTests()]))
    finally:
        if previous_autoload is None:
            os.environ.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD", None)
        else:
            os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = previous_autoload
        if previous_addopts is not None:
            os.environ["PYTEST_ADDOPTS"] = previous_addopts
        if previous_plugins is not None:
            os.environ["PYTEST_PLUGINS"] = previous_plugins


if __name__ == "__main__":
    raise SystemExit(main())
