"""US-034 y US-037: CLI fetch-rfef-results. Fixtures sinteticos escritos a mano (nunca JSON ni HTML reales de la RFEF), sin red."""
from __future__ import annotations

import copy
import datetime
import hashlib
import io
import json
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

import support  # debe ir antes que fetch_rfef_results: anade scripts/ al sys.path
import fetch_rfef_results as tool
import rfef_switch
from support import FEED, TempDirTestCase, run_script

TODAY = "2026-10-20"
NOW = datetime.datetime(2026, 10, 20, 9, 30, 0, tzinfo=datetime.timezone.utc)
TEAM_IDS = ["t%02d" % n for n in range(1, 17)]
DATATEAM = {team: str(100 + n) for n, team in enumerate(TEAM_IDS, start=1)}
PAIRS = [(TEAM_IDS[2 * i], TEAM_IDS[2 * i + 1]) for i in range(8)]  # t01-t02, t03-t04, ...
ROBOTS_ALLOWING = b"User-agent: Googlebot\nDisallow: /ajax\n"


def feed_match(index: int, status: str = "unknown", score: Optional[Tuple[int, int]] = None, date: str = "2026-10-17",
               time: Optional[str] = None) -> Dict[str, Any]:
    home, away = PAIRS[index - 1]
    return {"id": "a01-%02d" % index, "homeTeamId": home, "awayTeamId": away, "status": status,
            "kickoff": {"date": date, "time": time},
            "score": {"home": score[0], "away": score[1]} if score else None, "decidedBy": None, "note": None}


def feed_season(matches: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"schemaVersion": 1, "seasonId": "2026-27", "competitionName": "Liga de prueba", "updatedAt": "2026-10-01T10:00:00Z",
            "teams": [{"id": t, "name": t, "shortName": t, "crestURL": None} for t in TEAM_IDS],
            "phases": [{"id": "apertura", "name": "Apertura", "kind": "league", "order": 1, "rounds": [
                {"id": "apertura-01", "number": 1, "name": "Jornada 1", "matches": matches}]}]}


def rfef_match(index: int, goals: Optional[Tuple[str, str]] = ("2", "1"), status: str = "Finalizado", day_hour: str = "13:00",
               **overrides: Any) -> Dict[str, Any]:
    home, away = PAIRS[index - 1]
    raw = {"id": "9%02d" % index, "status_txt": status, "status": "x", "hour": day_hour, "no_hour": "0",
           "team_local": "Local  %s " % home, "datateam1": DATATEAM[home], "r_local": goals[0] if goals else "",
           "team_visit": "Visit %s" % away, "datateam2": DATATEAM[away], "r_visit": goals[1] if goals else "",
           "category": "Liga : Jornada 1", "league_id": "1297", "group_code": "1", "year": "2027"}
    raw.update(overrides)
    return raw


def rfef_doc(matches: List[Dict[str, Any]], day: str = "2026-10-17", resting: Optional[List[Any]] = None) -> Dict[str, Any]:
    return {day: {"date": "Sabado", "descansa": resting or [], "matches": matches}}


def standard_matches() -> List[Dict[str, Any]]:
    """3 finalizados y 5 sin marcador."""
    finished = [rfef_match(1, ("3", "2")), rfef_match(2, ("0", "0")), rfef_match(3, ("5", "1"))]
    pending = [rfef_match(i, None, "Sin comenzar") for i in range(4, 9)]
    return finished + pending


class FakeTransport:
    """`data`: una respuesta (se repite en cada lectura) o una lista, una por lectura (la ultima se repite)."""

    def __init__(self, robots: Tuple[int, bytes] = (200, ROBOTS_ALLOWING), data: Any = (200, b"{}"),
                 clock: Optional["Clock"] = None) -> None:
        self.robots = robots
        self.data = data if isinstance(data, list) else [data]
        self.calls = []  # type: List[Tuple[str, str, Optional[bytes]]]
        self.call_times = []  # type: List[datetime.datetime]
        self.clock = clock

    def request(self, method: str, url: str, data: Optional[bytes]) -> Tuple[int, bytes]:
        self.calls.append((method, url, data))
        if self.clock:
            self.call_times.append(self.clock.current)
        if url.endswith(tool.ROBOTS_PATH):
            return self.robots
        posts = sum(1 for call in self.calls if call[0] == "POST")
        return self.data[min(posts, len(self.data)) - 1]


