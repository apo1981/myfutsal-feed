"""US-045: canal de TV (broadcasters) desde la RFEF en auto-apply-rfef, con lista blanca. Sintetico, sin red."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import support  # debe ir antes que los modulos de scripts: anade scripts/ al sys.path
import apply_broadcasters as tool
import apply_results
import fetch_rfef_results as fetch
import test_fetch_rfef as base
from test_fetch_rfef import rfef_doc, rfef_match
from test_schedule import FUTURE_DAY, ScheduleTestCase

DAZN = {"id": "160", "name": "DAZN España", "url": None}
TELEDEPORTE = {"id": "3", "name": "Teledeporte", "url": "http://www.rtve.es/directo/teledeporte/"}
TDP_PLAY = {"id": "162", "name": "TDP Play", "url": None}
UNKNOWN = {"id": "999", "name": "Canal Nuevo", "url": "http://example.invalid/nuevo"}


def with_tvs(index: int, tvs: Optional[List[Dict[str, Any]]], status: str = "Sin comenzar", **overrides: Any) -> Dict[str, Any]:
    match = rfef_match(index, None, status, **overrides)
    if tvs is not None:
        match["tvs"] = tvs
    return match


def future_doc(tvs_by_index: Dict[int, Optional[List[Dict[str, Any]]]], default: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Jornada 1 con los 8 partidos 'Sin comenzar' el FUTURE_DAY (posterior a TODAY)."""
    return rfef_doc([with_tvs(i, tvs_by_index.get(i, default)) for i in range(1, 9)], FUTURE_DAY)


class BroadcastersTestCase(ScheduleTestCase):
    def broadcasters(self) -> Dict[str, Any]:
        return {identifier: match.get("broadcasters") for identifier, match in self.season_matches().items()}


class WhitelistTests(BroadcastersTestCase):
    def test_US045_known_ids_publish_only_whitelist_names_without_urls(self) -> None:
        result, _, _ = self.run_pending([], doc=future_doc({1: [TELEDEPORTE, DAZN, TDP_PLAY]}, default=[DAZN]))
        self.assertEqual(result.returncode, 0, result.stderr)
        published = self.broadcasters()
        self.assertEqual(published["a01-01"], ["Teledeporte", "DAZN España", "TDP Play"])
        self.assertEqual(published["a01-02"], ["DAZN España"])
        self.assertNotIn("http", self.season_bytes().decode("utf-8").split('"broadcasters"')[1][:80])
        self.assertEqual(self.commits(), 2)
        self.assertIn("Broadcasters: a01-01", self.git("log", "-1", "--format=%B"))

    def test_US045_whitelist_file_only_has_names_and_no_urls(self) -> None:
        names = fetch.load_broadcasters(base.FEED)
        self.assertEqual(names, {"3": "Teledeporte", "160": "DAZN España", "162": "TDP Play"})
        self.assertFalse(any("://" in name for name in names.values()))

    def test_US045_unknown_id_is_reported_and_not_applied_while_the_rest_continues(self) -> None:
        result, _, _ = self.run_pending([], doc=future_doc({1: [DAZN, UNKNOWN], 2: [UNKNOWN]}, default=[DAZN]))
        self.assertEqual(result.returncode, 0, result.stderr)
        published = self.broadcasters()
        self.assertEqual(published["a01-01"], ["DAZN España"])
        self.assertIsNone(published["a01-02"])  # solo ids desconocidos: no se toca
        self.assertEqual(published["a01-03"], ["DAZN España"])
        self.assertIn("a01-01: canal de TV con id 999", result.stderr)
        self.assertIn("a01-02: canal de TV con id 999", result.stderr)
        self.assertNotIn("Canal Nuevo", self.season_bytes().decode("utf-8"))


