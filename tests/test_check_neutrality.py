"""Tests for scripts/check_neutrality.py. Every literal here is made up (`acme-widgets`,
`examplecorp`), never a real workgroup value."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_neutrality as cn  # noqa: E402

LITERALS = "acme-widgets\nExampleCorp\n"


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, timeout=30)


class FixtureRepo(unittest.TestCase):
    """A throwaway git repo carrying this repo's real denylist and one clean file."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "scripts").mkdir()
        shutil.copy(ROOT / cn.DENYLIST, self.root / cn.DENYLIST)
        (self.root / "README.md").write_text("A neutral skill. Bind your repo with a variable.\n", encoding="utf-8")
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "seed")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def add(self, rel: str, text: str, message: str = "add") -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", message)

    def check(self, literals: str | None = LITERALS, require: bool = True, **kw):
        return cn.run(self.root, literals_raw=literals, require_literals=require, **kw)


class NeutralityTest(FixtureRepo):
    def test_clean_tree_passes(self) -> None:
        self.assertEqual(self.check(), ([], []))

    def test_literal_in_a_file_fails_without_printing_it(self) -> None:
        self.add("skills/x/SKILL.md", "line one\nOur org is ACME-Widgets on GitHub.\n")
        failures, _ = self.check()
        self.assertEqual(failures, [f"skills/x/SKILL.md:2: matches {cn.SECRET_ENV} entry 1"])
        self.assertNotIn("acme", " ".join(failures).lower())

    def test_literal_in_a_path_fails(self) -> None:
        self.add("skills/examplecorp-notes.md", "neutral\n")
        failures, _ = self.check()
        self.assertTrue(any(f.startswith("(path) skills/examplecorp-notes.md") for f in failures), failures)

    def test_generic_patterns_fail(self) -> None:
        # Assembled at run time so this file does not trip the repo's own scan.
        gh, agents = "github" + ".com", "someorg-" + "agents"
        cases = {
            "operator binding": "export SWARM_" + "OPERATOR_TEAM=x\n",
            "internal host": "curl https://build.corp." + "internal/api\n",
            "other agents repo url": f"clone https://{gh}/someorg/{agents}\n",
            "other agents repo ssh": f"git@{gh}:someorg/{agents}.git\n",
        }
        for label, text in cases.items():
            with self.subTest(label=label):
                self.add("doc.md", text)
                failures, _ = self.check()
                self.assertEqual(len(failures), 1, failures)
                self.assertIn(str(cn.DENYLIST), failures[0])

    def test_this_repo_url_is_allowed(self) -> None:
        self.add("doc.md", "https://github.com/Swarm-Hub/swarm-hub-agents.git and git@github.com:Swarm-Hub/swarm-hub-agents\n")
        self.assertEqual(self.check(), ([], []))

    def test_denylist_file_is_exempt_from_its_own_patterns_not_from_literals(self) -> None:
        self.assertEqual(self.check()[0], [])
        self.add(str(cn.DENYLIST), (ROOT / cn.DENYLIST).read_text(encoding="utf-8") + "examplecorp\n")
        failures, _ = self.check()
        self.assertEqual(len(failures), 1, failures)
        self.assertIn(f"{cn.SECRET_ENV} entry 2", failures[0])

    def test_missing_secret_fails_closed_when_required(self) -> None:
        for raw in (None, "", "\n  \n# only a comment\n"):
            with self.subTest(raw=raw):
                failures, _ = self.check(literals=raw, require=True)
                self.assertTrue(any("fail-closed" in f for f in failures), failures)

    def test_missing_secret_warns_on_a_laptop(self) -> None:
        failures, warnings = self.check(literals=None, require=False)
        self.assertEqual(failures, [])
        self.assertEqual(len(warnings), 1)

    def test_too_short_literal_is_refused(self) -> None:
        failures, _ = self.check(literals="ab\nexamplecorp\n")
        self.assertEqual(failures, [f"{cn.SECRET_ENV} entry 1: shorter than {cn.MIN_LITERAL} characters, refused"])

    def test_commit_messages_are_scanned(self) -> None:
        self.add("doc.md", "neutral\n", message="Fix for the ExampleCorp workspace")
        failures, _ = self.check(commits="HEAD~1..HEAD")
        self.assertEqual(len(failures), 1, failures)
        self.assertIn("message", failures[0])
        self.assertEqual(self.check(commits="HEAD~1")[0], [])  # the seed commit only

    def test_pr_text_is_scanned(self) -> None:
        failures, _ = self.check(texts={"PR_BODY": "neutral\nsee acme-widgets\n", "PR_TITLE": "ok"})
        self.assertEqual(failures, [f"PR_BODY:2: matches {cn.SECRET_ENV} entry 1"])


class MainTest(FixtureRepo):
    def test_ci_env_forces_fail_closed(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != cn.SECRET_ENV} | {"CI": "true"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(cn.main(["--root", str(self.root)]), 1)

    def test_main_passes_with_literals_and_reads_text_env(self) -> None:
        env = os.environ | {"CI": "true", cn.SECRET_ENV: LITERALS, "PR_BODY": "clean"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(cn.main(["--root", str(self.root), "--text-env", "PR_BODY"]), 0)
        env["PR_BODY"] = "about examplecorp"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(cn.main(["--root", str(self.root), "--text-env", "PR_BODY"]), 1)

    def test_this_repo_is_neutral_under_the_generic_patterns(self) -> None:
        # Literals assembled at run time: this test file itself spells LITERALS out.
        absent = "zq" + "xv-nowhere\n" + "qq" + "-unseen-org\n"
        failures, _ = cn.run(ROOT, literals_raw=absent, require_literals=True)
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
