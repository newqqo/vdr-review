"""Check that every file git would publish is allowed in this public repository.

Each tracked (or addable untracked) file must match the public allowlist, be
UTF-8 text of modest size, and contain no local paths, personal e-mail
addresses or credentials. Case-specific terms such as vessel or person names,
MMSI, dates and internal system names must never be written into this public
script: list them, one case-insensitive regular expression per line, in the
untracked local/sensitive_patterns.txt. This checks the working tree, not
earlier commits, and is a guard against accidents, not a security audit.
"""
from __future__ import annotations

import argparse
import fnmatch
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable, NamedTuple, Sequence

ROOT = Path(__file__).resolve().parents[2]
LOCAL_PATTERNS = Path("local/sensitive_patterns.txt")
MAX_BYTES = 1024 * 1024
# A new kind of file needs a deliberate entry here; "*" stays inside one path segment.
ALLOWED = (
    ".github/workflows/*.yml",
    ".gitignore", "AGENTS.md", "CLAUDE.md", "LICENSE", "README.md", "pytest.ini",
    "scripts/vdr_audio_export.py",
    "scripts/vdr_review/*.py", "scripts/vdr_review/*.md", "scripts/vdr_review/requirements-test.txt",
    "scripts/vdr_review/VERSION",
    "scripts/vdr_review/src/*.html", "scripts/vdr_review/src/*.js",
    "scripts/vdr_review/vendor/ffmpeg-g726/LGPL-2.1.txt",
    "scripts/vdr_review/vendor/ffmpeg-g726/NOTICE.md",
    "scripts/vdr_review/vendor/leaflet/LICENSE",
    "scripts/vdr_review/vendor/leaflet/leaflet.css",
    "scripts/vdr_review/vendor/leaflet/leaflet.js",
    "tests/test_vdr_*.py",
)
RULES = (
    ("local path", re.compile(r"(?i)\b[a-z]:[\\/]+(?:users|documents and settings)[\\/]|(?<![\w.])/(?:Users|home)/[^/\s'\"]+/")),
    ("e-mail address", re.compile(r"[\w.%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("access token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_\w{40,}|AKIA[0-9A-Z]{16}|xox[abposr]-[A-Za-z0-9-]{10,})\b")),
)
# GitHub noreply identities and RFC 2606 example names, including URL-userinfo test strings.
SAFE_EMAIL = re.compile(r"(?i)(?:^|[.@])example\.(?:com|net|org)(?:$|[.@])|\.(?:example|test|invalid)$|@users\.noreply\.github\.com$")


class Finding(NamedTuple):
    path: str
    line: int | None
    rule: str
    text: str = ""

    def __str__(self) -> str:
        where = self.path if self.line is None else f"{self.path}:{self.line}"
        return f"{where}: {self.rule}" + (f": {self.text}" if self.text else "")


def is_allowed(path: str) -> bool:
    # fnmatch's "*" also matches "/"; equal depth keeps every "*" inside one segment.
    return any(path.count("/") == pattern.count("/") and fnmatch.fnmatchcase(path, pattern)
               for pattern in ALLOWED)


def candidate_files(root: Path) -> list[str]:
    """Tracked files plus untracked files git would add; ignored files are excluded."""
    try:
        listing = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                                 check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"cannot list files with git under {root}: {exc}") from exc
    return sorted({name for name in listing.decode("utf-8").split("\0") if name})


def load_patterns(path: Path) -> list[tuple[str, re.Pattern]]:
    """Read local rules; blank lines and # comments are ignored, a missing file means none."""
    if not path.is_file():
        return []
    rules = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            pattern = re.compile(line, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"{path.name}:{number}: invalid pattern: {exc}") from exc
        if pattern.search(""):
            raise ValueError(f"{path.name}:{number}: pattern matches empty text")
        rules.append((f"local pattern (line {number})", pattern))
    return rules


def scan_text(path: str, text: str, rules: Sequence[tuple[str, re.Pattern]]) -> list[Finding]:
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        for name, pattern in rules:
            for match in pattern.finditer(line):
                if name == "e-mail address" and SAFE_EMAIL.search(match.group()):
                    continue
                findings.append(Finding(path, number, name, match.group()[:80]))
    return findings


def check(root: Path, files: Iterable[str], local_rules: Sequence[tuple[str, re.Pattern]] = ()) -> list[Finding]:
    root, rules, findings = root.resolve(), (*RULES, *local_rules), []
    for name in files:
        file = root / name
        if not is_allowed(name):
            findings.append(Finding(name, None, "not in the public allowlist"))
        elif file.is_symlink():
            findings.append(Finding(name, None, "symbolic link"))
        elif not file.is_file():
            findings.append(Finding(name, None, "listed by git but missing from the working tree"))
        elif file.stat().st_size > MAX_BYTES:
            findings.append(Finding(name, None, f"larger than {MAX_BYTES} bytes"))
        else:
            try:
                text = file.read_bytes().decode("utf-8-sig")
            except UnicodeDecodeError:
                findings.append(Finding(name, None, "not UTF-8 text"))
                continue
            if "\0" in text:
                findings.append(Finding(name, None, "binary content"))
                continue
            findings.extend(scan_text(name, text, rules))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    parser.add_argument("--patterns", type=Path, help=f"local pattern file (default: <root>/{LOCAL_PATTERNS.as_posix()})")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.patterns and not args.patterns.is_file():
        parser.error(f"pattern file not found: {args.patterns}")
    try:
        files = candidate_files(root)
        local_rules = load_patterns(args.patterns or root / LOCAL_PATTERNS)
    except ValueError as exc:
        parser.error(str(exc))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    findings = check(root, files, local_rules)
    for finding in findings:
        print(finding)
    scope = f"{len(local_rules)} local patterns" if local_rules else "no local patterns (generic checks only)"
    print(f"{len(files)} files checked, {scope}, {len(findings)} findings")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
