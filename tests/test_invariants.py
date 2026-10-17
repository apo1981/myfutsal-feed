"""US-007a, criterio 4: invariantes que el esquema no expresa. Cada caso falla por separado."""
from __future__ import annotations

import json
import os
import random
import unittest
from unittest import mock
from typing import Any, Callable, Dict, Optional

import support  # debe ir antes que invariants: anade scripts/ al sys.path
import invariants
from support import SEASON_ID, TempDirTestCase, match_of, output_of, run_script, season_with, write_json


def load_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class InvariantsCliTestCase(TempDirTestCase):
    def check(self, season: Optional[Dict[str, Any]] = None, index: Optional[Dict[str, Any]] = None,
              previous: Optional[Dict[str, Any]] = None):
        public = support.build_public(self.tmp, season, index)
        args = ["--feed", self.tmp, "--public", public]
        if previous is not None:
            previous_path = os.path.join(self.tmp, "previous.json")
            write_json(previous_path, previous)
            args += ["--previous", previous_path]
        return support.run_main(invariants.main, args)

    def assert_fails(self, result, invariant: str, subject: str) -> None:
        self.assertNotEqual(result.returncode, 0, output_of(result))
        self.assertIn("invariante %s: %s" % (invariant, subject), result.stderr)

    def assert_fails_with(self, mutate: Callable[[Dict[str, Any]], None], invariant: str, subject: str) -> None:
        self.assert_fails(self.check(season_with(mutate)), invariant, subject)


class ValidFeed(InvariantsCliTestCase):
    def test_us007a_c4_valid_feed_passes(self) -> None:
        result = self.check()
        self.assertEqual(result.returncode, 0, output_of(result))
        self.assertIn("invariantes correctas", result.stdout)

    def test_us007a_c4_all_contract_valid_fixtures_pass(self) -> None:
        valid_dir = os.path.join(support.FIXTURES, "valid")
        for name in sorted(os.listdir(valid_dir)):
            if not name.startswith("season-"):
                continue
            season = load_json(os.path.join(valid_dir, name))
            index = {"schemaVersion": 1, "currentSeasonId": season["seasonId"],
                     "seasons": [{"id": season["seasonId"], "name": "x", "url": "seasons/%s.json" % season["seasonId"]}]}
            public = os.path.join(self.tmp, name)
            write_json(os.path.join(public, "public", "v1", "index.json"), index)
            write_json(os.path.join(public, "public", "v1", "seasons", season["seasonId"] + ".json"), season)
            result = support.run_main(invariants.main, ["--feed", self.tmp, "--public", os.path.join(public, "public")])
            self.assertEqual(result.returncode, 0, "%s: %s" % (name, output_of(result)))


class DuplicateIds(InvariantsCliTestCase):
    def test_us007a_c4_duplicate_match_id(self) -> None:
        self.assert_fails_with(lambda s: match_of(s, "m-02").update(id="m-01"), "id-partido-duplicado", "m-01")

    def test_us007a_c4_duplicate_team_id(self) -> None:
        def mutate(season: Dict[str, Any]) -> None:
            season["teams"][2]["id"] = "aaa"
            match_of(season, "m-02")["awayTeamId"] = "aaa"
        self.assert_fails_with(mutate, "id-equipo-duplicado", "aaa")

    def test_us007a_c4_duplicate_phase_id(self) -> None:
        def mutate(season: Dict[str, Any]) -> None:
            clone = {"id": "liga", "name": "Otra", "kind": "league", "order": 2, "rounds": []}
            season["phases"].append(clone)
        self.assert_fails_with(mutate, "id-fase-duplicado", "liga")

    def test_us007a_c4_duplicate_round_id(self) -> None:
        def mutate(season: Dict[str, Any]) -> None:
            season["phases"][0]["rounds"].append({"id": "liga-01", "number": 2, "name": "Jornada 2", "matches": []})
        self.assert_fails_with(mutate, "id-ronda-duplicado", "liga-01")


