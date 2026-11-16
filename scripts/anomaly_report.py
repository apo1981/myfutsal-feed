"""Informe legible de las anomalias de `auto-apply-rfef` (traduce las lineas `anomalia [regla] ...` a un texto claro).

Solo biblioteca estandar, Python 3.9. No cambia ninguna regla ni decide nada: agrupa las lineas por causa, explica en
lenguaje llano que significa cada una, muestra los casos concretos y dice que hacer. Las lineas originales se conservan
al final como detalle tecnico. Una regla desconocida se muestra con una explicacion generica (nunca se pierde ninguna linea).
"""
from __future__ import annotations

import re
from typing import Dict, List, NamedTuple, Optional, Tuple

LINE_PATTERN = re.compile(r"^anomalia \[(?P<rule>[^\]]+)\] (?P<subject>.*?): esperado=(?P<expected>.*) obtenido=(?P<obtained>.*)$")
MAX_EXAMPLES = 8
COUNTER_NAMES = {"pg": "partidos ganados", "pe": "partidos empatados", "pp": "partidos perdidos", "gf": "goles a favor",
                 "gc": "goles en contra", "pt": "puntos"}
SIDE_NAMES = {"casa": "en casa", "fuera": "fuera"}
CHECK_ID_FIELDS = ("league_id", "group_code", "year")


class Item(NamedTuple):
    rule: str
    subject: str
    expected: str
    obtained: str
    raw: str


class Rule(NamedTuple):
    title: str      # que comprobacion ha fallado
    meaning: str    # que significa, en llano
    action: str     # que hacer


RULES = {
    "tabla": Rule(
        "La clasificación de la RFEF no cuadra con los resultados",
        "Con los resultados que el feed tendría tras aplicar esta jornada, la clasificación calculada es distinta de la que publica la RFEF.",
        "Compara los equipos afectados con la web de la RFEF. Lo más habitual: un resultado mal leído o editado en la RFEF, o un partido "
        "aplazado de una jornada anterior que ya se jugó (o no) en un lado y no en el otro. Corrige el feed a mano si el error es nuestro."),
    "tabla-equipo-sin-casar": Rule(
        "La tabla trae un equipo que no conocemos",
        "La clasificación de la RFEF incluye un identificador de equipo que no está en la lista de alias del feed.",
        "Añade el alias en scripts/rfef/aliases.json si el equipo es nuevo o ha cambiado de id."),
    "tabla-equipo-ausente": Rule(
        "Falta un equipo en la tabla de la RFEF",
        "Un equipo del feed no aparece en la clasificación que devuelve la RFEF.",
        "Revisa si la RFEF ha cambiado la tabla (disparador de revisión 3 del ADR-012) y vuelve a intentarlo más tarde."),
    "par-invertido": Rule(
        "Local y visitante invertidos",
        "La RFEF y el feed tienen el mismo partido pero con local y visitante al revés. Nunca se corrige girando el par.",
        "Comprueba cuál es correcto (web de la RFEF) y corrige el calendario del feed si el error es nuestro."),
    "par-de-mas": Rule(
        "La RFEF tiene un partido que el feed no tiene en esta jornada",
        "Aparece un enfrentamiento en la RFEF que no está en la jornada del feed.",
        "Puede ser un partido adelantado/aplazado o un error del calendario del feed. Revisa la jornada en la RFEF."),
    "par-de-menos": Rule(
        "El feed tiene un partido que la RFEF no tiene en esta jornada",
        "Un enfrentamiento del feed no aparece en la jornada de la RFEF.",
        "Puede ser un partido movido a otra jornada o un error del calendario del feed. Revisa la jornada en la RFEF."),
    "finished-distinto": Rule(
        "Un marcador ya publicado es distinto del de la RFEF",
        "El feed ya tiene el partido como finalizado con un marcador distinto del que ahora da la RFEF.",
        "Mira cuál es el correcto. Si la RFEF lo ha corregido, edita el marcador a mano en el feed; el script no sobrescribe resultados publicados."),
    "goles-fuera-de-rango": Rule(
        "Marcador imposible", "Los goles no son un número entre 0 y 25.",
        "Probable error de la RFEF o del formato de la respuesta. Espera y reintenta; si persiste, revisa el partido a mano."),
    "fecha-futura": Rule(
        "Partido con marcador pero con fecha futura", "Un partido trae goles y su fecha es posterior a hoy.",
        "Probable error en la fecha o en el marcador de la RFEF. Revisa el partido."),
    "fecha-temporada": Rule(
        "Fecha fuera de la temporada", "La fecha de un partido cae fuera de la temporada 2026/27.", "Revisa la fecha en la RFEF."),
    "no_hour": Rule(
        "Valor de hora no reconocido", "El indicador «sin hora» de la RFEF no es 0 ni 1.",
        "La RFEF ha podido cambiar el formato (disparador 3 del ADR-012). Revisa la respuesta cruda guardada."),
    "hora": Rule(
        "Hora inválida", "La hora de un partido no tiene formato HH:MM o está fuera de 09:00-23:59.", "Revisa la hora en la RFEF."),
    "jornada": Rule(
        "La RFEF ha devuelto otra jornada", "La respuesta no corresponde a la jornada pedida.",
        "Reintenta. Si persiste, la RFEF puede haber cambiado su API (disparador 3 del ADR-012)."),
    "equipos": Rule(
        "Número de equipos incorrecto", "La jornada no trae 16 equipos distintos.", "Revisa la jornada en la RFEF; puede estar incompleta."),
    "partidos": Rule(
        "Número de partidos incorrecto", "La jornada no trae 8 partidos.", "Revisa la jornada en la RFEF; puede estar incompleta."),
    "descansa": Rule(
        "Equipo en descanso", "La RFEF indica equipos que descansan, algo que no esperamos en esta liga.",
        "Revisa la jornada en la RFEF."),
    "par-repetido": Rule(
        "Partido repetido", "El mismo enfrentamiento aparece dos veces en la respuesta.", "Reintenta; si persiste, revisa la jornada en la RFEF."),
    "lecturas-distintas": Rule(
        "Las dos lecturas no coinciden", "La RFEF cambió datos entre las dos lecturas.", "Espera unos minutos y vuelve a lanzarlo."),
}
for _field in CHECK_ID_FIELDS:
    RULES[_field] = Rule(
        "La respuesta no es de la competición pedida",
        "El campo «%s» de la respuesta no coincide con lo que se pidió (liga, grupo o año)." % _field,
        "La RFEF puede haber cambiado sus identificadores (disparador 3 del ADR-012). Revisa la respuesta cruda guardada.")