class Clock:
    """Reloj simulado: `sleep` lo adelanta, sin esperas reales."""

    def __init__(self, start: datetime.datetime = NOW) -> None:
        self.current = start
        self.sleeps = []  # type: List[float]

    def now(self) -> datetime.datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += datetime.timedelta(seconds=seconds)


class FetchTestCase(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.feed = os.path.join(self.tmp, "feed")
        self.raw_dir = os.path.join(self.tmp, "local-data", "rfef")
        self.write_feed([feed_match(i) for i in range(1, 9)])
        aliases = {"version": 1, "aliases": {DATATEAM[t]: t for t in TEAM_IDS}}
        support.write_json(os.path.join(self.feed, tool.ALIASES_PATH), aliases)
        support.write_json(os.path.join(self.feed, rfef_switch.CONFIG_PATH), {"enabled": True})
        shutil.copy(os.path.join(FEED, tool.BROADCASTERS_PATH), os.path.join(self.feed, tool.BROADCASTERS_PATH))  # lista blanca real

    def enable_switch(self) -> None:
        """Un bloqueo (403/429) desactiva el interruptor; las pruebas que repiten estados lo reactivan como lo haria el humano."""
        support.write_json(os.path.join(self.feed, rfef_switch.CONFIG_PATH), {"enabled": True})

    def write_feed(self, matches: List[Dict[str, Any]]) -> None:
        support.build_public(self.feed, feed_season(matches))

    def write_response(self, document: Any, name: str = "response.json") -> str:
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as handle:
            handle.write(document if isinstance(document, bytes) else json.dumps(document).encode("utf-8"))
        return path

    def fetch(self, document: Any, *extra: str, transport: Optional[FakeTransport] = None) -> Any:
        """Ejecuta main sobre una respuesta local (--from-file)."""
        return self.invoke(["--from-file", self.write_response(document)] + list(extra), transport)

    def invoke(self, extra: List[str], transport: Optional[FakeTransport] = None, clock: Optional[Clock] = None) -> Any:
        clock = clock or (transport.clock if transport and transport.clock else Clock())
        out, err = io.StringIO(), io.StringIO()

        def no_network() -> Any:
            raise AssertionError("se ha intentado abrir red")

        argv = ["--feed", self.feed, "--phase", "apertura", "--round", "1", "--today", TODAY, "--raw-dir", self.raw_dir] + extra
        code = tool.main(argv, transport_factory=(lambda: transport) if transport else no_network,
                         sleep=clock.sleep, now=clock.now, stdout=out, stderr=err)
        return support.InProcessResult(code, out.getvalue(), err.getvalue())


class FinishedAndPendingTests(FetchTestCase):
    def test_US034_csv_only_has_the_three_finished_matches(self) -> None:
        result = self.fetch(rfef_doc(standard_matches()))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "id,home,away\na01-01,3,2\na01-02,0,0\na01-03,5,1\n")
        self.assertIn("pendientes: 5", result.stderr)

    def test_US034_matches_dict_indexed_by_string_is_accepted(self) -> None:
        document = rfef_doc(standard_matches())
        day = document["2026-10-17"]
        day["matches"] = {str(i): m for i, m in enumerate(day["matches"])}
        self.assertEqual(self.fetch(document).stdout.count("\n"), 4)

    def test_US034_out_writes_the_csv_to_a_file_and_not_to_stdout(self) -> None:
        target = os.path.join(self.tmp, "out.csv")
        result = self.fetch(rfef_doc(standard_matches()), "--out", target)
        self.assertEqual(result.stdout, "")
        with open(target, encoding="utf-8") as handle:
            self.assertTrue(handle.read().startswith("id,home,away\na01-01,3,2"))

    def test_US034_pair_matching_ignores_position_and_name_whitespace(self) -> None:
        matches = list(reversed(standard_matches()))
        result = self.fetch(rfef_doc(matches))
        self.assertEqual(result.stdout, "id,home,away\na01-01,3,2\na01-02,0,0\na01-03,5,1\n")


