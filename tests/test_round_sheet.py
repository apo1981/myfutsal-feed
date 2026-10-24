"""US-007b2c, criterios 7 y 8: hoja de jornada, pendientes y aplicador en bloque. Fixtures propios, sin red."""
from __future__ import annotations

import copy
import datetime
import json
import os
from typing import Any, Dict, List

import support  # debe ir antes que round_sheet: anade scripts/ al sys.path
import apply_results
import round_sheet
from support import TempDirTestCase, build_public, run_main

TODAY = datetime.date(2026, 10, 24)
OLD_UPDATED_AT = "2026-10-01T10:00:00Z"
NEW_UPDATED_AT = "2026-10-24T09:30:00Z"


def match(identifier: str, home: str, away: str, status: str, date: str, time: Any = None, score: Any = None) -> Dict[str, Any]:
    return {"id": identifier, "homeTeamId": home, "awayTeamId": away, "status": status,
            "kickoff": {"date": date, "time": time}, "score": score, "decidedBy": None, "note": None}


def season() -> Dict[str, Any]:
    doc = copy.deepcopy(support.BASE_SEASON)
    doc["updatedAt"] = OLD_UPDATED_AT
    doc["phases"] = [
        {"id": "liga", "name": "Liga", "kind": "league", "order": 1, "rounds": [
            {"id": "liga-01", "number": 1, "name": "Jornada 1", "matches": [
                match("m-01", "aaa", "bbb", "unknown", "2026-10-03"),
                # 14:00Z en octubre es CEST (UTC+2): 16:00 en Madrid.
                match("m-02", "ccc", "aaa", "finished", "2026-10-04", "2026-10-04T14:00:00Z", {"home": 3, "away": 1}),
            ]},
            {"id": "liga-02", "number": 2, "name": "Jornada 2", "matches": [
                match("m-03", "bbb", "ccc", "scheduled", "2026-10-10"),
                match("m-04", "aaa", "ccc", "scheduled", "2026-10-25"),
            ]},
        ]},
        {"id": "playoff", "name": "Playoff", "kind": "knockout", "order": 2, "rounds": [
            {"id": "po-01", "number": 1, "name": "Cuartos", "matches": [match("p-01", "aaa", "bbb", "unknown", "2026-10-05")]},
        ]},
    ]
    return doc


