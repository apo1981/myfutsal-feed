"""Hoja de jornada y pendientes de resultado para el operador (US-007b2c, criterios 7 y 8; D-178, D-180).

Solo lectura. Solo biblioteca estandar, Python 3.9.
  --round N [--phase ID]   imprime la jornada N (de todas las fases de liga que la tengan, o de la indicada)
                           con fecha y hora en Europe/Madrid, local, visitante, estado y marcador.
  --pending [--csv]        lista los partidos `unknown` y los `scheduled` con fecha anterior a hoy, por jornada;
                           con --csv emite la plantilla `id,home,away,...` que rellena el operador (ver apply_results).
La hoja nunca inventa un marcador: un partido sin marcador final se muestra como «pendiente».
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("Europe/Madrid")
SEASON_PATH = os.path.join("public", "v1", "seasons")
INDEX_PATH = os.path.join("public", "v1", "index.json")
NO_TIME_LABEL = "sin hora"
PENDING_SCORE_LABEL = "pendiente"
UNPLAYED_BUT_PAST = "scheduled"
UNKNOWN_STATUS = "unknown"
CSV_COLUMNS = ["id", "home", "away", "jornada", "fecha", "local", "visitante"]
DATE_FORMAT = "%Y-%m-%d"

Entry = Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]  # (fase, jornada, partido)


def load_season(feed: str) -> Dict[str, Any]:
    with open(os.path.join(feed, INDEX_PATH), "r", encoding="utf-8") as handle:
        season_id = json.load(handle)["currentSeasonId"]
    with open(os.path.join(feed, SEASON_PATH, season_id + ".json"), "r", encoding="utf-8") as handle:
        return json.load(handle)


def team_names(season: Dict[str, Any]) -> Dict[str, str]:
    return {team["id"]: team["name"] for team in season["teams"]}


def kickoff_label(kickoff: Dict[str, Any]) -> str:
    """Fecha y hora en Europe/Madrid. `time` es un instante UTC; sin hora se muestra la fecha y «sin hora»."""
    if kickoff.get("time"):
        instant = datetime.datetime.strptime(kickoff["time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        local = instant.astimezone(TIMEZONE)
        return local.strftime("%Y-%m-%d %H:%M")
    return "%s %s" % (kickoff["date"], NO_TIME_LABEL)


def score_label(match: Dict[str, Any]) -> str:
    score = match.get("score")
    if score is None:
        return PENDING_SCORE_LABEL
    return "%d-%d" % (score["home"], score["away"])


def match_line(match: Dict[str, Any], names: Dict[str, str]) -> str:
    return "%-6s  %-16s  %s  -  %s  [%s]  %s" % (
        match["id"], kickoff_label(match["kickoff"]), names[match["homeTeamId"]], names[match["awayTeamId"]],
        match["status"], score_label(match))


def round_sheet(season: Dict[str, Any], number: int, phase_id: Optional[str]) -> List[str]:
    names = team_names(season)
    lines = []  # type: List[str]
    for phase in season["phases"]:
        if phase_id is not None and phase["id"] != phase_id:
            continue
        for round_ in phase["rounds"]:
            if round_["number"] != number:
                continue
            lines.append("%s - %s (%s): %d partidos. Local - visitante tal como esta en el feed; fechas en Europe/Madrid"
                         % (phase["name"], round_["name"], round_["id"], len(round_["matches"])))
            lines.extend("  " + match_line(match, names) for match in round_["matches"])
            lines.append("")
    return lines


def is_pending(match: Dict[str, Any], today: datetime.date) -> bool:
    if match["status"] == UNKNOWN_STATUS:
        return True
    kickoff_day = datetime.datetime.strptime(match["kickoff"]["date"], DATE_FORMAT).date()
    return match["status"] == UNPLAYED_BUT_PAST and kickoff_day < today


def pending_entries(season: Dict[str, Any], today: datetime.date) -> List[Entry]:
    return [(phase, round_, match) for phase in season["phases"] for round_ in phase["rounds"]
            for match in round_["matches"] if is_pending(match, today)]


def pending_report(season: Dict[str, Any], today: datetime.date) -> List[str]:
    names = team_names(season)
    entries = pending_entries(season, today)
    lines = ["Pendientes de resultado a %s: %d partidos (`unknown` o `scheduled` con fecha pasada)" % (today.isoformat(), len(entries))]
    current = None
    for phase, round_, match in entries:
        if round_["id"] != current:
            current = round_["id"]
            lines.append("%s - %s" % (phase["name"], round_["name"]))
        lines.append("  " + match_line(match, names))
    return lines


def pending_csv(season: Dict[str, Any], today: datetime.date, out: Any) -> None:
    """Plantilla: `home` y `away` vacios para que los rellene el operador; el resto es contexto para no equivocarse."""
    names = team_names(season)
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for _, round_, match in pending_entries(season, today):
        writer.writerow([match["id"], "", "", round_["number"], match["kickoff"]["date"],
                         names[match["homeTeamId"]], names[match["awayTeamId"]]])


def madrid_today() -> datetime.date:
    return datetime.datetime.now(TIMEZONE).date()


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hoja de jornada y pendientes (US-007b2c)")
    parser.add_argument("--feed", required=True)
    parser.add_argument("--round", type=int, dest="round_number", help="numero de jornada a imprimir")
    parser.add_argument("--phase", help="id de fase (apertura, clausura); por defecto, todas las que tengan esa jornada")
    parser.add_argument("--pending", action="store_true", help="lista los partidos pendientes de resultado")
    parser.add_argument("--csv", action="store_true", help="con --pending, emite la plantilla CSV")
    parser.add_argument("--today", help="AAAA-MM-DD; por defecto, hoy en Europe/Madrid")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    if (args.round_number is None) == (not args.pending):
        print("FALLO: indica --round N o --pending (uno solo)", file=sys.stderr)
        return 2
    if args.csv and not args.pending:
        print("FALLO: --csv solo se usa con --pending", file=sys.stderr)
        return 2
    try:
        today = datetime.datetime.strptime(args.today, DATE_FORMAT).date() if args.today else madrid_today()
        season = load_season(args.feed)
    except (OSError, ValueError, KeyError) as error:
        print("FALLO: %s" % error, file=sys.stderr)
        return 1
    if args.pending:
        if args.csv:
            pending_csv(season, today, sys.stdout)
        else:
            print("\n".join(pending_report(season, today)))
        return 0
    lines = round_sheet(season, args.round_number, args.phase)
    if not lines:
        print("FALLO: no hay jornada %d%s" % (args.round_number, " en la fase %s" % args.phase if args.phase else ""), file=sys.stderr)
        return 1
    print("\n".join(lines).rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
