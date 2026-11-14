"""US-064: catalogo de competiciones, coherencia documento-entrada y espejo byte a byte de Primera."""
from __future__ import annotations

import copy
import json
import os
import unittest
from typing import Any, Callable, Dict

import support  # debe ir antes que invariants: anade scripts/ al sys.path
import catalog
import invariants
from support import PRIMERA_CATALOG, SEASON_ID, TempDirTestCase, output_of, season_with, write_json

CATALOG_PATH = ("v1", "catalog.json")
MAIN_PATH = ("v1", "competitions", "primera", SEASON_ID, "main.json")
MIRROR_INDEX_PATH = ("v1", "competitions", "primera", "index.json")
LEGACY_SEASON_PATH = ("v1", "seasons", SEASON_ID + ".json")
LEGACY_INDEX_PATH = ("v1", "index.json")


class PublicCatalogTestCase(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.public = support.build_public(self.tmp)

    def path(self, parts) -> str:
        return os.path.join(self.public, *parts)

    def check(self):
        return support.run_main(invariants.main, ["--feed", self.tmp, "--public", self.public])

    def assert_fails_with(self, text: str) -> None:
        result = self.check()
        self.assertNotEqual(result.returncode, 0, output_of(result))
        self.assertIn("invariante catalogo", result.stderr)
        self.assertIn(text, result.stderr)

    def edit_catalog(self, mutate: Callable[[Dict[str, Any]], None]) -> None:
        document = copy.deepcopy(PRIMERA_CATALOG)
        mutate(document)
        write_json(self.path(CATALOG_PATH), document)

    @staticmethod
    def first_feed(document: Dict[str, Any]) -> Dict[str, Any]:
        return document["competitions"][0]["seasons"][0]["feeds"][0]


class MirrorOfPrimera(PublicCatalogTestCase):
    def test_us064_c5_identical_mirror_passes(self) -> None:
        result = self.check()
        self.assertEqual(result.returncode, 0, output_of(result))

    def test_us064_c5_season_that_differs_by_one_byte_fails_and_names_both_files(self) -> None:
        with open(self.path(LEGACY_SEASON_PATH), "a", encoding="utf-8") as handle:
            handle.write(" ")
        self.assert_fails_with("seasons/2026-27.json y competitions/primera/2026-27/main.json")

    def test_us064_c5_index_that_differs_by_one_byte_fails(self) -> None:
        with open(self.path(LEGACY_INDEX_PATH), "a", encoding="utf-8") as handle:
            handle.write(" ")
        self.assert_fails_with("index.json y competitions/primera/index.json")

    def test_us064_c5_missing_mirror_file_fails(self) -> None:
        os.remove(self.path(MIRROR_INDEX_PATH))
        self.assert_fails_with("falta un fichero")

    def test_us064_c5_catalog_without_primera_fails(self) -> None:
        def rename(document: Dict[str, Any]) -> None:
            document["competitions"][0]["id"] = "otra"
            feed = self.first_feed(document)
            feed.update(feedId="otra.2026-27", url="competitions/otra/2026-27/main.json")
            document["defaultFeedId"] = "otra.2026-27"
        self.edit_catalog(rename)
        self.assert_fails_with("no tiene Primera")

    def test_us064_c5_sync_after_an_apply_script_keeps_the_mirror(self) -> None:
        with open(self.path(LEGACY_SEASON_PATH), "a", encoding="utf-8") as handle:
            handle.write(" ")
        catalog.sync_primera_mirror(self.path(LEGACY_SEASON_PATH))
        result = self.check()
        self.assertEqual(result.returncode, 0, output_of(result))


class DocumentAgainstCatalogEntry(PublicCatalogTestCase):
    def write_document(self, **fields: Any) -> None:
        """Escribe el mismo documento, con campos nuevos, en las dos rutas para no romper el espejo."""
        document = season_with(lambda season: season.update(fields))
        write_json(self.path(LEGACY_SEASON_PATH), document)
        write_json(self.path(MAIN_PATH), document)

    def test_us064_c3_document_with_other_competition_id_is_invalid(self) -> None:
        self.write_document(competitionId="otra")
        self.assert_fails_with("competitionId 'otra' != 'primera'")

    def test_us064_c3_document_with_other_group_id_is_invalid(self) -> None:
        self.write_document(groupId="grupo-1")
        self.assert_fails_with("groupId 'grupo-1' != None")

    def test_us064_c3_document_with_matching_ids_is_valid(self) -> None:
        self.write_document(competitionId="primera", groupId=None)
        result = self.check()
        self.assertEqual(result.returncode, 0, output_of(result))

    def test_us064_c3_document_with_other_season_id_is_invalid(self) -> None:
        self.write_document(seasonId="2027-28")
        self.assert_fails_with("seasonId '2027-28' != '2026-27'")

    def test_us064_c1_feed_without_file_is_invalid(self) -> None:
        os.remove(self.path(MAIN_PATH))
        self.assert_fails_with("no existe competitions/primera/2026-27/main.json")


class CatalogRules(PublicCatalogTestCase):
    def test_us064_c1_missing_catalog_fails(self) -> None:
        os.remove(self.path(CATALOG_PATH))
        self.assert_fails_with("falta v1/catalog.json")

    def test_us064_c2_feed_id_that_is_not_derived_fails(self) -> None:
        self.edit_catalog(lambda c: self.first_feed(c).update(feedId="primera.2025-26"))
        self.assert_fails_with("feedId 'primera.2025-26' debe ser 'primera.2026-27'")

    def test_us064_c2_url_that_is_not_derived_fails(self) -> None:
        self.edit_catalog(lambda c: self.first_feed(c).update(url="competitions/primera/2026-27/otro.json"))
        self.assert_fails_with("debe ser 'competitions/primera/2026-27/main.json'")

    def test_us064_c1_default_feed_id_must_exist_in_a_current_season(self) -> None:
        self.edit_catalog(lambda c: c.update(defaultFeedId="copa.2026-27"))
        self.assert_fails_with("defaultFeedId 'copa.2026-27'")

    def test_us064_c1_competition_needs_exactly_one_current_season(self) -> None:
        self.edit_catalog(lambda c: c["competitions"][0]["seasons"][0].update(current=False))
        self.assert_fails_with("exactamente una temporada current")

    def test_us064_c1_duplicated_feed_id_fails(self) -> None:
        self.edit_catalog(lambda c: c["competitions"][0]["seasons"][0]["feeds"].append(dict(self.first_feed(c))))
        self.assert_fails_with("feedId repetido: primera.2026-27")


class KnockoutRoundWithoutMatches(unittest.TestCase):
    def test_us064_c6_fixture_with_empty_knockout_round_is_in_the_contract(self) -> None:
        path = os.path.join(support.FIXTURES, "valid", "season-before-start.json")
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        knockout = [p for p in document["phases"] if p["kind"] == "knockout"]
        self.assertTrue(any(not r["matches"] for p in knockout for r in p["rounds"]))


class RealPublication(unittest.TestCase):
    def test_us064_c5_published_feed_has_a_coherent_catalog_and_mirror(self) -> None:
        self.assertEqual(catalog.check_catalog(os.path.join(support.FEED, "public", "v1")), [])


if __name__ == "__main__":
    unittest.main()