class SheetCase(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.feed = os.path.join(self.tmp, "feed")
        self.public = build_public(self.feed, season())
        self.season_path = os.path.join(self.public, "v1", "seasons", "%s.json" % support.SEASON_ID)

    def run_sheet(self, *args: str) -> Any:
        return run_main(round_sheet.main, ["--feed", self.feed] + list(args))

    def stored(self) -> Dict[str, Any]:
        with open(self.season_path, encoding="utf-8") as handle:
            return json.load(handle)


class RoundSheet(SheetCase):
    def test_us007b2c_c7_sheet_shows_teams_status_score_and_madrid_time(self) -> None:
        result = self.run_sheet("--round", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [" ".join(line.split()) for line in result.stdout.splitlines()]
        self.assertIn("m-01 2026-10-03 sin hora Equipo A - Equipo B [unknown] pendiente", lines)
        self.assertIn("m-02 2026-10-04 16:00 Equipo C - Equipo A [finished] 3-1", lines)

    def test_us007b2c_c7_winter_time_uses_utc_plus_one(self) -> None:
        self.assertEqual(round_sheet.kickoff_label({"date": "2026-12-05", "time": "2026-12-05T14:00:00Z"}), "2026-12-05 15:00")

    def test_us007b2c_c7_a_round_number_shared_by_two_phases_prints_both_unless_phase_is_given(self) -> None:
        both = self.run_sheet("--round", "1").stdout
        self.assertIn("Liga - Jornada 1", both)
        self.assertIn("Playoff - Cuartos", both)
        only = self.run_sheet("--round", "1", "--phase", "playoff").stdout
        self.assertNotIn("Liga - Jornada 1", only)

    def test_us007b2c_c7_missing_round_fails_and_says_which(self) -> None:
        result = self.run_sheet("--round", "9")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no hay jornada 9", result.stderr)

    def test_us007b2c_c7_needs_exactly_one_mode(self) -> None:
        self.assertEqual(self.run_sheet().returncode, 2)
        self.assertEqual(self.run_sheet("--round", "1", "--pending").returncode, 2)
        self.assertEqual(self.run_sheet("--round", "1", "--csv").returncode, 2)


class Pending(SheetCase):
    def test_us007b2c_c8_pending_lists_unknown_and_past_scheduled_grouped_by_round(self) -> None:
        out = self.run_sheet("--pending", "--today", TODAY.isoformat()).stdout
        self.assertIn("3 partidos", out)
        for identifier in ("m-01", "m-03", "p-01"):
            self.assertIn(identifier, out)
        self.assertNotIn("m-02", out)  # finished
        self.assertNotIn("m-04", out)  # scheduled y futuro
        self.assertLess(out.index("Liga - Jornada 1"), out.index("Liga - Jornada 2"))

    def test_us007b2c_c8_scheduled_on_the_same_day_is_not_pending_yet(self) -> None:
        out = self.run_sheet("--pending", "--today", "2026-10-10").stdout
        self.assertNotIn("m-03", out)

    def test_us007b2c_c8_csv_template_has_empty_scores_and_context(self) -> None:
        out = self.run_sheet("--pending", "--csv", "--today", TODAY.isoformat()).stdout.splitlines()
        self.assertEqual(out[0], "id,home,away,jornada,fecha,local,visitante")
        self.assertEqual(out[1], "m-01,,,1,2026-10-03,Equipo A,Equipo B")
        self.assertEqual(len(out), 4)


class ApplyResults(SheetCase):
    def write_csv(self, text: str) -> str:
        path = os.path.join(self.tmp, "resultados.csv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def apply(self, text: str) -> Any:
        return run_main(apply_results.main, ["--feed", self.feed, self.write_csv(text), "--updated-at", NEW_UPDATED_AT])

    def assert_untouched(self) -> None:
        self.assertEqual(self.stored(), season())

    def test_us007b2c_c8_applies_finished_score_and_new_updated_at(self) -> None:
        result = self.apply("id,home,away\nm-01,4,2\nm-03,0,0\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        stored = self.stored()
        first, third = stored["phases"][0]["rounds"][0]["matches"][0], stored["phases"][0]["rounds"][1]["matches"][0]
        self.assertEqual((first["status"], first["score"]), ("finished", {"home": 4, "away": 2}))
        self.assertEqual((third["status"], third["score"]), ("finished", {"home": 0, "away": 0}))
        self.assertEqual(stored["updatedAt"], NEW_UPDATED_AT)
        self.assertEqual(stored["phases"][0]["rounds"][1]["matches"][1]["status"], "scheduled")

    def test_us007b2c_c8_empty_score_leaves_the_match_as_it_is_and_never_invents_a_result(self) -> None:
        result = self.apply("id,home,away,local\nm-01,,,Equipo A\nm-03,,,Equipo B\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Sin cambios", result.stdout)
        self.assert_untouched()

    def test_us007b2c_c8_extra_columns_are_ignored(self) -> None:
        self.assertEqual(self.apply("id,home,away,jornada,fecha,local,visitante\nm-01,1,0,1,2026-10-03,Equipo A,Equipo B\n").returncode, 0)

    def test_us007b2c_c8_an_invalid_row_writes_nothing_even_if_other_rows_are_valid(self) -> None:
        cases = {
            "un solo gol": "id,home,away\nm-01,2,2\nm-03,1,\n",
            "no numerico": "id,home,away\nm-01,2,2\nm-03,x,1\n",
            "negativo": "id,home,away\nm-01,2,2\nm-03,-1,1\n",
            "id desconocido": "id,home,away\nm-01,2,2\nm-99,1,1\n",
            "repetido": "id,home,away\nm-01,2,2\nm-01,1,1\n",
            "ya finalizado": "id,home,away\nm-01,2,2\nm-02,1,1\n",
            "eliminatoria": "id,home,away\nm-01,2,2\np-01,1,0\n",
            "faltan columnas": "id,home\nm-01,2\n",
        }
        for name, text in cases.items():
            with self.subTest(name):
                result = self.apply(text)
                self.assertEqual(result.returncode, 1)
                self.assertIn("No se ha escrito nada", result.stderr)
                self.assert_untouched()

    def test_us007b2c_c8_updated_at_not_after_the_current_one_fails(self) -> None:
        result = run_main(apply_results.main, ["--feed", self.feed, self.write_csv("id,home,away\nm-01,1,1\n"), "--updated-at", OLD_UPDATED_AT])
        self.assertEqual(result.returncode, 1)
        self.assertIn("no es posterior", result.stderr)
        self.assert_untouched()

    def test_us007b2c_c8_the_applied_feed_keeps_the_exact_file_format(self) -> None:
        self.apply("id,home,away\nm-01,1,0\n")
        with open(self.season_path, encoding="utf-8") as handle:
            raw = handle.read()
        self.assertEqual(raw, json.dumps(json.loads(raw), indent=2, ensure_ascii=False))
