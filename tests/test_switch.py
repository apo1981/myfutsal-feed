"""US-038: interruptor enabled, auto-desactivacion, cuenta de anomalias seguidas e informe de diferencias no aplicadas.

Respuestas sinteticas, red y reloj simulados, repo git temporal; sin red real.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, List

import support  # debe ir antes que los modulos de scripts: anade scripts/ al sys.path
import fetch_rfef_results as fetch
import rfef_switch
import test_fetch_rfef as base
from test_auto_apply import AutoApplyTestCase
from test_fetch_rfef import FakeTransport, rfef_doc, rfef_match, standard_matches

OTHER_ROUND = [rfef_match(i, None, "Sin comenzar", category="Liga : Jornada 2") for i in range(1, 9)]
BLOCKING_ROBOTS = b"User-agent: *\nDisallow: /futsal_xhr\n"


class SwitchTestCase(AutoApplyTestCase):
    def config(self) -> Dict[str, Any]:
        with open(os.path.join(self.feed, rfef_switch.CONFIG_PATH), "r", encoding="utf-8") as handle:
            return json.load(handle)

    def set_enabled(self, value: bool) -> None:
        support.write_json(os.path.join(self.feed, rfef_switch.CONFIG_PATH), {"enabled": value})
        self.git("add", ".")
        if self.git("status", "--porcelain").strip():
            self.git("commit", "-q", "-m", "config")

    def anomaly(self) -> Any:
        result, _, _ = self.launch([(200, self.doc_bytes(OTHER_ROUND))])
        self.assertEqual(result.returncode, fetch.EXIT_ANOMALY, result.stderr)
        return result


class DisabledTests(SwitchTestCase):
    def test_US038_disabled_auto_apply_makes_no_request_and_says_so(self) -> None:
        self.set_enabled(False)
        result, transport, _ = self.green()
        self.assertEqual(result.returncode, fetch.EXIT_DISABLED)
        self.assertEqual(transport.calls, [])
        self.assertIn("deshabilitada", result.stderr)
        self.assertEqual(self.season_bytes(), self.original)

    def test_US038_disabled_fetch_makes_no_request_and_says_so(self) -> None:
        self.set_enabled(False)
        transport = FakeTransport(data=(200, self.doc_bytes()))
        result = self.invoke([], transport)
        self.assertEqual(result.returncode, fetch.EXIT_DISABLED)
        self.assertEqual(transport.calls, [])
        self.assertIn("deshabilitada", result.stderr)

    def test_US038_disabled_does_not_block_reading_a_local_file_because_it_makes_no_request(self) -> None:
        self.set_enabled(False)
        self.assertEqual(self.fetch(rfef_doc(standard_matches())).returncode, 0)

    def test_US038_missing_or_invalid_config_makes_no_request(self) -> None:
        for content in (None, "{", '{"enabled": "yes"}', "[]"):
            path = os.path.join(self.feed, rfef_switch.CONFIG_PATH)
            if content is None:
                os.remove(path)
            else:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(content)
            result, transport, _ = self.green()
            self.assertEqual(result.returncode, fetch.EXIT_ERROR, content)
            self.assertEqual(transport.calls, [], content)

    def test_US038_only_the_human_reenables_the_tool_never_a_green_run(self) -> None:
        self.set_enabled(False)
        for _ in range(2):
            self.green()
        self.assertFalse(self.config()["enabled"])
        self.set_enabled(True)  # edicion manual del humano
        result, transport, _ = self.green()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(transport.calls)


class BlockTests(SwitchTestCase):
    def test_US038_status_403_and_429_disable_the_switch_and_apply_nothing(self) -> None:
        for status in (403, 429):
            self.set_enabled(True)
            result, _, _ = self.launch([(status, b"")])
            self.assertEqual(result.returncode, fetch.EXIT_STOPPED, status)
            self.assertFalse(self.config()["enabled"], status)
            self.assertIn(str(status), self.config()["disabledReason"])
            self.assertEqual(self.season_bytes(), self.original)

    def test_US038_429_on_the_table_disables_the_switch_and_applies_nothing(self) -> None:
        body = self.doc_bytes()
        result, _, _ = self.launch([(200, body), (200, body), (429, b"")])
        self.assertEqual(result.returncode, fetch.EXIT_STOPPED)
        self.assertFalse(self.config()["enabled"])
        self.assertEqual(self.season_bytes(), self.original)
        self.assertEqual(self.commits(), 1)

    def test_US038_robots_that_vetoes_the_route_disables_the_switch(self) -> None:
        clock = base.Clock()
        transport = FakeTransport(robots=(200, BLOCKING_ROBOTS), data=(200, self.doc_bytes()), clock=clock)
        result = self.run_auto(transport, clock)
        self.assertEqual(result.returncode, fetch.EXIT_STOPPED)
        self.assertFalse(self.config()["enabled"])
        self.assertEqual([call[0] for call in transport.calls], ["GET"])

    def test_US038_fetch_also_disables_the_switch_on_429(self) -> None:
        result = self.invoke([], FakeTransport(data=(429, b"")))
        self.assertEqual(result.returncode, fetch.EXIT_STOPPED)
        self.assertFalse(self.config()["enabled"])

    def test_US038_a_server_error_stops_but_does_not_disable_the_switch(self) -> None:
        result, _, _ = self.launch([(503, b"")])
        self.assertEqual(result.returncode, fetch.EXIT_STOPPED)
        self.assertTrue(self.config()["enabled"])

    def run_auto(self, transport: FakeTransport, clock: base.Clock) -> Any:
        import io
        import auto_apply_results as auto
        out, err = io.StringIO(), io.StringIO()
        code = auto.main(["--feed", self.feed, "--phase", "apertura", "--round", "1", "--today", base.TODAY, "--raw-dir", self.raw_dir],
                         transport_factory=lambda: transport, sleep=clock.sleep, now=clock.now, stdout=out, stderr=err,
                         verify_runner=lambda feed: (True, ""))
        return support.InProcessResult(code, out.getvalue(), err.getvalue())


class ConsecutiveAnomalyTests(SwitchTestCase):
    def test_US038_one_anomaly_keeps_the_switch_enabled(self) -> None:
        result = self.anomaly()
        self.assertTrue(self.config()["enabled"])
        self.assertIn("1 de 2", result.stderr)

    def test_US038_two_consecutive_anomalies_disable_the_switch(self) -> None:
        self.anomaly()
        result = self.anomaly()
        self.assertFalse(self.config()["enabled"])
        self.assertIn("2 anomalias seguidas", self.config()["disabledReason"])
        self.assertIn("enabled puesto a false", result.stderr)
        self.assertEqual(self.season_bytes(), self.original)
        third, transport, _ = self.green()
        self.assertEqual(third.returncode, fetch.EXIT_DISABLED)
        self.assertEqual(transport.calls, [])

    def test_US038_a_green_run_between_two_anomalies_resets_the_count(self) -> None:
        self.anomaly()
        green, _, _ = self.green()
        self.assertEqual(green.returncode, 0, green.stderr)
        self.anomaly()
        self.assertTrue(self.config()["enabled"])
        self.assertEqual(rfef_switch.read_streak(os.path.dirname(self.raw_dir) if False else self.raw_dir), 1)

    def test_US038_a_run_without_changes_counts_as_green(self) -> None:
        self.anomaly()
        pending = self.doc_bytes([rfef_match(i, None, "Sin comenzar") for i in range(1, 9)])
        result, _, _ = self.launch([(200, pending)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.anomaly()
        self.assertTrue(self.config()["enabled"])


class ReportTests(SwitchTestCase):
    def advanced_feed(self) -> None:
        """Jornada 1 con 7 partidos y el octavo (t15-t16) adelantado en la jornada 2; a01-01 con otra fecha, a01-02 con otra hora
        y a01-04 aplazado."""
        matches = [base.feed_match(i) for i in range(1, 8)]
        matches[0] = base.feed_match(1, date="2026-10-18")
        matches[1] = base.feed_match(2, time="2026-10-17T10:00:00Z")  # 12:00 en Madrid; la RFEF dice 13:00
        matches[3] = base.feed_match(4, status="postponed")
        season = base.feed_season(matches)
        later = copy.deepcopy(base.feed_match(8, date="2026-10-14"))
        later["id"] = "a02-08"
        season["phases"][0]["rounds"].append({"id": "apertura-02", "number": 2, "name": "Jornada 2", "matches": [later]})
        self.write_feed_season(season)

    def write_feed_season(self, season: Dict[str, Any]) -> None:
        support.build_public(self.feed, season)

    def test_US038_report_lists_date_time_postponed_and_advanced_with_feed_and_rfef_values_and_changes_nothing(self) -> None:
        self.advanced_feed()
        before = self.season_bytes()
        result = self.fetch(rfef_doc(standard_matches()))
        self.assertEqual(result.returncode, 0, result.stderr)
        for expected in ("a01-01: fecha feed=2026-10-18, RFEF=2026-10-17",
                         "a01-02: hora feed=12:00, RFEF=13:00",
                         "a01-04: estado feed=postponed, RFEF=Sin comenzar (2026-10-17 13:00); no se cambia",
                         "a02-08: adelantado, el feed lo tiene en la jornada 2 (fecha=2026-10-14, estado=unknown) y la RFEF en la jornada 1"):
            self.assertIn(expected, result.stderr)
        self.assertNotIn("sin diferencias", result.stderr)
        self.assertEqual(self.season_bytes(), before)

    def test_US038_suspended_match_is_reported_with_feed_and_rfef_status(self) -> None:
        self.write_feed_season(base.feed_season([base.feed_match(1, status="suspended")] + [base.feed_match(i) for i in range(2, 9)]))
        result = self.fetch(rfef_doc(standard_matches()))
        self.assertIn("a01-01: estado feed=suspended, RFEF=Finalizado", result.stderr)

    def test_US038_auto_apply_prints_the_report_and_never_touches_dates_or_statuses(self) -> None:
        # a01-04 aplazado en el feed: el informe lo lista y su estado y fecha no cambian tras aplicar el lote.
        matches = [base.feed_match(i) for i in range(1, 9)]
        matches[3] = base.feed_match(4, status="postponed")
        self.write_feed_season(base.feed_season(matches))
        self.git("add", ".")
        self.git("commit", "-q", "-m", "feed")
        result, _, _ = self.green()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("a01-04: estado feed=postponed", result.stderr)
        applied = {m["id"]: m for m in json.loads(self.season_bytes())["phases"][0]["rounds"][0]["matches"]}
        self.assertEqual(applied["a01-04"]["status"], "postponed")
        self.assertEqual(applied["a01-04"]["kickoff"], {"date": "2026-10-17", "time": None})
        self.assertEqual(applied["a01-01"]["status"], "finished")

    def coherent_feed(self) -> None:
        self.write_feed_season(base.feed_season([base.feed_match(i, time="2026-10-17T11:00:00Z") for i in range(1, 9)]))  # 13:00 Madrid
        self.git("add", ".")
        self.git("commit", "-q", "-m", "feed")

    def test_US038_coherent_page_reports_no_differences_and_the_number_of_new_matches(self) -> None:
        self.coherent_feed()
        result, _, _ = self.green()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sin diferencias de fecha, hora ni estado con el feed; partidos nuevos: 3", result.stderr)

    def test_US038_no_changes_run_also_prints_the_report(self) -> None:
        self.coherent_feed()
        pending = self.doc_bytes([rfef_match(i, None, "Sin comenzar") for i in range(1, 9)])
        result, _, _ = self.launch([(200, pending)])
        self.assertIn("Sin cambios", result.stderr)
        self.assertIn("partidos nuevos: 0", result.stderr)
