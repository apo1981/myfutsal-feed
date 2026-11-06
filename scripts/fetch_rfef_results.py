"""Descarga una jornada de la RFEF y genera el CSV `id,home,away` de sus partidos finalizados (US-034; ADR-011, ADR-012).

Solo biblioteca estandar, Python 3.9. Una lectura de `POST /futsal_xhr/get_matchs_round` (JSON, ver docs/spike-rfef-resultados.md).
  - Nunca inventa un marcador: solo `status_txt == "Finalizado"` con dos enteros de 0 a 25 genera fila.
  - Reglas de cordura y casado de equipos/partidos: cualquier fallo termina sin CSV.
  - Diferencias de fecha, hora o estado respecto al feed: solo se informan.
  - Respeto: robots.txt antes, User-Agent no personal, sin cookies ni reintentos, parada ante 403/429/5xx.
  - La respuesta cruda se guarda solo fuera del repositorio (../local-data/rfef/).
La doble lectura, la clasificacion y el interruptor son de US-035..US-037.

Codigos de salida: 0 correcto; 2 error (uso, parseo, equipo o partido sin casar, red); 3 anomalia; 4 parada por la RFEF.
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

import round_sheet

TIMEZONE = ZoneInfo("Europe/Madrid")
BASE_URL = "https://futsal.rfef.es"
ENDPOINT_PATH = "/futsal_xhr/get_matchs_round"
ROBOTS_PATH = "/robots.txt"
CATEGORY = "25"
USER_AGENT = "myfutsal-feed-tool/1.0 (offline results tool; non-personal)"
REQUEST_INTERVAL_SECONDS = 10
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

EXIT_OK, EXIT_ERROR, EXIT_ANOMALY, EXIT_STOPPED = 0, 2, 3, 4


class ToolError(Exception):
    """Error explicito: no se genera CSV."""


class ParseError(ToolError):
    """La respuesta no tiene la estructura esperada."""


class StoppedError(ToolError):
    """La RFEF veta la ruta, limita o falla: hay que parar."""


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


def build_plan(matches: List[RfefMatch], round_data: Dict[str, Any], aliases: Dict[str, str]) -> Plan:
    """Casa por par local-visitante (nunca por posicion). Lanza ToolError (partido ausente) o AnomalyError (marcador distinto)."""
    by_pair = {(m["homeTeamId"], m["awayTeamId"]): m for m in round_data["matches"]}
    plan = Plan()
    missing = []  # type: List[str]
    conflicts = []  # type: List[str]
    for match in matches:
        pair = (aliases[match.home_datateam], aliases[match.away_datateam])
        feed_match = by_pair.get(pair)
        if feed_match is None:
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


def add_differences(plan: Plan, feed_match: Dict[str, Any], match: RfefMatch, score: Optional[Tuple[int, int]]) -> None:
    kickoff = feed_match["kickoff"]
    if kickoff.get("date") != match.day:
        plan.differences.append("%s: fecha feed=%s, RFEF=%s" % (feed_match["id"], kickoff.get("date"), match.day))
    if (madrid_time(kickoff) or None) != match.hour:
        plan.differences.append("%s: hora feed=%s, RFEF=%s" % (feed_match["id"], madrid_time(kickoff) or NO_TIME,
                                                               match.hour or NO_TIME))
    if feed_match["status"] == "finished" and score is None:
        plan.differences.append("%s: estado feed=finished, RFEF=%s sin marcador final" % (feed_match["id"], match.status_text))


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


def report_lines(plan: Plan, phase: str, number: int, source: str, fetched_at: str, digest: str) -> List[str]:
    lines = ["informe fetch-rfef-results: %s jornada %d" % (phase, number),
             "fuente: %s" % source,
             "instante UTC: %s" % fetched_at,
             "SHA-256: %s" % digest,
             "finalizados a aplicar: %d; ya publicados iguales: %d; pendientes: %d" % (
                 len(plan.rows), plan.already_published, plan.pending)]
    lines.extend("aviso: " + warning for warning in plan.warnings)
    if plan.differences:
        lines.append("diferencias con el feed (solo informativas, no cambian el CSV):")
        lines.extend("  " + difference for difference in plan.differences)
    else:
        lines.append("sin diferencias de fecha, hora ni estado con el feed")
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
    if status in STOP_STATUSES or status >= 500:
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
        raise StoppedError("robots.txt veta %s: no se hace la peticion" % ENDPOINT_PATH)


def request_body(phase: str, number: int, year: str) -> bytes:
    league, group = PHASES[phase]
    fields = [("category", CATEGORY), ("league", league), ("round", str(number)), ("action", "competicion"),
              ("year", year), ("group", group)]
    return urllib.parse.urlencode(fields).encode("ascii")


def download(transport: Transport, phase: str, number: int, year: str, sleep: Callable[[float], None]) -> bytes:
    ensure_robots_allow(transport)
    sleep(REQUEST_INTERVAL_SECONDS)
    status, body = transport.request("POST", BASE_URL + ENDPOINT_PATH, request_body(phase, number, year))
    check_status(status, ENDPOINT_PATH)
    return body


def save_raw(feed: str, raw_dir: Optional[str], phase: str, number: int, fetched_at: str, body: bytes) -> str:
    """Guarda la respuesta cruda solo fuera del repositorio del feed (nunca versionada)."""
    directory = os.path.realpath(raw_dir or os.path.join(feed, RAW_DIR_RELATIVE))
    root = os.path.realpath(feed)
    if directory == root or directory.startswith(root + os.sep):
        raise ToolError("la respuesta cruda no puede guardarse dentro del repositorio del feed")
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

    fetched_at = now().astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if args.from_file:
        with open(args.from_file, "rb") as handle:
            body = handle.read()
        source = "fichero local %s (sin peticiones)" % os.path.basename(args.from_file)
    else:
        body = download(transport_factory(), args.phase, args.round, year, sleep)
        source = BASE_URL + ENDPOINT_PATH
        save_raw(args.feed, args.raw_dir, args.phase, args.round, fetched_at, body)
    digest = hashlib.sha256(body).hexdigest()

    matches, resting = parse_response(body)
    found = sanity_anomalies(matches, resting, league, group, args.round, year, today)
    if found:
        raise AnomalyError(found)
    aliases = load_aliases(args.feed)
    unmatched = resolve_teams(matches, aliases)
    if unmatched:
        raise ToolError("\n".join(unmatched))
    plan = build_plan(matches, round_data, aliases)

    text = csv_text(plan.rows)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
    else:
        stdout.write(text)
    for line in report_lines(plan, args.phase, args.round, source, fetched_at, digest):
        print(line, file=stderr)
    return EXIT_OK


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CSV id,home,away de una jornada de la RFEF (US-034).")
    parser.add_argument("--feed", required=True, help="raiz del repositorio del feed")
    parser.add_argument("--phase", required=True, choices=sorted(PHASES))
    parser.add_argument("--round", required=True, type=int, choices=range(1, ROUNDS_PER_PHASE + 1), metavar="N")
    parser.add_argument("--out", help="fichero CSV (por defecto, salida estandar)")
    parser.add_argument("--from-file", dest="from_file", help="respuesta local: no hace ninguna peticion")
    parser.add_argument("--raw-dir", dest="raw_dir", help="directorio de la respuesta cruda (fuera del repo; por defecto ../local-data/rfef)")
    parser.add_argument("--today", help="fecha de hoy AAAA-MM-DD en Madrid (pruebas)")
    return parser.parse_args(argv)


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
    except StoppedError as error:
        print("PARADA: %s" % error, file=err)
        return EXIT_STOPPED
    except ToolError as error:
        print("FALLO: %s" % error, file=err)
        return EXIT_ERROR
    except OSError as error:
        print("FALLO: %s" % error, file=err)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
