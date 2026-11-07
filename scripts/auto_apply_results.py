"""Aplicacion autonoma de una jornada con commit trazable y politica de anomalias (US-036; ADR-012 §5).

Solo biblioteca estandar, Python 3.9. Orquesta US-034 (lectura y cordura), US-037 (doble lectura estable) y US-035
(tabla y pares); aplica con `apply_results` y comprueba con `scripts/verify`.
  - Sin las tres comprobaciones no se aplica nada (no hay modo degradado). Todo el analisis es en memoria: ante cualquier
    anomalia o fallo de la tabla el JSON no se toca y no hay commit.
  - Los pares de la RFEF salen de las mismas respuestas ya validadas como la jornada N pedida (`jornada` de la cordura) y se
    comparan con la jornada N del feed.
  - Sin filas nuevas (ya aplicado o sin finalizados nuevos) no cambia nada ni hace commit.
  - Si `verify` falla tras aplicar, restaura el JSON anterior byte a byte.
  - El commit exige identidad git `noreply`, solo incluye el JSON de la temporada y lleva URL, instante UTC y SHA-256 de las
    tres respuestas. Nunca hace push.
Interruptor (US-038): `enabled` false = sin peticiones (salida 5); 403/429, robots que veta o 2 anomalias seguidas lo ponen a false.
Informe: lista las diferencias no aplicadas (fecha, hora, estado, aplazados, adelantados) en la salida de error.
Codigos de salida: 0 correcto (con o sin cambios); 2 error (identidad, tabla no disponible, verify, red); 3 anomalia; 4 parada de la RFEF; 5 deshabilitada.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, List, Optional, Tuple

import apply_results
import cross_check
import fetch_rfef_results as fetch
import rfef_switch
import round_sheet
import structure

TABLE_ENDPOINT_PATH = "/futsal_xhr/get_table_round"
UPDATED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
PROGRESS_PREFIX = "[progreso] "
COUNTDOWN_INTERVAL_SECONDS = 60
TABLE_ANOMALY_NOTE = ("nota: hay anomalias de tabla; revisar aplazados y Clausura (semantica de la clasificacion sin verificar, "
                      "disparador 1 de ADR-012)")

Read = Tuple[str, str, str]  # (URL, instante UTC, SHA-256)
VerifyRunner = Callable[[str], Tuple[bool, str]]


class TableUnavailable(fetch.ToolError):
    """La tabla no se pudo leer: no hay modo degradado, no se aplica nada."""


def default_verify(feed: str) -> Tuple[bool, str]:
    result = subprocess.run([os.path.join(feed, "scripts", "verify")], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            universal_newlines=True)
    return result.returncode == 0, result.stdout


def git(feed: str, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", feed] + list(arguments), stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


def require_noreply_identity(feed: str) -> None:
    """Autor y committer efectivos (incluidas variables de entorno) deben ser la direccion noreply del operador."""
    for variable in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        result = git(feed, "var", variable)
        email = result.stdout.rsplit("<", 1)[-1].split(">", 1)[0] if result.returncode == 0 else ""
        if not structure.OPERATOR_NOREPLY_PATTERN.match(email):
            raise fetch.ToolError("la identidad git (%s) no es una direccion noreply (<id>+<usuario>@users.noreply.github.com): "
                                  "no se crea ningun commit" % (email or "sin configurar"))


def require_clean_season(feed: str, season_path: str) -> None:
    result = git(feed, "status", "--porcelain", "--", season_path)
    if result.returncode != 0:
        raise fetch.ToolError("el feed no es un repositorio git: %s" % result.stderr.strip())
    if result.stdout.strip():
        raise fetch.ToolError("la temporada tiene cambios sin commitear; el commit mezclaria otro trabajo")


def read_table(transport: fetch.Transport, year: str, round_number: int) -> bytes:
    """Tercera peticion. 403/429/5xx paran la herramienta; cualquier otro fallo es tabla no disponible."""
    try:
        status, body = transport.request("POST", fetch.BASE_URL + TABLE_ENDPOINT_PATH, cross_check.table_request_body(round_number, year))
        fetch.check_status(status, TABLE_ENDPOINT_PATH)
    except fetch.StoppedError:
        raise
    except fetch.ToolError as error:
        raise TableUnavailable("tabla no disponible: %s" % error)
    return body


def progress(stderr: Any, message: str) -> None:
    """Linea de progreso (solo stderr, prefijo fijo)."""
    print(PROGRESS_PREFIX + message, file=stderr)


def countdown_wait(seconds: float, sleep: Callable[[float], None], now: Callable[[], datetime.datetime], stderr: Any,
                   interval: float = COUNTDOWN_INTERVAL_SECONDS) -> None:
    """Espera `seconds` anunciando la hora prevista (Madrid) y, como mucho, una linea de tiempo restante por intervalo."""
    due = (now() + datetime.timedelta(seconds=seconds)).astimezone(fetch.TIMEZONE)
    progress(stderr, "hora prevista de la 2a lectura: %s (espera de %d min %02d s)" % (due.strftime("%H:%M"), seconds // 60, seconds % 60))
    left = seconds
    while left > 0:
        step = min(interval, left)
        sleep(step)
        left -= step
        if left > 0:
            progress(stderr, "quedan %d min %02d s para la 2a lectura" % (left // 60, left % 60))


def run(args: argparse.Namespace, transport_factory: Callable[[], fetch.Transport], sleep: Callable[[float], None],
        now: Callable[[], datetime.datetime], stdout: Any, stderr: Any, verify_runner: VerifyRunner = default_verify) -> int:
    feed = args.feed
    progress(stderr, "comprobaciones iniciales e interruptor")
    try:
        rfef_switch.ensure_enabled(feed)  # antes de cualquier otra cosa: deshabilitada = ninguna peticion
    except rfef_switch.DisabledError:
        progress(stderr, "herramienta deshabilitada: sin peticiones")
        raise
    require_noreply_identity(feed)
    season = round_sheet.load_season(feed)
    season_path = os.path.join(round_sheet.SEASON_PATH, round_sheet.current_season_id(feed) + ".json")
    require_clean_season(feed, season_path)
    year = str(int(season["seasonId"][:4]) + 1)
    league, group = fetch.PHASES[args.phase]
    today = datetime.date.fromisoformat(args.today) if args.today else now().astimezone(fetch.TIMEZONE).date()
    round_data = fetch.feed_round(season, args.phase, args.round)
    aliases = fetch.load_aliases(feed)

    reads = []  # type: List[Read]

    def record(url: str, body: bytes) -> datetime.datetime:
        instant = now()
        reads.append((url, instant.astimezone(datetime.timezone.utc).strftime(UPDATED_AT_FORMAT), hashlib.sha256(body).hexdigest()))
        fetch.save_raw(feed, args.raw_dir, args.phase if len(reads) < 3 else args.phase + "-tabla", args.round, reads[-1][1], body)
        return instant

    def check(body: bytes) -> List[fetch.RfefMatch]:
        parsed, resting = fetch.parse_response(body)
        found = fetch.sanity_anomalies(parsed, resting, league, group, args.round, year, today)
        if found:
            raise fetch.AnomalyError(found)
        return parsed

    transport = transport_factory()
    matches_url = fetch.BASE_URL + fetch.ENDPOINT_PATH
    progress(stderr, "1a lectura")
    first_body = fetch.download(transport, args.phase, args.round, year, sleep)
    first_at = record(matches_url, first_body)
    first = check(first_body)  # una primera lectura invalida no justifica esperar ni pedir mas
    progress(stderr, "2a lectura")
    second_body = fetch.download_again(transport, args.phase, args.round, year, sleep, now, first_at,
                                       wait=lambda seconds: countdown_wait(seconds, sleep, now, stderr))
    record(matches_url, second_body)
    matches = check(second_body)
    unstable = fetch.stability_anomalies(first, matches)
    if unstable:
        raise fetch.AnomalyError(unstable)
    unmatched = fetch.resolve_teams(matches, aliases)
    if unmatched:
        raise fetch.ToolError("\n".join(unmatched))
    plan = fetch.build_plan(matches, round_data, aliases, fetch.later_rounds(season, args.phase, args.round))
    report = fetch.report_lines(plan, args.phase, args.round, matches_url, [(read[1], read[2]) for read in reads[:2]])

    if not plan.rows:
        # Sin cambios: no hace falta la tabla ni el commit (idempotencia y jornada sin partidos nuevos).
        print("Sin cambios: %s. El JSON no se ha tocado y no hay commit." % (
            "el lote ya estaba aplicado (%d partidos publicados iguales)" % plan.already_published if plan.already_published
            else "no hay partidos finalizados nuevos"), file=stderr)
        print_report(report, stderr)
        return fetch.EXIT_OK

    progress(stderr, "tabla")
    sleep(fetch.REQUEST_INTERVAL_SECONDS)
    table_body = read_table(transport, year, args.round)
    record(fetch.BASE_URL + TABLE_ENDPOINT_PATH, table_body)

    progress(stderr, "cruce con la tabla")
    rfef_pairs = [(aliases[m.home_datateam], aliases[m.away_datateam]) for m in matches]  # jornada N: validada por la cordura
    try:
        found = cross_check.verify(cross_check.apply_batch(season, plan.rows), args.phase, args.round, league, table_body, aliases, rfef_pairs)
    except cross_check.TableUnavailableError as error:
        raise TableUnavailable("tabla no disponible: %s" % error)
    if found:
        if any("[tabla" in line for line in found):
            found.append(TABLE_ANOMALY_NOTE)
        raise fetch.AnomalyError(found)

    progress(stderr, "aplicar y verify")
    commit = apply_and_commit(args, plan, reads, now(), today, season_path, verify_runner, stderr)
    print_report(report, stderr)
    print("Aplicados %d resultados de %s jornada %d; commit %s (sin push)" % (len(plan.rows), args.phase, args.round, commit), file=stdout)
    return fetch.EXIT_OK


def print_report(lines: List[str], stderr: Any) -> None:
    """Informe de diferencias no aplicadas (fecha, hora, estado, aplazados, adelantados): solo informa, nunca cambia el JSON."""
    for line in lines:
        print(line, file=stderr)


def apply_and_commit(args: argparse.Namespace, plan: fetch.Plan, reads: List[Read], instant: datetime.datetime, today: datetime.date,
                     season_path: str, verify_runner: VerifyRunner, stderr: Any) -> str:
    feed = args.feed
    absolute = os.path.join(feed, season_path)
    with open(absolute, "rb") as handle:
        original = handle.read()
    stamp = instant.astimezone(datetime.timezone.utc).strftime(UPDATED_AT_FORMAT)
    try:  # cualquier salida sin commit (incluida una excepcion o KeyboardInterrupt) deja el JSON como estaba
        with tempfile.TemporaryDirectory(prefix="auto-apply.") as directory:
            csv_path = os.path.join(directory, "batch.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(fetch.csv_text(plan.rows))
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
                code = apply_results.main(["--feed", feed, csv_path, "--today", today.isoformat(), "--now", stamp, "--updated-at", stamp])
        if code != 0:
            raise fetch.ToolError("apply-results ha fallado; se ha restaurado el JSON anterior:\n" + captured.getvalue().strip())
        passed, output = verify_runner(feed)
        if not passed:
            raise fetch.ToolError("verify no ha pasado tras aplicar; se ha restaurado el JSON anterior, sin commit:\n" + output.strip())
        progress(stderr, "commit")
        if git(feed, "diff", "--quiet", "--", season_path).returncode == 0:
            raise fetch.ToolError("apply-results no ha cambiado el JSON; no hay nada que commitear")
        result = git(feed, "commit", "-m", commit_message(args, plan, reads), "--", season_path)
        if result.returncode != 0:
            raise fetch.ToolError("git commit ha fallado; se ha restaurado el JSON anterior: %s" % result.stderr.strip())
    except BaseException:
        restore(absolute, original)
        raise
    return git(feed, "rev-parse", "--short", "HEAD").stdout.strip()


def restore(path: str, original: bytes) -> None:
    with open(path, "wb") as handle:
        handle.write(original)


def commit_message(args: argparse.Namespace, plan: fetch.Plan, reads: List[Read]) -> str:
    lines = ["feat: apply %s round %d results from RFEF" % (args.phase, args.round), "",
             "Matches: %s" % ", ".join(row[0] for row in plan.rows), ""]
    for label, (url, fetched_at, digest) in zip(("Read 1 (matches)", "Read 2 (matches)", "Table"), reads):
        lines.append("%s: %s %s sha256=%s" % (label, url, fetched_at, digest))
    return "\n".join(lines) + "\n"


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aplica una jornada de la RFEF si todas las comprobaciones pasan (US-036).")
    parser.add_argument("--feed", required=True, help="raiz del repositorio del feed")
    parser.add_argument("--phase", required=True, choices=sorted(fetch.PHASES))
    parser.add_argument("--round", required=True, type=int, choices=range(1, fetch.ROUNDS_PER_PHASE + 1), metavar="N")
    parser.add_argument("--raw-dir", dest="raw_dir", help="directorio de las respuestas crudas (fuera del repo; por defecto ../local-data/rfef)")
    parser.add_argument("--today", help="fecha de hoy AAAA-MM-DD en Madrid (pruebas)")
    return parser.parse_args(argv)


def main(argv: List[str], transport_factory: Callable[[], fetch.Transport] = fetch.Transport, sleep: Callable[[float], None] = time.sleep,
         now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.timezone.utc), stdout: Any = None,
         stderr: Any = None, verify_runner: VerifyRunner = default_verify) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    args = parse_args(argv)
    try:
        code = run(args, transport_factory, sleep, now, out, err, verify_runner)
        reset_anomaly_streak(args, err)
        return code
    except rfef_switch.DisabledError as error:
        print("DESHABILITADA: %s" % error, file=err)
        return fetch.EXIT_DISABLED
    except fetch.AnomalyError as error:
        print("ANOMALIA: no se ha modificado nada ni hay commit", file=err)
        for line in error.anomalies:
            print("  " + line, file=err)
        count_anomaly(args, err)
        return fetch.EXIT_ANOMALY
    except fetch.StoppedError as error:
        print("PARADA: %s" % error, file=err)
        if isinstance(error, fetch.BlockedError):
            print(fetch.disable_after_block(args.feed, error), file=err)
        return fetch.EXIT_STOPPED
    except (fetch.ToolError, rfef_switch.SwitchError, OSError, KeyError, ValueError) as error:
        print("FALLO: %s. El JSON queda como estaba y no hay commit." % error, file=err)
        return fetch.EXIT_ERROR


def count_anomaly(args: argparse.Namespace, stderr: Any) -> None:
    """Suma una anomalia; a la segunda seguida desactiva el interruptor. La cuenta es local (fuera del repositorio)."""
    try:
        state_dir = fetch.raw_directory(args.feed, args.raw_dir)
        streak = rfef_switch.record_anomaly(state_dir)
        if streak >= rfef_switch.ANOMALY_LIMIT:
            rfef_switch.disable(args.feed, "%d anomalias seguidas" % streak)
            print("Interruptor: %d anomalias seguidas; enabled puesto a false en %s. Solo el humano lo reactiva." % (
                streak, rfef_switch.CONFIG_PATH), file=stderr)
        else:
            print("Anomalias seguidas: %d de %d (una ejecucion en verde reinicia la cuenta)." % (streak, rfef_switch.ANOMALY_LIMIT),
                  file=stderr)
    except (fetch.ToolError, rfef_switch.SwitchError, OSError) as error:
        print("AVISO: no se ha podido registrar la anomalia ni actualizar el interruptor: %s" % error, file=stderr)


def reset_anomaly_streak(args: argparse.Namespace, stderr: Any) -> None:
    try:
        rfef_switch.reset_streak(fetch.raw_directory(args.feed, args.raw_dir))
    except (fetch.ToolError, OSError) as error:
        print("AVISO: no se ha podido reiniciar la cuenta de anomalias: %s" % error, file=stderr)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
