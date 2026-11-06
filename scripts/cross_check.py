"""Verificacion cruzada del lote con `get_table_round` y de los pares contra el feed (US-035; ADR-012 §1).

Modulo puro, solo biblioteca estandar, Python 3.9. Sin red ni ficheros: recibe la temporada del feed con el lote ya aplicado
en memoria, la respuesta de `POST /futsal_xhr/get_table_round` ya descargada (o leida con --from-file) y la tabla de alias,
y devuelve la lista de anomalias. La orquestacion, la espera y el interruptor son de US-036.
  - Calcula, con todos los partidos `finished` de la fase de jornadas <= N, por equipo y por separado en casa y fuera:
    pg, pe, pp, gf, gc, y pt = 3*pg + pe; y lo compara con la tabla de la jornada N.
  - Los pares (local, visitante) de la RFEF se comparan con los del feed: nunca se giran para corregirlos.
  - Sin tabla utilizable (JSON inesperado, torneo no identificable) lanza TableUnavailableError: no hay modo degradado.
Sin verificar [NV] (disparador 1 de ADR-012): Clausura y aplazados; si la tabla difiere sale como anomalia.
"""
from __future__ import annotations

import copy
import json
import urllib.parse
from typing import Any, Dict, Iterable, List, Tuple

CATEGORY = "25"
POINTS_PER_WIN, POINTS_PER_DRAW = 3, 1
SIDES = ("home", "away")
SIDE_LABELS = {"home": "casa", "away": "fuera"}
COUNTERS = ("pg", "pe", "pp", "gf", "gc")
FINISHED = "finished"

Pair = Tuple[str, str]
Counters = Dict[str, int]
Standing = Dict[str, Any]  # {"home": Counters, "away": Counters, "pt": int}


class TableUnavailableError(Exception):
    """La tabla no se pudo leer o no es la esperada: la verificacion falla y no se aplica nada."""


def anomaly(rule: str, subject: str, expected: Any, obtained: Any) -> str:
    """Mismo formato que las anomalias de fetch_rfef_results."""
    return "anomalia [%s] %s: esperado=%s obtenido=%s" % (rule, subject, expected, obtained)


# --------------------------------------------------------------------------
# Lote en memoria y calculo
# --------------------------------------------------------------------------

def apply_batch(season: Dict[str, Any], rows: Iterable[Tuple[str, int, int]]) -> Dict[str, Any]:
    """Copia de la temporada con las filas (id, home, away) del lote como `finished`. No toca la original."""
    applied = copy.deepcopy(season)
    wanted = {identifier: (home, away) for identifier, home, away in rows}
    for phase in applied["phases"]:
        for round_ in phase["rounds"]:
            for match in round_["matches"]:
                if match["id"] in wanted:
                    match["status"] = FINISHED
                    match["score"] = {"home": wanted[match["id"]][0], "away": wanted[match["id"]][1]}
    return applied


def _empty_standing() -> Standing:
    return {"home": {name: 0 for name in COUNTERS}, "away": {name: 0 for name in COUNTERS}, "pt": 0}


def compute_standings(season: Dict[str, Any], phase_id: str, round_number: int) -> Dict[str, Standing]:
    """Clasificacion acumulada: todos los `finished` con marcador de la fase en jornadas <= N (no solo la N)."""
    standings = {}  # type: Dict[str, Standing]
    phase = next((candidate for candidate in season["phases"] if candidate["id"] == phase_id), None)
    if phase is None:
        raise TableUnavailableError("el feed no tiene la fase %s" % phase_id)
    for round_ in phase["rounds"]:
        if round_["number"] > round_number:
            continue
        for match in round_["matches"]:
            score = match.get("score")
            if match["status"] != FINISHED or not score:
                continue
            for side, team_id, scored, conceded in (("home", match["homeTeamId"], score["home"], score["away"]),
                                                    ("away", match["awayTeamId"], score["away"], score["home"])):
                counters = standings.setdefault(team_id, _empty_standing())[side]
                counters["gf"] += scored
                counters["gc"] += conceded
                counters["pg" if scored > conceded else "pp" if scored < conceded else "pe"] += 1
    for standing in standings.values():
        standing["pt"] = sum(POINTS_PER_WIN * standing[side]["pg"] + POINTS_PER_DRAW * standing[side]["pe"] for side in SIDES)
    return standings


# --------------------------------------------------------------------------
# Tabla de la RFEF
# --------------------------------------------------------------------------

def table_request_body(round_number: int, year: str) -> bytes:
    """Cuerpo del POST: no hay parametro `league`; el torneo se identifica en la respuesta (ADR-012 [NV])."""
    fields = [("category", CATEGORY), ("round", str(round_number)), ("action", "competicion"), ("year", year)]
    return urllib.parse.urlencode(fields).encode("ascii")


def _integer(value: Any, what: str) -> int:
    text = str(value).strip()
    if isinstance(value, bool) or not text.lstrip("-").isdigit():
        raise TableUnavailableError("la tabla tiene %s no entero: %r" % (what, value))
    return int(text)


