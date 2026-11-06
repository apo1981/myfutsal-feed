"""US-035: verificacion cruzada con get_table_round y pares contra el feed. Tablas sinteticas escritas a mano, sin red."""
from __future__ import annotations

import copy
import json
import unittest
from typing import Any, Dict, List, Optional, Tuple

import support  # debe ir antes que cross_check: anade scripts/ al sys.path
import cross_check as check

LEAGUE = "1297"
TEAMS = ["aaa", "bbb", "ccc", "ddd"]
ALIASES = {"1": "aaa", "2": "bbb", "3": "ccc", "4": "ddd"}


def match(identifier: str, home: str, away: str, score: Optional[Tuple[int, int]] = None, status: str = "") -> Dict[str, Any]:
    return {"id": identifier, "homeTeamId": home, "awayTeamId": away, "status": status or ("finished" if score else "scheduled"),
            "kickoff": {"date": "2026-10-17", "time": None},
            "score": {"home": score[0], "away": score[1]} if score else None, "decidedBy": None, "note": None}


def season() -> Dict[str, Any]:
    """Apertura: J1 aaa 2-1 bbb, ccc 0-0 ddd; J2 bbb 3-1 ccc, ddd 1-4 aaa (pendientes los de la J2); J3 ya jugada (futura a N=2).
    Clausura: un finished que no debe contar."""
    def round_(number: int, matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"id": "apertura-%02d" % number, "number": number, "name": "Jornada %d" % number, "matches": matches}
    return {"seasonId": "2026-27", "teams": [{"id": t} for t in TEAMS], "phases": [
        {"id": "apertura", "kind": "league", "rounds": [
            round_(1, [match("a01-1", "aaa", "bbb", (2, 1)), match("a01-2", "ccc", "ddd", (0, 0))]),
            round_(2, [match("a02-1", "bbb", "ccc"), match("a02-2", "ddd", "aaa")]),
            round_(3, [match("a03-1", "aaa", "ccc", (9, 9))])]},
        {"id": "clausura", "kind": "league", "rounds": [
            {"id": "clausura-01", "number": 1, "name": "Jornada 1", "matches": [match("c01-1", "aaa", "ddd", (7, 0))]}]}]}


BATCH = [("a02-1", 3, 1), ("a02-2", 1, 4)]


def table_body(computed: Dict[str, Any], mutate: Any = None) -> bytes:
    """Tabla en el formato de la RFEF construida a partir del calculo; `mutate(rows)` la estropea."""
    rows = []
    for datateam, team in ALIASES.items():
        standing = computed.get(team) or check._empty_standing()
        row = {"id": datateam, "pj": 2, "pt": standing["pt"], "total": {}, "home": dict(standing["home"]), "away": dict(standing["away"])}
        rows.append(row)
    if mutate:
        mutate(rows)
    return json.dumps({"table": {LEAGUE: rows}}).encode("utf-8")


def pairs_of(batch_season: Dict[str, Any], number: int = 2) -> List[Tuple[str, str]]:
    round_ = [r for r in batch_season["phases"][0]["rounds"] if r["number"] == number][0]
    return [(m["homeTeamId"], m["awayTeamId"]) for m in round_["matches"]]


class CrossCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = season()
        self.applied = check.apply_batch(self.base, BATCH)
        self.computed = check.compute_standings(self.applied, "apertura", 2)

    def verify(self, body: bytes, batch: Optional[List[Tuple[str, int, int]]] = None, pairs: Optional[List[Tuple[str, str]]] = None,
               applied: Optional[Dict[str, Any]] = None) -> List[str]:
        return check.verify(applied or self.applied, "apertura", 2, LEAGUE, body, ALIASES, pairs if pairs is not None else pairs_of(self.applied))

    def test_US035_table_matches(self) -> None:
        self.assertEqual(self.verify(table_body(self.computed)), [])
        aaa = self.computed["aaa"]  # 2-1 en casa y 4-1 fuera (J1 y J2): pg 1+1, gf 2 / 4, gc 1 / 1
        self.assertEqual(aaa["home"], {"pg": 1, "pe": 0, "pp": 0, "gf": 2, "gc": 1})
        self.assertEqual(aaa["away"], {"pg": 1, "pe": 0, "pp": 0, "gf": 4, "gc": 1})
        self.assertEqual(aaa["pt"], 6)
        self.assertEqual(self.computed["ccc"]["pt"], 1 + 0)  # 0-0 en casa; 1-3 fuera (pp)
        self.assertEqual(self.computed["ccc"]["away"]["pp"], 1)

    def test_US035_points_difference_is_anomaly(self) -> None:
        def mutate(rows: List[Dict[str, Any]]) -> None:
            rows[0]["pt"] = 7
        self.assertEqual(self.verify(table_body(self.computed, mutate)), ["anomalia [tabla] aaa pt: esperado=6 obtenido=7"])

    def test_US035_difference_in_one_team_names_team_field_expected_and_obtained(self) -> None:
        def mutate(rows: List[Dict[str, Any]]) -> None:
            rows[1]["away"]["gf"] += 1  # bbb fuera
        self.assertEqual(self.verify(table_body(self.computed, mutate)), ["anomalia [tabla] bbb fuera gf: esperado=%d obtenido=%d" % (
            self.computed["bbb"]["away"]["gf"], self.computed["bbb"]["away"]["gf"] + 1)])

    def test_US035_crossed_home_away_mapping_is_anomaly_even_if_pair_matches(self) -> None:
        swapped = [("a02-1", 1, 3), ("a02-2", 4, 1)]  # r_local y r_visit intercambiados; el par sigue siendo el del feed
        applied = check.apply_batch(self.base, swapped)
        found = self.verify(table_body(self.computed), applied=applied)
        self.assertEqual(check.pair_anomalies(pairs_of(applied), pairs_of(self.base)), [])
        self.assertTrue(any("bbb casa gf: esperado=1 obtenido=3" in line for line in found), found)
        self.assertTrue(any("ddd casa gf: esperado=4 obtenido=1" in line for line in found), found)

    def test_US035_inverted_pair_is_anomaly_and_never_rotated(self) -> None:
        rfef = [("ccc", "bbb"), ("ddd", "aaa")]
        found = self.verify(table_body(self.computed), pairs=rfef)
        self.assertEqual(found, ["anomalia [par-invertido] ccc - bbb: esperado=bbb - ccc (feed) obtenido=ccc - bbb (RFEF)"])
        self.assertEqual(rfef, [("ccc", "bbb"), ("ddd", "aaa")])
        self.assertEqual(pairs_of(self.applied), [("bbb", "ccc"), ("ddd", "aaa")])  # el feed tampoco se gira

    def test_US035_extra_pair_is_anomaly(self) -> None:
        found = self.verify(table_body(self.computed), pairs=pairs_of(self.applied) + [("aaa", "ccc")])
        self.assertEqual(found, ["anomalia [par-de-mas] aaa - ccc: esperado=par del feed obtenido=solo en la RFEF"])

    def test_US035_missing_pair_is_anomaly(self) -> None:
        found = self.verify(table_body(self.computed), pairs=[("bbb", "ccc")])
        self.assertEqual(found, ["anomalia [par-de-menos] ddd - aaa: esperado=par de la RFEF obtenido=solo en el feed"])

    def test_US035_accumulated_calculation_includes_all_finished_up_to_round_n(self) -> None:
        up_to_one = check.compute_standings(self.applied, "apertura", 1)
        self.assertEqual(up_to_one["aaa"]["pt"], 3)
        self.assertNotIn("ccc", {t for t, s in up_to_one.items() if s["away"]["pp"]})
        # N=2: incluye la J1 y la J2; no la J3 (>N) ni la fase de Clausura
        self.assertEqual(self.computed["aaa"]["home"]["gf"], 2)
        self.assertEqual(self.computed["aaa"]["away"]["gf"], 4)
        self.assertEqual(sum(s["home"]["pg"] + s["home"]["pe"] + s["home"]["pp"] for s in self.computed.values()), 4)
        self.assertEqual(check.compute_standings(self.applied, "apertura", 3)["aaa"]["home"]["gf"], 11)

    def test_US035_batch_is_applied_in_memory_only(self) -> None:
        self.assertEqual(self.base["phases"][0]["rounds"][1]["matches"][0]["status"], "scheduled")
        self.assertEqual(self.applied["phases"][0]["rounds"][1]["matches"][0]["score"], {"home": 3, "away": 1})

    def test_US035_unavailable_table_fails_without_degraded_mode(self) -> None:
        good = json.loads(table_body(self.computed))
        cases = {
            "no es JSON": b"<html>",
            "sin table": b'{"error": "x"}',
            "torneo no identificable": json.dumps({"table": {"9999": good["table"][LEAGUE]}}).encode(),
            "tabla vacia": json.dumps({"table": {LEAGUE: []}}).encode(),
            "sin desglose": json.dumps({"table": {LEAGUE: [{"id": "1", "pt": 3}]}}).encode(),
            "valor no entero": table_body(self.computed, lambda rows: rows[0]["home"].update(gf="x")),
            "id repetido": table_body(self.computed, lambda rows: rows.append(copy.deepcopy(rows[0]))),
        }
        for name, body in cases.items():
            with self.subTest(name):
                with self.assertRaises(check.TableUnavailableError):
                    self.verify(body)

    def test_US035_unknown_phase_or_round_fails(self) -> None:
        with self.assertRaises(check.TableUnavailableError):
            check.verify(self.applied, "apertura", 9, LEAGUE, table_body(self.computed), ALIASES, [])
        with self.assertRaises(check.TableUnavailableError):
            check.verify(self.applied, "otra", 2, LEAGUE, table_body(self.computed), ALIASES, [])

    def test_US035_table_team_without_alias_is_anomaly_naming_the_id(self) -> None:
        def mutate(rows: List[Dict[str, Any]]) -> None:
            rows.append({"id": "777", "pt": 0, "home": rows[0]["home"], "away": rows[0]["away"]})
        self.assertEqual(self.verify(table_body(self.computed, mutate)),
                         ["anomalia [tabla-equipo-sin-casar] id 777 de la tabla: esperado=id con alias obtenido=sin alias"])

    def test_US035_team_with_matches_missing_from_table_is_anomaly(self) -> None:
        body = table_body(self.computed, lambda rows: rows.pop())  # sin ddd
        self.assertEqual(self.verify(body), ["anomalia [tabla-equipo-ausente] ddd: esperado=presente en la tabla obtenido=ausente"])

    def test_US035_table_request_has_no_league_parameter(self) -> None:
        self.assertEqual(check.table_request_body(7, "2027"), b"category=25&round=7&action=competicion&year=2027")


if __name__ == "__main__":
    unittest.main()
