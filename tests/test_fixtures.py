"""US-007a, criterio 3: validacion contra el esquema con los fixtures de US-002a copiados byte a byte."""
from __future__ import annotations

import json
import os
import unittest
from typing import List

import support
import validate_schema
from support import FIXTURES, output_of, run_script

VALIDATE = os.path.join(support.SCRIPTS, "validate-schema")


def files_in(directory: str, skip: List[str]) -> List[str]:
    return [os.path.join(directory, name) for name in sorted(os.listdir(directory)) if name.endswith(".json") and name not in skip]


class ContractFixtures(unittest.TestCase):
    def test_us007a_c3_valid_fixtures_pass(self) -> None:
        valid = files_in(os.path.join(FIXTURES, "valid"), [])
        self.assertGreaterEqual(len(valid), 8)
        result = run_script([VALIDATE] + valid)
        self.assertEqual(result.returncode, 0, output_of(result))

    def test_us007a_c3_every_invalid_fixture_fails_and_is_named(self) -> None:
        invalid = files_in(os.path.join(FIXTURES, "invalid"), ["expected.json"])
        self.assertGreaterEqual(len(invalid), 15)
        result = run_script([VALIDATE] + invalid)
        self.assertNotEqual(result.returncode, 0)
        for path in invalid:
            self.assertIn("FALLO esquema: %s" % path, result.stderr)

    def test_us007a_c3_one_invalid_fixture_alone_returns_a_non_zero_code(self) -> None:
        result = run_script([VALIDATE, os.path.join(FIXTURES, "invalid", "negative-score.json")])
        self.assertNotEqual(result.returncode, 0)

    def test_us007a_c3_published_files_pass_the_schema(self) -> None:
        public = os.path.join(support.FEED, "public", "v1")
        result = run_script([VALIDATE, os.path.join(public, "index.json")] +
                            files_in(os.path.join(public, "seasons"), []))
        self.assertEqual(result.returncode, 0, output_of(result))

    def test_us007a_c3_no_arguments_is_a_usage_error(self) -> None:
        self.assertEqual(run_script([VALIDATE]).returncode, 2)

    def test_us007a_c3_expected_json_lists_every_invalid_fixture(self) -> None:
        directory = os.path.join(FIXTURES, "invalid")
        with open(os.path.join(directory, "expected.json"), encoding="utf-8") as handle:
            expected = sorted(json.load(handle))
        self.assertEqual(expected, sorted(os.path.basename(p) for p in files_in(directory, ["expected.json"])))


class ToolExitCode(support.TempDirTestCase):
    """S-5: un codigo de salida distinto de 0 sin errores descritos tambien es un fallo."""

    def fake_tool(self, stdout: str, code: int) -> str:
        path = os.path.join(self.tmp, "fake-check-jsonschema")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\ncat <<'EOF'\n%s\nEOF\nexit %d\n" % (stdout, code))
        os.chmod(path, 0o755)
        return path

    def validate(self, tool: str):
        target = os.path.join(self.tmp, "season.json")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("{}")
        return support.run_main(validate_schema.main, ["--feed", support.FEED, "--tool", tool, target])

    def test_us007a_c3_non_zero_exit_with_json_without_errors_fails(self) -> None:
        result = self.validate(self.fake_tool('{"status": "fail"}', 1))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("codigo 1", result.stderr)

    def test_us007a_c3_zero_exit_with_json_without_errors_passes(self) -> None:
        self.assertEqual(self.validate(self.fake_tool('{"status": "ok"}', 0)).returncode, 0)


if __name__ == "__main__":
    unittest.main()
