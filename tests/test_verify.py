"""US-007a, criterio 9: un unico comando local, sin red, que indica por nombre lo que falla.

Se ejecuta sobre copias temporales del feed (con FEED_VERIFY_NESTED=1 para no recursar);
el repositorio real no se toca. En la ejecucion anidada verify omite las pruebas unitarias.
"""
from __future__ import annotations

import json
import os
import subprocess
import unittest
from typing import Dict

import support
from support import TempDirTestCase, output_of, run_script, tool_env

REAL_VENV = os.path.normpath(os.path.join(support.FEED, "..", ".venv-tools"))
EXPECTED_CHECKS = ["1-estructura", "2-paridad-sha256sums", "3-esquema-de-public", "4-invariantes-de-public",
                   "5-workflow", "8-runbook", "9-idempotente"]


def git(directory: str, *args: str) -> None:
    subprocess.run(["git", "-C", directory] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


class CopiedFeed(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.feed = os.path.join(self.tmp, "myfutsal-feed")
        support.copy_feed(self.feed)

    def init_repo(self) -> None:
        git(self.feed, "init", "-q", "-b", "main")
        git(self.feed, "config", "--local", "user.name", "Feed Bot")
        git(self.feed, "config", "--local", "user.email", "feed@example.invalid")
        git(self.feed, "add", "-A")
        git(self.feed, "commit", "-q", "-m", "snapshot")

    def verify(self, extra_env: Dict[str, str] = None):
        env = tool_env({"FEED_TOOLS_VENV": REAL_VENV, "FEED_VERIFY_NESTED": "1", "GIT_CONFIG_GLOBAL": os.devnull})
        env.update(extra_env or {})
        return run_script([os.path.join(self.feed, "scripts", "verify")], cwd=self.tmp, env=env)


SEASON_PATH = os.path.join("public", "v1", "seasons", "2026-27.json")


class UpdatedAtAgainstHead(CopiedFeed):
    """US-007a2, criterio 8 (S-11): verify compara `updatedAt` con `git show HEAD:public/v1/seasons/<id>.json`."""

    def season(self) -> dict:
        with open(os.path.join(self.feed, SEASON_PATH), encoding="utf-8") as handle:
            return json.load(handle)

    def write_season(self, season: dict) -> None:
        with open(os.path.join(self.feed, SEASON_PATH), "w", encoding="utf-8") as handle:
            json.dump(season, handle, indent=2, ensure_ascii=False)
            handle.write("\n")

    def edit(self, updated_at: str = None, change_content: bool = True) -> None:
        season = self.season()
        if change_content:
            season["competitionName"] = season["competitionName"] + " (editado)"
        if updated_at:
            season["updatedAt"] = updated_at
        self.write_season(season)

    def test_us007a2_c8_changed_content_with_the_same_updated_at_fails_naming_the_invariant(self) -> None:
        self.init_repo()
        self.edit(change_content=True)
        result = self.verify()
        self.assertNotEqual(result.returncode, 0, output_of(result))
        self.assertIn("[FALLO] 4b-updated-at-frente-a-head", result.stdout)
        self.assertIn("updated-at-sin-avanzar", result.stdout)
        self.assertIn("4b-updated-at-frente-a-head", result.stderr)

    def test_us007a2_c8_changed_content_with_an_older_updated_at_fails(self) -> None:
        self.init_repo()
        self.edit(updated_at="2020-01-01T00:00:00Z")
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("updated-at-sin-avanzar", result.stdout)

    def test_us007a2_c8_changed_content_with_a_later_updated_at_passes(self) -> None:
        self.init_repo()
        self.edit(updated_at="2026-10-02T10:00:00Z")
        result = self.verify()
        self.assertEqual(result.returncode, 0, output_of(result))
        self.assertIn("[ OK ] 4b-updated-at-frente-a-head", result.stdout)

    def test_us007a2_c8_unchanged_content_passes(self) -> None:
        self.init_repo()
        result = self.verify()
        self.assertEqual(result.returncode, 0, output_of(result))
        self.assertIn("[ OK ] 4b-updated-at-frente-a-head", result.stdout)

    def test_us007a2_c8_unchanged_content_with_a_different_updated_at_passes(self) -> None:
        self.init_repo()
        self.edit(updated_at="2026-10-02T10:00:00Z", change_content=False)
        self.assertEqual(self.verify().returncode, 0)
        self.edit(updated_at="2000-01-01T00:00:00Z", change_content=False)
        self.assertEqual(self.verify().returncode, 0)

    def test_us007a2_c8_head_without_the_season_does_not_fail_and_warns(self) -> None:
        git(self.feed, "init", "-q", "-b", "main")
        git(self.feed, "config", "--local", "user.email", "feed@example.invalid")
        git(self.feed, "config", "--local", "user.name", "Feed Bot")
        git(self.feed, "add", "-A")
        git(self.feed, "rm", "-q", "--cached", SEASON_PATH)
        git(self.feed, "commit", "-q", "-m", "without the season")
        result = self.verify()
        self.assertIn("[ OK ] 4b-updated-at-frente-a-head", result.stdout)
        self.assertIn("aviso: HEAD no tiene", result.stdout)

    def test_us007a2_c8_no_commit_at_all_does_not_fail_and_warns(self) -> None:
        git(self.feed, "init", "-q", "-b", "main")
        result = self.verify()
        self.assertIn("[ OK ] 4b-updated-at-frente-a-head", result.stdout)
        self.assertIn("aviso: HEAD no tiene", result.stdout)

    def test_us007a2_c8_unreadable_head_version_does_not_fail_and_warns(self) -> None:
        self.init_repo()
        with open(os.path.join(self.feed, SEASON_PATH), encoding="utf-8") as handle:
            good = handle.read()
        with open(os.path.join(self.feed, SEASON_PATH), "w", encoding="utf-8") as handle:
            handle.write(good[:-40])
        git(self.feed, "commit", "-q", "-am", "broken")
        with open(os.path.join(self.feed, SEASON_PATH), "w", encoding="utf-8") as handle:
            handle.write(good)
        self.edit(updated_at="2026-10-02T10:00:00Z")
        result = self.verify()
        self.assertIn("[ OK ] 4b-updated-at-frente-a-head", result.stdout)
        self.assertIn("version anterior ilegible", result.stdout)

    def test_us007a2_c8_the_comparison_leaves_no_file_in_the_repository(self) -> None:
        self.init_repo()
        self.verify()
        status = subprocess.run(["git", "-C", self.feed, "status", "--porcelain", "--untracked-files=all"],
                                stdout=subprocess.PIPE, universal_newlines=True).stdout
        self.assertEqual(status, "")


class MissingTools(CopiedFeed):
    def test_us007a_c9_missing_tools_venv_fails_and_says_how_to_create_it(self) -> None:
        env = tool_env()
        env.pop("FEED_TOOLS_VENV", None)
        result = run_script([os.path.join(self.feed, "scripts", "verify")], cwd=self.tmp, env=env)
        self.assertEqual(result.returncode, 2, output_of(result))
        self.assertIn("scripts/bootstrap-contract-tools", result.stderr)
        self.assertIn("requirements-ci.txt", result.stderr)


class FullRun(CopiedFeed):
    def test_us007a_c9_passes_without_network_and_lists_every_check_by_name(self) -> None:
        self.init_repo()
        result = self.verify()
        self.assertEqual(result.returncode, 0, output_of(result))
        for name in EXPECTED_CHECKS:
            self.assertIn("[ OK ] " + name, result.stdout)
        self.assertIn("verify: todo correcto", result.stdout)
        self.assertIn("[OMITIDA] 2-paridad-con-la-app", result.stdout)

    def test_us007a_c9_a_failing_check_is_named_and_the_exit_code_is_non_zero(self) -> None:
        self.init_repo()
        with open(os.path.join(self.feed, "public", "v1", "calendario.pdf"), "w") as handle:
            handle.write("no debe estar aqui")
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[FALLO] 1-estructura", result.stdout)
        self.assertIn("calendario.pdf", result.stdout)
        self.assertIn("verify: FALLO", result.stderr)
        self.assertIn("1-estructura", result.stderr)

    def test_us007a_c9_a_failure_message_containing_the_word_warn_is_not_hidden(self) -> None:
        # S-6: solo se filtra el aviso conocido de urllib3, no cualquier linea con "warn".
        self.init_repo()
        with open(os.path.join(self.feed, "public", "v1", "warning-notes.pdf"), "w") as handle:
            handle.write("x")
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("warning-notes.pdf", result.stdout)

    def test_us007a_c9_a_broken_workflow_is_named(self) -> None:
        self.init_repo()
        path = os.path.join(self.feed, ".github", "workflows", "publish.yml")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace("needs: validate\n", "", 1))
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[FALLO] 5-workflow", result.stdout)

    def test_us007a_c9_is_idempotent_and_leaves_the_repository_untouched(self) -> None:
        self.init_repo()
        first = self.verify()
        status_after_first = subprocess.run(["git", "-C", self.feed, "status", "--porcelain", "--untracked-files=all"],
                                            stdout=subprocess.PIPE, universal_newlines=True).stdout
        second = self.verify()
        self.assertEqual(first.returncode, 0, output_of(first))
        self.assertEqual(second.returncode, 0, output_of(second))
        self.assertEqual(status_after_first, "")
        self.assertIn("[ OK ] 9-idempotente", second.stdout)


if __name__ == "__main__":
    unittest.main()
