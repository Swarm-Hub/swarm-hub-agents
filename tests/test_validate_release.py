"""Tests for scripts/validate_release.py. Every value is made up."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import validate_release as vr  # noqa: E402

SHA = "0123456789abcdef0123456789abcdef01234567"
ENTRY = {
    "version": "1.2.0",
    "tag": "demo-skill@1.2.0",
    "commit": SHA,
    "path": "skills/demo-skill",
    "default": False,
    "roles": ["builder"],
    "min_portal": None,
}


def manifest(plugins: dict | None = None) -> dict:
    return {"schema_version": 1, "source": vr.SOURCE, "plugins": plugins or {}}


def skill_md(name: str, version: str, body: str = "") -> str:
    return (f"---\nname: {name}\ndescription: A made-up skill.\nmetadata:\n  version: \"{version}\"\n---\n\n"
            f"# {name}\n\n{body}\n")


class RepoFixture:
    """A throwaway repo layout with one released single-skill plugin, `demo-skill` 1.2.0."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.release = manifest({"demo-skill": copy.deepcopy(ENTRY)})
        self.plugin = {"name": "demo-skill", "version": "1.2.0", "skills": ["."]}
        self.skill = skill_md("demo-skill", "1.2.0", "**This copy is version 1.2.0.** Check it first.")
        self.market = {"name": "demo", "owner": {"name": "Demo"},
                       "plugins": [{"name": "demo-skill", "source": "./skills/demo-skill"}]}

    def write(self) -> Path:
        folder = self.root / "skills" / "demo-skill"
        (folder / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (self.root / ".claude-plugin").mkdir(exist_ok=True)
        (self.root / "release.json").write_text(json.dumps(self.release), encoding="utf-8")
        (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps(self.plugin), encoding="utf-8")
        (folder / "SKILL.md").write_text(self.skill, encoding="utf-8")
        (self.root / ".claude-plugin" / "marketplace.json").write_text(json.dumps(self.market), encoding="utf-8")
        return self.root

    def close(self) -> None:
        self._tmp.cleanup()


class ManifestSchemaTest(unittest.TestCase):
    def test_empty_release_is_valid(self) -> None:
        self.assertEqual(vr.check_manifest(manifest()), [])

    def test_repo_release_json_and_example_are_valid(self) -> None:
        self.assertEqual(vr.check_repo(ROOT), [])
        self.assertEqual(vr.main(["--manifest-only", str(ROOT / "examples" / "release.example.json")]), 0)

    def test_full_entry_is_valid(self) -> None:
        self.assertEqual(vr.check_manifest(manifest({"demo-skill": ENTRY})), [])

    def test_tag_must_be_plugin_at_version(self) -> None:
        entry = dict(ENTRY, tag="demo-skill@1.1.0")
        self.assertIn("must be demo-skill@1.2.0", " ".join(vr.check_manifest(manifest({"demo-skill": entry}))))
        entry = dict(ENTRY, tag="main")
        self.assertIn("tag must match", " ".join(vr.check_manifest(manifest({"demo-skill": entry}))))

    def test_field_shapes(self) -> None:
        cases = {
            "version": "1.2",
            "commit": "abc123",
            "path": "elsewhere/demo-skill",
            "default": "yes",
            "roles": ["Builder"],
            "min_portal": "latest",
        }
        for key, bad in cases.items():
            with self.subTest(key=key):
                errors = vr.check_manifest(manifest({"demo-skill": dict(ENTRY, **{key: bad})}))
                self.assertTrue(any(key in e for e in errors), errors)

    def test_duplicate_roles_and_unknown_or_missing_keys(self) -> None:
        errors = vr.check_manifest(manifest({"demo-skill": dict(ENTRY, roles=["qa", "qa"])}))
        self.assertIn("twice", " ".join(errors))
        entry = {k: v for k, v in ENTRY.items() if k != "commit"}
        entry["extra"] = 1
        joined = " ".join(vr.check_manifest(manifest({"demo-skill": entry})))
        self.assertIn("missing keys ['commit']", joined)
        self.assertIn("unknown keys ['extra']", joined)

    def test_top_level(self) -> None:
        bad = {"schema_version": 2, "source": "https://example.invalid/x.git", "plugins": {}, "x": 1}
        joined = " ".join(vr.check_manifest(bad))
        for needle in ("schema_version must be 1", "source must be", "unknown keys ['x']"):
            self.assertIn(needle, joined)
        self.assertIn("missing keys", " ".join(vr.check_manifest({})))

    def test_malformed_shapes_are_findings_not_tracebacks(self) -> None:
        for data in ([], "x", None, manifest() | {"plugins": []}, manifest() | {"plugins": "x"},
                     manifest({"demo-skill": "1.2.0"}), manifest({"demo-skill": None}),
                     manifest({"Bad_Name": ENTRY})):
            with self.subTest(data=data):
                errors = vr.check_manifest(data)
                self.assertTrue(errors)


class RepoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = RepoFixture()

    def tearDown(self) -> None:
        self.fx.close()

    def test_consistent_release_passes(self) -> None:
        self.assertEqual(vr.check_repo(self.fx.write()), [])

    def test_plugin_json_version_mismatch_fails(self) -> None:
        self.fx.plugin["version"] = "1.3.0"
        self.assertIn("plugin.json: version '1.3.0' must equal", " ".join(vr.check_repo(self.fx.write())))

    def test_skill_metadata_version_mismatch_fails(self) -> None:
        self.fx.skill = skill_md("demo-skill", "1.1.0")
        self.assertIn("metadata.version '1.1.0' must equal", " ".join(vr.check_repo(self.fx.write())))

    def test_body_version_mismatch_fails(self) -> None:
        self.fx.skill = skill_md("demo-skill", "1.2.0", "**This copy is version 1.1.9.** Check it first.")
        self.assertIn("This copy is version 1.1.9", " ".join(vr.check_repo(self.fx.write())))

    def test_body_version_anchors_on_the_phrase_not_markup(self) -> None:
        self.fx.skill = skill_md("demo-skill", "1.2.0", "This copy is version 1.2.0 and newer ones exist.")
        self.assertEqual(vr.check_repo(self.fx.write()), [])
        self.fx.skill = skill_md("demo-skill", "1.2.0", "Note: This copy is version 9.9.9.")
        self.assertIn("This copy is version 9.9.9", " ".join(vr.check_repo(self.fx.write())))

    def test_bundle_checks_every_skill(self) -> None:
        root = self.fx.write()
        folder = root / "skills" / "demo-skill"
        (folder / "SKILL.md").unlink()
        self.fx.plugin["skills"] = ["./skills"]
        (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps(self.fx.plugin), encoding="utf-8")
        for name, body in (("one", ""), ("two", "**This copy is version 0.0.1.**")):
            (folder / "skills" / name).mkdir(parents=True)
            (folder / "skills" / name / "SKILL.md").write_text(skill_md(name, "0.1.0", body), encoding="utf-8")
        errors = vr.check_repo(root)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("skills/two/SKILL.md", errors[0])

    def test_unreleased_folder_fails(self) -> None:
        root = self.fx.write()
        (root / "skills" / "stray-skill").mkdir()
        self.assertIn("skills/stray-skill: not in release.json", " ".join(vr.check_repo(root)))

    def test_released_plugin_without_folder_fails(self) -> None:
        self.fx.release["plugins"]["ghost-skill"] = dict(ENTRY, tag="ghost-skill@1.2.0", path="skills/ghost-skill")
        self.fx.market["plugins"].append({"name": "ghost-skill", "source": "./skills/ghost-skill"})
        self.assertIn("has no folder skills/ghost-skill", " ".join(vr.check_repo(self.fx.write())))

    def test_marketplace_must_match_release(self) -> None:
        self.fx.market["plugins"] = [{"name": "other", "source": "./skills/other", "version": "1.0.0"}]
        joined = " ".join(vr.check_repo(self.fx.write()))
        self.assertIn("released plugin 'demo-skill' is not listed", joined)
        self.assertIn("'other' is listed but not in release.json", joined)
        self.assertIn("sets 'version'", joined)

    def test_malformed_release_json_is_a_finding(self) -> None:
        root = self.fx.write()
        (root / "release.json").write_text("{ not json", encoding="utf-8")
        errors = vr.check_repo(root)
        self.assertEqual(len(errors), 1)
        self.assertIn("release.json: not valid JSON", errors[0])
        self.assertEqual(vr.main(["--root", str(root)]), 1)

    def test_malformed_entries_do_not_crash_the_repo_check(self) -> None:
        self.fx.release["plugins"] = {"demo-skill": ["not", "an", "object"]}
        errors = vr.check_repo(self.fx.write())
        self.assertIn("entry must be an object", " ".join(errors))
        self.fx.release["plugins"] = [1, 2]
        self.assertIn("'plugins' must be an object", " ".join(vr.check_repo(self.fx.write())))

    def test_malformed_plugin_json_is_a_finding(self) -> None:
        root = self.fx.write()
        (root / "skills" / "demo-skill" / ".claude-plugin" / "plugin.json").write_text("[", encoding="utf-8")
        self.assertIn("plugin.json: not valid JSON", " ".join(vr.check_repo(root)))


class SchemaMirrorTest(unittest.TestCase):
    """release.schema.json and the validator agree on the keys and the fixed values."""

    def test_schema_matches_validator_constants(self) -> None:
        schema = json.loads((ROOT / "release.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(tuple(schema["required"]), vr.TOP_KEYS)
        self.assertEqual(schema["properties"]["schema_version"]["const"], vr.SCHEMA_VERSION)
        self.assertEqual(schema["properties"]["source"]["const"], vr.SOURCE)
        plugin = schema["$defs"]["plugin"]
        self.assertEqual(tuple(plugin["required"]), vr.PLUGIN_KEYS)
        self.assertEqual(set(plugin["properties"]), set(vr.PLUGIN_KEYS))
        self.assertEqual(plugin["properties"]["tag"]["pattern"], vr.TAG_RE.pattern)
        self.assertEqual(plugin["properties"]["commit"]["pattern"], vr.SHA_RE.pattern)


if __name__ == "__main__":
    unittest.main()
