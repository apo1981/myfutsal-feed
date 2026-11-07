"""US-036: orquestador auto-apply-results. Respuestas sinteticas, red y reloj simulados, repo git temporal; sin red real."""
from __future__ import annotations

import copy
import datetime
import hashlib
import io
import json
import os
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple

import support  # debe ir antes que los modulos de scripts: anade scripts/ al sys.path
import auto_apply_results as tool
import cross_check
import fetch_rfef_results as fetch
import test_fetch_rfef as base
from test_fetch_rfef import Clock, FakeTransport, rfef_doc, rfef_match, standard_matches

NOREPLY = "12345+operator@users.noreply.github.com"
SEASON_FILE = os.path.join("public", "v1", "seasons", "2026-27.json")
TABLE_URL = fetch.BASE_URL + tool.TABLE_ENDPOINT_PATH
MATCHES_URL = fetch.BASE_URL + fetch.ENDPOINT_PATH


def always_ok(feed: str) -> Tuple[bool, str]:
    return True, ""


class AutoApplyTestCase(base.FetchTestCase):
    def setUp(self) -> None:
        super().setUp()
        for command in (["init", "-q"], ["config", "user.email", NOREPLY], ["config", "user.name", "operator"],
                        ["add", "."], ["commit", "-q", "-m", "initial"]):
            self.git(*command)
        self.original = self.season_bytes()

    def git(self, *arguments: str) -> str:
        result = subprocess.run(["git", "-C", self.feed] + list(arguments), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def season_bytes(self) -> bytes:
        with open(os.path.join(self.feed, SEASON_FILE), "rb") as handle:
            return handle.read()

    def commits(self) -> int:
        return int(self.git("rev-list", "--count", "HEAD").strip())

    def table(self, mutate: Optional[Callable[[List[Dict[str, Any]]], None]] = None, finished: int = 3) -> bytes:
        """Tabla correcta para el lote estandar (3 finalizados) y la jornada 1, en el formato supuesto de la RFEF."""
        season = json.loads(self.original)
        batch = [("a01-01", 3, 2), ("a01-02", 0, 0), ("a01-03", 5, 1)][:finished]
        computed = cross_check.compute_standings(cross_check.apply_batch(season, batch), "apertura", 1)
        rows = []
        for team in base.TEAM_IDS:
            standing = computed.get(team) or cross_check._empty_standing()
            rows.append({"id": base.DATATEAM[team], "pj": 1, "pt": standing["pt"], "home": dict(standing["home"]),
                         "away": dict(standing["away"]), "total": {}})
        if mutate:
            mutate(rows)
        return json.dumps({"table": {"1297": rows}}).encode("utf-8")

    def launch(self, data: List[Tuple[int, bytes]], verify: Callable[[str], Tuple[bool, str]] = always_ok,
               extra: Optional[List[str]] = None) -> Tuple[Any, FakeTransport, Clock]:
        clock = Clock()
        transport = FakeTransport(data=data, clock=clock)
        out, err = io.StringIO(), io.StringIO()
        argv = ["--feed", self.feed, "--phase", "apertura", "--round", "1", "--today", base.TODAY, "--raw-dir", self.raw_dir] + (extra or [])
        code = tool.main(argv, transport_factory=lambda: transport, sleep=clock.sleep, now=clock.now, stdout=out, stderr=err,
                         verify_runner=verify)
        return support.InProcessResult(code, out.getvalue(), err.getvalue()), transport, clock

    def doc_bytes(self, matches: Optional[List[Dict[str, Any]]] = None) -> bytes:
        return json.dumps(rfef_doc(matches or standard_matches())).encode("utf-8")

    def green(self, table: Optional[bytes] = None) -> Tuple[Any, FakeTransport, Clock]:
        body = self.doc_bytes()
        return self.launch([(200, body), (200, body), (200, table or self.table())])

    def assert_untouched(self, commits: int) -> None:
        self.assertEqual(self.season_bytes(), self.original)
        self.assertEqual(self.commits(), commits)
        self.assertEqual(self.git("status", "--porcelain").strip(), "")


class GreenTests(AutoApplyTestCase):
    def test_US036_all_green_applies_verifies_and_commits_with_traceability(self) -> None:
        result, transport, _ = self.green()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commits(), 2)
        applied = json.loads(self.season_bytes())
        matches = {m["id"]: m for m in applied["phases"][0]["rounds"][0]["matches"]}
        self.assertEqual([matches[i]["status"] for i in ("a01-01", "a01-02", "a01-03", "a01-04")], ["finished"] * 3 + ["unknown"])
        self.assertEqual(matches["a01-03"]["score"], {"home": 5, "away": 1})
        self.assertEqual(self.git("diff", "--name-only", "HEAD~1", "HEAD").split(), [SEASON_FILE.replace(os.sep, "/")])

    def test_US036_commit_message_has_url_utc_instant_and_sha256_of_the_three_responses(self) -> None:
        body, table = self.doc_bytes(), self.table()
        self.launch([(200, body), (200, body), (200, table)])
        message = self.git("log", "-1", "--format=%B")
        self.assertIn(MATCHES_URL, message)
        self.assertIn(TABLE_URL, message)
        for digest in (hashlib.sha256(body).hexdigest(), hashlib.sha256(table).hexdigest()):
            self.assertIn("sha256=" + digest, message)
        self.assertEqual(message.count("sha256="), 3)
        self.assertRegex(message, r"Read 1 \(matches\): \S+ 2026-10-20T09:\d\d:\d\dZ sha256=")
        self.assertRegex(message, r"Table: \S+ 2026-10-20T09:\d\d:\d\dZ sha256=")

    def test_US036_commit_uses_the_noreply_identity_and_never_pushes(self) -> None:
        self.green()
        self.assertEqual(self.git("log", "-1", "--format=%ae|%ce").strip(), "%s|%s" % (NOREPLY, NOREPLY))
        self.assertEqual(self.git("remote").strip(), "")  # sin remoto no hay push posible

    def test_US036_three_reads_with_waits_between_them(self) -> None:
        _, transport, clock = self.green()
        posts = [call for call in transport.calls if call[0] == "POST"]
        self.assertEqual([call[1].rsplit("/", 1)[-1] for call in posts], ["get_matchs_round", "get_matchs_round", "get_table_round"])
        self.assertGreaterEqual(sum(clock.sleeps), fetch.READ_SEPARATION_SECONDS)  # espera larga troceada por la cuenta atras (US-041)
        self.assertEqual(clock.sleeps[-1], fetch.REQUEST_INTERVAL_SECONDS)
        self.assertNotIn(b"league", posts[2][2])

    def test_US036_raw_responses_are_saved_outside_the_repo(self) -> None:
        self.green()
        self.assertEqual(len(os.listdir(self.raw_dir)), 3)

    def test_US036_verify_runs_after_apply_on_the_applied_json(self) -> None:
        seen = []  # type: List[bytes]

        def spy(feed: str) -> Tuple[bool, str]:
            seen.append(self.season_bytes())
            return True, ""
        body = self.doc_bytes()
        self.launch([(200, body), (200, body), (200, self.table())], verify=spy)
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0], self.original)


