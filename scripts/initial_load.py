"""Comprobacion de la carga inicial antes del push (US-007b2a1, criterio 5; ADR-004, D-078).

Comando de un solo uso, fuera del workflow y de la politica. Solo biblioteca estandar, Python 3.9.
No escribe nada. Comprueba sobre la temporada vigente, sin fijar el numero de equipos:
  - ningun `id` es el marcador `pendiente` y ninguna fase es `knockout`;
  - cada fase de liga con N equipos tiene N-1 jornadas de N/2 partidos, cada equipo juega una vez
    por jornada y cada pareja se enfrenta una vez por fase;
  - ningun partido anterior al corte queda `scheduled` o `live`, y ninguno `finished` es posterior;
y ademas esquema, invariantes de US-007a y tamano comprimido (invariantes). Resume cada fase de liga
(jornadas y partidos) y lista los `unknown`.
Cada fallo se imprime como `FALLO carga-inicial <nombre>: <identificador>: <detalle>`.
"""
from __future__ import annotations

import argparse
import datetime
import itertools
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

import invariants

PLACEHOLDER_ID = "pendiente"
KNOCKOUT_KIND = "knockout"
LEAGUE_KIND = "league"
UNPLAYED_STATUSES = ("scheduled", "live")
FINISHED_STATUS = "finished"
UNKNOWN_STATUS = "unknown"
MATCHES_PER_PAIR = 1
DATE_FORMAT = "%Y-%m-%d"


class Finding:
    """Un incumplimiento de la carga inicial: nombre estable, elemento afectado y explicacion."""

    def __init__(self, name: str, subject: str, detail: str) -> None:
        self.name = name
        self.subject = subject
        self.detail = detail

    def __str__(self) -> str:
        return "carga-inicial %s: %s: %s" % (self.name, self.subject, self.detail)


def placeholder_findings(season: Dict[str, Any]) -> List[Finding]:
    """D-074(1): el marcador `pendiente` no puede quedar en equipos, fases, rondas ni partidos."""
    found = []  # type: List[Finding]
    rounds = [r for p in season.get("phases", []) for r in p.get("rounds", [])]
    groups = (("equipo", season.get("teams", [])), ("fase", season.get("phases", [])),
              ("ronda", rounds), ("partido", list(invariants.iter_matches(season))))
    for noun, items in groups:
        for item in items:
            if item.get("id") == PLACEHOLDER_ID:
                found.append(Finding("id-pendiente", PLACEHOLDER_ID, "el marcador `pendiente` sigue en un elemento de tipo %s" % noun))
    return found


def knockout_findings(season: Dict[str, Any]) -> List[Finding]:
    """S-4 de la revision de US-007a2: el playoff se anade en US-009, no en la carga inicial."""
    return [Finding("fase-knockout", phase.get("id", "?"), "la carga inicial no incluye el playoff")
            for phase in season.get("phases", []) if phase.get("kind") == KNOCKOUT_KIND]


def pair_of(match: Dict[str, Any]) -> Tuple[str, str]:
    return tuple(sorted((str(match.get("homeTeamId")), str(match.get("awayTeamId")))))  # type: ignore[return-value]