GENERIC = Rule("Comprobación no clasificada", "El script ha detectado una incoherencia.", "Mira el detalle técnico.")


def parse(lines: List[str]) -> List[Item]:
    items = []
    for line in lines:
        found = LINE_PATTERN.match(line.strip())
        items.append(Item(found.group("rule"), found.group("subject"), found.group("expected"), found.group("obtained"), line)
                     if found else Item("", line, "", "", line))
    return items


def describe(item: Item) -> str:
    """Una frase por caso concreto."""
    if item.rule == "tabla":
        team, _, rest = item.subject.partition(" ")
        words = rest.split()
        counter = COUNTER_NAMES.get(words[-1], words[-1]) if words else rest
        side = SIDE_NAMES.get(words[0], "") if len(words) > 1 else "en total"
        return "%s, %s, %s: según los partidos del feed %s, según la tabla de la RFEF %s" % (team, side, counter, item.expected, item.obtained)
    if item.rule == "par-invertido":
        return "%s (feed: %s; RFEF: %s)" % (item.subject, item.expected.replace(" (feed)", ""), item.obtained.replace(" (RFEF)", ""))
    if item.rule in ("par-de-mas", "par-de-menos"):
        return "%s (%s)" % (item.subject, item.obtained)
    if item.rule == "finished-distinto":
        return "%s: feed %s, RFEF %s" % (item.subject, item.expected.replace(" (feed)", ""), item.obtained.replace(" (RFEF)", ""))
    if item.rule == "tabla-equipo-sin-casar":
        return item.subject
    if item.rule == "tabla-equipo-ausente":
        return item.subject
    return "%s: se esperaba %s y la RFEF da %s" % (item.subject, item.expected, item.obtained) if item.rule else item.raw


def group(items: List[Item]) -> List[Tuple[str, List[Item]]]:
    order = []  # type: List[str]
    grouped = {}  # type: Dict[str, List[Item]]
    for item in items:
        if item.rule not in grouped:
            grouped[item.rule] = []
            order.append(item.rule)
        grouped[item.rule].append(item)
    return [(rule, grouped[rule]) for rule in order]


def teams_affected(items: List[Item]) -> Optional[List[str]]:
    if not items or items[0].rule != "tabla":
        return None
    return sorted({item.subject.split(" ", 1)[0] for item in items})


def explain(lines: List[str], note: Optional[str] = None) -> List[str]:
    """Texto completo del informe (una cadena por linea)."""
    notes = [line for line in lines if line.startswith("nota:")]
    items = parse([line for line in lines if not line.startswith("nota:")])
    note = note or (" ".join(notes) or None)
    out = ["Qué ha pasado: %d diferencia(s) en %d comprobación(es). El script se ha parado ANTES de escribir: el feed no se ha tocado y no hay commit." % (
        len(items), len(group(items))), ""]
    for number, (rule, members) in enumerate(group(items), start=1):
        info = RULES.get(rule, GENERIC)
        out.append("%d. %s (%d)" % (number, info.title, len(members)))
        out.append("   Qué significa: " + info.meaning)
        teams = teams_affected(members)
        if teams:
            out.append("   Equipos afectados (%d): %s" % (len(teams), ", ".join(teams)))
        for member in members[:MAX_EXAMPLES]:
            out.append("   - " + describe(member))
        if len(members) > MAX_EXAMPLES:
            out.append("   - … y %d más (están en el detalle técnico)" % (len(members) - MAX_EXAMPLES))
        out.append("   Qué hacer: " + info.action)
        out.append("")
    if note:
        out.append(note)
        out.append("")
    out.append("Detalle técnico:")
    out.extend("  " + item.raw for item in items)
    return out