class PrettyTests(FetchTestCase):
    LINES = ["a01-01  Alfa 3-2 Beta", "a01-02  Gamma 0-0 Delta", "a01-03  Epsilon 5-1 Zeta"]

    def named_feed(self, drop: Optional[str] = None) -> None:
        season = feed_season([feed_match(i) for i in range(1, 9)])
        names = ["Alfa", "Beta", "Gamma", "Delta", "Epsilon", "Zeta"]
        for team, name in zip(season["teams"], names):
            team["name"] = name
        if drop:
            season["teams"] = [t for t in season["teams"] if t["id"] != drop]
        support.build_public(self.feed, season)

    def test_US039_one_readable_line_per_finished_match_with_feed_names(self) -> None:
        self.named_feed()
        result = self.fetch(rfef_doc(standard_matches()), "--pretty")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), self.LINES)
        self.assertNotIn("Local", result.stdout)

    def test_US039_lists_the_same_matches_as_the_csv_in_the_same_order(self) -> None:
        self.named_feed()
        matches = list(reversed(standard_matches()))
        csv_rows = [line.split(",")[0] for line in self.fetch(rfef_doc(matches)).stdout.splitlines()[1:]]
        pretty = self.fetch(rfef_doc(matches), "--pretty").stdout.splitlines()
        self.assertEqual([line.split("  ")[0] for line in pretty], csv_rows)
        self.assertEqual(len(pretty), 3)

    def test_US039_default_output_is_still_the_csv(self) -> None:
        self.assertEqual(self.fetch(rfef_doc(standard_matches())).stdout, "id,home,away\na01-01,3,2\na01-02,0,0\na01-03,5,1\n")

    def test_US039_with_out_the_file_has_the_csv_and_stdout_only_the_lines(self) -> None:
        self.named_feed()
        target = os.path.join(self.tmp, "out.csv")
        result = self.fetch(rfef_doc(standard_matches()), "--pretty", "--out", target)
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "id,home,away\na01-01,3,2\na01-02,0,0\na01-03,5,1\n")
        self.assertEqual(result.stdout.splitlines(), self.LINES)

    def test_US039_report_stays_on_stderr_and_exit_code_is_the_same(self) -> None:
        self.named_feed()
        plain = self.fetch(rfef_doc(standard_matches()))
        pretty = self.fetch(rfef_doc(standard_matches()), "--pretty")
        self.assertEqual(pretty.returncode, plain.returncode)
        self.assertIn("pendientes: 5", pretty.stderr)
        self.assertNotIn("informe", pretty.stdout)

    def test_US039_anomaly_unknown_alias_and_missing_match_fail_like_without_pretty(self) -> None:
        anomalous = standard_matches()
        anomalous[0] = rfef_match(1, ("26", "1"))
        unaliased = standard_matches()
        unaliased[0] = rfef_match(1, ("3", "2"), datateam1="999")
        for matches in (anomalous, unaliased):
            plain = self.fetch(rfef_doc(matches))
            pretty = self.fetch(rfef_doc(matches), "--pretty")
            self.assertNotEqual(plain.returncode, 0)
            self.assertEqual(pretty.returncode, plain.returncode)
            self.assertEqual(pretty.stdout, "")
        self.write_feed([feed_match(i) for i in range(2, 9)])
        plain = self.fetch(rfef_doc(standard_matches()))
        pretty = self.fetch(rfef_doc(standard_matches()), "--pretty")
        self.assertEqual((pretty.returncode, pretty.stdout), (plain.returncode, ""))
        self.assertEqual(pretty.returncode, tool.EXIT_ERROR)

    def test_US039_team_missing_from_the_feed_names_the_team_id_and_prints_nothing(self) -> None:
        self.named_feed(drop="t02")
        result = self.fetch(rfef_doc(standard_matches()), "--pretty")
        self.assertEqual(result.returncode, tool.EXIT_ERROR)
        self.assertEqual(result.stdout, "")
        self.assertIn("t02", result.stderr)

    def test_US039_from_file_makes_no_request_and_gives_the_same_lines(self) -> None:
        self.named_feed()
        document = rfef_doc(standard_matches())
        online = self.invoke(["--pretty"], FakeTransport(data=(200, json.dumps(document).encode("utf-8"))))
        offline = self.fetch(document, "--pretty")  # el transporte de esta ruta falla si se abre
        self.assertEqual(online.stdout, offline.stdout)
        self.assertEqual(offline.stdout.splitlines(), self.LINES)


