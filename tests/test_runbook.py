"""US-007a, criterio 8: contenido del runbook y existencia de los comandos que cita."""
from __future__ import annotations

import os
import re
import shutil
import unittest

import support
import runbook_check
from support import TempDirTestCase, run_main

REAL_RUNBOOK = os.path.join(support.FEED, "RUNBOOK.md")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class RealRunbook(unittest.TestCase):
    def test_us007a_c8_real_runbook_has_every_section_and_existing_commands(self) -> None:
        result = run_main(runbook_check.main, ["--feed", support.FEED])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_us007a_c8_the_required_sections_cover_the_story(self) -> None:
        names = [name for name, _heading, _concepts in runbook_check.REQUIRED_SECTIONS]
        self.assertEqual(len(names), 8)
        for fragment in ("primera vez", "publicar", "aplazar", "validate", "posterior", "rollback", "iPhone", "tiempo"):
            self.assertTrue(any(fragment.lower() in name.lower() for name in names), fragment)

    def test_us007a_c8_first_time_section_mentions_the_local_identity_change(self) -> None:
        sections = dict(runbook_check.split_sections(read(REAL_RUNBOOK)))
        body = next(body for heading, body in sections.items() if heading.lower().startswith("primera vez"))
        self.assertRegex(body, r"noreply")
        self.assertRegex(body, r"user\.email")

    def test_us007a_c8_every_script_in_code_blocks_exists(self) -> None:
        cited = {m for block in runbook_check.code_blocks(read(REAL_RUNBOOK)) for m in re.findall(r"scripts/[A-Za-z0-9_./-]+", block)}
        self.assertGreaterEqual(len(cited), 5)
        for path in cited:
            self.assertTrue(os.path.isfile(os.path.join(support.FEED, path)), path)


class BrokenRunbooks(TempDirTestCase):
    def check(self, text: str):
        feed = os.path.join(self.tmp, "feed")
        shutil.copytree(os.path.join(support.FEED, "scripts"), os.path.join(feed, "scripts"), ignore=shutil.ignore_patterns("__pycache__"))
        path = os.path.join(feed, "RUNBOOK.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return run_main(runbook_check.main, ["--feed", feed])

    def test_us007a_c8_missing_section_fails_and_is_named(self) -> None:
        text = read(REAL_RUNBOOK)
        text = text.replace("## Revertir (rollback) a la versión anterior", "## Otra cosa")
        result = self.check(text)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("falta la seccion 'revertir (rollback)'", result.stderr)

    def test_us007a_c8_section_without_its_key_concept_fails(self) -> None:
        result = self.check(read(REAL_RUNBOOK).replace("noreply", "anonima"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("'primera vez' no menciona", result.stderr)

    def test_us007a_c8_command_that_does_not_exist_fails(self) -> None:
        text = read(REAL_RUNBOOK) + "\n```\nscripts/no-existe --ayuda\n```\n"
        result = self.check(text)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("scripts/no-existe", result.stderr)

    def test_us007a_c8_command_only_in_prose_is_not_extracted(self) -> None:
        result = self.check(read(REAL_RUNBOOK) + "\nTambien existe scripts/solo-en-prosa pero no esta en un bloque.\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_us007a_c8_missing_runbook_fails(self) -> None:
        result = run_main(runbook_check.main, ["--feed", self.tmp, os.path.join(self.tmp, "nada.md")])
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