class AnomalyTests(AutoApplyTestCase):
    def test_US036_table_difference_changes_nothing_and_reports_rule_team_expected_and_obtained(self) -> None:
        def mutate(rows: List[Dict[str, Any]]) -> None:
            rows[0]["home"]["gf"] = 9  # t01 marco 3 en casa
        result, _, _ = self.green(self.table(mutate))
        self.assertEqual(result.returncode, fetch.EXIT_ANOMALY)
        self.assert_untouched(1)
        self.assertIn("anomalia [tabla] t01 casa gf: esperado=3 obtenido=9", result.stderr)
        self.assertIn("disparador 1 de ADR-012", result.stderr)

    def test_US036_crossed_home_away_mapping_is_an_anomaly(self) -> None:
        def mutate(rows: List[Dict[str, Any]]) -> None:
            rows[0]["home"], rows[0]["away"] = rows[0]["away"], rows[0]["home"]
        result, _, _ = self.green(self.table(mutate))
        self.assertEqual(result.returncode, fetch.EXIT_ANOMALY)
        self.assert_untouched(1)

    def test_US036_unstable_second_read_stops_before_the_table_request(self) -> None:
        changed = standard_matches()
        changed[0] = rfef_match(1, ("3", "3"))
        result, transport, _ = self.launch([(200, self.doc_bytes()), (200, self.doc_bytes(changed)), (200, self.table())])
        self.assertEqual(result.returncode, fetch.EXIT_ANOMALY)
        self.assertIn("lecturas-distintas", result.stderr)
        self.assertEqual(sum(1 for call in transport.calls if call[0] == "POST"), 2)
        self.assert_untouched(1)

    def test_US036_sanity_failure_reports_it_and_changes_nothing(self) -> None:
        bad = standard_matches()
        bad[0] = rfef_match(1, ("30", "2"))
        result, _, _ = self.launch([(200, self.doc_bytes(bad))])
        self.assertEqual(result.returncode, fetch.EXIT_ANOMALY)
        self.assertIn("goles-fuera-de-rango", result.stderr)
        self.assert_untouched(1)

    def test_US036_response_of_another_round_is_an_anomaly_so_pairs_are_always_of_round_n(self) -> None:
        other = [rfef_match(i, ("1", "0") if i == 1 else None, "Finalizado" if i == 1 else "Sin comenzar",
                            category="Liga : Jornada 2") for i in range(1, 9)]
        result, _, _ = self.launch([(200, self.doc_bytes(other))])
        self.assertEqual(result.returncode, fetch.EXIT_ANOMALY)
        self.assertIn("anomalia [jornada]", result.stderr)
        self.assert_untouched(1)

    def test_US036_finished_in_feed_with_different_score_is_an_anomaly_and_nothing_is_applied(self) -> None:
        matches = [base.feed_match(i) for i in range(1, 9)]
        matches[0] = base.feed_match(1, "finished", (1, 0))
        self.write_feed(matches)
        self.git("commit", "-q", "-am", "feed with finished match")
        self.original = self.season_bytes()
        result, _, _ = self.green()
        self.assertEqual(result.returncode, fetch.EXIT_ANOMALY)
        self.assertIn("finished-distinto", result.stderr)
        self.assert_untouched(2)


