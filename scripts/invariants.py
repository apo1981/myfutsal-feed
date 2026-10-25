"""Invariantes del feed que el esquema no expresa (US-007a, criterio 4; ADR-004 §3).

Solo biblioteca estandar, compatible con Python 3.9. No conoce Git: la version anterior
del documento de temporada llega con `--previous <fichero>`. No escribe nada.
Cada violacion se imprime como `invariante <nombre>: <identificador>: <detalle>`.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import gzip
import json
import os
import sys
from typing import Any, Dict, Iterator, List, Optional, Tuple

MAX_COMPRESSED_BYTES = 100 * 1024
LIVE_OR_FINISHED = ("live", "finished")
KNOCKOUT_KIND = "knockout"
DECIDED_BY_PENALTIES = "penalties"
MAX_NOTE_LENGTH = 140
INSTANT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
FUTURE_TOLERANCE = datetime.timedelta(minutes=15)  # D-544: cubre redondear a mano al minuto siguiente
CET_OFFSET_HOURS = 1
CEST_OFFSET_HOURS = 2
EU_TRANSITION_HOUR_UTC = 1
MARCH = 3
OCTOBER = 10
SUNDAY = 6
SEASON_URL_TEMPLATE = "seasons/%s.json"
FEED_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE_TEAMS = os.path.join("examples", "initial-load", "equipos.csv")


class Violation:
    """Una invariante incumplida: nombre estable, elemento afectado y explicacion."""

    def __init__(self, invariant: str, subject: str, detail: str) -> None:
        self.invariant = invariant
        self.subject = subject
        self.detail = detail

    def __str__(self) -> str:
        return "invariante %s: %s: %s" % (self.invariant, self.subject, self.detail)


# --------------------------------------------------------------------------
# Hora de Madrid (Europe/Madrid) sin base de datos de zonas horarias
# --------------------------------------------------------------------------

def last_sunday_transition(year: int, month: int) -> datetime.datetime:
    """Ultimo domingo del mes a las 01:00 UTC: cambio de hora de la Union Europea."""
    day = datetime.date(year, month, 31)
    while day.weekday() != SUNDAY:
        day -= datetime.timedelta(days=1)
    return datetime.datetime(day.year, day.month, day.day, EU_TRANSITION_HOUR_UTC)


def madrid_offset_hours(instant_utc: datetime.datetime) -> int:
    """Desfase de Madrid respecto a UTC: +2 en horario de verano (CEST), +1 el resto (CET)."""
    start = last_sunday_transition(instant_utc.year, MARCH)
    end = last_sunday_transition(instant_utc.year, OCTOBER)
    return CEST_OFFSET_HOURS if start <= instant_utc < end else CET_OFFSET_HOURS


def madrid_date(instant: str) -> str:
    """Fecha local de Madrid (AAAA-MM-DD) de un instante UTC `AAAA-MM-DDTHH:MM:SSZ`."""
    utc = datetime.datetime.strptime(instant, INSTANT_FORMAT)
    local = utc + datetime.timedelta(hours=madrid_offset_hours(utc))
    return local.strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# Recorrido del documento de temporada
# --------------------------------------------------------------------------

def iter_matches(season: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    for phase in season.get("phases", []):
        for rnd in phase.get("rounds", []):
            for match in rnd.get("matches", []):
                yield match


def duplicates(identifiers: List[str]) -> List[str]:
    seen = set()  # type: set
    repeated = []  # type: List[str]
    for identifier in identifiers:
        if identifier in seen and identifier not in repeated:
            repeated.append(identifier)
        seen.add(identifier)
    return repeated


def unique_ids(season: Dict[str, Any]) -> List[Violation]:
    rounds = [r for p in season.get("phases", []) for r in p.get("rounds", [])]
    groups = (
        ("equipo", "id-equipo-duplicado", season.get("teams", [])),
        ("fase", "id-fase-duplicado", season.get("phases", [])),
        ("ronda", "id-ronda-duplicado", rounds),
        ("partido", "id-partido-duplicado", list(iter_matches(season))),
    )
    found = []
    for noun, name, items in groups:
        for repeated in duplicates([item.get("id") for item in items]):
            found.append(Violation(name, repeated, "el identificador de %s aparece mas de una vez" % noun))
    return found


def example_team_ids() -> List[str]:
    with open(os.path.join(FEED_ROOT, EXAMPLE_TEAMS), "r", encoding="utf-8", newline="") as handle:
        return [row["id"] for row in csv.DictReader(handle)]


def example_id_violations(season: Dict[str, Any], examples: List[str]) -> List[Violation]:
    """Ningun `id` de equipo de los datos de ejemplo (`examples/initial-load/equipos.csv`) puede estar en `public/`.

    Mantiene A4 (sin datos inventados en lo publicado) durante toda la vida del feed, tambien en la puerta de CI.
    Solo se miran los equipos: los partidos de ejemplo no pueden publicarse sin su equipo (`equipo-inexistente`),
    y los ids de fases y rondas de los ejemplos (`apertura`, ...) pueden ser legitimos en los datos reales.
    """
    return [Violation("id-de-ejemplo", str(team.get("id")),
                      "el equipo %r de %s es un dato de ejemplo (%s)" % (team.get("id"), season.get("seasonId"), EXAMPLE_TEAMS))
            for team in season.get("teams", []) if team.get("id") in examples]


def kickoff_coherence(match: Dict[str, Any]) -> Optional[Violation]:
    """S-7: `kickoff.time` no nulo exige `kickoff.date` igual a la fecha de Madrid de ese instante."""
    kickoff = match.get("kickoff") or {}
    time, date = kickoff.get("time"), kickoff.get("date")
    if time is None:
        return None
    expected = madrid_date(time)
    if date != expected:
        return Violation(
            "kickoff-fecha-incoherente", match.get("id", "?"),
            "kickoff.time %s es el %s en Madrid y kickoff.date es %s" % (time, expected, date))
    return None


def match_violations(match: Dict[str, Any], team_ids: List[str]) -> List[Violation]:
    identifier = match.get("id", "?")
    found = []  # type: List[Violation]
    for key in ("homeTeamId", "awayTeamId"):
        if match.get(key) not in team_ids:
            found.append(Violation("equipo-inexistente", identifier, "%s %r no esta en teams" % (key, match.get(key))))
    if match.get("homeTeamId") == match.get("awayTeamId"):
        found.append(Violation("local-igual-visitante", identifier, "%r juega contra si mismo" % match.get("homeTeamId")))
    status, score = match.get("status"), match.get("score")
    if score is not None and status not in LIVE_OR_FINISHED:
        found.append(Violation("score-con-estado-invalido", identifier, "hay score con status %r" % status))
    if score is None and status == "finished":
        found.append(Violation("score-ausente-en-finished", identifier, "partido finished sin score"))
    coherence = kickoff_coherence(match)
    if coherence:
        found.append(coherence)
    return found


def has_winner(match: Dict[str, Any]) -> bool:
    """Hay ganador si el marcador no esta empatado, o si lo esta y `decidedBy` es `penalties` con una tanda no empatada."""
    score = match.get("score") or {}
    if score.get("home") != score.get("away"):
        return True
    shootout = match.get("penalties") or {}
    return match.get("decidedBy") == DECIDED_BY_PENALTIES and shootout.get("home") != shootout.get("away") \
        and shootout.get("home") is not None and shootout.get("away") is not None


def knockout_violations(season: Dict[str, Any]) -> List[Violation]:
    """US-012: un partido `finished` de una fase `knockout` necesita ganador. En liga un empate es valido."""
    found = []  # type: List[Violation]
    for phase in season.get("phases", []):
        if phase.get("kind") != KNOCKOUT_KIND:
            continue
        for rnd in phase.get("rounds", []):
            for match in rnd.get("matches", []):
                if match.get("status") == "finished" and match.get("score") is not None and not has_winner(match):
                    found.append(Violation(
                        "eliminatoria-sin-ganador", match.get("id", "?"),
                        "partido finished de la fase %s con marcador empatado y sin desenlace por penaltis valido" % phase.get("id", "?")))
    return found


def note_warnings(season: Dict[str, Any]) -> List[str]:
    """US-012: avisa (no falla) de una `note` de mas de MAX_NOTE_LENGTH caracteres o con saltos de linea."""
    warnings = []  # type: List[str]
    for match in iter_matches(season):
        note = match.get("note")
        if not isinstance(note, str):
            continue
        if len(note) > MAX_NOTE_LENGTH:
            warnings.append("aviso nota-larga: %s: la note tiene %d caracteres (maximo recomendado %d)" % (match.get("id", "?"), len(note), MAX_NOTE_LENGTH))
        if "\n" in note or "\r" in note:
            warnings.append("aviso nota-multilinea: %s: la note tiene saltos de linea" % match.get("id", "?"))
    return warnings


def season_violations(season: Dict[str, Any]) -> List[Violation]:
    found = unique_ids(season)
    team_ids = [team.get("id") for team in season.get("teams", [])]
    for team in season.get("teams", []):
        if team.get("crestURL") is not None:
            found.append(Violation("crest-url-no-nulo", team.get("id", "?"), "crestURL debe ser null hasta tener permiso (NFR-010)"))
    for match in iter_matches(season):
        found.extend(match_violations(match, team_ids))
    found.extend(knockout_violations(season))
    return found


# --------------------------------------------------------------------------
# Indice, tamano y version anterior
# --------------------------------------------------------------------------

def compressed_size(path: str) -> int:
    with open(path, "rb") as handle:
        return len(gzip.compress(handle.read(), 9, mtime=0))


def size_violation(path: str) -> Optional[Violation]:
    size = compressed_size(path)
    if size > MAX_COMPRESSED_BYTES:
        return Violation("tamano-comprimido", os.path.basename(path),
                         "%d bytes con gzip; el maximo es %d (NFR-012)" % (size, MAX_COMPRESSED_BYTES))
    return None


def index_violations(index: Dict[str, Any], v1_dir: str) -> List[Violation]:
    found = []  # type: List[Violation]
    seasons = index.get("seasons", [])
    ids = [entry.get("id") for entry in seasons]
    if index.get("currentSeasonId") not in ids:
        found.append(Violation("temporada-vigente-inexistente", str(index.get("currentSeasonId")), "currentSeasonId no esta en seasons"))
    root = os.path.realpath(v1_dir)
    for entry in seasons:
        identifier = str(entry.get("id"))
        target = os.path.realpath(os.path.join(v1_dir, str(entry.get("url"))))
        inside = target.startswith(root + os.sep)
        if not inside or not os.path.isfile(target):
            found.append(Violation("url-de-temporada-inexistente", identifier,
                                   "seasons[].url %r no apunta a un fichero de public/v1/" % entry.get("url")))
        found.extend(index_coherence(entry, target if inside and os.path.isfile(target) else None))
    return found


def index_coherence(entry: Dict[str, Any], season_file: Optional[str]) -> List[Violation]:
    """I-3: `url` debe ser `seasons/<id>.json` y el `seasonId` del fichero debe ser el `id` del indice.

    La invariante de `updatedAt` elige el fichero por `id`; si el indice apunta a otro, dejaria de aplicarse.
    """
    identifier = str(entry.get("id"))
    found = []  # type: List[Violation]
    expected_url = SEASON_URL_TEMPLATE % identifier
    if entry.get("url") != expected_url:
        found.append(Violation("indice-incoherente", identifier,
                               "seasons[].url es %r y debe ser %r" % (entry.get("url"), expected_url)))
    if season_file is not None:
        season_id = load(season_file).get("seasonId")
        if season_id != entry.get("id"):
            found.append(Violation("indice-incoherente", identifier,
                                   "el seasonId %r de %s no es el id del indice" % (season_id, entry.get("url"))))
    return found


def canonical_without_updated_at(season: Dict[str, Any]) -> str:
    content = {key: value for key, value in season.items() if key != "updatedAt"}
    return json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def parse_instant(value: Any) -> datetime.datetime:
    return datetime.datetime.strptime(str(value), INSTANT_FORMAT)


def readable_previous(path: str, log: List[str]) -> Optional[Dict[str, Any]]:
    """B-1: una version anterior ilegible es un commit que no paso la puerta, nunca se desplego.

    No hay nada que proteger frente a ella: se avisa y se omite la invariante, para no bloquear
    el commit que la corrige ni el relanzamiento manual.
    """
    try:
        previous = load(path)
        parse_instant(previous["updatedAt"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        log.append("updatedAt: version anterior ilegible (commit no publicado); se omite la invariante (%s)" % type(error).__name__)
        return None
    return previous


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)


def is_future(value: Any, now: datetime.datetime) -> bool:
    return parse_instant(value) > now + FUTURE_TOLERANCE


def future_violation(season: Dict[str, Any], now: datetime.datetime) -> Optional[Violation]:
    """D-544: `updatedAt` no puede estar en el futuro (mas alla de la tolerancia). Se aplica siempre."""
    if is_future(season.get("updatedAt"), now):
        return Violation("updated-at-en-el-futuro", str(season.get("seasonId")),
                         "updatedAt %s esta mas de %d min por delante de la hora actual (%s UTC); recuerda escribirlo en UTC" % (
                             season.get("updatedAt"), FUTURE_TOLERANCE.seconds // 60, now.strftime(INSTANT_FORMAT)))
    return None


def updated_at_violation(season: Dict[str, Any], previous: Dict[str, Any], log: List[str],
                         now: datetime.datetime) -> Optional[Violation]:
    """`updatedAt` debe avanzar cuando el contenido cambia (la app lo usa para la antiguedad de los datos).

    D-544: si la version anterior estaba en el futuro (error ya publicado), se acepta bajar a cualquier
    valor que no este en el futuro.
    """
    subject = str(season.get("seasonId"))
    if canonical_without_updated_at(season) == canonical_without_updated_at(previous):
        log.append("updatedAt: contenido sin cambios respecto a la version anterior")
        return None
    if is_future(previous.get("updatedAt"), now):
        log.append("updatedAt: corrige un updatedAt futuro de la version anterior")
        return None
    if parse_instant(season.get("updatedAt")) <= parse_instant(previous.get("updatedAt")):
        return Violation("updated-at-sin-avanzar", subject,
                         "el contenido cambio pero updatedAt %s no es posterior al anterior %s" % (
                             season.get("updatedAt"), previous.get("updatedAt")))
    log.append("updatedAt: contenido cambiado y updatedAt posterior")
    return None


def updated_at_check(season: Dict[str, Any], previous_path: Optional[str], log: List[str],
                     now: datetime.datetime) -> List[Violation]:
    """Aplica la invariante de `updatedAt` segun haya, no haya o sea ilegible la version anterior.

    S-12: una version anterior ilegible solo se avisa; no es ademas una «primera publicacion».
    """
    found = []  # type: List[Violation]
    future = future_violation(season, now)
    if future:
        found.append(future)
    if previous_path is None:
        log.append("updatedAt: primera publicacion (sin version anterior)")
        return found
    previous = readable_previous(previous_path, log)
    if previous is not None:
        violation = updated_at_violation(season, previous, log, now)
        if violation:
            found.append(violation)
    return found


# --------------------------------------------------------------------------
# Orquestacion
# --------------------------------------------------------------------------

def load(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def season_paths(v1_dir: str) -> List[Tuple[str, str]]:
    directory = os.path.join(v1_dir, "seasons")
    if not os.path.isdir(directory):
        return []
    return [(name[:-len(".json")], os.path.join(directory, name)) for name in sorted(os.listdir(directory)) if name.endswith(".json")]


def check_feed(public_dir: str, season_id: Optional[str], previous_path: Optional[str], log: List[str],
               now: datetime.datetime) -> List[Violation]:
    v1_dir = os.path.join(public_dir, "v1")
    index = load(os.path.join(v1_dir, "index.json"))
    found = index_violations(index, v1_dir)
    found_size = size_violation(os.path.join(v1_dir, "index.json"))
    if found_size:
        found.append(found_size)
    target_id = season_id or index.get("currentSeasonId")
    paths = season_paths(v1_dir)
    if target_id not in [identifier for identifier, _ in paths]:
        found.append(Violation("temporada-vigente-sin-fichero", str(target_id),
                               "no hay seasons/%s.json: la invariante de updatedAt no se puede aplicar" % target_id))
    try:
        examples = example_team_ids()
    except (OSError, KeyError) as error:
        examples = []
        found.append(Violation("id-de-ejemplo", EXAMPLE_TEAMS, "no se pueden leer los ids de ejemplo (%s): la comprobacion no se puede aplicar" % type(error).__name__))
    for identifier, path in paths:
        season = load(path)
        found.extend(season_violations(season))
        log.extend(note_warnings(season))
        found.extend(example_id_violations(season, examples))
        size = size_violation(path)
        if size:
            found.append(size)
        if identifier == target_id:
            found.extend(updated_at_check(season, previous_path, log, now))
    return found


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Invariantes del feed (US-007a)")
    parser.add_argument("--feed", required=True, help="raiz del repositorio del feed")
    parser.add_argument("--public", help="directorio public/ (por defecto <feed>/public)")
    parser.add_argument("--season", help="temporada a la que se aplica --previous (por defecto currentSeasonId)")
    parser.add_argument("--previous", help="version anterior de seasons/<id>.json; sin ella, primera publicacion")
    parser.add_argument("--now", help=argparse.SUPPRESS)  # AAAA-MM-DDTHH:MM:SSZ; solo para pruebas (D-544)
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    public_dir = args.public or os.path.join(args.feed, "public")
    log = []  # type: List[str]
    try:
        now = parse_instant(args.now) if args.now else utc_now()
        violations = check_feed(public_dir, args.season, args.previous, log, now)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print("FALLO invariantes: no se pudo leer el feed (valide antes el esquema): %s: %s" % (type(error).__name__, error), file=sys.stderr)
        return 1
    for line in log:
        print(line)
    for violation in violations:
        print("FALLO " + str(violation), file=sys.stderr)
    if violations:
        print("%d invariante(s) incumplida(s)" % len(violations), file=sys.stderr)
        return 1
    print("invariantes correctas")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