class EmptyTvsTests(BroadcastersTestCase):
    def test_US045_empty_tvs_keeps_a_null_value_null(self) -> None:
        matches = [base.feed_match(i, "scheduled", date=FUTURE_DAY) for i in range(1, 9)]
        self.commit_feed(matches)
        result, _, _ = self.run_pending([], doc=future_doc({}, default=[]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(value is None for value in self.broadcasters().values()))
        self.assertNotIn("Broadcasters:", self.git("log", "-1", "--format=%B"))  # el commit es solo del horario

    def test_US045_empty_tvs_empties_a_published_value(self) -> None:
        matches = [base.feed_match(i, "scheduled", date=FUTURE_DAY) for i in range(1, 9)]
        matches[0]["broadcasters"] = ["DAZN España"]
        matches[1]["broadcasters"] = ["DAZN España"]
        self.commit_feed(matches)
        result, _, _ = self.run_pending([], doc=future_doc({2: [DAZN]}, default=[]))
        self.assertEqual(result.returncode, 0, result.stderr)
        published = self.broadcasters()
        self.assertIsNone(published["a01-01"])
        self.assertEqual(published["a01-02"], ["DAZN España"])


class NotPublishableTests(BroadcastersTestCase):
    def test_US045_finished_match_is_never_touched(self) -> None:
        matches = [base.feed_match(i, "scheduled", date=FUTURE_DAY) for i in range(1, 9)]
        matches[0] = base.feed_match(1, "finished", (3, 2), date=FUTURE_DAY)
        matches[0]["broadcasters"] = ["Teledeporte"]
        self.commit_feed(matches)
        result, _, _ = self.run_pending([], doc=future_doc({}, default=[DAZN]))
        self.assertEqual(result.returncode, 0, result.stderr)
        published = self.broadcasters()
        self.assertEqual(published["a01-01"], ["Teledeporte"])
        self.assertEqual(published["a01-02"], ["DAZN España"])

    def test_US045_past_date_without_result_gets_no_new_channel(self) -> None:
        result, _, _ = self.run_pending([with_tvs(i, [DAZN]) for i in range(1, 9)], day="2026-10-17")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(value is None for value in self.broadcasters().values()))

    def test_US045_match_in_play_today_gets_no_new_channel(self) -> None:
        in_play = [with_tvs(i, [DAZN], status="En juego") for i in range(1, 9)]
        doc = rfef_doc(in_play, FUTURE_DAY)
        result, _, _ = self.run_pending([], doc=doc)
        self.assertTrue(all(value is None for value in self.broadcasters().values()), result.stderr)

    def test_US045_match_missing_tvs_field_is_left_untouched(self) -> None:
        matches = [base.feed_match(i, "scheduled", date=FUTURE_DAY) for i in range(1, 9)]
        matches[0]["broadcasters"] = ["DAZN España"]
        self.commit_feed(matches)
        result, _, _ = self.run_pending([], doc=future_doc({}, default=None))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.broadcasters()["a01-01"], ["DAZN España"])

    def test_US045_postponed_match_in_the_feed_is_untouched(self) -> None:
        matches = [base.feed_match(i, "scheduled", date=FUTURE_DAY) for i in range(1, 9)]
        matches[0]["status"] = "postponed"
        self.commit_feed(matches)
        result, _, _ = self.run_pending([], doc=future_doc({}, default=[DAZN]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(self.broadcasters()["a01-01"])
        self.assertEqual(self.broadcasters()["a01-02"], ["DAZN España"])


class DoubleReadTests(BroadcastersTestCase):
    def test_US045_tvs_changing_between_reads_is_an_anomaly_and_applies_nothing(self) -> None:
        first = json.dumps(future_doc({}, default=[DAZN])).encode("utf-8")
        second = json.dumps(future_doc({3: [DAZN, TDP_PLAY]}, default=[DAZN])).encode("utf-8")
        result, _, _ = self.launch([(200, first), (200, second)])
        self.assertEqual(result.returncode, fetch.EXIT_ANOMALY)
        self.assertIn("[lecturas-distintas]", result.stderr)
        self.assertIn("canales", result.stderr)
        self.assert_nothing_applied()

    def test_US045_stable_tvs_in_both_reads_are_applied(self) -> None:
        result, transport, _ = self.run_pending([], doc=future_doc({}, default=[DAZN]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.broadcasters()["a01-05"], ["DAZN España"])


class NoExtraRequestsTests(BroadcastersTestCase):
    def test_US045_no_requests_beyond_robots_and_the_two_reads(self) -> None:
        result, transport, _ = self.run_pending([], doc=future_doc({}, default=[DAZN]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(transport.calls), 3)  # robots + 2 lecturas; sin tabla porque no hay marcadores nuevos (NFR-019)


class ApplyBroadcastersToolTests(BroadcastersTestCase):
    def write_csv(self, text: str) -> str:
        path = os.path.join(self.tmp, "b.csv")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_US045_tool_rejects_urls_and_unknown_matches_writing_nothing(self) -> None:
        for text in ("id,broadcasters\na01-01,http://x.invalid/y\n", "id,broadcasters\na99-99,DAZN España\n"):
            result = support.run_main(tool.main, ["--feed", self.feed, self.write_csv(text), "--updated-at", "2026-10-02T10:00:00Z",
                                                  "--now", "2026-10-02T11:00:00Z"])
            self.assertEqual(result.returncode, 1)
            self.assertEqual(self.season_bytes(), self.original)

    def test_US045_tool_refuses_finished_matches(self) -> None:
        matches = [base.feed_match(i, "scheduled") for i in range(1, 9)]
        matches[0] = base.feed_match(1, "finished", (3, 2))
        self.commit_feed(matches)
        result = support.run_main(tool.main, ["--feed", self.feed, self.write_csv("id,broadcasters\na01-01,DAZN España\n"),
                                              "--updated-at", "2026-10-02T10:00:00Z", "--now", "2026-10-02T11:00:00Z"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("finished", result.stderr)
        self.assertEqual(self.season_bytes(), self.original)