class KickoffCoherence(InvariantsCliTestCase):
    """S-7: kickoff.time no nulo exige kickoff.date igual a la fecha de Madrid de ese instante."""

    def kickoff(self, date: Optional[str], time: Optional[str]) -> Callable[[Dict[str, Any]], None]:
        return lambda season: match_of(season, "m-02").update(kickoff={"date": date, "time": time})

    def test_us007a_c4_time_with_null_date_fails(self) -> None:
        self.assert_fails(self.check(season_with(self.kickoff(None, "2026-10-31T18:00:00Z"))), "kickoff-fecha-incoherente", "m-02")

    def test_us007a_c4_date_differs_from_madrid_date_fails(self) -> None:
        self.assert_fails(self.check(season_with(self.kickoff("2026-10-30", "2026-10-31T18:00:00Z"))), "kickoff-fecha-incoherente", "m-02")

    def test_us007a_c4_cest_near_midnight_utc(self) -> None:
        # 22:30 UTC en horario de verano son las 00:30 del dia siguiente en Madrid.
        self.assertEqual(invariants.madrid_date("2026-06-30T22:30:00Z"), "2026-07-01")
        self.assertEqual(self.check(season_with(self.kickoff("2026-07-01", "2026-06-30T22:30:00Z"))).returncode, 0)
        self.assert_fails(self.check(season_with(self.kickoff("2026-06-30", "2026-06-30T22:30:00Z"))), "kickoff-fecha-incoherente", "m-02")

    def test_us007a_c4_cet_near_midnight_utc(self) -> None:
        # 22:30 UTC en horario de invierno son las 23:30 del mismo dia; 23:30 UTC ya son las 00:30 del siguiente.
        self.assertEqual(invariants.madrid_date("2026-12-31T22:30:00Z"), "2026-12-31")
        self.assertEqual(invariants.madrid_date("2026-12-31T23:30:00Z"), "2027-01-01")
        self.assertEqual(self.check(season_with(self.kickoff("2027-01-01", "2026-12-31T23:30:00Z"))).returncode, 0)
        self.assert_fails(self.check(season_with(self.kickoff("2026-12-31", "2026-12-31T23:30:00Z"))), "kickoff-fecha-incoherente", "m-02")

    def test_us007a_c4_regime_changes_at_01_utc_on_last_sunday(self) -> None:
        self.assertEqual(invariants.madrid_date("2026-03-28T22:30:00Z"), "2026-03-28")  # sabado, CET: 23:30
        self.assertEqual(invariants.madrid_date("2026-03-29T00:30:00Z"), "2026-03-29")  # CET: 01:30
        self.assertEqual(invariants.madrid_date("2026-03-29T01:00:00Z"), "2026-03-29")  # ya CEST: 03:00
        self.assertEqual(invariants.madrid_date("2026-10-24T22:30:00Z"), "2026-10-25")  # CEST: 00:30
        self.assertEqual(invariants.madrid_date("2026-10-25T00:30:00Z"), "2026-10-25")  # CEST: 02:30
        self.assertEqual(invariants.madrid_date("2026-10-25T22:59:59Z"), "2026-10-25")  # CET: 23:59:59
        self.assertEqual(invariants.madrid_date("2026-10-25T23:00:00Z"), "2026-10-26")  # CET: 00:00

    def test_us007a_c4_madrid_date_matches_the_system_timezone_database_when_available(self) -> None:
        try:
            from zoneinfo import ZoneInfo
            zone = ZoneInfo("Europe/Madrid")
        except Exception:  # noqa: BLE001 - sin zoneinfo o sin base de datos: la prueba no aplica
            self.skipTest("zoneinfo o la base de datos de zonas no estan disponibles")
        import datetime
        generator = random.Random(7)
        start = datetime.datetime(2025, 1, 1)
        for _ in range(2000):
            instant = start + datetime.timedelta(minutes=generator.randrange(0, 60 * 24 * 365 * 3))
            local = instant.replace(tzinfo=datetime.timezone.utc).astimezone(zone)
            self.assertEqual(invariants.madrid_date(instant.strftime("%Y-%m-%dT%H:%M:%SZ")), local.strftime("%Y-%m-%d"), instant)

    def test_us007a_c4_null_time_with_or_without_date_passes(self) -> None:
        self.assertEqual(self.check(season_with(self.kickoff("2026-10-10", None))).returncode, 0)
        self.assertEqual(self.check(season_with(self.kickoff(None, None))).returncode, 0)


