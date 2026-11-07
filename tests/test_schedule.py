"""US-042: horario automatico (fecha y hora) desde la RFEF en auto-apply-results y apply-schedule. Sintetico, sin red."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import support  # debe ir antes que los modulos de scripts: anade scripts/ al sys.path
import apply_schedule as schedule_tool
import fetch_rfef_results as fetch
import test_fetch_rfef as base
from test_auto_apply import AutoApplyTestCase, SEASON_FILE
from test_fetch_rfef import rfef_doc, rfef_match

FUTURE_DAY = "2026-10-25"  # posterior a TODAY (2026-10-20)


def pending(index: int, hour: Optional[str] = "20:30", **overrides: Any) -> Dict[str, Any]:
    """Partido 'Sin comenzar'; hour None = por confirmar (no_hour 1)."""
    if hour is None:
        overrides.update(no_hour="1", hour="")
    else:
        overrides["day_hour"] = hour
    return rfef_match(index, None, "Sin comenzar", **overrides)


def all_pending(**changes: Any) -> List[Dict[str, Any]]:
    return [pending(i, **changes) for i in range(1, 9)]


class ScheduleTestCase(AutoApplyTestCase):
    def run_pending(self, matches: List[Dict[str, Any]], day: str = "2026-10-17", doc: Optional[Dict[str, Any]] = None) -> Any:
        body = json.dumps(doc or rfef_doc(matches, day)).encode("utf-8")
        return self.launch([(200, body), (200, body)])

    def season_matches(self) -> Dict[str, Dict[str, Any]]:
        season = json.loads(self.season_bytes())
        return {m["id"]: m for m in season["phases"][0]["rounds"][0]["matches"]}

    def assert_nothing_applied(self) -> None:
        """Como assert_untouched, pero tolera que la anomalia haya desactivado el interruptor (2 seguidas) y lo restaura."""
        self.assertEqual(self.season_bytes(), self.original)
        self.assertEqual(self.commits(), 1)
        self.git("checkout", "--", "scripts/rfef/config.json")

    def commit_feed(self, matches: List[Dict[str, Any]]) -> None:
        self.write_feed(matches)
        self.git("commit", "-q", "-am", "feed")
        self.original = self.season_bytes()


class ConfirmedAndUnconfirmedTests(ScheduleTestCase):
    def test_US042_confirmed_hour_is_applied_as_madrid_time_in_utc_in_one_commit(self) -> None:
        result, transport, _ = self.run_pending(all_pending())
        self.assertEqual(result.returncode, 0, result.stderr)
        matches = self.season_matches()
        self.assertEqual(matches["a01-04"]["kickoff"], {"date": "2026-10-17", "time": "2026-10-17T18:30:00Z"})  # CEST, UTC+2
        self.assertEqual(self.commits(), 2)
        self.assertEqual(self.git("diff", "--name-only", "HEAD~1", "HEAD").split(), [SEASON_FILE.replace(os.sep, "/")])
        self.assertIn("Schedule: a01-01", self.git("log", "-1", "--format=%B"))

    def test_US042_winter_hour_uses_utc_plus_one(self) -> None:
        self.assertEqual(schedule_tool.madrid_to_utc("2026-12-12", "20:30"), "2026-12-12T19:30:00Z")

    def test_US042_unconfirmed_hour_becomes_null_and_is_never_invented(self) -> None:
        self.commit_feed([base.feed_match(i, "scheduled", time="2026-10-17T18:30:00Z") for i in range(1, 9)])
        result, _, _ = self.run_pending(all_pending(hour=None))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.season_matches()["a01-02"]["kickoff"], {"date": "2026-10-17", "time": None})

    def test_US042_unconfirmed_hour_with_a_leftover_hour_text_still_gives_null(self) -> None:
        match = pending(1, hour=None)
        match["hour"] = "20:30"  # no_hour manda
        self.assertEqual(fetch.RfefMatch("2026-10-17", match).hour, None)


class FutureMatchesTests(ScheduleTestCase):
    def test_US042_round_with_future_matches_without_score_is_not_an_anomaly(self) -> None:
        document = rfef_doc([pending(i) for i in range(1, 5)])
        document[FUTURE_DAY] = {"date": "Domingo", "descansa": [], "matches": [pending(i) for i in range(5, 9)]}
        result, _, _ = self.run_pending([], doc=document)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.season_matches()["a01-08"]["kickoff"]["date"], FUTURE_DAY)  # tambien se aplica la fecha

    def test_US042_future_match_with_goals_is_an_anomaly_and_nothing_is_applied(self) -> None:
        document = rfef_doc([pending(i) for i in range(1, 8)])
        document[FUTURE_DAY] = {"date": "Domingo", "descansa": [], "matches": [rfef_match(8, ("1", "0"), "Sin comenzar")]}
        result, _, _ = self.run_pending([], doc=document)
        self.assertEqual(result.returncode, fetch.EXIT_ANOMALY)
        self.assertIn("[fecha-futura]", result.stderr)
        self.assert_untouched(1)


class SeasonRangeTests(ScheduleTestCase):
    def test_US042_date_inside_the_season_that_differs_from_the_feed_is_updated(self) -> None:
        matches = all_pending()
        document = rfef_doc(matches[:7], "2026-10-17")
        document["2026-08-01"] = {"date": "Sabado", "descansa": [], "matches": [matches[7]]}
        result, _, _ = self.run_pending([], doc=document)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.season_matches()["a01-08"]["kickoff"]["date"], "2026-08-01")

    def test_US042_date_outside_the_season_is_an_anomaly_and_nothing_is_applied(self) -> None:
        for outside in ("2026-07-31", "2027-07-01"):
            matches = all_pending()
            document = rfef_doc(matches[:7], "2026-10-17")
            document[outside] = {"date": "Dia", "descansa": [], "matches": [matches[7]]}
            result, _, _ = self.run_pending([], doc=document)
            self.assertEqual(result.returncode, fetch.EXIT_ANOMALY, outside)
            self.assertIn("[fecha-temporada]", result.stderr)
            self.assert_nothing_applied()

    def test_US042_season_limits_are_inclusive(self) -> None:
        self.assertEqual(schedule_tool.season_bounds(2027), ("2026-08-01", "2027-06-30"))
        self.assertIsNone(schedule_tool.date_problem("2027-06-30", 2027))
        self.assertIsNotNone(schedule_tool.date_problem("2027-06-31", 2027))


class InvalidHourTests(ScheduleTestCase):
    def test_US042_hour_outside_0900_2359_or_with_invalid_format_is_an_anomaly(self) -> None:
        for bad in ("08:59", "00:30", "24:00", "9:30", "20.30", "", "abc"):
            matches = all_pending()
            matches[3] = pending(4, hour=bad)
            result, _, _ = self.run_pending(matches)
            self.assertEqual(result.returncode, fetch.EXIT_ANOMALY, repr(bad))
            self.assertIn("[hora]", result.stderr)
            self.assert_nothing_applied()

    def test_US042_boundary_hours_are_valid(self) -> None:
        for good in ("09:00", "23:59"):
            self.assertIsNone(schedule_tool.time_problem(good))


class UntouchedMatchesTests(ScheduleTestCase):
    def test_US042_finished_and_cancelled_keep_their_date_and_hour(self) -> None:
        matches = [base.feed_match(i, "scheduled") for i in range(1, 9)]
        matches[0] = base.feed_match(1, "finished", (3, 2), date="2026-10-10", time="2026-10-10T10:00:00Z")
        matches[1] = base.feed_match(2, "cancelled", date="2026-10-11", time="2026-10-11T10:00:00Z")
        self.commit_feed(matches)
        rfef = all_pending()
        rfef[0] = rfef_match(1, ("3", "2"))
        result, _, _ = self.run_pending(rfef)
        self.assertEqual(result.returncode, 0, result.stderr)
        applied = self.season_matches()
        self.assertEqual(applied["a01-01"]["kickoff"], {"date": "2026-10-10", "time": "2026-10-10T10:00:00Z"})
        self.assertEqual(applied["a01-02"]["kickoff"], {"date": "2026-10-11", "time": "2026-10-11T10:00:00Z"})
        self.assertEqual(applied["a01-03"]["kickoff"]["time"], "2026-10-17T18:30:00Z")

    def test_US042_advanced_match_of_another_round_is_only_reported(self) -> None:
        season = base.feed_season([base.feed_match(i) for i in range(1, 8)])
        later = base.feed_match(8, date="2026-10-14")
        later["id"] = "a02-08"
        season["phases"][0]["rounds"].append({"id": "apertura-02", "number": 2, "name": "Jornada 2", "matches": [later]})
        support.build_public(self.feed, season)
        self.git("commit", "-q", "-am", "feed")
        self.original = self.season_bytes()
        result, _, _ = self.run_pending(all_pending())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("a02-08: adelantado", result.stderr)
        round_two = json.loads(self.season_bytes())["phases"][0]["rounds"][1]["matches"][0]
        self.assertEqual(round_two["kickoff"], {"date": "2026-10-14", "time": None})


class StabilityTests(ScheduleTestCase):
    def test_US042_hour_or_date_changing_between_reads_is_an_anomaly(self) -> None:
        first, second = all_pending(), all_pending()
        second[0] = pending(1, hour="21:00")
        body_a, body_b = (json.dumps(rfef_doc(m)).encode("utf-8") for m in (first, second))
        result, _, _ = self.launch([(200, body_a), (200, body_b)])
        self.assertEqual(result.returncode, fetch.EXIT_ANOMALY)
        self.assertIn("lecturas-distintas", result.stderr)
        self.assert_untouched(1)


class WithoutNewScoresTests(ScheduleTestCase):
    def test_US042_only_schedule_changes_skip_the_table_and_verify_passes(self) -> None:
        result, transport, _ = self.run_pending(all_pending())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c[0] for c in transport.calls], ["GET", "POST", "POST"])  # robots.txt y 2 POST
        self.assertNotIn("tabla", result.stderr.replace("tabla de alias", ""))

    def test_US042_applied_season_passes_the_kickoff_coherence_invariants(self) -> None:
        import invariants
        self.run_pending(all_pending())
        self.assertEqual(invariants.season_violations(json.loads(self.season_bytes())), [])


class NoChangeTests(ScheduleTestCase):
    def test_US042_same_date_and_hour_makes_no_commit(self) -> None:
        self.commit_feed([base.feed_match(i, "scheduled", time="2026-10-17T11:00:00Z") for i in range(1, 9)])  # 13:00 Madrid
        result, _, _ = self.run_pending(all_pending(hour="13:00"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Sin cambios", result.stderr)
        self.assert_untouched(2)


class VerifyFailureTests(ScheduleTestCase):
    def test_US042_failing_verify_restores_the_feed_and_does_not_commit(self) -> None:
        body = json.dumps(rfef_doc(all_pending())).encode("utf-8")
        result, _, _ = self.launch([(200, body), (200, body)], verify=lambda feed: (False, "[FALLO] 4-invariantes"))
        self.assertEqual(result.returncode, fetch.EXIT_ERROR)
        self.assertIn("restaurado", result.stderr)
        self.assert_untouched(1)


class ApplyScheduleToolTests(ScheduleTestCase):
    def write_csv(self, text: str) -> str:
        path = os.path.join(self.tmp, "schedule.csv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def run_tool(self, text: str, *extra: str) -> int:
        return schedule_tool.main(["--feed", self.feed, self.write_csv(text), "--now", "2026-10-20T10:00:00Z"] + list(extra))

    def test_US042_invalid_rows_write_nothing(self) -> None:
        for text in ("id,date,time\na01-01,2026-10-17,08:00\n", "id,date,time\na01-01,2027-07-01,10:00\n",
                     "id,date,time\nzzz,2026-10-17,10:00\n", "id,date\na01-01,2026-10-17\n",
                     "id,date,time\na01-01,2026-10-17,10:00\na01-01,2026-10-17,11:00\n"):
            self.assertEqual(self.run_tool(text), 1, text)
            self.assertEqual(self.season_bytes(), self.original)

    def test_US042_finished_match_is_refused(self) -> None:
        self.commit_feed([base.feed_match(1, "finished", (1, 0))] + [base.feed_match(i) for i in range(2, 9)])
        self.assertEqual(self.run_tool("id,date,time\na01-01,2026-10-18,10:00\n"), 1)
        self.assertEqual(self.season_bytes(), self.original)

    def test_US042_updated_at_must_move_forward_and_is_set_on_change(self) -> None:
        self.assertEqual(self.run_tool("id,date,time\na01-01,2026-10-18,10:00\n"), 0)
        season = json.loads(self.season_bytes())
        self.assertEqual(season["updatedAt"], "2026-10-20T10:00:00Z")
        self.assertEqual(self.run_tool("id,date,time\na01-02,2026-10-18,10:00\n"), 1)  # mismo updatedAt: no es posterior
