"""US-007a, criterio 5: el workflow se comprueba de forma estatica (un workflow no se ejecuta en local)."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import unittest
from typing import Any, Dict, List

import support  # debe ir antes que invariants y runbook_check: anade scripts/ al sys.path
import invariants
import runbook_check
from support import TempDirTestCase, output_of, run_script

WORKFLOW = os.path.join(support.FEED, ".github", "workflows", "publish.yml")
POLICY = os.path.join(support.FEED, "policy", "workflow-policy.schema.json")
CASES = os.path.join(support.FEED, "tests", "cases", "workflows")
CHECK_WORKFLOW = os.path.join(support.SCRIPTS, "check-workflow")
ZEROS = "0" * 40
RULE_NUMBERS = set(range(0, 13))  # 0 = vendor.github-workflows; 1 a 12 = reglas de la politica y del grep


def venv_tool(name: str) -> str:
    for candidate in (os.environ.get("FEED_TOOLS_VENV"), os.path.join(support.FEED, ".venv"),
                      os.path.join(support.FEED, "..", ".venv-tools")):
        if candidate and os.path.exists(os.path.join(candidate, "bin", name)):
            return os.path.join(candidate, "bin", name)
    raise unittest.SkipTest("falta el venv de herramientas")


def load_cases() -> Dict[str, Dict[str, str]]:
    with open(os.path.join(CASES, "expected.json"), encoding="utf-8") as handle:
        return json.load(handle)


def policy_errors(files: List[str]) -> Dict[str, List[str]]:
    """Errores de la politica por fichero, en una sola ejecucion de la herramienta."""
    proc = subprocess.run([venv_tool("check-jsonschema"), "--no-cache", "--output-format", "json", "--schemafile", POLICY] + files,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    grouped = {}  # type: Dict[str, List[str]]
    for error in json.loads(proc.stdout).get("errors", []):
        grouped.setdefault(os.path.basename(error["filename"]), []).append("%s %s" % (error["path"], error["message"]))
    return grouped


class ValidWorkflow(unittest.TestCase):
    def test_us007a_c5_the_real_workflow_passes_the_three_phases(self) -> None:
        result = run_script([CHECK_WORKFLOW])
        self.assertEqual(result.returncode, 0, output_of(result))
        for phase in ("vendor-github-workflows", "politica-propia", "sin-secretos"):
            self.assertIn("ok " + phase, result.stdout)

    def test_us007a_c5_every_policy_rule_has_an_invalid_example(self) -> None:
        rules = {int(entry["rule"].split()[0]) for entry in load_cases().values()}
        self.assertEqual(rules, RULE_NUMBERS)

    def test_us007a_c5_expected_json_and_example_files_match(self) -> None:
        files = sorted(name for name in os.listdir(CASES) if name.endswith(".yml"))
        self.assertEqual(files, sorted(load_cases()))


class InvalidExamples(unittest.TestCase):
    def policy_cases(self) -> Dict[str, Dict[str, str]]:
        return {name: entry for name, entry in load_cases().items() if entry["phase"] == "politica-propia"}

    def test_us007a_c5_policy_rejects_each_example_at_the_expected_place(self) -> None:
        cases = self.policy_cases()
        errors = policy_errors([os.path.join(CASES, name) for name in sorted(cases)])
        for name, entry in cases.items():
            with self.subTest(name):
                self.assertIn(name, errors, "la politica acepto %s (regla %s)" % (name, entry["rule"]))
                self.assertTrue(any(entry["contains"] in error for error in errors[name]),
                                "%s: %r no aparece en %s" % (name, entry["contains"], errors[name]))

    def test_us007a_c5_policy_examples_are_valid_for_the_vendor_schema(self) -> None:
        names = sorted(self.policy_cases())
        proc = subprocess.run([venv_tool("check-jsonschema"), "--no-cache", "--builtin-schema", "vendor.github-workflows"] +
                              [os.path.join(CASES, n) for n in names], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_us007a_c5_each_example_differs_from_the_real_workflow_in_a_few_lines(self) -> None:
        with open(WORKFLOW, encoding="utf-8") as handle:
            real = handle.read().splitlines()
        for name in load_cases():
            with self.subTest(name):
                with open(os.path.join(CASES, name), encoding="utf-8") as handle:
                    example = handle.read().splitlines()
                changed = len(set(real) ^ set(example))
                self.assertLessEqual(changed, 8, "%s se aleja demasiado del workflow real" % name)

    def test_us007a_c5_the_script_rejects_a_policy_violation_naming_the_phase(self) -> None:
        result = run_script([CHECK_WORKFLOW, os.path.join(CASES, "deploy-without-needs.yml")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FALLO politica-propia", result.stderr)
        self.assertIn("ok vendor-github-workflows", result.stdout)

    def test_us007a_c5_the_script_rejects_a_foreign_secret_with_grep(self) -> None:
        result = run_script([CHECK_WORKFLOW, os.path.join(CASES, "secret-reference.yml")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FALLO sin-secretos", result.stderr)
        self.assertIn("secrets.DEPLOY_KEY", result.stderr)
        self.assertIn("ok politica-propia", result.stdout)

    def test_us007a_c5_the_script_rejects_a_foreign_secret_on_the_same_line_as_github_token(self) -> None:
        # S-7: antes, la linea entera se descartaba por contener secrets.GITHUB_TOKEN.
        result = run_script([CHECK_WORKFLOW, os.path.join(CASES, "secret-after-github-token.yml")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FALLO sin-secretos", result.stderr)
        self.assertIn("secrets.DEPLOY_KEY", result.stderr)
        self.assertNotIn("secrets.GITHUB_TOKEN", result.stderr)

    def test_us007a_c5_the_script_rejects_a_vendor_schema_violation(self) -> None:
        result = run_script([CHECK_WORKFLOW, os.path.join(CASES, "vendor-schema-typo.yml")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FALLO vendor-github-workflows", result.stderr)

    def test_us007a_c5_github_token_is_the_only_secret_allowed(self) -> None:
        with open(WORKFLOW, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn("secrets.", text.replace("secrets.GITHUB_TOKEN", ""))


class WorkflowShape(unittest.TestCase):
    """Lo que la politica garantiza, comprobado de forma independiente sobre el YAML cargado."""

    @classmethod
    def setUpClass(cls) -> None:
        sys_path_python = venv_tool("python")
        code = "import json,sys; from ruamel.yaml import YAML; print(json.dumps(YAML(typ='safe').load(open(sys.argv[1]))))"
        proc = subprocess.run([sys_path_python, "-c", code, WORKFLOW], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        cls.workflow = json.loads(proc.stdout)  # type: Dict[str, Any]

    def test_us007a_c5_triggers_are_push_to_main_and_manual_only(self) -> None:
        self.assertEqual(sorted(self.workflow["on"]), ["push", "workflow_dispatch"])
        self.assertEqual(self.workflow["on"]["push"], {"branches": ["main"]})

    def test_us007a_c5_deploy_permissions_exist_only_in_the_deploy_job(self) -> None:
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        holders = [name for name, job in self.workflow["jobs"].items()
                   if {"pages", "id-token"} & set(job.get("permissions", {}))]
        self.assertEqual(holders, ["deploy"])

    def test_us007a_c5_deploy_needs_validate_and_smoke_needs_deploy(self) -> None:
        jobs = self.workflow["jobs"]
        self.assertEqual(jobs["deploy"]["needs"], "validate")
        self.assertEqual(jobs["smoke"]["needs"], "deploy")

    def test_us007a_c5_no_job_or_step_can_skip_the_gate(self) -> None:
        with open(WORKFLOW, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotRegex(text, r"(?i)(always|failure|cancelled)\s*\(")
        self.assertNotIn("continue-on-error", text)

    def test_us007a_c5_actions_runner_and_concurrency(self) -> None:
        uses = [step["uses"] for job in self.workflow["jobs"].values() for step in job["steps"] if "uses" in step]
        self.assertTrue(uses)
        self.assertTrue(all(u.startswith("actions/") for u in uses), uses)
        self.assertEqual({job["runs-on"] for job in self.workflow["jobs"].values()}, {"ubuntu-24.04"})
        self.assertEqual(self.workflow["concurrency"], {"group": "pages", "cancel-in-progress": False})

    def test_us007a_c5_pages_actions_use_the_node_24_major_version(self) -> None:
        # I-2: la v4 usa Node 20, retirado de los runners el 23-09-2026.
        uses = [step["uses"] for step in self.workflow["jobs"]["deploy"]["steps"] if "uses" in step]
        self.assertIn("actions/upload-pages-artifact@v5", uses)
        self.assertIn("actions/deploy-pages@v5", uses)

    def test_us007a_c5_deploy_and_smoke_have_no_if(self) -> None:
        for name in ("deploy", "smoke"):
            self.assertNotIn("if", self.workflow["jobs"][name], name)

    def test_us007a_c5_checkouts_do_not_persist_the_token(self) -> None:
        # S-2: validate instala paquetes de PyPI tras el checkout; el token no debe quedar en .git/config.
        checkouts = [s for job in self.workflow["jobs"].values() for s in job["steps"]
                     if s.get("uses", "").startswith("actions/checkout@")]
        self.assertEqual(len(checkouts), 3)
        self.assertTrue(all(s["with"]["persist-credentials"] is False for s in checkouts))

    def test_us007a_c5_validate_checks_out_the_full_history(self) -> None:
        checkout = [s for s in self.workflow["jobs"]["validate"]["steps"] if s.get("uses", "").startswith("actions/checkout@")]
        self.assertEqual([s["with"]["fetch-depth"] for s in checkout], [0])


class TempGitFeed(TempDirTestCase):
    """El paso `previous` del workflow se ejecuta de verdad en un repositorio Git temporal."""

    @classmethod
    def step_script(cls) -> str:
        code = ("import sys; from ruamel.yaml import YAML; wf = YAML(typ='safe').load(open(sys.argv[1]));"
                "print([s for s in wf['jobs']['validate']['steps'] if s.get('id') == 'previous'][0]['run'])")
        proc = subprocess.run([venv_tool("python"), "-c", code, WORKFLOW], stdout=subprocess.PIPE, universal_newlines=True, check=True)
        return proc.stdout

    def setUp(self) -> None:
        super().setUp()
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "public", "v1", "seasons"))
        self.git("init", "-q", "-b", "main")
        support.write_json(os.path.join(self.repo, "public", "v1", "index.json"), support.BASE_INDEX)
        self.output = os.path.join(self.tmp, "github_output")
        self.runner_temp = os.path.join(self.tmp, "runner")
        os.makedirs(self.runner_temp)

    def git(self, *args: str) -> str:
        proc = subprocess.run(["git", "-C", self.repo, "-c", "user.name=T", "-c", "user.email=t@example.invalid"] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, check=True)
        return proc.stdout.strip()

    def season_path(self) -> str:
        return os.path.join(self.repo, "public", "v1", "seasons", support.SEASON_ID + ".json")

    def write_season(self, updated_at: str, postponed: bool = False) -> None:
        def mutate(season: Dict[str, Any]) -> None:
            season.update(updatedAt=updated_at)
            if postponed:
                support.match_of(season, "m-02").update(status="postponed")
        support.write_json(self.season_path(), support.season_with(mutate))

    def commit_season(self, updated_at: str, postponed: bool = False) -> str:
        self.write_season(updated_at, postponed)
        return self.commit_all(updated_at)

    def commit_broken_season(self) -> str:
        """Un commit con JSON mal formado (el error tipico al editar a mano): no pasa la puerta."""
        with open(self.season_path(), "w", encoding="utf-8") as handle:
            handle.write('{"schemaVersion": 1,, "seasonId": ')
        return self.commit_all("broken json")

    def commit_all(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def run_step(self, before: str) -> str:
        env = {"PATH": os.environ["PATH"], "BEFORE": before, "GITHUB_OUTPUT": self.output, "RUNNER_TEMP": self.runner_temp}
        proc = subprocess.run(["bash", "-e", "-c", self.step_script()], cwd=self.repo, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(self.output, encoding="utf-8") as handle:
            line = [l for l in handle.read().splitlines() if l.startswith("previous=")][-1]
        return line[len("previous="):]

    def run_invariants(self, before: str):
        """El paso `previous` real seguido de la invariante, como en el job `validate`."""
        self.git("checkout", "-q", "--", ".")  # CI valida un checkout limpio: solo cuenta lo confirmado, no el arbol de trabajo
        previous = self.run_step(before)
        args = ["--feed", self.repo, "--public", os.path.join(self.repo, "public")]
        return support.run_main(invariants.main, args + (["--previous", previous] if previous else []))

    def previous_updated_at(self, path: str) -> str:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)["updatedAt"]



class PreviousVersionStep(TempGitFeed):
    def test_us007a_c5_uses_github_event_before_when_it_exists(self) -> None:
        first = self.commit_season("2026-10-01T10:00:00Z")
        self.commit_season("2026-10-02T10:00:00Z")
        self.commit_season("2026-10-03T10:00:00Z")
        path = self.run_step(first)
        self.assertEqual(self.previous_updated_at(path), "2026-10-01T10:00:00Z")

    def test_us007a_c5_all_zeros_before_falls_back_to_head_caret(self) -> None:
        self.commit_season("2026-10-01T10:00:00Z")
        self.commit_season("2026-10-02T10:00:00Z")
        self.assertEqual(self.previous_updated_at(self.run_step(ZEROS)), "2026-10-01T10:00:00Z")

    def test_us007a_c5_empty_before_from_workflow_dispatch_falls_back_to_head_caret(self) -> None:
        self.commit_season("2026-10-01T10:00:00Z")
        self.commit_season("2026-10-02T10:00:00Z")
        self.assertEqual(self.previous_updated_at(self.run_step("")), "2026-10-01T10:00:00Z")

    def test_us007a_c5_unknown_before_commit_falls_back_to_head_caret(self) -> None:
        self.commit_season("2026-10-01T10:00:00Z")
        self.commit_season("2026-10-02T10:00:00Z")
        self.assertEqual(self.previous_updated_at(self.run_step("f" * 40)), "2026-10-01T10:00:00Z")

    def test_us007a_c5_no_previous_commit_is_a_first_publication(self) -> None:
        self.commit_season("2026-10-01T10:00:00Z")
        self.assertEqual(self.run_step(ZEROS), "")

    def test_us007a_c5_previous_commit_without_the_season_file_is_a_first_publication(self) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "index only")
        self.commit_season("2026-10-01T10:00:00Z")
        self.assertEqual(self.run_step(ZEROS), "")

    def test_us007a_c4_fix_after_a_broken_commit_passes_the_gate_on_push_and_on_manual_rerun(self) -> None:
        # B-1: bueno -> roto (no se publico) -> correccion. Antes, la correccion fallaba por leer el commit roto.
        self.commit_season("2026-10-01T10:00:00Z")
        broken = self.commit_broken_season()
        self.commit_season("2026-10-02T10:00:00Z", postponed=True)
        for label, before in (("push", broken), ("workflow_dispatch", "")):
            with self.subTest(label):
                result = self.run_invariants(before)
                self.assertEqual(result.returncode, 0, output_of(result))
                self.assertIn("version anterior ilegible", result.stdout)

    def test_us007a_c4_a_broken_commit_still_fails_the_gate_itself(self) -> None:
        self.commit_season("2026-10-01T10:00:00Z")
        good = self.commit_season("2026-10-02T10:00:00Z")
        self.commit_broken_season()
        self.assertNotEqual(self.run_invariants(good).returncode, 0)


class RollbackProcedure(TempGitFeed):
    """B-2: el procedimiento de rollback del RUNBOOK, ejecutado de verdad en un repositorio temporal."""

    COMMIT_MESSAGE_TIME = "(tiempo: 5 min)"

    @staticmethod
    def runbook_git_commands(runbook_text: str) -> List[str]:
        """Las lineas `git ...` de los bloques de la seccion de rollback, en orden y tal como estan escritas."""
        sections = [body for heading, body in runbook_check.split_sections(runbook_text) if "rollback" in heading.lower()]
        assert len(sections) == 1, sections
        lines = [line.strip() for block in runbook_check.code_blocks(sections[0]) for line in block.splitlines()]
        return [line for line in lines if line.startswith("git ")]

    def runbook_text(self) -> str:
        with open(os.path.join(support.FEED, "RUNBOOK.md"), encoding="utf-8") as handle:
            return handle.read()

    def deploy_bad_version(self) -> str:
        self.commit_season("2026-10-01T10:00:00Z")
        bad = self.commit_season("2026-10-02T10:00:00Z", postponed=True)  # valido, desplegado y erroneo
        return bad

    def edit_updated_at(self, updated_at: str) -> None:
        """El paso manual del runbook: editar `updatedAt` en el fichero ya revertido."""
        with open(self.season_path(), encoding="utf-8") as handle:
            season = json.load(handle)
        season["updatedAt"] = updated_at
        support.write_json(self.season_path(), season)

    def follow_runbook(self, runbook_text: str, bad: str, new_updated_at: str) -> None:
        """Ejecuta literalmente cada comando `git` del runbook (salvo `push`, que no tiene remoto aqui)."""
        commands = [c for c in self.runbook_git_commands(runbook_text) if not c.startswith("git push")]
        self.assertTrue(commands[0].startswith("git revert --no-commit"), commands)
        for number, command in enumerate(commands):
            words = shlex.split(command.replace("<commit>", bad).replace("<id>", support.SEASON_ID))
            self.git(*words[1:])
            if number == 0:
                self.edit_updated_at(new_updated_at)

    def test_us007a_c8_following_the_runbook_literally_passes_the_real_gate(self) -> None:
        bad = self.deploy_bad_version()
        self.follow_runbook(self.runbook_text(), bad, "2026-10-03T10:00:00Z")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("show", "HEAD:public/v1/seasons/%s.json" % support.SEASON_ID).count("2026-10-03T10:00:00Z"), 1)
        result = self.run_invariants(bad)
        self.assertEqual(result.returncode, 0, output_of(result))
        self.assertIn("contenido cambiado y updatedAt posterior", result.stdout)

    def test_us007a_c8_the_test_fails_if_the_runbook_loses_its_git_add(self) -> None:
        # Control de B-2: sin `git add` el commit se lleva el updatedAt antiguo y la puerta lo rechaza.
        without_add = "\n".join(line for line in self.runbook_text().splitlines() if not line.strip().startswith("git add"))
        self.assertNotEqual(without_add, self.runbook_text())
        bad = self.deploy_bad_version()
        self.follow_runbook(without_add, bad, "2026-10-03T10:00:00Z")
        result = self.run_invariants(bad)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invariante updated-at-sin-avanzar", result.stderr)

    def test_us007a_c8_a_plain_revert_without_a_new_updated_at_is_rejected(self) -> None:
        # Control: es el fallo que tenia el procedimiento anterior (`git revert` a secas).
        bad = self.deploy_bad_version()
        self.git("revert", "--no-edit", bad)
        result = self.run_invariants(bad)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invariante updated-at-sin-avanzar", result.stderr)


if __name__ == "__main__":
    unittest.main()