class NeverInventsScoreTests(FetchTestCase):
    def blank_cases(self) -> List[Dict[str, Any]]:
        return [rfef_match(4, None, "Aplazado"), rfef_match(5, None, "Suspendido"), rfef_match(6, ("1", ""), "Finalizado"),
                rfef_match(7, ("2", "2"), "Sin comenzar", day_hour="", no_hour="1"), rfef_match(8, ("", ""), "Finalizado")]

    def test_US034_postponed_suspended_no_time_single_number_leave_the_score_empty(self) -> None:
        matches = [rfef_match(i, ("1", "0")) for i in (1, 2, 3)] + self.blank_cases()
        result = self.fetch(rfef_doc(matches))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[1:], ["a01-01,1,0", "a01-02,1,0", "a01-03,1,0"])
        self.assertIn("estado RFEF no reconocido 'Aplazado'", result.stderr)

    def test_US034_goals_out_of_range_are_an_anomaly_and_no_csv(self) -> None:
        for value in ("26", "-1"):
            matches = standard_matches()
            matches[0] = rfef_match(1, (value, "1"))
            result = self.fetch(rfef_doc(matches))
            self.assertEqual(result.returncode, tool.EXIT_ANOMALY)
            self.assertEqual(result.stdout, "")
            self.assertIn("goles-fuera-de-rango", result.stderr)
            self.assertIn(value, result.stderr)

    def test_US034_boundary_goals_0_and_25_are_valid(self) -> None:
        matches = standard_matches()
        matches[0] = rfef_match(1, ("25", "0"))
        self.assertIn("a01-01,25,0", self.fetch(rfef_doc(matches)).stdout)


class MatchingTests(FetchTestCase):
    def test_US034_unknown_team_names_it_and_generates_no_csv(self) -> None:
        matches = standard_matches()
        matches[0] = rfef_match(1, ("3", "2"), datateam1="999", team_local="Club  Nuevo ")
        result = self.fetch(rfef_doc(matches))
        self.assertEqual(result.returncode, tool.EXIT_ERROR)
        self.assertEqual(result.stdout, "")
        self.assertIn("datateam 999 (Club Nuevo)", result.stderr)

    def test_US034_match_not_in_feed_is_an_error_and_no_csv(self) -> None:
        self.write_feed([feed_match(i) for i in range(2, 9)] + [dict(feed_match(1), homeTeamId="t01", awayTeamId="t03")])
        result = self.fetch(rfef_doc(standard_matches()))
        self.assertEqual(result.returncode, tool.EXIT_ERROR)
        self.assertEqual(result.stdout, "")
        self.assertIn("partido no encontrado en el feed", result.stderr)
        self.assertIn("t01 - t02", result.stderr)

    def test_US034_round_missing_in_feed_is_an_error(self) -> None:
        result = self.invoke(["--round", "2", "--from-file", self.write_response(rfef_doc(standard_matches()))])
        self.assertEqual(result.returncode, tool.EXIT_ERROR)
        self.assertIn("no tiene la jornada 2", result.stderr)


class SanityTests(FetchTestCase):
    def assert_anomaly(self, document: Any, rule: str, expected: str, obtained: str) -> None:
        result = self.fetch(document)
        self.assertEqual(result.returncode, tool.EXIT_ANOMALY, result.stderr)
        self.assertEqual(result.stdout, "")
        line = [l for l in result.stderr.splitlines() if "[%s]" % rule in l]
        self.assertTrue(line, result.stderr)
        self.assertIn("esperado=%s" % expected, line[0])
        self.assertIn("obtenido=%s" % obtained, line[0])

    def test_US034_wrong_league_group_round_or_year(self) -> None:
        for key, value, rule, expected in (("league_id", "1298", "league_id", "1297"), ("group_code", "2", "group_code", "1"),
                                           ("year", "2026", "year", "2027"), ("category", "Liga : Jornada 7", "jornada", "1")):
            matches = standard_matches()
            matches[3][key] = value
            obtained = "7" if key == "category" else value
            self.assert_anomaly(rfef_doc(matches), rule, expected, obtained)

    def test_US034_fewer_than_16_teams_or_8_matches(self) -> None:
        self.assert_anomaly(rfef_doc(standard_matches()[:7]), "partidos", "8", "7")
        self.assert_anomaly(rfef_doc(standard_matches()[:7]), "equipos", "16", "14")

    def test_US034_non_empty_descansa(self) -> None:
        self.assert_anomaly(rfef_doc(standard_matches(), resting=["Club X"]), "descansa", "vacio", '["Club X"]')

    def test_US034_US042_future_dated_match_with_goals_is_an_anomaly(self) -> None:
        matches = standard_matches()
        matches[7] = rfef_match(8, ("1", "0"), "Sin comenzar")
        document = rfef_doc(matches[:7], "2026-10-17")
        document["2026-10-25"] = {"date": "Domingo", "descansa": [], "matches": [matches[7]]}
        self.assert_anomaly(document, "fecha-futura", "<= " + TODAY, "2026-10-25")

    def test_US034_anomaly_does_not_depend_on_alias_or_feed_errors(self) -> None:
        matches = standard_matches()
        matches[0]["datateam1"] = "999"
        matches[1]["league_id"] = "1298"
        result = self.fetch(rfef_doc(matches))
        self.assertEqual(result.returncode, tool.EXIT_ANOMALY)


