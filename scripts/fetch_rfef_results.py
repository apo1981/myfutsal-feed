"""Descarga una jornada de la RFEF y genera el CSV `id,home,away` de sus partidos finalizados (US-034; ADR-011, ADR-012).

Solo biblioteca estandar, Python 3.9. Una lectura de `POST /futsal_xhr/get_matchs_round` (JSON, ver docs/spike-rfef-resultados.md).
  - Nunca inventa un marcador: solo `status_txt == "Finalizado"` con dos enteros de 0 a 25 genera fila.
  - Reglas de cordura y casado de equipos/partidos: cualquier fallo termina sin CSV.
  - Diferencias de fecha, hora o estado respecto al feed: solo se informan.
  - Respeto: robots.txt antes, User-Agent no personal, sin cookies ni reintentos, parada ante 403/429/5xx.
  - La respuesta cruda se guarda solo fuera del repositorio (../local-data/rfef/).
Doble lectura (US-037): dos POST separados >= 15 min que deben coincidir en par, estado, goles, fecha y hora; la segunda es la base.
Presupuesto: robots.txt + 2 lecturas de partidos (la de la tabla es de US-036), >= 10 s entre peticiones, sin reintentos.
Con --from-file solo hay una lectura (sin peticiones ni espera). La clasificacion es de US-035/US-036.

Codigos de salida: 0 correcto; 2 error (uso, parseo, equipo o partido sin casar, red); 3 anomalia; 4 parada por la RFEF; 5 deshabilitada
(interruptor `enabled` de scripts/rfef/config.json, US-038; 403/429 o robots que veta lo ponen a false).
"""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import rfef_switch
import round_sheet

TIMEZONE = ZoneInfo("Europe/Madrid")
BASE_URL = "https://futsal.rfef.es"
ENDPOINT_PATH = "/futsal_xhr/get_matchs_round"
ROBOTS_PATH = "/robots.txt"
CATEGORY = "25"
USER_AGENT = "myfutsal-feed-tool/1.0 (offline results tool; non-personal)"
REQUEST_INTERVAL_SECONDS = 10
READ_SEPARATION_SECONDS = 15 * 60  # separacion minima entre las dos lecturas de partidos (ADR-012 §3)
TIMEOUT_SECONDS = 30
STOP_STATUSES = (403, 429)
ALIASES_PATH = os.path.join("scripts", "rfef", "aliases.json")
RAW_DIR_RELATIVE = os.path.join("..", "local-data", "rfef")
# fase -> (league_id, group_code) de la RFEF
PHASES = {"apertura": ("1297", "1"), "clausura": ("1298", "2")}
ROUNDS_PER_PHASE = 15
TEAMS_PER_ROUND = 16
MATCHES_PER_ROUND = 8
MIN_GOALS, MAX_GOALS = 0, 25
FINISHED_LABEL = "Finalizado"
NOT_STARTED_LABEL = "Sin comenzar"
FEED_FILLABLE_STATUSES = ("unknown", "scheduled", "live")
NO_TIME = "sin hora"
ROUND_PATTERN = re.compile(r"Jornada (\d+)\s*$")
INTEGER_PATTERN = re.compile(r"^-?\d+$")
REQUIRED_MATCH_KEYS = ("team_local", "team_visit", "datateam1", "datateam2", "status_txt", "r_local", "r_visit",
                       "hour", "no_hour", "league_id", "group_code", "category", "year")
CSV_COLUMNS = ["id", "home", "away"]

EXIT_OK, EXIT_ERROR, EXIT_ANOMALY, EXIT_STOPPED, EXIT_DISABLED = 0, 2, 3, 4, 5


class ToolError(Exception):
    """Error explicito: no se genera CSV."""


class ParseError(ToolError):
    """La respuesta no tiene la estructura esperada."""


class StoppedError(ToolError):
    """La RFEF veta la ruta, limita o falla: hay que parar."""


class BlockedError(StoppedError):
    """La RFEF bloquea (403/429) o veta la ruta (robots.txt): ademas de parar, hay que desactivar el interruptor."""


class AnomalyError(ToolError):
    """Una o mas reglas de cordura fallan; `anomalies` lleva una linea por cada una."""

    def __init__(self, anomalies: List[str]) -> None:
        super().__init__("%d anomalia(s)" % len(anomalies))
        self.anomalies = anomalies


