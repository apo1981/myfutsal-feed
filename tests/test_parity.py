"""US-007a, criterio 2: paridad del esquema. La parte `shasum` se prueba aqui; la comparacion con la
copia canonica la ejecuta `scripts/check-contract --feed myfutsal-feed` desde el repositorio de la app
(que a su vez prueba sus propios caminos de fallo en `scripts/test-check-contract`)."""
from __future__ import annotations

import os
import shutil
import unittest

import support
from support import TempDirTestCase, output_of, run_script

SCHEMA_FILES = ["league-feed-v1.season.schema.json", "league-feed-v1.index.schema.json"]


class ShasumParity(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.schema = os.path.join(self.tmp, "schema")
        shutil.copytree(os.path.join(support.FEED, "schema"), self.schema)

    def shasum(self):
        return run_script(["shasum", "-a", "256", "-c", "SHA256SUMS"], cwd=self.schema)

    def test_us007a_c2_unmodified_schema_copy_passes(self) -> None:
        result = self.shasum()
        self.assertEqual(result.returncode, 0, output_of(result))

    def test_us007a_c2_one_edited_byte_fails_and_names_the_file(self) -> None:
        for name in SCHEMA_FILES:
            with self.subTest(name):
                path = os.path.join(self.schema, name)
                with open(path, "rb") as handle:
                    original = handle.read()
                with open(path, "wb") as handle:
                    handle.write(original + b" ")
                result = self.shasum()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("%s: FAILED" % name, result.stdout)
                with open(path, "wb") as handle:
                    handle.write(original)

    def test_us007a_c2_missing_schema_file_fails(self) -> None:
        os.remove(os.path.join(self.schema, SCHEMA_FILES[0]))
        self.assertNotEqual(self.shasum().returncode, 0)

    def test_us007a_c2_sha256sums_lists_exactly_the_two_schemas(self) -> None:
        with open(os.path.join(support.FEED, "schema", "SHA256SUMS"), encoding="utf-8") as handle:
            listed = sorted(line.split("  ", 1)[1].strip() for line in handle if line.strip())
        self.assertEqual(listed, sorted(SCHEMA_FILES))


if __name__ == "__main__":
    unittest.main()