class FeedComparisonTests(FetchTestCase):
    def test_US034_published_finished_with_different_score_is_an_anomaly(self) -> None:
        self.write_feed([feed_match(1, "finished", (3, 2))] + [feed_match(i) for i in range(2, 9)])
        matches = standard_matches()
        matches[0] = rfef_match(1, ("3", "3"))
        result = self.fetch(rfef_doc(matches))
        self.assertEqual(result.returncode, tool.EXIT_ANOMALY)
        self.assertEqual(result.stdout, "")
        self.assertIn("[finished-distinto] a01-01", result.stderr)
        self.assertIn("esperado=3-2 (feed)", result.stderr)
        self.assertIn("obtenido=3-3 (RFEF)", result.stderr)

    def test_US034_published_finished_with_same_score_is_not_in_the_csv(self) -> None:
        self.write_feed([feed_match(1, "finished", (3, 2))] + [feed_match(i) for i in range(2, 9)])
        result = self.fetch(rfef_doc(standard_matches()))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["id,home,away", "a01-02,0,0", "a01-03,5,1"])
        self.assertIn("ya publicados iguales: 1", result.stderr)

    def test_US034_date_time_and_status_differences_are_only_reported(self) -> None:
        # 11:00Z en octubre (CEST) = 13:00 en Madrid: la hora coincide; el partido 2 difiere en fecha y hora.
        self.write_feed([feed_match(1, time="2026-10-17T11:00:00Z"), feed_match(2, date="2026-10-18", time="2026-10-18T10:00:00Z")]
                        + [feed_match(i) for i in range(3, 9)])
        baseline = self.fetch(rfef_doc(standard_matches()))
        self.write_feed([feed_match(i) for i in range(1, 9)])
        plain = self.fetch(rfef_doc(standard_matches()))
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        self.assertEqual(baseline.stdout, plain.stdout)
        self.assertIn("a01-02: fecha feed=2026-10-18, RFEF=2026-10-17", baseline.stderr)
        self.assertIn("a01-02: hora feed=12:00, RFEF=13:00", baseline.stderr)
        self.assertNotIn("a01-01: hora", baseline.stderr)

    def test_US034_feed_finished_but_rfef_without_final_score_is_reported(self) -> None:
        self.write_feed([feed_match(4, "finished", (1, 1))] + [feed_match(i) for i in (1, 2, 3, 5, 6, 7, 8)])
        result = self.fetch(rfef_doc(standard_matches()))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("a01-04: estado feed=finished, RFEF=Sin comenzar sin marcador final", result.stderr)


class ParseFailureTests(FetchTestCase):
    def test_US034_unexpected_structure_is_an_explicit_error_without_report(self) -> None:
        for body in (b"<html>no es json</html>", b"[]", b"{}", json.dumps({"2026-10-17": {"matches": []}}).encode(),
                     json.dumps({"no-fecha": {"descansa": [], "matches": []}}).encode(),
                     json.dumps(rfef_doc([{"team_local": "x"}])).encode()):
            result = self.fetch(body)
            self.assertEqual(result.returncode, tool.EXIT_ERROR, body)
            self.assertEqual(result.stdout, "")
            self.assertTrue(result.stderr.startswith("FALLO:"), result.stderr)
            self.assertNotIn("informe", result.stderr)


