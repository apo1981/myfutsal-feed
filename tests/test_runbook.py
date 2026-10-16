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
        self.assertEqual(len(names), 12)
        for fragment in ("primera vez", "publicar", "aplazar", "carga inicial", "validate", "posterior", "rollback", "iPhone", "tiempo",
                         "ensayo de la puerta", "url base", "registro de evidencias"):
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


class FirstPublicationSections(unittest.TestCase):
    """US-007b1, criterios 1, 3, 4, 7c y 8: la guia del humano, el ensayo de la puerta y la URL base estan en el runbook."""

    def test_us007b1_c7c_first_time_follows_the_guide_in_order(self) -> None:
        body = section_body(read(REAL_RUNBOOK), "primera vez")
        positions = [body.index(fragment) for fragment in (
            "Email privado", "Crear el repositorio", "Pages:", "Protección contra push de secretos", "Token para el push",
            "Identidad y remoto locales", "Primer push", "Después de editar en la web")]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(body.index("user.email"), body.index("git remote add origin"), "la identidad va antes que el remoto")

    def test_us007b1_c7c_the_runbook_never_asks_to_store_the_token_anywhere(self) -> None:
        body = section_body(read(REAL_RUNBOOK), "primera vez")
        self.assertIn("Nunca pegues el token en un fichero", body)
        # Los prefijos se parten para que este fichero no dispare la comprobacion de secretos.
        self.assertNotRegex(read(REAL_RUNBOOK), "github" + "_pat_|gh" + "p_")

    def test_us007b1_c3_the_rehearsal_gives_the_exact_commands_and_the_rollback(self) -> None:
        body = section_body(read(REAL_RUNBOOK), "ensayo de la puerta")
        for command in ("git switch main", "git merge --ff-only ensayo-puerta", "git push origin main", "git revert --no-commit HEAD",
                        "git add public/v1/seasons/2026-27.json", "scripts/verify"):
            self.assertIn(command, body)

    def test_us007b1_c4_the_evidence_table_names_the_propagation_datum(self) -> None:
        body = section_body(read(REAL_RUNBOOK), "registro de evidencias")
        self.assertIn("NFR-011", body)
        self.assertRegex(body, r"servida tras [0-9.]+ s|≤ [0-9]+ s")

    def test_us007b1_c8_the_base_url_includes_the_path(self) -> None:
        body = section_body(read(REAL_RUNBOOK), "url base")
        self.assertIn("\nhttps://apo1981.github.io/myfutsal-feed\n", body)
        self.assertNotIn("<usuario>", body)


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

    def test_us007a2_i1_publish_that_commits_before_verify_fails(self) -> None:
        # I-1: con el commit hecho, `verify` compara HEAD consigo mismo y el verde es enganoso.
        text = read(REAL_RUNBOOK)
        start = text.index("## Publicar un resultado")
        end = text.index("## Cambiar hora")
        section = text[start:end]
        verify_block = "   ```\n   scripts/verify\n   ```\n"
        commit_block = '   ```\n   git add public/v1/seasons/<id>.json\n   git commit -m "Jornada 7: resultados (tiempo: 12 min)"\n   git push origin main\n   ```\n'
        self.assertIn(verify_block, section)
        self.assertIn(commit_block, section)
        self.assertLess(section.index(verify_block), section.index(commit_block))
        swapped = section.replace(verify_block, "@@V@@").replace(commit_block, "@@C@@")
        swapped = swapped.replace("@@V@@", commit_block).replace("@@C@@", verify_block)
        result = self.check(text[:start] + swapped + text[end:])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("confirma (`git commit`) sin ejecutar antes `scripts/verify`", result.stderr)

    def test_us007a2_i1_publish_without_verify_or_without_commit_in_code_fails(self) -> None:
        text = read(REAL_RUNBOOK)
        result = self.check(text.replace("   ```\n   scripts/verify\n   ```\n", "", 1))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("'Publicar un resultado' debe tener", result.stderr)

    def test_us007a2_i1_initial_load_that_commits_before_verify_fails(self) -> None:
        text = read(REAL_RUNBOOK)
        start = text.index("## Carga inicial")
        end = text.index("## Qué hacer si falla `validate`")
        section = text[start:end].replace("scripts/verify\n", "scripts/check-allowlist\n")
        result = self.check(text[:start] + section + text[end:])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("'Carga inicial' confirma", result.stderr)

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
