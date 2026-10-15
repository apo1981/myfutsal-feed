"""US-007a, criterio 5: el workflow se comprueba de forma estatica (un workflow no se ejecuta en local)."""
from __future__ import annotations

import json
import os
import subprocess
import unittest
from typing import Any, Dict, List

import support
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
        self.assertNotRegex(text, r"(?i)always\(\)")
        self.assertNotIn("continue-on-error", text)

    def test_us007a_c5_actions_runner_and_concurrency(self) -> None:
        uses = [step["uses"] for job in self.workflow["jobs"].values() for step in job["steps"] if "uses" in step]
        self.assertTrue(uses)
        self.assertTrue(all(u.startswith("actions/") for u in uses), uses)
        self.assertEqual({job["runs-on"] for job in self.workflow["jobs"].values()}, {"ubuntu-24.04"})
        self.assertEqual(self.workflow["concurrency"], {"group": "pages", "cancel-in-progress": False})

    def test_us007a_c5_validate_checks_out_the_full_history(self) -> None:
        checkout = [s for s in self.workflow["jobs"]["validate"]["steps"] if s.get("uses", "").startswith("actions/checkout@")]
        self.assertEqual([s["with"]["fetch-depth"] for s in checkout], [0])


class PreviousVersionStep(TempDirTestCase):
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

    def commit_season(self, updated_at: str) -> str:
        season = support.season_with(lambda s: s.update(updatedAt=updated_at))
        support.write_json(os.path.join(self.repo, "public", "v1", "seasons", support.SEASON_ID + ".json"), season)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", updated_at)
        return self.git("rev-parse", "HEAD")

    def run_step(self, before: str) -> str:
        env = {"PATH": os.environ["PATH"], "BEFORE": before, "GITHUB_OUTPUT": self.output, "RUNNER_TEMP": self.runner_temp}
        proc = subprocess.run(["bash", "-e", "-c", self.step_script()], cwd=self.repo, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(self.output, encoding="utf-8") as handle:
            line = [l for l in handle.read().splitlines() if l.startswith("previous=")][-1]
        return line[len("previous="):]

    def previous_updated_at(self, path: str) -> str:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)["updatedAt"]

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


if __name__ == "__main__":
    unittest.main()
