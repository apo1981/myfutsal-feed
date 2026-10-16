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
        self.assertEqual(len(names), 9)
        for fragment in ("primera vez", "publicar", "aplazar", "carga inicial", "validate", "posterior", "rollback", "iPhone", "tiempo"):
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


def section_body(text: str, heading_start: str) -> str:
    return next(body for heading, body in runbook_check.split_sections(text) if heading.lower().startswith(heading_start))


class InitialLoadSection(unittest.TestCase):
    def test_us007a2_c6_real_runbook_passes_the_content_check_with_the_initial_load_section(self) -> None:
        result = run_main(runbook_check.main, ["--feed", support.FEED])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("carga inicial", [heading.lower() for heading, _ in runbook_check.split_sections(read(REAL_RUNBOOK))])

    def test_us007a2_c6_the_section_covers_prepare_convert_validate_publish_and_the_pdf_rule(self) -> None:
        body = section_body(read(REAL_RUNBOOK), "carga inicial")
        for step in ("Prepara los CSV", "Convierte", "valida", "Publica"):
            self.assertIn(step, body)
        self.assertRegex(body, r"PDF[^.]*nunca")
        self.assertIn("scripts/convert-csv", body)
        self.assertIn("scripts/verify", body)

    def test_us007a2_c6_every_command_of_the_section_exists(self) -> None:
        blocks = runbook_check.code_blocks(section_body(read(REAL_RUNBOOK), "carga inicial"))
        cited = {m for block in blocks for m in re.findall(r"scripts/[A-Za-z0-9_./-]+", block)}
        self.assertIn("scripts/convert-csv", cited)
        for path in cited:
            self.assertTrue(os.path.isfile(os.path.join(support.FEED, path)), path)

    def test_us007a2_c10_the_section_and_step_3_of_validate_tell_to_pass_a_new_updated_at(self) -> None:
        text = read(REAL_RUNBOOK)
        self.assertRegex(section_body(text, "carga inicial"), r"--updated-at[^\n]*nuevo|nuevo[^\n]*--updated-at")
        step3 = next(line for line in section_body(text, "qué hacer si falla `validate`").splitlines() if line.startswith("3."))
        self.assertIn("--updated-at", step3)
        self.assertIn("nuevo", step3)
        self.assertIn("a mano", step3)

    def test_us007a2_c10_the_manual_instruction_of_s10_is_kept_for_direct_json_edits(self) -> None:
        step3 = next(line for line in section_body(read(REAL_RUNBOOK), "qué hacer si falla `validate`").splitlines() if line.startswith("3."))
        self.assertIn("pon de nuevo `updatedAt` en el instante actual", step3)


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

    def test_us007a_c8_rollback_that_forgets_the_new_updated_at_fails(self) -> None:
        # B-2: el rollback sin `updatedAt` nuevo es rechazado por la puerta.
        text = read(REAL_RUNBOOK)
        start = text.index("## Revertir")
        end = text.index("## Verificación en el iPhone")
        result = self.check(text[:start] + "## Revertir (rollback)\n\n```\ngit revert <commit>\n```\n\n" + text[end:])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("'revertir (rollback)' no menciona", result.stderr)

    def test_us007a_c8_rollback_without_git_add_before_the_commit_fails(self) -> None:
        # B-2 (ciclo 1): sin `git add` el commit se lleva el updatedAt antiguo.
        text = read(REAL_RUNBOOK)
        self.assertIn("git add public/v1/seasons/<id>.json\n", text)
        result = self.check(text.replace("git add public/v1/seasons/<id>.json\n", ""))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no hace `git add`", result.stderr)

    def test_us007a_c8_rollback_with_commit_a_instead_of_git_add_is_accepted(self) -> None:
        text = read(REAL_RUNBOOK).replace("git add public/v1/seasons/<id>.json\n", "").replace("git commit -m", "git commit -am")
        result = self.check(text)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_us007a_c8_first_time_without_the_email_push_block_fails(self) -> None:
        result = self.check(read(REAL_RUNBOOK).replace("Block command line pushes", "otra cosa"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Block command line pushes", result.stderr)

    def test_us007a_c8_command_that_does_not_exist_fails(self) -> None:
        text = read(REAL_RUNBOOK) + "\n```\nscripts/no-existe --ayuda\n```\n"
        result = self.check(text)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("scripts/no-existe", result.stderr)

    def test_us007a_c8_command_only_in_prose_is_not_extracted(self) -> None:
        result = self.check(read(REAL_RUNBOOK) + "\nTambien existe scripts/solo-en-prosa pero no esta en un bloque.\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_us007a2_c6_missing_initial_load_section_fails_and_is_named(self) -> None:
        result = self.check(read(REAL_RUNBOOK).replace("## Carga inicial", "## Otra cosa"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("falta la seccion 'carga inicial'", result.stderr)

    def test_us007a2_c6_initial_load_without_the_pdf_rule_fails(self) -> None:
        result = self.check(read(REAL_RUNBOOK).replace("no se sube nunca", "se puede subir"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("'carga inicial' no menciona", result.stderr)

    def test_us007a2_c6_initial_load_citing_a_missing_converter_fails(self) -> None:
        result = self.check(read(REAL_RUNBOOK).replace("scripts/convert-csv --teams", "scripts/convierte-todo --teams"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("scripts/convierte-todo", result.stderr)

    def test_us007a_c8_missing_runbook_fails(self) -> None:
        result = run_main(runbook_check.main, ["--feed", self.tmp, os.path.join(self.tmp, "nada.md")])
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