def anomaly(rule: str, subject: str, expected: Any, obtained: Any) -> str:
    return "anomalia [%s] %s: esperado=%s obtenido=%s" % (rule, subject, expected, obtained)


def clean(text: Any) -> str:
    """Nombres de la RFEF con espacios finales o dobles."""
    return " ".join(str(text).split())


# --------------------------------------------------------------------------
# Parseo
# --------------------------------------------------------------------------

class RfefMatch:
    """Partido de la RFEF ya validado en estructura (sin interpretar)."""

    def __init__(self, day: str, raw: Dict[str, Any]) -> None:
        self.day = day
        self.raw = raw
        self.home_datateam = str(raw["datateam1"]).strip()
        self.away_datateam = str(raw["datateam2"]).strip()
        self.home_name = clean(raw["team_local"])
        self.away_name = clean(raw["team_visit"])
        self.status_text = clean(raw["status_txt"])
        self.hour = None if str(raw["no_hour"]).strip() == "1" else (clean(raw["hour"]) or None)

    @property
    def label(self) -> str:
        return "%s - %s (%s)" % (self.home_name, self.away_name, self.day)


def parse_response(body: bytes) -> Tuple[List[RfefMatch], List[Tuple[str, Any]]]:
    """Partidos y filas `descansa` no vacias. Lanza ParseError si la estructura no es la esperada."""
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ParseError("la respuesta no es JSON valido")
    if not isinstance(document, dict) or not document:
        raise ParseError("se esperaba un objeto JSON con una clave por fecha y no esta o esta vacio")
    matches = []  # type: List[RfefMatch]
    resting = []  # type: List[Tuple[str, Any]]
    for day, entry in document.items():
        try:
            datetime.datetime.strptime(day, round_sheet.DATE_FORMAT)
        except ValueError:
            raise ParseError("la clave %r no es una fecha AAAA-MM-DD" % day)
        if not isinstance(entry, dict) or "matches" not in entry or "descansa" not in entry:
            raise ParseError("el dia %s no tiene 'matches' y 'descansa'" % day)
        if entry["descansa"]:
            resting.append((day, entry["descansa"]))
        listed = entry["matches"]
        if isinstance(listed, dict):  # la web devuelve lista o dict indexado segun el dia
            listed = list(listed.values())
        if not isinstance(listed, list):
            raise ParseError("el dia %s: 'matches' no es lista ni objeto" % day)
        for raw in listed:
            if not isinstance(raw, dict):
                raise ParseError("el dia %s: un partido no es un objeto" % day)
            missing = [key for key in REQUIRED_MATCH_KEYS if key not in raw]
            if missing:
                raise ParseError("el dia %s: a un partido le faltan campos: %s" % (day, ", ".join(missing)))
            matches.append(RfefMatch(day, raw))
    return matches, resting


# --------------------------------------------------------------------------
# Reglas de cordura (ADR-012 §4, parte de US-034)
# --------------------------------------------------------------------------

def parse_goals(match: RfefMatch) -> Tuple[Optional[Tuple[int, int]], List[str]]:
    """Marcador final (solo si 'Finalizado' con dos enteros en rango) y anomalias de rango. Nunca devuelve 0-0 por defecto."""
    anomalies = []  # type: List[str]
    values = []  # type: List[Optional[int]]
    for column in ("r_local", "r_visit"):
        text = str(match.raw[column]).strip()
        if not INTEGER_PATTERN.match(text):
            values.append(None)
            continue
        goals = int(text)
        if not MIN_GOALS <= goals <= MAX_GOALS:
            anomalies.append(anomaly("goles-fuera-de-rango", "%s %s" % (match.label, column),
                                     "%d..%d" % (MIN_GOALS, MAX_GOALS), goals))
        values.append(goals)
    if match.status_text != FINISHED_LABEL or values[0] is None or values[1] is None or anomalies:
        return None, anomalies
    return (values[0], values[1]), anomalies


