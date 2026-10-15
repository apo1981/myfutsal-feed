"""US-007a, criterio 9: un unico comando local, sin red, que indica por nombre lo que falla.

Se ejecuta sobre copias temporales del feed (con FEED_VERIFY_NESTED=1 para no recursar);
el repositorio real no se toca. En la ejecucion anidada verify omite las pruebas unitarias.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from typing import Dict

import support
from support import TempDirTestCase, output_of, run_script, tool_env

SKIPPED = shutil.ignore_patterns(".git", ".venv", "__pycache__")
REAL_VENV = os.path.normpath(os.path.join(support.FEED, "..", ".venv-tools"))
EXPECTED_CHECKS = ["1-estructura", "2-paridad-sha256sums", "3-esquema-de-public", "4-invariantes-de-public",
                   "5-workflow", "8-runbook", "9-idempotente"]


def git(directory: str, *args: str) -> None:
    subprocess.run(["git", "-C", directory] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


class CopiedFeed(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.feed = os.path.join(self.tmp, "myfutsal-feed")
        shutil.copytree(support.FEED, self.feed, ignore=SKIPPED)

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