class MatchRules(InvariantsCliTestCase):
    def test_us007a_c4_unknown_team(self) -> None:
        self.assert_fails_with(lambda s: match_of(s, "m-02").update(homeTeamId="zzz"), "equipo-inexistente", "m-02")
        self.assert_fails_with(lambda s: match_of(s, "m-02").update(awayTeamId="zzz"), "equipo-inexistente", "m-02")

    def test_us007a_c4_home_equals_away(self) -> None:
        self.assert_fails_with(lambda s: match_of(s, "m-02").update(awayTeamId="bbb"), "local-igual-visitante", "m-02")

    def test_us007a_c4_score_with_scheduled_status(self) -> None:
        self.assert_fails_with(lambda s: match_of(s, "m-02").update(score={"home": 0, "away": 0}), "score-con-estado-invalido", "m-02")

    def test_us007a_c4_score_with_postponed_status(self) -> None:
        self.assert_fails_with(lambda s: match_of(s, "m-01").update(status="postponed"), "score-con-estado-invalido", "m-01")

    def test_us007a_c4_live_with_score_passes(self) -> None:
        result = self.check(season_with(lambda s: match_of(s, "m-02").update(status="live", score={"home": 1, "away": 0})))
        self.assertEqual(result.returncode, 0, output_of(result))

    def test_us007a_c4_finished_without_score(self) -> None:
        self.assert_fails_with(lambda s: match_of(s, "m-01").update(score=None), "score-ausente-en-finished", "m-01")

    def test_us007a_c4_crest_url_not_null(self) -> None:
        self.assert_fails_with(lambda s: s["teams"][0].update(crestURL="https://example.org/a.png"), "crest-url-no-nulo", "aaa")


class IndexAndSize(InvariantsCliTestCase):
    def test_us007a_c4_compressed_size_over_100_kb(self) -> None:
        generator = random.Random(1)
        noise = "".join(generator.choice("0123456789abcdef") for _ in range(260_000))
        result = self.check(season_with(lambda s: match_of(s, "m-02").update(note=noise)))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invariante tamano-comprimido: %s.json" % SEASON_ID, result.stderr)

    def test_us007a_c4_compressed_size_counts_gzip_not_raw_size(self) -> None:
        # 300 KB sin comprimir pero muy repetitivo: comprime muy por debajo de 100 KB.
        result = self.check(season_with(lambda s: match_of(s, "m-02").update(note="a" * 300_000)))
        self.assertEqual(result.returncode, 0, output_of(result))

    def test_us007a_c4_current_season_missing_from_seasons(self) -> None:
        index = dict(support.BASE_INDEX, currentSeasonId="2099-00")
        self.assert_fails(self.check(index=index), "temporada-vigente-inexistente", "2099-00")

    def test_us007a_c4_season_url_points_to_missing_file(self) -> None:
        index = dict(support.BASE_INDEX, seasons=[{"id": SEASON_ID, "name": "x", "url": "seasons/2030-31.json"}])
        self.assert_fails(self.check(index=index), "url-de-temporada-inexistente", SEASON_ID)

    def test_us007a_c4_url_that_is_not_seasons_id_json_fails_even_if_the_file_exists(self) -> None:
        # I-3: sin esta invariante, `updatedAt` se comprobaria contra otro fichero (o no se comprobaria).
        public = support.build_public(self.tmp, index=dict(
            support.BASE_INDEX, currentSeasonId="temporada",
            seasons=[{"id": "temporada", "name": "x", "url": "seasons/%s.json" % SEASON_ID}]))
        result = support.run_main(invariants.main, ["--feed", self.tmp, "--public", public])
        self.assert_fails(result, "indice-incoherente", "temporada")
        self.assertIn("seasons/temporada.json", result.stderr)

    def test_us007a_c4_season_id_of_the_file_must_be_the_index_id(self) -> None:
        result = self.check(season_with(lambda s: s.update(seasonId="2030-31")))
        self.assert_fails(result, "indice-incoherente", SEASON_ID)
        self.assertIn("2030-31", result.stderr)

    def test_us007a_c4_current_season_without_a_file_fails_instead_of_skipping_updated_at(self) -> None:
        index = dict(support.BASE_INDEX, currentSeasonId="2030-31",
                     seasons=[{"id": "2030-31", "name": "x", "url": "seasons/2030-31.json"}])
        result = self.check(index=index)
        self.assert_fails(result, "temporada-vigente-sin-fichero", "2030-31")

    def test_us007a_c4_season_url_escaping_v1_fails(self) -> None:
        index = dict(support.BASE_INDEX, seasons=[{"id": SEASON_ID, "name": "x", "url": "../index.json"}])
        self.assert_fails(self.check(index=index), "url-de-temporada-inexistente", SEASON_ID)