def round_findings(rnd: Dict[str, Any], team_ids: List[str]) -> List[Finding]:
    """Una jornada completa: N/2 partidos y cada equipo exactamente una vez."""
    matches = rnd.get("matches", [])
    subject = rnd.get("id", "?")
    found = []  # type: List[Finding]
    if len(matches) != len(team_ids) // 2:
        found.append(Finding("jornada-incompleta", subject, "tiene %d partidos y debe tener %d"
                             % (len(matches), len(team_ids) // 2)))
    appearances = [team for match in matches for team in (match.get("homeTeamId"), match.get("awayTeamId"))]
    for team in team_ids:
        times = appearances.count(team)
        if times != 1:
            found.append(Finding("equipo-no-juega-una-vez", subject, "%r aparece %d veces" % (team, times)))
    return found


def pairing_findings(phase: Dict[str, Any], team_ids: List[str]) -> List[Finding]:
    """Cada pareja de equipos se enfrenta exactamente una vez por fase."""
    counts = {}  # type: Dict[Tuple[str, str], int]
    for rnd in phase.get("rounds", []):
        for match in rnd.get("matches", []):
            counts[pair_of(match)] = counts.get(pair_of(match), 0) + 1
    found = []  # type: List[Finding]
    for pair in itertools.combinations(sorted(team_ids), 2):
        times = counts.get(pair, 0)
        if times != MATCHES_PER_PAIR:
            found.append(Finding("pareja-no-una-vez", phase.get("id", "?"), "%s-%s se enfrentan %d veces" % (pair[0], pair[1], times)))
    return found


def league_phase_findings(phase: Dict[str, Any], team_ids: List[str]) -> List[Finding]:
    subject = phase.get("id", "?")
    if len(team_ids) < 2 or len(team_ids) % 2 != 0:
        return [Finding("equipos-impares", subject, "hay %d equipos; una liga completa necesita un numero par" % len(team_ids))]
    found = []  # type: List[Finding]
    rounds = phase.get("rounds", [])
    if len(rounds) != len(team_ids) - 1:
        found.append(Finding("jornadas-incompletas", subject, "tiene %d jornadas y con %d equipos debe tener %d"
                             % (len(rounds), len(team_ids), len(team_ids) - 1)))
    for rnd in rounds:
        found.extend(round_findings(rnd, team_ids))
    found.extend(pairing_findings(phase, team_ids))
    return found


def calendar_findings(season: Dict[str, Any]) -> List[Finding]:
    team_ids = [team.get("id") for team in season.get("teams", [])]
    found = []  # type: List[Finding]
    for phase in season.get("phases", []):
        if phase.get("kind") == LEAGUE_KIND:
            found.extend(league_phase_findings(phase, team_ids))
    return found


def cutoff_findings(season: Dict[str, Any], cutoff: datetime.date) -> List[Finding]:
    """Antes del corte nada pendiente; despues del corte nada jugado. Sin fecha no se puede juzgar."""
    found = []  # type: List[Finding]
    for match in invariants.iter_matches(season):
        date = (match.get("kickoff") or {}).get("date")
        if date is None:
            continue
        played_on = datetime.datetime.strptime(date, DATE_FORMAT).date()
        status = match.get("status")
        if played_on < cutoff and status in UNPLAYED_STATUSES:
            found.append(Finding("pasado-sin-resultado", match.get("id", "?"),
                                 "%s es anterior al corte %s y esta %s" % (date, cutoff, status)))
        if played_on > cutoff and status == FINISHED_STATUS:
            found.append(Finding("finalizado-tras-el-corte", match.get("id", "?"),
                                 "%s es posterior al corte %s y esta finished" % (date, cutoff)))
    return found


def unknown_matches(season: Dict[str, Any]) -> List[str]:
    return ["%s (%s)" % (match.get("id", "?"), (match.get("kickoff") or {}).get("date"))
            for match in invariants.iter_matches(season) if match.get("status") == UNKNOWN_STATUS]


def phase_summaries(season: Dict[str, Any]) -> List[str]:
    """7d-4: una linea por fase de liga con sus jornadas y partidos, para ver al instante si falta una fase."""
    return ["%s: %d jornadas, %d partidos" % (
                phase.get("id", "?"), len(phase.get("rounds", [])),
                sum(len(rnd.get("matches", [])) for rnd in phase.get("rounds", [])))
            for phase in season.get("phases", []) if phase.get("kind") == LEAGUE_KIND]


def season_findings(season: Dict[str, Any], cutoff: datetime.date) -> List[Finding]:
    return (placeholder_findings(season) + knockout_findings(season)
            + calendar_findings(season) + cutoff_findings(season, cutoff))


def schema_problems(public_dir: str, season_path: str, validate_schema: str) -> Optional[str]:
    """Ejecuta `scripts/validate-schema` (necesita el venv); devuelve su salida si falla."""
    result = subprocess.run([validate_schema, os.path.join(public_dir, "v1", "index.json"), season_path],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    return None if result.returncode == 0 else result.stdout.strip()


def parse_cutoff(value: str) -> datetime.date:
    try:
        return datetime.datetime.strptime(value, DATE_FORMAT).date()
    except ValueError:
        raise argparse.ArgumentTypeError("la fecha de corte debe tener la forma AAAA-MM-DD: %r" % value)


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Comprobacion de la carga inicial (US-007b2a1)")
    parser.add_argument("--feed", required=True, help="raiz del repositorio del feed")
    parser.add_argument("--cutoff", required=True, type=parse_cutoff, help="fecha de corte AAAA-MM-DD")
    parser.add_argument("--public", help="directorio public/ (por defecto <feed>/public)")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    public_dir = args.public or os.path.join(args.feed, "public")
    v1_dir = os.path.join(public_dir, "v1")
    try:
        season_id = invariants.load(os.path.join(v1_dir, "index.json"))["currentSeasonId"]
        season_path = os.path.join(v1_dir, "seasons", "%s.json" % season_id)
        season = invariants.load(season_path)
        findings = season_findings(season, args.cutoff)
        problems = schema_problems(public_dir, season_path, os.path.join(args.feed, "scripts", "validate-schema"))
        rules = invariants.check_feed(public_dir, None, None, [], invariants.utc_now())
        size = invariants.compressed_size(season_path)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print("FALLO carga-inicial: no se pudo leer el feed: %s: %s" % (type(error).__name__, error), file=sys.stderr)
        return 1
    failures = ["FALLO " + str(finding) for finding in findings]
    if problems:
        failures.append("FALLO carga-inicial esquema: %s" % problems)
    failures.extend("FALLO " + str(violation) for violation in rules)
    unknown = unknown_matches(season)
    print("temporada %s, corte %s: %d equipos, %d partidos, %d bytes con gzip (maximo %d)"
          % (season_id, args.cutoff, len(season.get("teams", [])), len(list(invariants.iter_matches(season))),
             size, invariants.MAX_COMPRESSED_BYTES))
    for summary in phase_summaries(season):
        print(summary)
    print("partidos unknown para el runbook: %d%s" % (len(unknown), "".join("\n  " + item for item in unknown)))
    # 7d-5: con `2>&1 | tee` stdout va por un tubo y se almacena en bloque; se vuelca antes de escribir los fallos.
    sys.stdout.flush()
    for failure in failures:
        print(failure, file=sys.stderr)
    if failures:
        print("%d incumplimiento(s) en la carga inicial" % len(failures), file=sys.stderr)
        return 1
    print("carga inicial correcta")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