class NetworkTests(FetchTestCase):
    def good_transport(self) -> FakeTransport:
        return FakeTransport(data=(200, json.dumps(rfef_doc(standard_matches())).encode("utf-8")))

    def test_US034_robots_is_read_first_then_two_posts_with_the_expected_form(self) -> None:
        transport = self.good_transport()
        result = self.invoke([], transport)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([(m, u[len(tool.BASE_URL):]) for m, u, _ in transport.calls],
                         [("GET", tool.ROBOTS_PATH), ("POST", tool.ENDPOINT_PATH), ("POST", tool.ENDPOINT_PATH)])
        self.assertEqual(transport.calls[1][2], b"category=25&league=1297&round=1&action=competicion&year=2027&group=1")
        self.assertEqual(result.stdout.splitlines()[0], "id,home,away")

    def test_US034_clausura_uses_its_league_and_group(self) -> None:
        self.assertIn(b"league=1298", tool.request_body("clausura", 3, "2027"))
        self.assertIn(b"group=2", tool.request_body("clausura", 3, "2027"))

    def test_US034_robots_veto_aborts_before_the_request(self) -> None:
        transport = FakeTransport(robots=(200, b"User-agent: *\nDisallow: /futsal_xhr/\n"))
        result = self.invoke([], transport)
        self.assertEqual(result.returncode, tool.EXIT_STOPPED)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("robots.txt veta", result.stderr)

    def test_US034_stops_on_403_429_and_5xx_without_csv(self) -> None:
        for status in (403, 429, 500, 503):
            self.enable_switch()
            transport = FakeTransport(data=(status, b""))
            result = self.invoke([], transport)
            self.assertEqual(result.returncode, tool.EXIT_STOPPED, status)
            self.assertEqual(result.stdout, "")
            self.assertEqual(len(transport.calls), 2, "sin reintentos ni segunda lectura")
        self.enable_switch()
        blocked_robots = FakeTransport(robots=(429, b""))
        self.assertEqual(self.invoke([], blocked_robots).returncode, tool.EXIT_STOPPED)
        self.assertEqual(len(blocked_robots.calls), 1)

    def test_US034_waits_the_request_interval_between_robots_and_data(self) -> None:
        waits = []  # type: List[float]
        tool.download(self.good_transport(), "apertura", 1, "2027", waits.append)
        self.assertEqual(waits, [tool.REQUEST_INTERVAL_SECONDS])
        self.assertGreaterEqual(tool.REQUEST_INTERVAL_SECONDS, 10)

    def test_US034_user_agent_is_identifiable_non_personal_and_no_cookies_or_redirects(self) -> None:
        self.assertIn("non-personal", tool.USER_AGENT)
        self.assertNotIn("@", tool.USER_AGENT)
        with open(os.path.join(support.SCRIPTS, "fetch_rfef_results.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("HTTPCookieProcessor", source)
        self.assertIsNone(tool._NoRedirect().redirect_request(None, None, 302, "", {}, ""))


class FromFileTests(FetchTestCase):
    def test_US034_from_file_makes_no_request_and_gives_the_same_csv(self) -> None:
        document = rfef_doc(standard_matches())
        online = self.invoke([], FakeTransport(data=(200, json.dumps(document).encode("utf-8"))))
        offline = self.fetch(document)  # el transporte de esta ruta falla si se abre
        self.assertEqual(offline.returncode, 0, offline.stderr)
        self.assertEqual(online.stdout, offline.stdout)
        self.assertEqual(len(os.listdir(self.raw_dir)), 2, "solo las 2 lecturas en linea guardan copia; --from-file no")


class TraceabilityTests(FetchTestCase):
    def test_US034_report_has_url_utc_instant_and_sha256(self) -> None:
        body = json.dumps(rfef_doc(standard_matches())).encode("utf-8")
        result = self.invoke([], FakeTransport(data=(200, body)))
        self.assertIn("fuente: https://futsal.rfef.es/futsal_xhr/get_matchs_round", result.stderr)
        self.assertIn("instante UTC: 2026-10-20T09:30:10Z", result.stderr)
        self.assertIn("SHA-256: " + hashlib.sha256(body).hexdigest(), result.stderr)

    def test_US034_raw_response_is_saved_outside_the_repo_only(self) -> None:
        body = json.dumps(rfef_doc(standard_matches())).encode("utf-8")
        self.invoke([], FakeTransport(data=(200, body)))
        saved = os.listdir(self.raw_dir)
        self.assertEqual(len(saved), 2, "una copia por lectura")
        for name in saved:
            with open(os.path.join(self.raw_dir, name), "rb") as handle:
                self.assertEqual(handle.read(), body)
        self.assertEqual(os.listdir(self.feed).count("local-data"), 0)

    def test_US034_default_raw_dir_is_outside_the_feed_repository(self) -> None:
        default = os.path.realpath(os.path.join(FEED, tool.RAW_DIR_RELATIVE))
        self.assertFalse(default.startswith(os.path.realpath(FEED) + os.sep))

    def test_US034_raw_dir_inside_the_repo_is_refused(self) -> None:
        body = json.dumps(rfef_doc(standard_matches())).encode("utf-8")
        result = self.invoke(["--raw-dir", os.path.join(self.feed, "raw")], FakeTransport(data=(200, body)))
        self.assertEqual(result.returncode, tool.EXIT_ERROR)
        self.assertEqual(result.stdout, "")
        self.assertFalse(os.path.exists(os.path.join(self.feed, "raw")))


def body_of(matches: List[Dict[str, Any]], **kwargs: Any) -> bytes:
    return json.dumps(rfef_doc(matches, **kwargs)).encode("utf-8")


class DoubleReadTests(FetchTestCase):
    """US-037: dos lecturas separadas >= 15 min que deben coincidir."""

    def run_pair(self, first: bytes, second: bytes) -> Tuple[Any, FakeTransport]:
        clock = Clock()
        transport = FakeTransport(data=[(200, first), (200, second)], clock=clock)
        return self.invoke([], transport), transport

    def changed(self, mutate: Any) -> Tuple[Any, FakeTransport]:
        altered = standard_matches()
        mutate(altered)
        return self.run_pair(body_of(standard_matches()), body_of(altered))

    def assert_unstable(self, result: Any, expected: str, obtained: str) -> None:
        self.assertEqual(result.returncode, tool.EXIT_ANOMALY, result.stderr)
        self.assertEqual(result.stdout, "", "no se genera CSV")
        self.assertIn("lecturas-distintas", result.stderr)
        self.assertIn("esperado=%s obtenido=%s" % (expected, obtained), result.stderr)

    def test_US037_identical_reads_continue_with_the_second_as_base(self) -> None:
        first = body_of(standard_matches())
        second = json.dumps(rfef_doc(standard_matches()), indent=2).encode("utf-8")  # otros bytes, mismos campos
        result, _ = self.run_pair(first, second)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 4)

    def test_US037_changed_score_names_the_match_and_both_values(self) -> None:
        result, _ = self.changed(lambda m: m.__setitem__(0, rfef_match(1, ("3", "3"))))
        self.assert_unstable(result, "3-2", "3-3")
        self.assertIn("Local t01 - Visit t02", result.stderr)

    def test_US037_changed_status_is_an_anomaly(self) -> None:
        result, _ = self.changed(lambda m: m.__setitem__(4, rfef_match(5, None, "Aplazado")))
        self.assert_unstable(result, "Sin comenzar", "Aplazado")

    def test_US037_changed_date_is_an_anomaly(self) -> None:
        first, second = standard_matches(), standard_matches()
        document = rfef_doc(second[:7], day="2026-10-17")
        document.update(rfef_doc(second[7:], day="2026-10-18"))
        result, _ = self.run_pair(body_of(first), json.dumps(document).encode("utf-8"))
        self.assert_unstable(result, "2026-10-17", "2026-10-18")

    def test_US037_changed_time_is_an_anomaly(self) -> None:
        result, _ = self.changed(lambda m: m.__setitem__(0, rfef_match(1, ("3", "2"), day_hour="14:00")))
        self.assert_unstable(result, "13:00", "14:00")

    def test_US037_changed_pair_is_an_anomaly(self) -> None:
        def swap(matches: List[Dict[str, Any]]) -> None:
            home, away = PAIRS[0]
            matches[0] = rfef_match(1, ("3", "2"), team_local="Visit %s" % away, datateam1=DATATEAM[away],
                                    team_visit="Local %s" % home, datateam2=DATATEAM[home])
        result, _ = self.changed(swap)
        self.assertEqual(result.returncode, tool.EXIT_ANOMALY, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.count("solo en la"), 2)

    def test_US037_list_order_and_irrelevant_fields_are_not_an_anomaly(self) -> None:
        reordered = list(reversed(standard_matches()))
        for raw in reordered:
            raw["id"] = "x" + raw["id"]
            raw["status"] = "otro"
        result, _ = self.run_pair(body_of(standard_matches()), body_of(reordered))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("lecturas-distintas", result.stderr)

    def test_US037_dict_indexed_and_list_forms_count_as_equal(self) -> None:
        indexed = {"2026-10-17": {"date": "Sabado", "descansa": [], "matches": {str(n): m for n, m in enumerate(standard_matches())}}}
        result, _ = self.run_pair(body_of(standard_matches()), json.dumps(indexed).encode("utf-8"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_US037_second_read_waits_at_least_15_minutes(self) -> None:
        body = body_of(standard_matches())
        clock = Clock()
        transport = FakeTransport(data=(200, body), clock=clock)
        self.assertEqual(self.invoke([], transport).returncode, 0)
        posts = [t for call, t in zip(transport.calls, transport.call_times) if call[0] == "POST"]
        self.assertGreaterEqual((posts[1] - posts[0]).total_seconds(), 15 * 60)
        self.assertEqual(clock.sleeps[0], tool.REQUEST_INTERVAL_SECONDS)
        self.assertEqual(clock.sleeps[1], 15 * 60)

    def test_US037_second_read_is_refused_when_the_clock_does_not_confirm_the_gap(self) -> None:
        body = body_of(standard_matches())
        clock = Clock()
        clock.sleep = lambda seconds: None  # type: ignore[assignment]  # la espera no avanza el reloj
        transport = FakeTransport(data=(200, body), clock=clock)
        result = self.invoke([], transport)
        self.assertEqual(result.returncode, tool.EXIT_ERROR)
        self.assertEqual(result.stdout, "")
        self.assertEqual(sum(1 for call in transport.calls if call[0] == "POST"), 1, "nunca lee antes")

    def test_US037_no_wait_is_needed_if_enough_time_already_passed(self) -> None:
        clock = Clock()
        first_at = clock.current - datetime.timedelta(minutes=20)
        transport = FakeTransport(data=(200, body_of(standard_matches())), clock=clock)
        tool.download_again(transport, "apertura", 1, "2027", clock.sleep, clock.now, first_at)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(len(transport.calls), 1)

    def test_US037_budget_is_robots_plus_two_posts_and_spaced_by_10_seconds(self) -> None:
        clock = Clock()
        transport = FakeTransport(data=(200, body_of(standard_matches())), clock=clock)
        self.assertEqual(self.invoke([], transport).returncode, 0)
        self.assertEqual([c[0] for c in transport.calls], ["GET", "POST", "POST"])
        gaps = [(b - a).total_seconds() for a, b in zip(transport.call_times, transport.call_times[1:])]
        self.assertTrue(all(gap >= 10 for gap in gaps), gaps)

    def test_US037_stops_on_403_429_5xx_in_the_second_read_without_retry_or_csv(self) -> None:
        good = (200, body_of(standard_matches()))
        for status in (403, 429, 500, 503):
            self.enable_switch()
            transport = FakeTransport(data=[good, (status, b"")], clock=Clock())
            result = self.invoke([], transport)
            self.assertEqual(result.returncode, tool.EXIT_STOPPED, status)
            self.assertEqual(result.stdout, "")
            self.assertEqual(len(transport.calls), 3, "sin reintentos")

    def test_US037_invalid_first_read_skips_the_wait_and_the_second_request(self) -> None:
        clock = Clock()
        transport = FakeTransport(data=(200, b"no es json"), clock=clock)
        result = self.invoke([], transport)
        self.assertEqual(result.returncode, tool.EXIT_ERROR)
        self.assertEqual(clock.sleeps, [tool.REQUEST_INTERVAL_SECONDS])
        self.assertEqual(len(transport.calls), 2)

    def test_US037_report_has_instant_and_sha256_of_each_read_even_if_bytes_differ(self) -> None:
        first = body_of(standard_matches())
        second = json.dumps(rfef_doc(standard_matches()), indent=1).encode("utf-8")
        result, _ = self.run_pair(first, second)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("lectura 1 instante UTC: 2026-10-20T09:30:10Z", result.stderr)
        self.assertIn("lectura 1 SHA-256: " + hashlib.sha256(first).hexdigest(), result.stderr)
        self.assertIn("lectura 2 instante UTC: 2026-10-20T09:45:10Z", result.stderr)
        self.assertIn("lectura 2 SHA-256: " + hashlib.sha256(second).hexdigest(), result.stderr)
        self.assertNotEqual(hashlib.sha256(first).hexdigest(), hashlib.sha256(second).hexdigest())

    def test_US037_stability_check_does_not_apply_to_from_file(self) -> None:
        result = self.fetch(rfef_doc(standard_matches()))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("lectura 2", result.stderr)


class AliasTableTests(support.TempDirTestCase):
    def test_US034_versioned_table_covers_the_16_real_teams_without_sponsor_names(self) -> None:
        aliases = tool.load_aliases(FEED)
        season = tool.round_sheet.load_season(FEED)
        self.assertEqual(sorted(aliases.values()), sorted(team["id"] for team in season["teams"]))
        self.assertEqual(len(aliases), 16)
        self.assertTrue(all(key.isdigit() for key in aliases))
        with open(os.path.join(FEED, tool.ALIASES_PATH), encoding="utf-8") as handle:
            text = handle.read().lower()
        for sponsor in ("wanapix ", "catgas", "jimbee cartagena costa", "elpozo murcia costa", "viña albali"):
            self.assertNotIn(sponsor, text)

    def test_US034_wrapper_script_runs_with_python_3_9_syntax_and_shows_usage(self) -> None:
        result = run_script([os.path.join(FEED, "scripts", "fetch-rfef-results"), "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--from-file", result.stdout)
