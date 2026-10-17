"""US-007b2a1, criterio 5: `check-initial-load --cutoff`. Fixtures propios; no dependen de `public/`.

Cada comprobacion tiene un caso que pasa y otro que falla.
"""
from __future__ import annotations

import copy
import datetime
import os
import random
import unittest
from typing import Any, Callable, Dict, List

import support  # debe ir antes que initial_load: anade scripts/ al sys.path
import initial_load
from support import TempDirTestCase, output_of, run_script, write_json

CUTOFF = "2026-10-20"
FIRST_DAY = datetime.date(2026, 10, 3)
DAYS_BETWEEN_ROUNDS = 10
TEAM_COUNT = 4
RANDOM_BYTES = 200000


def round_robin(team_ids: List[str]) -> List[List[Any]]:
    """Calendario por el metodo del circulo: N-1 jornadas de N/2 parejas."""
    rotating = list(team_ids)
    rounds = []
    for _ in range(len(team_ids) - 1):
        half = len(rotating) // 2
        rounds.append(list(zip(rotating[:half], reversed(rotating[half:]))))
        rotating = [rotating[0]] + [rotating[-1]] + rotating[1:-1]
    return rounds


def league_phase(phase_id: str, team_ids: List[str], first_day: datetime.date) -> Dict[str, Any]:
    rounds = []
    for number, pairs in enumerate(round_robin(team_ids), start=1):
        date = (first_day + datetime.timedelta(days=DAYS_BETWEEN_ROUNDS * (number - 1))).isoformat()
        rounds.append({"id": "%s-%02d" % (phase_id, number), "number": number, "name": "Jornada %d" % number, "matches": [
            {"id": "%s-%02d-%d" % (phase_id, number, index), "homeTeamId": home, "awayTeamId": away,
             "status": "finished" if date < CUTOFF else "scheduled",
             "kickoff": {"date": date, "time": None},
             "score": {"home": 1, "away": 0} if date < CUTOFF else None, "decidedBy": None, "note": None}
            for index, (home, away) in enumerate(pairs, start=1)]})
    return {"id": phase_id, "name": phase_id, "kind": "league", "order": 1, "rounds": rounds}


def full_season(count: int = TEAM_COUNT) -> Dict[str, Any]:
    ids = ["eq%02d" % number for number in range(count)]
    season = copy.deepcopy(support.BASE_SEASON)
    season["teams"] = [{"id": i, "name": "Equipo " + i, "shortName": i.upper(), "crestURL": None} for i in ids]
    season["phases"] = [league_phase("liga", ids, FIRST_DAY)]
    return season


