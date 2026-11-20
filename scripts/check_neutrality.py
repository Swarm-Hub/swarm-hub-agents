#!/usr/bin/env python3
"""Refuse workgroup values in this public repo: files, commit messages, and PR title and body.

A released skill is workgroup-neutral: every workgroup value (an org, a repo, a project, a host)
reaches it through a binding at run time, never through its text. Two lists say what a
workgroup value looks like:

* ``scripts/neutrality-denylist.txt`` holds GENERIC regex patterns only (an operator binding name,
  an internal host, another workgroup's ``<slug>-agents`` repo URL). The file is public.
* ``NEUTRALITY_DENYLIST`` (an environment variable fed from the CI secret of that name) holds the
  workgroup LITERALS, one per line, matched case-insensitively as plain substrings. They live only
  in the secret, because writing them in a public file would disclose them.

**Fail-closed:** under CI (``CI=true``) or with ``--require-literals``, a missing or empty
``NEUTRALITY_DENYLIST`` is itself a failure, so a run that could not check the literals never
reports green. On a laptop without the secret the generic patterns still run, with a warning.

No finding ever prints the text it matched: a literal hit names the file and line and the entry
number, a pattern hit the pattern's line in the denylist file. Exit 0 clean, 1 on any finding.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DENYLIST = Path("scripts") / "neutrality-denylist.txt"
SECRET_ENV = "NEUTRALITY_DENYLIST"
MIN_LITERAL = 3  # shorter entries would match nearly every file; reported as a config error


@dataclass(frozen=True)
class Pattern:
    label: str  # "denylist line N" or "NEUTRALITY_DENYLIST entry N"; never the value itself
    regex: re.Pattern[str]


def load_patterns(path: Path) -> tuple[list[Pattern], list[str]]:
    patterns: list[Pattern] = []
    errors: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [], [f"{DENYLIST}: cannot read ({exc.strerror})"]
    for n, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            patterns.append(Pattern(f"{DENYLIST} line {n}", re.compile(line.strip(), re.IGNORECASE)))
        except re.error as exc:
            errors.append(f"{DENYLIST} line {n}: not a valid regex ({exc})")
    return patterns, errors


def load_literals(raw: str | None) -> tuple[list[Pattern], list[str]]:
    patterns: list[Pattern] = []
    errors: list[str] = []
    for n, line in enumerate((raw or "").splitlines(), 1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if len(value) < MIN_LITERAL:
            errors.append(f"{SECRET_ENV} entry {n}: shorter than {MIN_LITERAL} characters, refused")
            continue
        patterns.append(Pattern(f"{SECRET_ENV} entry {n}", re.compile(re.escape(value), re.IGNORECASE)))
    return patterns, errors


def scan_text(where: str, text: str, patterns: list[Pattern]) -> list[str]:
    findings: list[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        for p in patterns:
            if p.regex.search(line):
                findings.append(f"{where}:{n}: matches {p.label}")
    return findings


def tracked_files(root: Path) -> list[Path]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], check=True,
                         capture_output=True, timeout=60).stdout
    return [root / p for p in out.decode("utf-8").split("\0") if p]


def commit_messages(root: Path, rev_range: str) -> list[tuple[str, str]]:
    out = subprocess.run(["git", "-C", str(root), "log", "--format=%H%x00%B%x01", rev_range],
                         check=True, capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
    msgs = []
    for chunk in out.split("\x01"):
        sha, sep, body = chunk.strip("\n").partition("\0")
        if sep:
            msgs.append((sha[:12], body))
    return msgs


def run(root: Path, *, literals_raw: str | None, require_literals: bool,
        commits: str | None = None, texts: dict[str, str] | None = None) -> tuple[list[str], list[str]]:
    """Return (failures, warnings)."""
    generic, errors = load_patterns(root / DENYLIST)
    literals, literal_errors = load_literals(literals_raw)
    errors += literal_errors
    warnings: list[str] = []
    if not literals:
        if require_literals:
            errors.append(f"{SECRET_ENV} is missing or empty: workgroup literals cannot be checked "
                          f"(fail-closed; set the repo's {SECRET_ENV} Actions secret)")
        else:
            warnings.append(f"{SECRET_ENV} not set: checked the generic patterns only")
    denylist_path = (root / DENYLIST).resolve()
    for path in tracked_files(root):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        text = path.read_bytes().decode("utf-8", "replace")
        # The pattern file is exempt from its own patterns (it IS them), never from the literals.
        pats = literals if path.resolve() == denylist_path else generic + literals
        errors.extend(scan_text(rel, text, pats))
        errors.extend(scan_text(f"(path) {rel}", rel, generic + literals))
    if commits:
        for sha, body in commit_messages(root, commits):
            errors.extend(scan_text(f"commit {sha} message", body, generic + literals))
    for name, text in (texts or {}).items():
        errors.extend(scan_text(name, text, generic + literals))
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--commits", metavar="RANGE", help="also scan commit messages in this git range (base..head)")
    parser.add_argument("--text-env", action="append", default=[], metavar="VAR",
                        help="also scan the text in this environment variable (e.g. PR_TITLE, PR_BODY)")
    parser.add_argument("--require-literals", action="store_true",
                        help=f"fail when {SECRET_ENV} is missing (always on when CI=true)")
    args = parser.parse_args(argv)
    require = args.require_literals or os.environ.get("CI", "").lower() == "true"
    texts = {var: os.environ.get(var, "") for var in args.text_env}
    try:
        failures, warnings = run(args.root.resolve(), literals_raw=os.environ.get(SECRET_ENV),
                                 require_literals=require, commits=args.commits, texts=texts)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL check_neutrality: git failed ({exc}); nothing was checked", file=sys.stderr)
        return 1
    for w in warnings:
        print(f"WARN {w}", file=sys.stderr)
    if failures:
        for f in failures:
            print(f"FAIL {f}", file=sys.stderr)
        print(f"check_neutrality: {len(failures)} finding(s); workgroup values belong in bindings, not in this repo",
              file=sys.stderr)
        return 1
    print("check_neutrality: no workgroup values found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