class TableUnavailableTests(AutoApplyTestCase):
    def test_US036_table_with_unexpected_json_applies_nothing_and_says_so(self) -> None:
        for body in (b"no es json", json.dumps({"table": {"9999": []}}).encode("utf-8")):
            result, _, _ = self.green(body)
            self.assertEqual(result.returncode, fetch.EXIT_ERROR, result.stderr)
            self.assertIn("tabla no disponible", result.stderr)
            self.assert_untouched(1)

    def test_US036_table_http_error_applies_nothing_and_says_so(self) -> None:
        body = self.doc_bytes()
        result, _, _ = self.launch([(200, body), (200, body), (404, b"")])
        self.assertEqual(result.returncode, fetch.EXIT_ERROR)
        self.assertIn("tabla no disponible", result.stderr)
        self.assert_untouched(1)

    def test_US036_table_429_stops_the_tool(self) -> None:
        body = self.doc_bytes()
        result, _, _ = self.launch([(200, body), (200, body), (429, b"")])
        self.assertEqual(result.returncode, fetch.EXIT_STOPPED)
        self.assertEqual(self.season_bytes(), self.original)  # el 429 desactiva el interruptor (US-038): config.json cambia
        self.assertEqual(self.commits(), 1)


class NoChangeTests(AutoApplyTestCase):
    def test_US036_rerunning_an_applied_round_changes_nothing_and_says_so(self) -> None:
        self.green()
        applied, commits = self.season_bytes(), self.commits()
        self.original = applied
        body = self.doc_bytes()
        result, transport, _ = self.launch([(200, body), (200, body), (200, self.table())])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ya estaba aplicado", result.stderr)
        self.assert_untouched(commits)
        self.assertEqual(sum(1 for call in transport.calls if call[0] == "POST"), 2)

    def test_US036_round_without_new_finished_matches_changes_nothing_and_says_so(self) -> None:
        pending = [rfef_match(i, None, "Sin comenzar", no_hour="1") for i in range(1, 9)]  # sin hora, como el feed
        body = self.doc_bytes(pending)
        result, _, _ = self.launch([(200, body), (200, body)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no hay partidos finalizados nuevos", result.stderr)
        self.assert_untouched(1)


class VerifyFailureTests(AutoApplyTestCase):
    def test_US036_failing_verify_restores_the_previous_json_and_does_not_commit(self) -> None:
        seen = []  # type: List[bool]

        def failing(feed: str) -> Tuple[bool, str]:
            seen.append(self.season_bytes() != self.original)  # el JSON estaba aplicado cuando verify corrio
            return False, "[FALLO] 4-invariantes"
        body = self.doc_bytes()
        result, _, _ = self.launch([(200, body), (200, body), (200, self.table())], verify=failing)
        self.assertEqual(seen, [True])
        self.assertEqual(result.returncode, fetch.EXIT_ERROR)
        self.assertIn("restaurado", result.stderr)
        self.assertIn("4-invariantes", result.stderr)
        self.assert_untouched(1)


class ExceptionSafetyTests(AutoApplyTestCase):
    def test_US036_exception_in_verify_runner_restores_the_json_and_propagates(self) -> None:
        def exploding(feed: str) -> Tuple[bool, str]:
            raise KeyboardInterrupt()
        body = self.doc_bytes()
        with self.assertRaises(KeyboardInterrupt):
            self.launch([(200, body), (200, body), (200, self.table())], verify=exploding)
        self.assert_untouched(1)

    def test_US036_unexpected_error_in_verify_runner_restores_the_json(self) -> None:
        def broken(feed: str) -> Tuple[bool, str]:
            raise OSError("verify no se pudo ejecutar")
        body = self.doc_bytes()
        result, _, _ = self.launch([(200, body), (200, body), (200, self.table())], verify=broken)
        self.assertEqual(result.returncode, fetch.EXIT_ERROR)
        self.assertIn("queda como estaba", result.stderr)
        self.assert_untouched(1)


class GuardTests(AutoApplyTestCase):
    def test_US036_non_noreply_identity_refuses_before_any_request(self) -> None:
        self.git("config", "user.email", "persona@example.com")
        result, transport, _ = self.green()
        self.assertEqual(result.returncode, fetch.EXIT_ERROR)
        self.assertIn("noreply", result.stderr)
        self.assertEqual(transport.calls, [])
        self.assert_untouched(1)

    def test_US036_uncommitted_season_changes_are_refused(self) -> None:
        with open(os.path.join(self.feed, SEASON_FILE), "a", encoding="utf-8") as handle:
            handle.write("\n")
        result, transport, _ = self.green()
        self.assertEqual(result.returncode, fetch.EXIT_ERROR)
        self.assertEqual(transport.calls, [])
        self.assertEqual(self.commits(), 1)


class ProgressTests(AutoApplyTestCase):
    def progress_lines(self, result: Any) -> List[str]:
        return [line for line in result.stderr.splitlines() if line.startswith(tool.PROGRESS_PREFIX)]

    def test_US041_phases_are_announced_in_order_on_stderr_only(self) -> None:
        result, _, _ = self.green()
        text = " | ".join(self.progress_lines(result))
        order = ["comprobaciones iniciales", "1a lectura", "2a lectura", "tabla", "cruce", "aplicar y verify", "commit"]
        positions = [text.index(name) for name in order]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn(tool.PROGRESS_PREFIX, result.stdout)
        self.assertEqual(result.stdout.count("\n"), 1)

    def test_US041_long_wait_announces_madrid_time_and_at_most_one_line_per_interval(self) -> None:
        result, _, clock = self.green()
        lines = self.progress_lines(result)
        self.assertTrue(any("hora prevista" in line and "espera de 15 min" in line for line in lines))
        countdown = [line for line in lines if "quedan" in line]
        self.assertEqual(len(countdown), fetch.READ_SEPARATION_SECONDS // tool.COUNTDOWN_INTERVAL_SECONDS - 1)
        self.assertEqual(clock.sleeps.count(tool.COUNTDOWN_INTERVAL_SECONDS), len(countdown) + 1)

    def test_US041_madrid_time_is_hh_mm_of_the_due_instant(self) -> None:
        clock, err = base.Clock(), io.StringIO()
        tool.countdown_wait(900, clock.sleep, clock.now, err)
        due = (base.NOW + datetime.timedelta(seconds=900)).astimezone(fetch.TIMEZONE).strftime("%H:%M")
        self.assertIn("hora prevista de la 2a lectura: " + due, err.getvalue())

    def test_US041_short_pauses_write_no_line_and_no_extra_wait_line_after_the_wait(self) -> None:
        result, _, _ = self.green()
        lines = self.progress_lines(result)
        self.assertEqual(len([line for line in lines if "hora prevista" in line]), 1)
        last_countdown = max(i for i, line in enumerate(lines) if "quedan" in line)
        self.assertFalse(any("espera" in line for line in lines[last_countdown + 1:]))
        self.assertEqual(len(lines), 7 + 1 + 14)  # 7 fases + hora prevista + 14 cuentas atras: ninguna por pausa corta

    def test_US041_failure_keeps_exit_code_and_last_progress_line_names_the_phase(self) -> None:
        body = self.doc_bytes()
        result, _, _ = self.launch([(200, body), (200, body), (500, b"")])
        self.assertEqual(result.returncode, fetch.EXIT_STOPPED)
        self.assertEqual(self.progress_lines(result)[-1], tool.PROGRESS_PREFIX + "tabla")