def all_matches(season: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [m for p in season["phases"] for r in p["rounds"] for m in r["matches"]]


def names(findings: List[initial_load.Finding]) -> List[str]:
    return sorted(f.name for f in findings)


def mutated(mutate: Callable[[Dict[str, Any]], None], count: int = TEAM_COUNT) -> Dict[str, Any]:
    season = full_season(count)
    mutate(season)
    return season


class Placeholder(unittest.TestCase):
    def test_us007b2a1_c5_complete_season_has_no_placeholder(self) -> None:
        self.assertEqual(initial_load.placeholder_findings(full_season()), [])

    def test_us007b2a1_c5_pendiente_team_fails(self) -> None:
        season = mutated(lambda s: s["teams"].append({"id": "pendiente", "name": "x", "shortName": "TBD", "crestURL": None}))
        self.assertEqual(names(initial_load.placeholder_findings(season)), ["id-pendiente"])

    def test_us007b2a1_c5_pendiente_phase_fails(self) -> None:
        season = mutated(lambda s: s["phases"].append({"id": "pendiente", "name": "x", "kind": "league", "order": 2, "rounds": []}))
        self.assertEqual(names(initial_load.placeholder_findings(season)), ["id-pendiente"])


class Knockout(unittest.TestCase):
    def test_us007b2a1_c5_league_only_passes(self) -> None:
        self.assertEqual(initial_load.knockout_findings(full_season()), [])

    def test_us007b2a1_c5_knockout_phase_fails(self) -> None:
        season = mutated(lambda s: s["phases"].append({"id": "playoff", "name": "Playoff", "kind": "knockout", "order": 2, "rounds": []}))
        self.assertEqual(names(initial_load.knockout_findings(season)), ["fase-knockout"])


class LeagueCalendar(unittest.TestCase):
    def test_us007b2a1_c5_complete_calendar_passes_for_any_team_count(self) -> None:
        for count in (2, 4, 6, 16):
            self.assertEqual(initial_load.calendar_findings(full_season(count)), [], "equipos: %d" % count)

    def test_us007b2a1_c5_two_league_phases_pass(self) -> None:
        def add_second(season: Dict[str, Any]) -> None:
            second = league_phase("clausura", [t["id"] for t in season["teams"]], FIRST_DAY + datetime.timedelta(days=60))
            second["order"] = 2
            season["phases"].append(second)
        self.assertEqual(initial_load.calendar_findings(mutated(add_second)), [])

    def test_us007b2a1_c5_missing_round_fails(self) -> None:
        season = mutated(lambda s: s["phases"][0]["rounds"].pop())
        self.assertIn("jornadas-incompletas", names(initial_load.calendar_findings(season)))

    def test_us007b2a1_c5_round_with_a_missing_match_fails(self) -> None:
        season = mutated(lambda s: s["phases"][0]["rounds"][0]["matches"].pop())
        found = names(initial_load.calendar_findings(season))
        self.assertIn("jornada-incompleta", found)
        self.assertIn("pareja-no-una-vez", found)

    def test_us007b2a1_c5_team_playing_twice_in_a_round_fails(self) -> None:
        def clash(season: Dict[str, Any]) -> None:
            first, second = season["phases"][0]["rounds"][0]["matches"]
            second["homeTeamId"], second["awayTeamId"] = first["homeTeamId"], "eq03" if first["awayTeamId"] != "eq03" else "eq02"
        found = names(initial_load.calendar_findings(mutated(clash)))
        self.assertIn("equipo-no-juega-una-vez", found)

    def test_us007b2a1_c5_repeated_pair_fails(self) -> None:
        def repeat(season: Dict[str, Any]) -> None:
            rounds = season["phases"][0]["rounds"]
            rounds[1]["matches"] = copy.deepcopy(rounds[0]["matches"])
            for index, match in enumerate(rounds[1]["matches"]):
                match["id"] = "dup-%d" % index
        found = initial_load.calendar_findings(mutated(repeat))
        self.assertIn("pareja-no-una-vez", names(found))

    def test_us007b2a1_c5_odd_team_count_fails(self) -> None:
        self.assertEqual(names(initial_load.calendar_findings(full_season(3))), ["equipos-impares"])


class Cutoff(unittest.TestCase):
    cutoff = datetime.date(2026, 10, 20)

    def test_us007b2a1_c5_consistent_statuses_pass(self) -> None:
        self.assertEqual(initial_load.cutoff_findings(full_season(), self.cutoff), [])

    def test_us007b2a1_c5_scheduled_before_cutoff_fails(self) -> None:
        season = mutated(lambda s: all_matches(s)[0].update(status="scheduled", score=None))
        self.assertEqual(names(initial_load.cutoff_findings(season, self.cutoff)), ["pasado-sin-resultado"])

    def test_us007b2a1_c5_live_before_cutoff_fails(self) -> None:
        season = mutated(lambda s: all_matches(s)[0].update(status="live"))
        self.assertEqual(names(initial_load.cutoff_findings(season, self.cutoff)), ["pasado-sin-resultado"])

    def test_us007b2a1_c5_finished_after_cutoff_fails(self) -> None:
        season = mutated(lambda s: all_matches(s)[-1].update(status="finished", score={"home": 1, "away": 1}))
        self.assertEqual(names(initial_load.cutoff_findings(season, self.cutoff)), ["finalizado-tras-el-corte"])

    def test_us007b2a1_c5_unknown_postponed_and_undated_before_cutoff_pass(self) -> None:
        def relax(season: Dict[str, Any]) -> None:
            matches = all_matches(season)
            matches[0].update(status="unknown", score=None)
            matches[1].update(status="postponed", score=None)
            matches[2].update(status="scheduled", score=None, kickoff={"date": None, "time": None})
        self.assertEqual(initial_load.cutoff_findings(mutated(relax), self.cutoff), [])

    def test_us007b2a1_c5_match_on_the_cutoff_day_may_be_either(self) -> None:
        for status, score in (("scheduled", None), ("finished", {"home": 2, "away": 2})):
            season = mutated(lambda s: all_matches(s)[0].update(status=status, score=score, kickoff={"date": CUTOFF, "time": None}))
            self.assertEqual(initial_load.cutoff_findings(season, self.cutoff), [], status)

    def test_us007b2a1_c5_unknown_matches_are_listed(self) -> None:
        season = mutated(lambda s: all_matches(s)[0].update(status="unknown", score=None))
        self.assertEqual(initial_load.unknown_matches(season), ["liga-01-1 (2026-10-03)"])
        self.assertEqual(initial_load.unknown_matches(full_season()), [])


class CommandLine(TempDirTestCase):
    def check(self, season: Dict[str, Any], cutoff: str = CUTOFF):
        public = support.build_public(self.tmp, season)
        return run_script([os.path.join(support.FEED, "scripts", "check-initial-load"), "--cutoff", cutoff, "--public", public])

    def test_us007b2a1_c5_valid_load_passes_and_lists_unknown(self) -> None:
        season = mutated(lambda s: all_matches(s)[0].update(status="unknown", score=None))
        result = self.check(season)
        self.assertEqual(result.returncode, 0, output_of(result))
        self.assertIn("carga inicial correcta", result.stdout)
        self.assertIn("partidos unknown para el runbook: 1", result.stdout)
        self.assertIn("liga-01-1", result.stdout)

    def test_us007b2a1_c5_structure_failure_names_the_check(self) -> None:
        result = self.check(mutated(lambda s: s["phases"][0]["rounds"].pop()))
        self.assertEqual(result.returncode, 1, output_of(result))
        self.assertIn("FALLO carga-inicial jornadas-incompletas: liga", result.stderr)

    def test_us007b2a1_c5_cutoff_failure_names_the_match(self) -> None:
        result = self.check(full_season(), cutoff="2026-11-30")
        self.assertEqual(result.returncode, 1, output_of(result))
        self.assertIn("FALLO carga-inicial pasado-sin-resultado: liga-0", result.stderr)

    def test_us007b2a1_c5_us007a_schema_validation_is_applied(self) -> None:
        result = self.check(mutated(lambda s: s["phases"][0].update(kind="legue")))
        self.assertEqual(result.returncode, 1, output_of(result))
        self.assertIn("FALLO carga-inicial esquema", result.stderr)

    def test_us007b2a1_c5_us007a_invariants_are_applied(self) -> None:
        result = self.check(mutated(lambda s: s["teams"][0].update(crestURL="https://example.invalid/x.png")))
        self.assertEqual(result.returncode, 1, output_of(result))
        self.assertIn("invariante crest-url-no-nulo: eq00", result.stderr)

    def test_us007b2a1_c5_compressed_size_over_100_kb_fails(self) -> None:
        noise = random.Random(7).getrandbits(8 * RANDOM_BYTES).to_bytes(RANDOM_BYTES, "big").hex()
        result = self.check(mutated(lambda s: all_matches(s)[0].update(note=noise)))
        self.assertEqual(result.returncode, 1, output_of(result))
        self.assertIn("invariante tamano-comprimido", result.stderr)

    def test_us007b2a1_c5_cutoff_is_required_and_validated(self) -> None:
        script = os.path.join(support.FEED, "scripts", "check-initial-load")
        self.assertNotEqual(run_script([script]).returncode, 0)
        bad = run_script([script, "--cutoff", "20-10-2026"])
        self.assertEqual(bad.returncode, 2, output_of(bad))
        self.assertIn("AAAA-MM-DD", bad.stderr)

    def test_us007b2a1_c5_unreadable_feed_fails_cleanly(self) -> None:
        result = run_script([os.path.join(support.FEED, "scripts", "check-initial-load"), "--cutoff", CUTOFF, "--public", self.tmp])
        self.assertEqual(result.returncode, 1, output_of(result))
        self.assertIn("no se pudo leer el feed", result.stderr)


if __name__ == "__main__":
    unittest.main()
