"""US-007b1, criterio 3: el commit invalido del ensayo de la puerta se prepara en local, sin publicar."""
from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import unittest

import support
import gate_rehearsal
from support import TempDirTestCase, output_of, run_script, tool_env

SKIPPED = shutil.ignore_patterns(".git", ".venv", "__pycache__")
REAL_VENV = os.path.normpath(os.path.join(support.FEED, "..", ".venv-tools"))
SEASON = os.path.join("public", "v1", "seasons", "2026-27.json")


def git(directory: str, *args: str) -> str:
    return subprocess.run(["git", "-C", directory] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, check=True).stdout.strip()


class Rehearsal(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.feed = os.path.join(self.tmp, "myfutsal-feed")
        shutil.copytree(support.FEED, self.feed, ignore=SKIPPED)
        git(self.feed, "init", "-q", "-b", "main")
        git(self.feed, "config", "--local", "user.name", "Feed Bot")
        git(self.feed, "config", "--local", "user.email", "feed@example.invalid")
        git(self.feed, "add", "-A")
        git(self.feed, "commit", "-q", "-m", "snapshot")
        self.main_tip = git(self.feed, "rev-parse", "HEAD")

    def prepare(self, *extra: str):
        return run_script([os.path.join(self.feed, "scripts", "prepare-gate-rehearsal")] + list(extra), cwd=self.tmp,
                          env=tool_env({"GIT_CONFIG_GLOBAL": os.devnull, "FEED_TOOLS_VENV": REAL_VENV}))

    def show(self, ref: str) -> str:
        return git(self.feed, "show", "%s:%s" % (ref, SEASON))

    def validate(self, content: str):
        path = os.path.join(self.tmp, "season.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return run_script([os.path.join(self.feed, "scripts", "validate-schema"), path], cwd=self.tmp,
                          env=tool_env({"FEED_TOOLS_VENV": REAL_VENV}))

    def test_us007b1_c3_creates_one_commit_on_a_local_branch_and_returns_to_main(self) -> None:
        result = self.prepare()
        self.assertEqual(result.returncode, 0, output_of(result))
        self.assertEqual(git(self.feed, "branch", "--show-current"), "main")
        self.assertEqual(git(self.feed, "rev-parse", "HEAD"), self.main_tip, "main no debe cambiar")
        self.assertEqual(git(self.feed, "rev-list", "--count", "main..ensayo-puerta"), "1")
        self.assertEqual(git(self.feed, "diff", "--name-only", "main", "ensayo-puerta"), SEASON.replace(os.sep, "/"))
        self.assertEqual(git(self.feed, "status", "--porcelain"), "")

    def test_us007b1_c3_the_prepared_commit_fails_the_schema_and_main_passes_it(self) -> None:
        self.prepare()
        bad = self.validate(self.show("ensayo-puerta"))
        self.assertEqual(bad.returncode, 1, output_of(bad))
        self.assertIn("legue", output_of(bad))
        self.assertEqual(self.validate(self.show("main")).returncode, 0)

    def test_us007b1_c3_only_the_schema_fails_because_updated_at_advances(self) -> None:
        self.prepare()
        previous = os.path.join(self.tmp, "previous.json")
        current = os.path.join(self.tmp, "current-public")
        with open(previous, "w", encoding="utf-8") as handle:
            handle.write(self.show("main"))
        os.makedirs(os.path.join(current, "v1", "seasons"))
        shutil.copyfile(os.path.join(self.feed, "public", "v1", "index.json"), os.path.join(current, "v1", "index.json"))
        with open(os.path.join(current, "v1", "seasons", "2026-27.json"), "w", encoding="utf-8") as handle:
            handle.write(self.show("ensayo-puerta"))
        result = run_script([os.path.join(self.feed, "scripts", "check-invariants"), "--public", current, "--previous", previous],
                            cwd=self.tmp, env=tool_env({"FEED_TOOLS_VENV": REAL_VENV}))
        self.assertEqual(result.returncode, 0, output_of(result))
        self.assertGreater(json.loads(self.show("ensayo-puerta"))["updatedAt"], json.loads(self.show("main"))["updatedAt"])

    def test_us007b1_c3_it_never_touches_a_remote(self) -> None:
        git(self.feed, "remote", "add", "origin", "https://github.com/operador/myfutsal-feed.git")
        before = git(self.feed, "config", "--local", "--get-regexp", "^remote\\.")
        self.assertEqual(self.prepare().returncode, 0)
        self.assertEqual(git(self.feed, "config", "--local", "--get-regexp", "^remote\\."), before)
        self.assertEqual(git(self.feed, "for-each-ref", "refs/remotes"), "")

    def test_us007b1_c3_refuses_a_dirty_tree(self) -> None:
        with open(os.path.join(self.feed, "RUNBOOK.md"), "a", encoding="utf-8") as handle:
            handle.write("\nx\n")
        result = self.prepare()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no esta limpio", result.stderr)
        self.assertNotIn("ensayo-puerta", git(self.feed, "branch", "--list"))

    def test_us007b1_c3_refuses_outside_main(self) -> None:
        git(self.feed, "switch", "-q", "-c", "otra")
        result = self.prepare()
        self.assertEqual(result.returncode, 1)
        self.assertIn("rama main", result.stderr)

    def test_us007b1_c3_existing_branch_needs_recreate(self) -> None:
        self.assertEqual(self.prepare().returncode, 0)
        tip = git(self.feed, "rev-parse", "ensayo-puerta")
        again = self.prepare()
        self.assertEqual(again.returncode, 1)
        self.assertIn("--recreate", again.stderr)
        self.assertEqual(git(self.feed, "rev-parse", "ensayo-puerta"), tip)
        self.assertEqual(self.prepare("--recreate").returncode, 0)
        self.assertEqual(git(self.feed, "rev-list", "--count", "main..ensayo-puerta"), "1")

    def test_us007b1_c3_if_the_change_were_valid_nothing_is_committed(self) -> None:
        # Sin ningun "kind": "league" no hay nada que estropear: debe fallar y no dejar rama ni cambios.
        path = os.path.join(self.feed, SEASON)
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace('"league"', '"cup"'))
        git(self.feed, "commit", "-q", "-am", "no league")
        result = self.prepare()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(git(self.feed, "branch", "--list", "ensayo-puerta"), "")
        self.assertEqual(git(self.feed, "status", "--porcelain"), "")

    def test_us007b1_c3_updated_at_advances_even_if_the_clock_is_behind(self) -> None:
        text = '{"kind": "league", "updatedAt": "2099-01-01T00:00:00Z"}'
        changed = gate_rehearsal.invalid_text(text, datetime.datetime(2026, 1, 1))
        self.assertIn('"updatedAt": "2099-01-01T00:00:01Z"', changed)
        self.assertIn('"kind": "legue"', changed)

    def test_us007b1_c3_the_runbook_rehearsal_rollback_is_accepted_by_the_gate_logic(self) -> None:
        """Ensayo completo en local: la rama invalida se fusiona y el rollback del runbook deja un contenido que pasa verify."""
        self.prepare()
        git(self.feed, "merge", "--ff-only", "-q", "ensayo-puerta")
        git(self.feed, "revert", "--no-commit", "HEAD")
        path = os.path.join(self.feed, SEASON)
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        with open(path, "w", encoding="utf-8") as handle:
            # Posterior al updatedAt del commit invalido (la hora actual) pero dentro de la tolerancia de D-544.
            soon = (datetime.datetime.utcnow() + datetime.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            handle.write(gate_rehearsal.UPDATED_AT_PATTERN.sub(r'\1"%s"' % soon, text, count=1))
        git(self.feed, "add", SEASON)
        verify = run_script([os.path.join(self.feed, "scripts", "verify")], cwd=self.tmp,
                            env=tool_env({"GIT_CONFIG_GLOBAL": os.devnull, "FEED_VERIFY_NESTED": "1", "FEED_TOOLS_VENV": REAL_VENV}))
        self.assertEqual(verify.returncode, 0, output_of(verify))


if __name__ == "__main__":
    unittest.main()
