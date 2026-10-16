"""Conversor CSV -> documento de temporada de `league-feed-v1` (US-007a2).

Dos CSV (equipos y partidos, UTF-8, separados por comas, con cabecera) producen el JSON de
`public/v1/seasons/<id>.json`. Reutiliza las invariantes de `invariants.py`, y cada error se
informa como `<fichero>:<linea>: <detalle>`. Es determinista (el `updatedAt` llega por parametro),
no usa la red y no escribe nada si hay un solo error. Nunca escribe dentro de `public/`.
Solo biblioteca estandar, compatible con Python 3.9.

Formato de `partidos.csv` (la hora es la LOCAL de Madrid, `HH:MM`; el conversor la pasa a UTC):
  id, phaseId, phaseName, phaseKind, phaseOrder, roundId, roundNumber, roundName,
  homeTeamId, awayTeamId, status, date, time, homeScore, awayScore, decidedBy, note
Formato de `equipos.csv`: id, name, shortName   (`crestURL` es siempre null, NFR-010).
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import invariants

SCHEMA_VERSION = 1
SEASON_ID_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}$")
LOCAL_TIME_PATTERN = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
INTEGER_PATTERN = re.compile(r"^[0-9]+$")
DEFAULT_STATUS = "scheduled"
STATUSES = ("scheduled", "live", "finished", "postponed", "suspended", "cancelled", "unknown")
PHASE_KINDS = ("league", "knockout")
DECIDED_BY = ("extraTime", "penalties")
TEAM_COLUMNS = ("id", "name", "shortName")
MATCH_COLUMNS = ("id", "phaseId", "phaseName", "phaseKind", "phaseOrder", "roundId", "roundNumber", "roundName",
                 "homeTeamId", "awayTeamId", "status", "date", "time", "homeScore", "awayScore", "decidedBy", "note")
HEADER_LINE = 1
EXIT_FAILED = 1
EXIT_USAGE = 2
JSON_INDENT = 2


class Problems:
    """Acumula errores `fichero:linea: detalle` para informar de todos a la vez."""

    def __init__(self) -> None:
        self.items = []  # type: List[str]

    def add(self, source: str, line: int, detail: str) -> None:
        self.items.append("%s:%d: %s" % (os.path.basename(source), line, detail))

    def __bool__(self) -> bool:
        return bool(self.items)


Row = Tuple[int, Dict[str, str]]


def read_rows(path: str, columns: Tuple[str, ...], problems: Problems) -> List[Row]:
    """Filas (numero de linea, valores recortados). Exige la cabecera exacta; ignora lineas en blanco."""
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            rows = [(reader.line_num, record) for record in reader]
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        problems.add(path, HEADER_LINE, "no se pudo leer el CSV (%s)" % type(error).__name__)
        return []
    if not rows or tuple(cell.strip() for cell in rows[0][1]) != columns:
        problems.add(path, HEADER_LINE, "la cabecera debe ser exactamente: %s" % ",".join(columns))
        return []
    result = []  # type: List[Row]
    for line, record in rows[1:]:
        if not any(cell.strip() for cell in record):
            continue
        if len(record) != len(columns):
            problems.add(path, line, "tiene %d columnas y se esperaban %d" % (len(record), len(columns)))
            continue
        result.append((line, {name: cell.strip() for name, cell in zip(columns, record)}))
    return result


def required(source: str, line: int, row: Dict[str, str], names: Tuple[str, ...], problems: Problems) -> bool:
    missing = [name for name in names if not row[name]]
    for name in missing:
        problems.add(source, line, "la columna %s no puede estar vacia" % name)
    return not missing


def parse_teams(path: str, problems: Problems) -> List[Dict[str, Any]]:
    teams = []  # type: List[Dict[str, Any]]
    lines = {}  # type: Dict[str, int]
    for line, row in read_rows(path, TEAM_COLUMNS, problems):
        if not required(path, line, row, TEAM_COLUMNS, problems):
            continue
        if row["id"] in lines:
            problems.add(path, line, "id de equipo repetido %r (ya en la linea %d)" % (row["id"], lines[row["id"]]))
            continue
        lines[row["id"]] = line
        teams.append({"id": row["id"], "name": row["name"], "shortName": row["shortName"], "crestURL": None})
    return teams


def parse_date(value: str) -> Optional[datetime.date]:
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
    return parsed if parsed.strftime("%Y-%m-%d") == value else None


def local_to_utc(date: datetime.date, hour: int, minute: int) -> Tuple[Optional[str], Optional[str]]:
    """Hora local de Madrid a instante UTC `...Z`. Devuelve (instante, motivo del error)."""
    local = datetime.datetime(date.year, date.month, date.day, hour, minute)
    candidates = []
    for offset in (invariants.CET_OFFSET_HOURS, invariants.CEST_OFFSET_HOURS):
        utc = local - datetime.timedelta(hours=offset)
        if invariants.madrid_offset_hours(utc) == offset:
            candidates.append(utc)
    if not candidates:
        return None, "esa hora local no existe en Madrid (salto al horario de verano)"
    if len(candidates) > 1:
        return None, "esa hora local es ambigua en Madrid (cambio al horario de invierno)"
    return candidates[0].strftime(invariants.INSTANT_FORMAT), None


def parse_kickoff(path: str, line: int, row: Dict[str, str], problems: Problems) -> Optional[Dict[str, Optional[str]]]:
    date_text, time_text = row["date"], row["time"]
    date = None  # type: Optional[datetime.date]
    if date_text:
        date = parse_date(date_text)
        if date is None:
            problems.add(path, line, "date %r no es una fecha AAAA-MM-DD valida" % date_text)
            return None
    if not time_text:
        return {"date": date_text or None, "time": None}
    match = LOCAL_TIME_PATTERN.match(time_text)
    if match is None:
        problems.add(path, line, "time %r no es una hora local HH:MM" % time_text)
        return None
    if date is None:
        problems.add(path, line, "hay time %r pero date esta vacia" % time_text)
        return None
    instant, reason = local_to_utc(date, int(match.group(1)), int(match.group(2)))
    if instant is None:
        problems.add(path, line, "time %r del %s: %s" % (time_text, date_text, reason))
        return None
    return {"date": date_text, "time": instant}


def parse_goal(path: str, line: int, row: Dict[str, str], problems: Problems) -> Optional[Dict[str, int]]:
    home, away = row["homeScore"], row["awayScore"]
    if not home and not away:
        return None
    if not (INTEGER_PATTERN.match(home) and INTEGER_PATTERN.match(away)):
        problems.add(path, line, "homeScore y awayScore deben ser ambos enteros >= 0 (o ambos vacios)")
        return None
    return {"home": int(home), "away": int(away)}


def parse_match(path: str, line: int, row: Dict[str, str], problems: Problems) -> Optional[Dict[str, Any]]:
    before = len(problems.items)
    status = row["status"] or DEFAULT_STATUS
    if status not in STATUSES:
        problems.add(path, line, "status %r desconocido (validos: %s)" % (status, ", ".join(STATUSES)))
    if row["decidedBy"] and row["decidedBy"] not in DECIDED_BY:
        problems.add(path, line, "decidedBy %r desconocido (validos: %s)" % (row["decidedBy"], ", ".join(DECIDED_BY)))
    kickoff = parse_kickoff(path, line, row, problems)
    score = parse_goal(path, line, row, problems)
    if len(problems.items) > before:
        return None
    return {"id": row["id"], "homeTeamId": row["homeTeamId"], "awayTeamId": row["awayTeamId"], "status": status,
            "kickoff": kickoff, "score": score, "decidedBy": row["decidedBy"] or None, "note": row["note"] or None}


def parse_structure(path: str, line: int, row: Dict[str, str], problems: Problems) -> Optional[Tuple[str, str, int, int]]:
    """Valida los campos de fase y ronda de la fila. Devuelve (tipo de fase, nombre de ronda, orden, numero)."""
    before = len(problems.items)
    if row["phaseKind"] not in PHASE_KINDS:
        problems.add(path, line, "phaseKind %r desconocido (validos: %s)" % (row["phaseKind"], ", ".join(PHASE_KINDS)))
    for column in ("phaseOrder", "roundNumber"):
        if not INTEGER_PATTERN.match(row[column]) or int(row[column]) < 1:
            problems.add(path, line, "%s %r debe ser un entero >= 1" % (column, row[column]))
    if len(problems.items) > before:
        return None
    return row["phaseKind"], row["roundName"], int(row["phaseOrder"]), int(row["roundNumber"])


class Calendar:
    """Construye fases y rondas en orden de aparicion y vigila que cada fila las describa igual."""

    def __init__(self, path: str, problems: Problems) -> None:
        self.path = path
        self.problems = problems
        self.phases = []  # type: List[Dict[str, Any]]
        self.rounds = {}  # type: Dict[str, Dict[str, Any]]
        self.phase_by_id = {}  # type: Dict[str, Dict[str, Any]]
        self.round_phase = {}  # type: Dict[str, str]

    def round_for(self, line: int, row: Dict[str, str], structure: Tuple[str, str, int, int]) -> Optional[Dict[str, Any]]:
        kind, _round_name, order, number = structure
        phase = self.phase_by_id.get(row["phaseId"])
        if phase is None:
            phase = {"id": row["phaseId"], "name": row["phaseName"], "kind": kind, "order": order, "rounds": []}
            self.phase_by_id[row["phaseId"]] = phase
            self.phases.append(phase)
        elif (phase["name"], phase["kind"], phase["order"]) != (row["phaseName"], kind, order):
            self.problems.add(self.path, line, "la fase %r no coincide con la descrita antes (nombre, tipo u orden)" % row["phaseId"])
            return None
        rnd = self.rounds.get(row["roundId"])
        if rnd is None:
            rnd = {"id": row["roundId"], "number": number, "name": row["roundName"], "matches": []}
            self.rounds[row["roundId"]] = rnd
            self.round_phase[row["roundId"]] = row["phaseId"]
            phase["rounds"].append(rnd)
        elif (rnd["number"], rnd["name"], self.round_phase[row["roundId"]]) != (number, row["roundName"], row["phaseId"]):
            self.problems.add(self.path, line, "la ronda %r no coincide con la descrita antes (fase, numero o nombre)" % row["roundId"])
            return None
        return rnd


def invariant_problems(path: str, line: int, match: Dict[str, Any], team_ids: List[str], problems: Problems) -> None:
    for violation in invariants.match_violations(match, team_ids):
        problems.add(path, line, "%s: %s" % (violation.invariant, violation.detail))


def parse_matches(path: str, team_ids: List[str], problems: Problems) -> List[Dict[str, Any]]:
    calendar = Calendar(path, problems)
    seen = {}  # type: Dict[str, int]
    for line, row in read_rows(path, MATCH_COLUMNS, problems):
        names = ("id", "phaseId", "phaseName", "phaseKind", "phaseOrder", "roundId", "roundNumber", "roundName", "homeTeamId", "awayTeamId")
        if not required(path, line, row, names, problems):
            continue
        if row["id"] in seen:
            problems.add(path, line, "id de partido repetido %r (ya en la linea %d)" % (row["id"], seen[row["id"]]))
            continue
        seen[row["id"]] = line
        structure = parse_structure(path, line, row, problems)
        match = parse_match(path, line, row, problems)
        if structure is None or match is None:
            continue
        invariant_problems(path, line, match, team_ids, problems)
        rnd = calendar.round_for(line, row, structure)
        if rnd is not None:
            rnd["matches"].append(match)
    return calendar.phases


def build_season(args: argparse.Namespace, problems: Problems) -> Optional[Dict[str, Any]]:
    teams = parse_teams(args.teams, problems)
    phases = parse_matches(args.matches, [team["id"] for team in teams], problems)
    if problems:
        return None
    season = {"schemaVersion": SCHEMA_VERSION, "seasonId": args.season_id, "competitionName": args.competition_name,
              "updatedAt": args.updated_at, "teams": teams, "phases": phases}
    for violation in invariants.season_violations(season):
        problems.add(args.matches, HEADER_LINE, str(violation))
    if not teams or not phases:
        problems.add(args.matches, HEADER_LINE, "hacen falta al menos un equipo y un partido")
    return None if problems else season


def render(season: Dict[str, Any]) -> str:
    return json.dumps(season, indent=JSON_INDENT, ensure_ascii=False) + "\n"


def write_atomically(path: str, text: str) -> None:
    """Escribe en un temporal del mismo directorio y lo renombra: nunca queda un fichero a medias."""
    directory = os.path.dirname(os.path.abspath(path))
    handle, temporary = tempfile.mkstemp(prefix=".convert-csv.", dir=directory)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise


def inside_public(path: str, feed: str) -> bool:
    public = os.path.realpath(os.path.join(feed, "public"))
    target = os.path.realpath(path)
    return target == public or target.startswith(public + os.sep)


def argument_problems(args: argparse.Namespace, feed: str) -> List[str]:
    found = []
    if not SEASON_ID_PATTERN.match(args.season_id):
        found.append("--season-id %r debe tener la forma AAAA-AA" % args.season_id)
    try:
        invariants.parse_instant(args.updated_at)
    except ValueError:
        found.append("--updated-at %r debe ser un instante UTC AAAA-MM-DDTHH:MM:SSZ" % args.updated_at)
    if not args.competition_name.strip():
        found.append("--competition-name no puede estar vacio")
    if inside_public(args.out, feed):
        found.append("--out no puede estar dentro de public/: copia el resultado a mano tras revisarlo (RUNBOOK, carga inicial)")
    return found


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Conversor CSV -> temporada league-feed-v1 (US-007a2)")
    parser.add_argument("--feed", required=True, help="raiz del repositorio del feed")
    parser.add_argument("--teams", required=True, help="CSV de equipos")
    parser.add_argument("--matches", required=True, help="CSV de partidos")
    parser.add_argument("--season-id", required=True, help="identificador de temporada, AAAA-AA")
    parser.add_argument("--competition-name", required=True)
    parser.add_argument("--updated-at", required=True, help="instante UTC AAAA-MM-DDTHH:MM:SSZ (no se lee el reloj)")
    parser.add_argument("--out", required=True, help="fichero de salida, fuera de public/")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    invalid = argument_problems(args, args.feed)
    for message in invalid:
        print("FALLO conversor: %s" % message, file=sys.stderr)
    if invalid:
        return EXIT_USAGE
    problems = Problems()
    season = build_season(args, problems)
    if season is None:
        for item in problems.items:
            print("FALLO conversor: %s" % item, file=sys.stderr)
        print("%d error(es): no se ha escrito ninguna salida" % len(problems.items), file=sys.stderr)
        return EXIT_FAILED
    try:
        write_atomically(args.out, render(season))
    except OSError as error:
        print("FALLO conversor: no se pudo escribir %s (%s)" % (args.out, type(error).__name__), file=sys.stderr)
        return EXIT_FAILED
    matches = sum(len(rnd["matches"]) for phase in season["phases"] for rnd in phase["rounds"])
    print("conversor: %d equipos y %d partidos escritos en %s" % (len(season["teams"]), matches, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