def sanity_anomalies(matches: List[RfefMatch], resting: List[Tuple[str, Any]], league_id: str, group: str,
                     round_number: int, year: str, today: datetime.date) -> List[str]:
    found = []  # type: List[str]
    expected = {"league_id": league_id, "group_code": group, "year": year}
    for match in matches:
        for key, wanted in expected.items():
            if str(match.raw[key]).strip() != wanted:
                found.append(anomaly(key, match.label, wanted, str(match.raw[key]).strip()))
        found_round = ROUND_PATTERN.search(clean(match.raw["category"]))
        obtained = found_round.group(1) if found_round else clean(match.raw["category"])
        if obtained != str(round_number):
            found.append(anomaly("jornada", match.label, round_number, obtained))
        if datetime.datetime.strptime(match.day, round_sheet.DATE_FORMAT).date() > today:
            found.append(anomaly("fecha-futura", match.label, "<= %s" % today.isoformat(), match.day))
    teams = {team for match in matches for team in (match.home_datateam, match.away_datateam)}
    if len(teams) != TEAMS_PER_ROUND:
        found.append(anomaly("equipos", "jornada %d" % round_number, TEAMS_PER_ROUND, len(teams)))
    if len(matches) != MATCHES_PER_ROUND:
        found.append(anomaly("partidos", "jornada %d" % round_number, MATCHES_PER_ROUND, len(matches)))
    for day, names in resting:
        found.append(anomaly("descansa", day, "vacio", json.dumps(names, ensure_ascii=False)))
    pairs = {}  # type: Dict[Tuple[str, str], int]
    for match in matches:
        key = (match.home_datateam, match.away_datateam)
        pairs[key] = pairs.get(key, 0) + 1
    for (home, away), count in sorted(pairs.items()):
        if count > 1:
            found.append(anomaly("par-repetido", "datateam %s - %s" % (home, away), 1, count))
    for match in matches:
        found.extend(parse_goals(match)[1])
    return found


# --------------------------------------------------------------------------
# Casado con el feed
# --------------------------------------------------------------------------

def load_aliases(feed: str) -> Dict[str, str]:
    with open(os.path.join(feed, ALIASES_PATH), "r", encoding="utf-8") as handle:
        return dict(json.load(handle)["aliases"])


def resolve_teams(matches: List[RfefMatch], aliases: Dict[str, str]) -> List[str]:
    """Errores de equipos sin casar (uno por equipo, con su nombre RFEF)."""
    missing = {}  # type: Dict[str, str]
    for match in matches:
        for datateam, name in ((match.home_datateam, match.home_name), (match.away_datateam, match.away_name)):
            if datateam not in aliases:
                missing[datateam] = name
    return ["equipo sin casar: datateam %s (%s) no esta en la tabla de alias" % (datateam, name)
            for datateam, name in sorted(missing.items())]


def feed_round(season: Dict[str, Any], phase_id: str, number: int) -> Dict[str, Any]:
    for phase in season["phases"]:
        if phase["id"] == phase_id:
            for candidate in phase["rounds"]:
                if candidate["number"] == number:
                    return candidate
    raise ToolError("el feed no tiene la jornada %d de la fase %s" % (number, phase_id))


def later_rounds(season: Dict[str, Any], phase_id: str, number: int) -> List[Dict[str, Any]]:
    """Jornadas posteriores de la fase: un partido de la RFEF casado con una de ellas es un partido adelantado."""
    for phase in season["phases"]:
        if phase["id"] == phase_id:
            return [candidate for candidate in phase["rounds"] if candidate["number"] > number]
    return []