def parse_table(body: bytes, league_id: str) -> Dict[str, Standing]:
    """Filas de la tabla del torneo `league_id`, por id de la RFEF (= datateam). Lanza TableUnavailableError si no es la esperada."""
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise TableUnavailableError("la tabla no es JSON valido")
    if not isinstance(document, dict) or not isinstance(document.get("table"), dict):
        raise TableUnavailableError("la respuesta no tiene el objeto 'table'")
    tournaments = document["table"]
    if league_id not in tournaments:
        raise TableUnavailableError("torneo no identificable: 'table' no contiene el torneo %s (claves: %s)" % (
            league_id, ", ".join(sorted(tournaments)) or "ninguna"))
    entries = tournaments[league_id]
    if isinstance(entries, dict):
        entries = list(entries.values())
    if not isinstance(entries, list) or not entries:
        raise TableUnavailableError("la tabla del torneo %s esta vacia o no es una lista" % league_id)
    rows = {}  # type: Dict[str, Standing]
    for entry in entries:
        if not isinstance(entry, dict) or "id" not in entry or "pt" not in entry:
            raise TableUnavailableError("una fila de la tabla no tiene 'id' y 'pt'")
        team = str(entry["id"]).strip()
        row = {"pt": _integer(entry["pt"], "pt de %s" % team)}  # type: Standing
        for side in SIDES:
            counters = entry.get(side)
            if not isinstance(counters, dict):
                raise TableUnavailableError("la fila %s no tiene el desglose '%s'" % (team, side))
            if any(name not in counters for name in COUNTERS):
                raise TableUnavailableError("el desglose '%s' de %s no tiene %s" % (side, team, "/".join(COUNTERS)))
            row[side] = {name: _integer(counters[name], "%s.%s de %s" % (side, name, team)) for name in COUNTERS}
        if team in rows:
            raise TableUnavailableError("el id %s aparece repetido en la tabla" % team)
        rows[team] = row
    return rows


# --------------------------------------------------------------------------
# Comparaciones
# --------------------------------------------------------------------------

def table_anomalies(computed: Dict[str, Standing], table: Dict[str, Standing], aliases: Dict[str, str]) -> List[str]:
    """Una anomalia por cada campo distinto (equipo, campo, esperado = calculo del feed, obtenido = tabla) y por cada id sin alias."""
    found = []  # type: List[str]
    seen = set()
    for datateam in sorted(table):
        team = aliases.get(datateam)
        if team is None:
            found.append(anomaly("tabla-equipo-sin-casar", "id %s de la tabla" % datateam, "id con alias", "sin alias"))
            continue
        seen.add(team)
        expected, obtained = computed.get(team, _empty_standing()), table[datateam]
        for side in SIDES:
            for name in COUNTERS:
                if expected[side][name] != obtained[side][name]:
                    found.append(anomaly("tabla", "%s %s %s" % (team, SIDE_LABELS[side], name),
                                         expected[side][name], obtained[side][name]))
        if expected["pt"] != obtained["pt"]:
            found.append(anomaly("tabla", "%s pt" % team, expected["pt"], obtained["pt"]))
    for team in sorted(set(computed) - seen):
        found.append(anomaly("tabla-equipo-ausente", team, "presente en la tabla", "ausente"))
    return found


def pair_anomalies(rfef_pairs: List[Pair], feed_pairs: List[Pair]) -> List[str]:
    """Pares (local, visitante) de la RFEF frente a los del feed, ya como teamId. Invertido, de mas o de menos: anomalia. Nunca se gira."""
    found = []  # type: List[str]
    rfef, feed = set(rfef_pairs), set(feed_pairs)
    for home, away in sorted(rfef - feed):
        if (away, home) in feed:
            found.append(anomaly("par-invertido", "%s - %s" % (home, away), "%s - %s (feed)" % (away, home), "%s - %s (RFEF)" % (home, away)))
        else:
            found.append(anomaly("par-de-mas", "%s - %s" % (home, away), "par del feed", "solo en la RFEF"))
    for home, away in sorted(feed - rfef):
        if (away, home) not in rfef:
            found.append(anomaly("par-de-menos", "%s - %s" % (home, away), "par de la RFEF", "solo en el feed"))
    return found


def verify(season_with_batch: Dict[str, Any], phase_id: str, round_number: int, league_id: str, table_body: bytes,
           aliases: Dict[str, str], rfef_pairs: List[Pair]) -> List[str]:
    """Anomalias de pares y de tabla. Lanza TableUnavailableError si la tabla no es utilizable (la verificacion falla)."""
    table = parse_table(table_body, league_id)
    phase = next((candidate for candidate in season_with_batch["phases"] if candidate["id"] == phase_id), None)
    round_ = next((candidate for candidate in phase["rounds"] if candidate["number"] == round_number), None) if phase else None
    if round_ is None:
        raise TableUnavailableError("el feed no tiene la jornada %d de la fase %s" % (round_number, phase_id))
    feed_pairs = [(match["homeTeamId"], match["awayTeamId"]) for match in round_["matches"]]
    found = pair_anomalies(rfef_pairs, feed_pairs)
    found.extend(table_anomalies(compute_standings(season_with_batch, phase_id, round_number), table, aliases))
    return found
