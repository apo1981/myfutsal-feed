"""US-007a, criterio 3: validacion contra el esquema con los fixtures de US-002a copiados byte a byte."""
from __future__ import annotations

import json
import os
import unittest
from typing import List

import support
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


if __name__ == "__main__":
    unittest.main()