class UpdatedAt(InvariantsCliTestCase):
    def changed(self, season: Dict[str, Any]) -> None:
        match_of(season, "m-02").update(status="postponed")

    def test_us007a_c4_unchanged_content_with_different_updated_at_passes(self) -> None:
        current = season_with(lambda s: s.update(updatedAt="2026-10-02T10:00:00Z"))
        result = self.check(current, previous=support.BASE_SEASON)
        self.assertEqual(result.returncode, 0, output_of(result))

    def test_us007a_c4_changed_content_with_same_updated_at_fails(self) -> None:
        result = self.check(season_with(self.changed), previous=support.BASE_SEASON)
        self.assert_fails(result, "updated-at-sin-avanzar", SEASON_ID)

    def test_us007a_c4_changed_content_with_older_updated_at_fails(self) -> None:
        current = season_with(lambda s: (self.changed(s), s.update(updatedAt="2026-09-30T10:00:00Z")))
        self.assert_fails(self.check(current, previous=support.BASE_SEASON), "updated-at-sin-avanzar", SEASON_ID)

    def test_us007a_c4_changed_content_with_later_updated_at_passes(self) -> None:
        current = season_with(lambda s: (self.changed(s), s.update(updatedAt="2026-10-02T10:00:00Z")))
        result = self.check(current, previous=support.BASE_SEASON)
        self.assertEqual(result.returncode, 0, output_of(result))

    def test_us007a_c4_key_order_alone_is_not_a_content_change(self) -> None:
        reordered = dict(reversed(list(support.BASE_SEASON.items())))
        result = self.check(reordered, previous=support.BASE_SEASON)
        self.assertEqual(result.returncode, 0, output_of(result))

    def check_with_previous_text(self, text: str, season: Optional[Dict[str, Any]] = None):
        public = support.build_public(self.tmp, season)
        previous_path = os.path.join(self.tmp, "previous.json")
        with open(previous_path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return support.run_main(invariants.main, ["--feed", self.tmp, "--public", public, "--previous", previous_path])

    def test_us007a_c4_unreadable_previous_version_is_skipped_with_a_notice(self) -> None:
        # B-1: la version anterior es de un commit que no paso la puerta; la correccion debe poder publicarse.
        truncated = json.dumps(support.BASE_SEASON)[:-30]
        result = self.check_with_previous_text(truncated, season_with(lambda s: match_of(s, "m-02").update(status="postponed")))
        self.assertEqual(result.returncode, 0, output_of(result))
        self.assertIn("version anterior ilegible", result.stdout)
        self.assertIn("se omite la invariante", result.stdout)

    def test_us007a_c4_previous_version_without_a_valid_updated_at_is_skipped_with_a_notice(self) -> None:
        for text in ("[]", "{}", json.dumps(dict(support.BASE_SEASON, updatedAt="ayer")), ""):
            with self.subTest(text[:20]):
                result = self.check_with_previous_text(text)
                self.assertEqual(result.returncode, 0, output_of(result))
                self.assertIn("version anterior ilegible", result.stdout)

    def test_us007a_c4_unreadable_previous_does_not_hide_other_violations(self) -> None:
        result = self.check_with_previous_text("{", season_with(lambda s: match_of(s, "m-02").update(awayTeamId="bbb")))
        self.assert_fails(result, "local-igual-visitante", "m-02")

    def test_us007a2_c9_unreadable_previous_does_not_also_claim_a_first_publication(self) -> None:
        # S-12: JSON truncado y updatedAt que no es un instante; ambos avisan y ninguno dice «primera publicacion».
        valid_json_bad_instant = json.dumps(dict(support.BASE_SEASON, updatedAt="ayer"))
        for text in (json.dumps(support.BASE_SEASON)[:-30], valid_json_bad_instant):
            with self.subTest(text[:20]):
                result = self.check_with_previous_text(text)
                self.assertEqual(result.returncode, 0, output_of(result))
                self.assertIn("version anterior ilegible", result.stdout)
                self.assertIn("se omite la invariante", result.stdout)
                self.assertNotIn("primera publicacion", result.stdout)

    def test_us007a2_c9_a_readable_previous_never_reports_a_first_publication(self) -> None:
        result = self.check(previous=support.BASE_SEASON)
        self.assertEqual(result.returncode, 0, output_of(result))
        self.assertNotIn("primera publicacion", result.stdout)

    def test_us007a_c4_without_previous_reports_first_publication(self) -> None:
        result = self.check()
        self.assertEqual(result.returncode, 0, output_of(result))
        self.assertIn("primera publicacion", result.stdout)


class ExampleIds(InvariantsCliTestCase):
    """US-007b2a2, 7d-1 (S-3): ningun `id` de `examples/initial-load/equipos.csv` puede estar en `public/`.

    Es una invariante (no de `check-structure`) para que la aplique la puerta de CI (B-1 de la revision).
    """

    def with_team(self, identifier: str) -> Dict[str, Any]:
        return season_with(lambda s: s["teams"].append({"id": identifier, "name": "x", "shortName": "XXX", "crestURL": None}))

    def test_us007b2a2_7d1_example_ids_are_read_from_the_csv(self) -> None:
        self.assertEqual(invariants.example_team_ids(), ["norte", "sur", "este", "oeste"])

    def test_us007b2a2_7d1_public_without_example_ids_passes(self) -> None:
        result = self.check(season_with())
        self.assertEqual(result.returncode, 0, output_of(result))

    def test_us007b2a2_7d1_each_example_id_in_public_fails_and_is_named(self) -> None:
        for identifier in invariants.example_team_ids():
            with self.subTest(identifier):
                self.assert_fails(self.check(self.with_team(identifier)), "id-de-ejemplo", identifier)

    def test_us007b2a2_7d1_the_script_used_by_the_gate_fails_on_an_example_team(self) -> None:
        public = support.build_public(self.tmp, self.with_team("norte"))
        result = run_script([os.path.join(support.SCRIPTS, "check-invariants"), "--public", public])
        self.assertEqual(result.returncode, 1, output_of(result))
        self.assertIn("invariante id-de-ejemplo: norte", result.stderr)

    def test_us007b2a2_7d1_ids_of_phases_rounds_and_matches_are_not_checked(self) -> None:
        season = season_with(lambda s: s["phases"][0].update(id="norte"))
        self.assertEqual(self.check(season).returncode, 0)

    def test_us007b2a2_7d1_missing_examples_file_fails_in_the_gate(self) -> None:
        with mock.patch.object(invariants, "FEED_ROOT", self.tmp):
            result = self.check(season_with())
        self.assert_fails(result, "id-de-ejemplo", invariants.EXAMPLE_TEAMS)

    def test_us007b2a2_7d1_broken_season_json_is_a_named_failure_not_an_exception(self) -> None:
        public = support.build_public(self.tmp)
        with open(os.path.join(public, "v1", "seasons", SEASON_ID + ".json"), "w", encoding="utf-8") as handle:
            handle.write("{no es json")
        result = support.run_main(invariants.main, ["--feed", self.tmp, "--public", public])
        self.assertEqual(result.returncode, 1, output_of(result))
        self.assertIn("FALLO invariantes: no se pudo leer el feed", result.stderr)


class MalformedInput(InvariantsCliTestCase):
    def test_us007a_c4_unreadable_feed_fails_without_traceback(self) -> None:
        result = support.run_main(invariants.main, ["--feed", self.tmp, "--public", os.path.join(self.tmp, "no-existe")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FALLO invariantes", result.stderr)

    def test_us007a_c4_entry_point_runs_the_checks_and_returns_the_exit_code(self) -> None:
        public = support.build_public(self.tmp, season_with(lambda s: match_of(s, "m-02").update(awayTeamId="bbb")))
        result = run_script([os.path.join(support.SCRIPTS, "check-invariants"), "--public", public])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invariante local-igual-visitante: m-02", result.stderr)
        valid = run_script([os.path.join(support.SCRIPTS, "check-invariants")])
        self.assertEqual(valid.returncode, 0, output_of(valid))


if __name__ == "__main__":
    unittest.main()