def madrid_time(kickoff: Dict[str, Any]) -> Optional[str]:
    if not kickoff.get("time"):
        return None
    instant = datetime.datetime.strptime(kickoff["time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    return instant.astimezone(TIMEZONE).strftime("%H:%M")


class Plan:
    """Resultado del analisis: filas del CSV, pendientes y diferencias informativas."""

    def __init__(self) -> None:
        self.rows = []  # type: List[Tuple[str, int, int]]
        self.pending = 0
        self.already_published = 0
        self.differences = []  # type: List[str]
        self.warnings = []  # type: List[str]


def build_plan(matches: List[RfefMatch], round_data: Dict[str, Any], aliases: Dict[str, str],
               later: Optional[List[Dict[str, Any]]] = None) -> Plan:
    """Casa por par local-visitante (nunca por posicion). Lanza ToolError (partido ausente) o AnomalyError (marcador distinto).

    Un par que el feed tiene en una jornada posterior (`later`) es un partido adelantado: se informa y no se aplica."""
    by_pair = {(m["homeTeamId"], m["awayTeamId"]): m for m in round_data["matches"]}
    plan = Plan()
    missing = []  # type: List[str]
    conflicts = []  # type: List[str]
    for match in matches:
        pair = (aliases[match.home_datateam], aliases[match.away_datateam])
        feed_match = by_pair.get(pair)
        if feed_match is None:
            advanced = find_advanced(later or [], pair)
            if advanced is not None:
                plan.differences.append("%s: adelantado, el feed lo tiene en la jornada %d (fecha=%s, estado=%s) y la RFEF en la jornada %s "
                                        "(fecha=%s, %s); no se aplica" % (
                                            advanced[1]["id"], advanced[0], advanced[1]["kickoff"].get("date"), advanced[1]["status"],
                                            round_data["number"], match.day, match.status_text))
                continue
            missing.append("partido no encontrado en el feed (%s, jornada %s): %s - %s" % (
                round_data["id"], round_data["number"], pair[0], pair[1]))
            continue
        score, _ = parse_goals(match)
        if match.status_text not in (FINISHED_LABEL, NOT_STARTED_LABEL):
            plan.warnings.append("%s: estado RFEF no reconocido %r, queda pendiente" % (feed_match["id"], match.status_text))
        add_differences(plan, feed_match, match, score)
        if score is None:
            plan.pending += 1
        elif feed_match["status"] == "finished":
            published = feed_match.get("score") or {}
            if (published.get("home"), published.get("away")) != score:
                conflicts.append(anomaly("finished-distinto", feed_match["id"],
                                         "%s-%s (feed)" % (published.get("home"), published.get("away")),
                                         "%d-%d (RFEF)" % score))
            else:
                plan.already_published += 1
        elif feed_match["status"] in FEED_FILLABLE_STATUSES:
            plan.rows.append((feed_match["id"], score[0], score[1]))
        else:
            plan.differences.append("%s: estado feed=%s, RFEF=%s con %d-%d (no se aplica)" % (
                feed_match["id"], feed_match["status"], FINISHED_LABEL, score[0], score[1]))
    if missing:
        raise ToolError("\n".join(missing))
    if conflicts:
        raise AnomalyError(conflicts)
    plan.rows.sort()
    return plan


def find_advanced(later: List[Dict[str, Any]], pair: Tuple[str, str]) -> Optional[Tuple[int, Dict[str, Any]]]:
    for candidate in later:
        for feed_match in candidate["matches"]:
            if (feed_match["homeTeamId"], feed_match["awayTeamId"]) == pair:
                return candidate["number"], feed_match
    return None


def add_differences(plan: Plan, feed_match: Dict[str, Any], match: RfefMatch, score: Optional[Tuple[int, int]]) -> None:
    kickoff = feed_match["kickoff"]
    if kickoff.get("date") != match.day:
        plan.differences.append("%s: fecha feed=%s, RFEF=%s" % (feed_match["id"], kickoff.get("date"), match.day))
    if (madrid_time(kickoff) or None) != match.hour:
        plan.differences.append("%s: hora feed=%s, RFEF=%s" % (feed_match["id"], madrid_time(kickoff) or NO_TIME,
                                                               match.hour or NO_TIME))
    if feed_match["status"] in ("postponed", "suspended"):
        plan.differences.append("%s: estado feed=%s, RFEF=%s (%s %s); no se cambia" % (
            feed_match["id"], feed_match["status"], match.status_text, match.day, match.hour or NO_TIME))
    if feed_match["status"] == "finished" and score is None:
        plan.differences.append("%s: estado feed=finished, RFEF=%s sin marcador final" % (feed_match["id"], match.status_text))


# --------------------------------------------------------------------------
# Estabilidad entre lecturas (US-037)
# --------------------------------------------------------------------------

def relevant_fields(match: RfefMatch) -> Dict[str, str]:
    """Campos normalizados que deben coincidir entre lecturas; el orden de la lista y el resto de campos no cuentan."""
    score, _ = parse_goals(match)
    return {"estado": match.status_text,
            "marcador": "%d-%d" % score if score else "sin marcador (%s-%s)" % (clean(match.raw["r_local"]),
                                                                              clean(match.raw["r_visit"])),
            "fecha": match.day, "hora": match.hour or NO_TIME}


def stability_anomalies(first: List[RfefMatch], second: List[RfefMatch]) -> List[str]:
    """Una anomalia por cada partido que aparece, desaparece o cambia un campo relevante entre las dos lecturas."""
    before = {(m.home_datateam, m.away_datateam): m for m in first}
    after = {(m.home_datateam, m.away_datateam): m for m in second}
    found = []  # type: List[str]
    for pair in sorted(set(before) | set(after)):
        old, new = before.get(pair), after.get(pair)
        if old is None or new is None:
            present = new or old
            found.append(anomaly("lecturas-distintas", present.label, "presente en ambas lecturas",
                                 "solo en la %s" % ("segunda" if old is None else "primera")))
            continue
        old_fields, new_fields = relevant_fields(old), relevant_fields(new)
        for field in old_fields:
            if old_fields[field] != new_fields[field]:
                found.append(anomaly("lecturas-distintas", "%s, %s" % (new.label, field), old_fields[field], new_fields[field]))
    return found


# --------------------------------------------------------------------------
# Salida
# --------------------------------------------------------------------------

def csv_text(rows: List[Tuple[str, int, int]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for identifier, home, away in rows:
        writer.writerow([identifier, home, away])
    return out.getvalue()


def pretty_lines(rows: List[Tuple[str, int, int]], round_data: Dict[str, Any], season: Dict[str, Any]) -> List[str]:
    """Una linea 'id  Local G-G Visitante' por fila del CSV, con teams[].name del feed (US-039)."""
    names = {team["id"]: team["name"] for team in season["teams"]}
    by_id = {m["id"]: m for m in round_data["matches"]}
    lines = []  # type: List[str]
    for identifier, home, away in rows:
        feed_match = by_id[identifier]
        for team_id in (feed_match["homeTeamId"], feed_match["awayTeamId"]):
            if team_id not in names:
                raise ToolError("el equipo %s del partido %s no figura en teams[] del feed" % (team_id, identifier))
        lines.append("%s  %s %d-%d %s" % (identifier, names[feed_match["homeTeamId"]], home, away,
                                          names[feed_match["awayTeamId"]]))
    return lines


def report_lines(plan: Plan, phase: str, number: int, source: str, reads: List[Tuple[str, str]]) -> List[str]:
    """`reads`: (instante UTC, SHA-256) de cada lectura, en orden; la ultima es la base del CSV."""
    lines = ["informe fetch-rfef-results: %s jornada %d" % (phase, number),
             "fuente: %s" % source]
    for position, (fetched_at, digest) in enumerate(reads, start=1):
        prefix = "lectura %d " % position if len(reads) > 1 else ""
        lines.append("%sinstante UTC: %s" % (prefix, fetched_at))
        lines.append("%sSHA-256: %s" % (prefix, digest))
    lines += [
             "finalizados a aplicar: %d; ya publicados iguales: %d; pendientes: %d" % (
                 len(plan.rows), plan.already_published, plan.pending)]
    lines.extend("aviso: " + warning for warning in plan.warnings)
    if plan.differences:
        lines.append("diferencias con el feed (solo informativas, no cambian el CSV):")
        lines.extend("  " + difference for difference in plan.differences)
    else:
        lines.append("sin diferencias de fecha, hora ni estado con el feed; partidos nuevos: %d" % len(plan.rows))
    return lines


# --------------------------------------------------------------------------
# Red
# --------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class Transport:
    """Una peticion HTTP sin cookies, sin redirecciones ni reintentos. Devuelve (estado, cuerpo)."""

    def request(self, method: str, url: str, data: Optional[bytes]) -> Tuple[int, bytes]:
        headers = {"User-Agent": USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        opener = urllib.request.build_opener(_NoRedirect)  # sin manejador de cookies: no hay sesion
        try:
            with opener.open(urllib.request.Request(url, data=data, headers=headers, method=method), timeout=TIMEOUT_SECONDS) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, b""
        except (urllib.error.URLError, OSError) as error:
            raise ToolError("fallo de red en %s: %s" % (urllib.parse.urlsplit(url).path, error))


def check_status(status: int, what: str) -> None:
    if status in STOP_STATUSES:
        raise BlockedError("la RFEF respondio %d a %s: se detiene sin generar CSV" % (status, what))
    if status >= 500:
        raise StoppedError("la RFEF respondio %d a %s: se detiene sin generar CSV" % (status, what))
    if status != 200:
        raise ToolError("la RFEF respondio %d a %s" % (status, what))


def ensure_robots_allow(transport: Transport) -> None:
    status, body = transport.request("GET", BASE_URL + ROBOTS_PATH, None)
    if status == 404:
        return  # sin robots.txt, nada que vetar
    check_status(status, ROBOTS_PATH)
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(body.decode("utf-8", "replace").splitlines())
    if not parser.can_fetch(USER_AGENT, BASE_URL + ENDPOINT_PATH):
        raise BlockedError("robots.txt veta %s: no se hace la peticion" % ENDPOINT_PATH)


def request_body(phase: str, number: int, year: str) -> bytes:
    league, group = PHASES[phase]
    fields = [("category", CATEGORY), ("league", league), ("round", str(number)), ("action", "competicion"),
              ("year", year), ("group", group)]
    return urllib.parse.urlencode(fields).encode("ascii")


def post_round(transport: Transport, phase: str, number: int, year: str) -> bytes:
    status, body = transport.request("POST", BASE_URL + ENDPOINT_PATH, request_body(phase, number, year))
    check_status(status, ENDPOINT_PATH)
    return body


def download(transport: Transport, phase: str, number: int, year: str, sleep: Callable[[float], None]) -> bytes:
    """Primera lectura: robots.txt, pausa y un POST."""
    ensure_robots_allow(transport)
    sleep(REQUEST_INTERVAL_SECONDS)
    return post_round(transport, phase, number, year)


def download_again(transport: Transport, phase: str, number: int, year: str, sleep: Callable[[float], None],
                   now: Callable[[], datetime.datetime], first_read_at: datetime.datetime,
                   wait: Optional[Callable[[float], None]] = None) -> bytes:
    """Segunda lectura: espera hasta cumplir la separacion minima y, si el reloj no la confirma, se niega (nunca lee antes).

    `wait` sustituye a `sleep` solo para la espera larga (p. ej. con cuenta atras); la pausa corta sigue usando `sleep`."""
    remaining = READ_SEPARATION_SECONDS - (now() - first_read_at).total_seconds()
    if remaining > 0:
        (wait or sleep)(remaining)
        if (now() - first_read_at).total_seconds() < READ_SEPARATION_SECONDS:
            raise ToolError("no han pasado %d min desde la primera lectura: no se hace la segunda" % (READ_SEPARATION_SECONDS // 60))
    return post_round(transport, phase, number, year)


def raw_directory(feed: str, raw_dir: Optional[str]) -> str:
    """Directorio local del operador (respuestas crudas y cuenta de anomalias), siempre fuera del repositorio del feed."""
    directory = os.path.realpath(raw_dir or os.path.join(feed, RAW_DIR_RELATIVE))
    root = os.path.realpath(feed)
    if directory == root or directory.startswith(root + os.sep):
        raise ToolError("la respuesta cruda no puede guardarse dentro del repositorio del feed")
    return directory


def save_raw(feed: str, raw_dir: Optional[str], phase: str, number: int, fetched_at: str, body: bytes) -> str:
    """Guarda la respuesta cruda solo fuera del repositorio del feed (nunca versionada)."""
    directory = raw_directory(feed, raw_dir)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "%s-%02d-%s.json" % (phase, number, fetched_at.replace(":", "")))
    with open(path, "wb") as handle:
        handle.write(body)
    return path


# --------------------------------------------------------------------------
# Orquestacion
# --------------------------------------------------------------------------

def run(args: argparse.Namespace, transport_factory: Callable[[], Transport], sleep: Callable[[float], None],
        now: Callable[[], datetime.datetime], stdout: Any, stderr: Any) -> int:
    season = round_sheet.load_season(args.feed)
    year = str(int(season["seasonId"][:4]) + 1)
    league, group = PHASES[args.phase]
    today = datetime.date.fromisoformat(args.today) if args.today else now().astimezone(TIMEZONE).date()
    round_data = feed_round(season, args.phase, args.round)

    reads = []  # type: List[Tuple[str, str]]

    def record(body: bytes) -> datetime.datetime:
        instant = now()
        reads.append((instant.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), hashlib.sha256(body).hexdigest()))
        return instant

    def check(body: bytes) -> List[RfefMatch]:
        parsed, resting = parse_response(body)
        found = sanity_anomalies(parsed, resting, league, group, args.round, year, today)
        if found:
            raise AnomalyError(found)
        return parsed

    if args.from_file:
        with open(args.from_file, "rb") as handle:
            body = handle.read()
        source = "fichero local %s (sin peticiones)" % os.path.basename(args.from_file)
        record(body)
        matches = check(body)
    else:
        rfef_switch.ensure_enabled(args.feed)
        transport = transport_factory()
        source = BASE_URL + ENDPOINT_PATH
        first_body = download(transport, args.phase, args.round, year, sleep)
        first_read_at = record(first_body)
        save_raw(args.feed, args.raw_dir, args.phase, args.round, reads[0][0], first_body)
        first_matches = check(first_body)  # una primera lectura invalida no justifica esperar ni pedir la segunda
        body = download_again(transport, args.phase, args.round, year, sleep, now, first_read_at)
        record(body)
        save_raw(args.feed, args.raw_dir, args.phase, args.round, reads[1][0], body)
        matches = check(body)
        unstable = stability_anomalies(first_matches, matches)
        if unstable:
            raise AnomalyError(unstable)
    aliases = load_aliases(args.feed)
    unmatched = resolve_teams(matches, aliases)
    if unmatched:
        raise ToolError("\n".join(unmatched))
    plan = build_plan(matches, round_data, aliases, later_rounds(season, args.phase, args.round))

    text = csv_text(plan.rows)
    readable = pretty_lines(plan.rows, round_data, season) if args.pretty else []
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
    elif not args.pretty:
        stdout.write(text)
    for line in readable:
        print(line, file=stdout)
    for line in report_lines(plan, args.phase, args.round, source, reads):
        print(line, file=stderr)
    return EXIT_OK


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CSV id,home,away de una jornada de la RFEF (US-034).")
    parser.add_argument("--feed", required=True, help="raiz del repositorio del feed")
    parser.add_argument("--phase", required=True, choices=sorted(PHASES))
    parser.add_argument("--round", required=True, type=int, choices=range(1, ROUNDS_PER_PHASE + 1), metavar="N")
    parser.add_argument("--out", help="fichero CSV (por defecto, salida estandar)")
    parser.add_argument("--pretty", action="store_true",
                        help="una linea 'id  Local G-G Visitante' por partido en la salida estandar en vez del CSV; "
                             "con --out el CSV va al fichero y la salida estandar solo lleva las lineas legibles")
    parser.add_argument("--from-file", dest="from_file", help="respuesta local: no hace ninguna peticion")
    parser.add_argument("--raw-dir", dest="raw_dir", help="directorio de la respuesta cruda (fuera del repo; por defecto ../local-data/rfef)")
    parser.add_argument("--today", help="fecha de hoy AAAA-MM-DD en Madrid (pruebas)")
    return parser.parse_args(argv)


def disable_after_block(feed: str, error: BlockedError) -> str:
    """La RFEF bloquea o veta la ruta: desactiva el interruptor (solo el humano lo reactiva). Devuelve la linea informativa."""
    try:
        rfef_switch.disable(feed, str(error))
    except rfef_switch.SwitchError as failure:
        return "AVISO: %s" % failure
    return "Interruptor: enabled puesto a false en %s; solo el humano lo reactiva." % rfef_switch.CONFIG_PATH


def main(argv: List[str], transport_factory: Callable[[], Transport] = Transport, sleep: Callable[[float], None] = time.sleep,
         now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.timezone.utc),
         stdout: Any = None, stderr: Any = None) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    args = parse_args(argv)
    try:
        return run(args, transport_factory, sleep, now, out, err)
    except AnomalyError as error:
        print("FALLO: anomalia, no se genera CSV", file=err)
        for line in error.anomalies:
            print("  " + line, file=err)
        return EXIT_ANOMALY
    except rfef_switch.DisabledError as error:
        print("DESHABILITADA: %s" % error, file=err)
        return EXIT_DISABLED
    except StoppedError as error:
        print("PARADA: %s" % error, file=err)
        if isinstance(error, BlockedError):
            print(disable_after_block(args.feed, error), file=err)
        return EXIT_STOPPED
    except (ToolError, rfef_switch.SwitchError) as error:
        print("FALLO: %s" % error, file=err)
        return EXIT_ERROR
    except OSError as error:
        print("FALLO: %s" % error, file=err)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
