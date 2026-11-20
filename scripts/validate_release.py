#!/usr/bin/env python3
"""Validate the release repo: ``release.json``, the plugin folders it names, and the marketplace.

``release.json`` is the interface every reader uses (the portal's release reconciler, the portal
catalogue, a skill's own latest-version check): one entry per released plugin. Checks (stdlib only,
so CI and a laptop run the same thing):

* ``release.json`` parses and matches the schema in ``release.schema.json`` (the rules are mirrored
  here so CI needs no third-party validator; ``tests/test_validate_release.py`` pins the mirror):
  ``schema_version`` 1, ``source`` this repo's clone URL, ``plugins`` an object keyed by plugin name,
  each entry exactly ``PLUGIN_KEYS``;
* per plugin: ``version`` semver, ``tag`` = ``<plugin>@<version>``, ``commit`` a full 40-hex SHA,
  ``path`` = ``skills/<plugin>``, ``default`` a bool, ``roles`` unique role names, ``min_portal``
  semver or null;
* version consistency: ``.claude-plugin/plugin.json`` ``version`` = the release version; for a
  single-skill plugin (``"skills": ["."]``) ``SKILL.md`` ``metadata.version`` = the same; in every
  ``SKILL.md`` a body sentence "This copy is version X.Y.Z" = that skill's ``metadata.version``;
* layout: every folder under ``skills/`` is released and every release has its folder, so nothing
  unreleased sits on ``main``;
* ``.claude-plugin/marketplace.json`` lists exactly the released plugins, each at ``./skills/<name>``
  with no ``version`` of its own (``release.json`` and plugin.json are the version authorities).

Malformed input is reported as findings, never as a traceback. Exit 0 when everything passes,
1 with one ``FAIL`` line per finding otherwise.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RELEASE = Path("release.json")
MARKETPLACE = Path(".claude-plugin") / "marketplace.json"
SKILLS_DIR = Path("skills")
SCHEMA_VERSION = 1
SOURCE = "https://github.com/Swarm-Hub/swarm-hub-agents.git"
TOP_KEYS = ("schema_version", "source", "plugins")
PLUGIN_KEYS = ("version", "tag", "commit", "path", "default", "roles", "min_portal")
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
# The reconciler's tag allowlist (design §Security): `<plugin>@<version>`, nothing else.
TAG_RE = re.compile(r"^[a-z0-9-]+@\d+\.\d+\.\d+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# A skill's own latest-version check reads this sentence; anchored on the phrase, not on markup.
# Lazy capture stops before a sentence-ending dot, so "version 0.3.1.**" reads 0.3.1 and a
# malformed "version v1" is still caught and compared (and fails).
BODY_VERSION_RE = re.compile(r"This copy is version (\S+?)\.?(?=\*|\s|$)", re.MULTILINE)


def _load_json(path: Path, rel: Path) -> tuple[Any, list[str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), []
    except FileNotFoundError:
        return None, [f"{rel}: missing"]
    except UnicodeDecodeError:
        return None, [f"{rel}: not UTF-8 text"]
    except json.JSONDecodeError as exc:
        return None, [f"{rel}: not valid JSON ({exc.msg} at line {exc.lineno})"]


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_frontmatter(text: str) -> dict | None:
    """The ``---`` fenced frontmatter as a dict (``key: value`` plus one-level maps), or None."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None
    data: dict = {}
    current_map: str | None = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if ":" not in raw:
            return None
        key, _, value = raw.strip().partition(":")
        key = key.strip()
        if raw.startswith((" ", "\t")):
            if current_map is None:
                return None
            data[current_map][key] = _unquote(value)
        elif value.strip() == "":
            current_map = key
            data[key] = {}
        else:
            current_map = None
            data[key] = _unquote(value)
    return data


def check_manifest(data: Any, rel: Path = RELEASE) -> list[str]:
    """Schema checks on a parsed ``release.json`` (no file system access)."""
    if not isinstance(data, dict):
        return [f"{rel}: must be a JSON object"]
    errors: list[str] = []
    missing = [k for k in TOP_KEYS if k not in data]
    unknown = sorted(k for k in data if k not in TOP_KEYS)
    if missing:
        errors.append(f"{rel}: missing keys {missing}")
    if unknown:
        errors.append(f"{rel}: unknown keys {unknown}; allowed {list(TOP_KEYS)}")
    if "schema_version" in data and data["schema_version"] != SCHEMA_VERSION:
        errors.append(f"{rel}: schema_version must be {SCHEMA_VERSION}, got {data['schema_version']!r}")
    if "source" in data and data["source"] != SOURCE:
        errors.append(f"{rel}: source must be {SOURCE!r}, got {data['source']!r}")
    plugins = data.get("plugins")
    if "plugins" in data and not isinstance(plugins, dict):
        errors.append(f"{rel}: 'plugins' must be an object keyed by plugin name, got {type(plugins).__name__}")
        return errors
    for name, entry in (plugins or {}).items():
        errors.extend(check_plugin_entry(name, entry, rel))
    return errors


