"""Aplica en bloque fecha y hora de un CSV `id,date,time` a la temporada (US-042; ADR-013 decision 2).

Solo biblioteca estandar, Python 3.9. Todo o nada: si una fila es invalida no se escribe nada.
  - `date` AAAA-MM-DD dentro de la temporada (1 ago del ano de inicio a 30 jun del ano siguiente); `time` HH:MM de Madrid entre
    09:00 y 23:59, o vacio para dejar la hora por confirmar (`kickoff.time` = null). Nunca se inventa una hora.
  - Solo partidos que no estan `finished` ni `cancelled`. El resto del partido (estado, marcador) no se toca.
  - Solo cuenta lo que cambia: sin cambios no se escribe nada. Si cambia algo, `updatedAt` nuevo (UTC, posterior al actual y no futuro;
    con `--same-commit` se acepta el que ya puso `apply-results` en el mismo lote).
La verificacion posterior la hace `scripts/apply-schedule`.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import apply_results
import round_sheet

TIMEZONE = ZoneInfo("Europe/Madrid")
INSTANT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
REQUIRED_COLUMNS = ("id", "date", "time")
UNTOUCHED_STATUSES = ("finished", "cancelled")
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
EARLIEST_TIME, LATEST_TIME = "09:00", "23:59"
SEASON_START_MONTH_DAY, SEASON_END_MONTH_DAY = "08-01", "06-30"
FIRST_DATA_LINE = 2

Row = Tuple[str, str, str]  # (id, fecha AAAA-MM-DD, hora HH:MM de Madrid o "")


def season_bounds(end_year: int) -> Tuple[str, str]:
    """Primer y ultimo dia aceptados; `end_year` es el ano en que termina la temporada (2027 para 2026/27)."""
    return "%d-%s" % (end_year - 1, SEASON_START_MONTH_DAY), "%d-%s" % (end_year, SEASON_END_MONTH_DAY)


def date_problem(date: str, end_year: int) -> Optional[str]:
    """Motivo por el que la fecha no es valida o cae fuera de la temporada; None si es correcta."""
    try:
        datetime.datetime.strptime(date, round_sheet.DATE_FORMAT)
    except ValueError:
        return "no es una fecha AAAA-MM-DD"
    first, last = season_bounds(end_year)
    if not first <= date <= last:
        return "fuera de la temporada (%s a %s)" % (first, last)
    return None


def time_problem(time: str) -> Optional[str]:
    """Motivo por el que la hora no es valida (formato o fuera de 09:00-23:59); None si es correcta. Vacio = por confirmar."""
    if not TIME_PATTERN.match(time):
        return "no es HH:MM"
    if not EARLIEST_TIME <= time <= LATEST_TIME:
        return "fuera de %s-%s" % (EARLIEST_TIME, LATEST_TIME)
    return None


def madrid_to_utc(date: str, time: str) -> str:
    """Hora local de Madrid de ese dia a instante UTC `...Z`. Dentro de 09:00-23:59 no hay huecos ni repeticiones de cambio horario."""
    local = datetime.datetime.strptime("%s %s" % (date, time), "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE)
    return local.astimezone(datetime.timezone.utc).strftime(INSTANT_FORMAT)


def read_rows(path: str, end_year: int) -> List[Row]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in REQUIRED_COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise apply_results.ApplyError("faltan columnas en el CSV: %s" % ", ".join(missing))
        rows = []  # type: List[Row]
        seen = set()
        for line, row in enumerate(reader, start=FIRST_DATA_LINE):
            identifier, date, time = (row["id"] or "").strip(), (row["date"] or "").strip(), (row["time"] or "").strip()
            if identifier in seen:
                raise apply_results.ApplyError("linea %d: %s aparece repetido" % (line, identifier))
            seen.add(identifier)
            problem = date_problem(date, end_year)
            if problem:
                raise apply_results.ApplyError("linea %d: %s date=%r %s" % (line, identifier, date, problem))
            problem = time_problem(time) if time else None
            if problem:
                raise apply_results.ApplyError("linea %d: %s time=%r %s" % (line, identifier, time, problem))
            rows.append((identifier, date, time))
        return rows


def apply_rows(season: Dict[str, Any], rows: List[Row], updated_at: str, now: datetime.datetime, same_commit: bool = False) -> int:
    """Modifica `season` en memoria y devuelve cuantos partidos cambian. Lanza ApplyError sin haber cambiado nada si una fila no es aplicable."""
    index = {match["id"]: match for phase in season["phases"] for round_ in phase["rounds"] for match in round_["matches"]}
    changes = []  # type: List[Tuple[Dict[str, Any], str, Optional[str]]]
    for identifier, date, time in rows:
        match = index.get(identifier)
        if match is None:
            raise apply_results.ApplyError("%s: no existe ese partido en la temporada" % identifier)
        if match["status"] in UNTOUCHED_STATUSES:
            raise apply_results.ApplyError("%s: esta %s; su fecha y su hora no se tocan" % (identifier, match["status"]))
        instant = madrid_to_utc(date, time) if time else None
        if match["kickoff"].get("date") != date or match["kickoff"].get("time") != instant:
            changes.append((match, date, instant))
    if changes:
        apply_results.check_updated_at(season, updated_at, now, same_batch=same_commit)
    for match, date, instant in changes:
        match["kickoff"] = {"date": date, "time": instant}
    if changes:
        season["updatedAt"] = updated_at
    return len(changes)


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aplica fecha y hora en bloque (US-042)")
    parser.add_argument("--feed", required=True)
    parser.add_argument("csv", help="CSV con columnas id,date,time (hora de Madrid HH:MM; vacia = por confirmar)")
    parser.add_argument("--now", help=argparse.SUPPRESS)  # AAAA-MM-DDTHH:MM:SSZ; solo para pruebas
    parser.add_argument("--updated-at", help="AAAA-MM-DDTHH:MM:SSZ; por defecto, ahora en UTC")
    parser.add_argument("--same-commit", action="store_true",
                        help="el lote de resultados del mismo commit ya puso este updatedAt: se acepta igual al actual")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    try:
        season = round_sheet.load_season(args.feed)
        with open(os.path.join(args.feed, round_sheet.INDEX_PATH), "r", encoding="utf-8") as handle:
            season_path = os.path.join(args.feed, round_sheet.SEASON_PATH, json.load(handle)["currentSeasonId"] + ".json")
        rows = read_rows(args.csv, int(season["seasonId"][:4]) + 1)
        now = datetime.datetime.strptime(args.now, INSTANT_FORMAT) if args.now else apply_results.now_utc()
        applied = apply_rows(season, rows, args.updated_at or now.strftime(INSTANT_FORMAT), now, args.same_commit)
    except (OSError, ValueError, KeyError, apply_results.ApplyError) as error:
        print("FALLO horario: %s. No se ha escrito nada." % error, file=sys.stderr)
        return 1
    if applied == 0:
        print("Sin cambios: fecha y hora ya coinciden con el feed. El feed no se ha tocado.")
        return 0
    with open(season_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(season, indent=2, ensure_ascii=False) + "\n")
    print("Aplicados %d horarios; updatedAt = %s" % (applied, season["updatedAt"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