def check_plugin_entry(name: str, entry: Any, rel: Path = RELEASE) -> list[str]:
    where = f"{rel}: plugin {name!r}"
    if not NAME_RE.match(name) or len(name) > 64:
        return [f"{where}: name must be 1-64 chars matching {NAME_RE.pattern}"]
    if not isinstance(entry, dict):
        return [f"{where}: entry must be an object, got {type(entry).__name__}"]
    errors: list[str] = []
    missing = [k for k in PLUGIN_KEYS if k not in entry]
    unknown = sorted(k for k in entry if k not in PLUGIN_KEYS)
    if missing:
        errors.append(f"{where}: missing keys {missing}")
    if unknown:
        errors.append(f"{where}: unknown keys {unknown}; allowed {list(PLUGIN_KEYS)}")
    version = entry.get("version")
    if "version" in entry and not (isinstance(version, str) and SEMVER_RE.match(version)):
        errors.append(f"{where}: version must be semver x.y.z, got {version!r}")
        version = None
    tag = entry.get("tag")
    if "tag" in entry:
        if not (isinstance(tag, str) and TAG_RE.match(tag)):
            errors.append(f"{where}: tag must match {TAG_RE.pattern}, got {tag!r}")
        elif version is not None and tag != f"{name}@{version}":
            errors.append(f"{where}: tag {tag!r} must be {name}@{version}")
    commit = entry.get("commit")
    if "commit" in entry and not (isinstance(commit, str) and SHA_RE.match(commit)):
        errors.append(f"{where}: commit must be a full 40-hex lowercase SHA, got {commit!r}")
    if "path" in entry and entry["path"] != f"{SKILLS_DIR.as_posix()}/{name}":
        errors.append(f"{where}: path must be {SKILLS_DIR.as_posix()}/{name}, got {entry['path']!r}")
    if "default" in entry and not isinstance(entry["default"], bool):
        errors.append(f"{where}: default must be true or false, got {entry['default']!r}")
    roles = entry.get("roles")
    if "roles" in entry:
        if not isinstance(roles, list) or not all(isinstance(r, str) and NAME_RE.match(r) for r in roles):
            errors.append(f"{where}: roles must be a list of role names matching {NAME_RE.pattern}, got {roles!r}")
        elif len(set(roles)) != len(roles):
            errors.append(f"{where}: roles lists a role twice: {roles!r}")
    min_portal = entry.get("min_portal")
    if "min_portal" in entry and min_portal is not None and not (
            isinstance(min_portal, str) and SEMVER_RE.match(min_portal)):
        errors.append(f"{where}: min_portal must be semver x.y.z or null, got {min_portal!r}")
    return errors


def check_skill_md(skill_md: Path, root: Path) -> tuple[str | None, list[str]]:
    """Return (metadata.version, findings) for one ``SKILL.md``."""
    rel = skill_md.relative_to(root)
    try:
        text = skill_md.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None, [f"{rel}: not UTF-8 text"]
    fm = parse_frontmatter(text)
    if fm is None:
        return None, [f"{rel}: missing or malformed frontmatter (expected a '---' fenced block)"]
    errors: list[str] = []
    if fm.get("name") != skill_md.parent.name:
        errors.append(f"{rel}: name {fm.get('name')!r} must equal its folder name {skill_md.parent.name!r}")
    metadata = fm.get("metadata")
    version = metadata.get("version") if isinstance(metadata, dict) else None
    if not (isinstance(version, str) and SEMVER_RE.match(version)):
        errors.append(f"{rel}: metadata.version must be semver x.y.z, got {version!r}")
        version = None
    for stated in BODY_VERSION_RE.findall(text):
        if stated != version:
            errors.append(f"{rel}: the body says 'This copy is version {stated}' but metadata.version is {version!r}")
    return version, errors


def check_plugin_folder(name: str, version: str | None, root: Path) -> list[str]:
    """The released folder agrees with ``release.json``: plugin.json name and version, and the
    single skill's ``metadata.version`` when the plugin is one skill."""
    folder = root / SKILLS_DIR / name
    if not folder.is_dir():
        return [f"{RELEASE}: plugin {name!r} has no folder {SKILLS_DIR.as_posix()}/{name}"]
    manifest_rel = (SKILLS_DIR / name / ".claude-plugin" / "plugin.json")
    data, errors = _load_json(root / manifest_rel, manifest_rel)
    if errors:
        return errors
    if not isinstance(data, dict):
        return [f"{manifest_rel}: must be a JSON object"]
    if data.get("name") != name:
        errors.append(f"{manifest_rel}: name {data.get('name')!r} must equal {name!r}")
    if version is not None and data.get("version") != version:
        errors.append(f"{manifest_rel}: version {data.get('version')!r} must equal release.json's {version!r}")
    skills = data.get("skills")
    if skills == ["."]:
        skill_md = folder / "SKILL.md"
        if not skill_md.is_file():
            return errors + [f"{manifest_rel}: 'skills': [\".\"] needs SKILL.md beside .claude-plugin/"]
        skill_version, found = check_skill_md(skill_md, root)
        errors.extend(found)
        if version is not None and skill_version is not None and skill_version != version:
            errors.append(f"{skill_md.relative_to(root)}: metadata.version {skill_version!r} must equal release.json's {version!r}")
    elif skills is None or skills == ["./skills"]:
        found_any = False
        for skill_md in sorted((folder / "skills").glob("*/SKILL.md")):
            found_any = True
            errors.extend(check_skill_md(skill_md, root)[1])
        if not found_any:
            errors.append(f"{manifest_rel}: a bundle needs skills/<name>/SKILL.md (none found)")
    else:
        errors.append(f"{manifest_rel}: 'skills' must be [\".\"], [\"./skills\"] or absent, got {skills!r}")
    return errors


def check_marketplace(released: set[str], root: Path) -> list[str]:
    data, errors = _load_json(root / MARKETPLACE, MARKETPLACE)
    if errors:
        return errors
    if not isinstance(data, dict):
        return [f"{MARKETPLACE}: must be a JSON object"]
    if not isinstance(data.get("name"), str) or not NAME_RE.match(data["name"]):
        errors.append(f"{MARKETPLACE}: 'name' must match {NAME_RE.pattern}, got {data.get('name')!r}")
    if not isinstance(data.get("owner"), dict) or not isinstance(data["owner"].get("name"), str):
        errors.append(f"{MARKETPLACE}: 'owner.name' is required")
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        return errors + [f"{MARKETPLACE}: 'plugins' must be a list"]
    listed: set[str] = set()
    for entry in plugins:
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name:
            errors.append(f"{MARKETPLACE}: every plugin entry must be an object with a 'name'")
            continue
        if name in listed:
            errors.append(f"{MARKETPLACE}: plugin {name!r} is listed twice")
        listed.add(name)
        if "version" in entry:
            errors.append(f"{MARKETPLACE}: plugin {name!r} sets 'version'; release.json and plugin.json hold it")
        if entry.get("source") != f"./{SKILLS_DIR.as_posix()}/{name}":
            errors.append(f"{MARKETPLACE}: plugin {name!r} source must be ./{SKILLS_DIR.as_posix()}/{name}, got {entry.get('source')!r}")
    for name in sorted(released - listed):
        errors.append(f"{MARKETPLACE}: released plugin {name!r} is not listed")
    for name in sorted(listed - released):
        errors.append(f"{MARKETPLACE}: plugin {name!r} is listed but not in release.json")
    return errors


def check_repo(root: Path = ROOT) -> list[str]:
    data, errors = _load_json(root / RELEASE, RELEASE)
    if errors:
        return errors
    errors = check_manifest(data)
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return errors
    released = {n for n in plugins if isinstance(n, str) and NAME_RE.match(n)}
    for name in sorted(released):
        entry = plugins[name]
        version = entry.get("version") if isinstance(entry, dict) else None
        errors.extend(check_plugin_folder(name, version if isinstance(version, str) and SEMVER_RE.match(version) else None, root))
    skills_root = root / SKILLS_DIR
    on_disk = {p.name for p in skills_root.iterdir() if p.is_dir()} if skills_root.is_dir() else set()
    for name in sorted(on_disk - set(plugins)):
        errors.append(f"{SKILLS_DIR.as_posix()}/{name}: not in release.json; only released plugins live here")
    errors.extend(check_marketplace(released, root))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="repo root (default: this checkout)")
    parser.add_argument("--manifest-only", type=Path, metavar="PATH",
                        help="check only the schema of one release.json-shaped file (e.g. the example)")
    args = parser.parse_args(argv)
    if args.manifest_only is not None:
        data, errors = _load_json(args.manifest_only, args.manifest_only)
        if not errors:
            errors = check_manifest(data, args.manifest_only)
        label = str(args.manifest_only)
    else:
        errors = check_repo(args.root.resolve())
        label = "release"
    if errors:
        for err in errors:
            print(f"FAIL {err}", file=sys.stderr)
        print(f"validate_release: {len(errors)} problem(s) in {label}", file=sys.stderr)
        return 1
    print(f"validate_release: {label} valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
