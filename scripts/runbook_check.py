"""Comprobacion de contenido de RUNBOOK.md (US-007a, criterio 8).

Cada seccion obligatoria debe existir como encabezado de nivel 2 y mencionar sus conceptos
clave; y cada ruta `scripts/...` citada en los bloques de codigo debe existir en el repositorio.
Solo biblioteca estandar, compatible con Python 3.9. No escribe nada.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import List, Optional, Tuple

# (nombre, patron del encabezado, conceptos que deben aparecer en el cuerpo de la seccion)
REQUIRED_SECTIONS = [
    ("primera vez", r"primera vez", [r"crear.*repositorio", r"Pages", r"push protection|protecci[oó]n contra push", r"noreply", r"Block command line pushes"]),
    ("publicar un resultado", r"publicar un resultado", [r"updatedAt", r"scripts/verify"]),
    ("cambiar hora, aplazar o suspender", r"cambiar hora|aplazar|suspender", [r"postponed", r"suspended", r"kickoff"]),
    ("falla validate", r"falla .*validate", [r"scripts/validate-schema", r"scripts/check-invariants"]),
    ("falla la comprobacion posterior", r"falla la comprobaci[oó]n posterior", [r"scripts/smoke-check", r"600"]),
    ("revertir (rollback)", r"revertir|rollback", [r"git revert --no-commit", r"updatedAt"]),
    ("verificacion en el iPhone", r"iPhone", [r"pull to refresh"]),
    ("anotar el tiempo por dia de partido", r"tiempo", [r"tiempo:", r"git log"]),
]
SCRIPT_PATH = re.compile(r"\bscripts/[A-Za-z0-9_./-]+")
FENCE = "```"


def split_sections(text: str) -> List[Tuple[str, str]]:
    """Secciones de nivel 2: (encabezado, cuerpo)."""
    sections = []  # type: List[Tuple[str, str]]
    heading = None  # type: Optional[str]
    body = []  # type: List[str]
    for line in text.splitlines():
        if line.startswith("## "):
            if heading is not None:
                sections.append((heading, "\n".join(body)))
            heading, body = line[3:].strip(), []
        elif heading is not None:
            body.append(line)
    if heading is not None:
        sections.append((heading, "\n".join(body)))
    return sections


def code_blocks(text: str) -> List[str]:
    blocks, current, inside = [], [], False
    for line in text.splitlines():
        if line.strip().startswith(FENCE):
            if inside:
                blocks.append("\n".join(current))
                current = []
            inside = not inside
        elif inside:
            current.append(line)
    return blocks


def section_problems(text: str) -> List[str]:
    sections = split_sections(text)
    problems = []
    for name, heading_pattern, concepts in REQUIRED_SECTIONS:
        matching = [body for heading, body in sections if re.search(heading_pattern, heading, re.IGNORECASE)]
        if not matching:
            problems.append("falta la seccion '%s'" % name)
            continue
        for concept in concepts:
            if not any(re.search(concept, body, re.IGNORECASE) for body in matching):
                problems.append("la seccion '%s' no menciona %r" % (name, concept))
    return problems


ROLLBACK_HEADING = re.compile(r"revertir|rollback", re.IGNORECASE)
STAGES_BEFORE_COMMIT = re.compile(r"(?s)^git add\b.*^git commit\b|^git commit (?:-[a-zA-Z]*a[a-zA-Z]*|--all)\b", re.MULTILINE)


def rollback_staging_problems(text: str) -> List[str]:
    """B-2: `git revert --no-commit` deja el contenido en el indice; sin `git add` (o `commit -a`) antes del
    `git commit` se confirma el `updatedAt` antiguo. Se mira solo el codigo, no la prosa."""
    for heading, body in split_sections(text):
        if ROLLBACK_HEADING.search(heading):
            code = "\n".join(line.strip() for block in code_blocks(body) for line in block.splitlines())
            if not STAGES_BEFORE_COMMIT.search(code):
                return ["la seccion 'revertir (rollback)' no hace `git add` (o `git commit -a`) antes de `git commit` en sus bloques de codigo"]
    return []


def command_problems(text: str, feed: str) -> List[str]:
    cited = sorted(set(path.rstrip(".,;:") for block in code_blocks(text) for path in SCRIPT_PATH.findall(block)))
    return ["el comando %s citado en un bloque de codigo no existe" % path
            for path in cited if not os.path.isfile(os.path.join(feed, path))]


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Contenido del runbook (US-007a)")
    parser.add_argument("--feed", required=True)
    parser.add_argument("runbook", nargs="?", help="por defecto <feed>/RUNBOOK.md")
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    path = args.runbook or os.path.join(args.feed, "RUNBOOK.md")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as error:
        print("FALLO runbook: no se pudo leer %s: %s" % (path, error), file=sys.stderr)
        return 1
    problems = section_problems(text) + rollback_staging_problems(text) + command_problems(text, args.feed)
    for problem in problems:
        print("FALLO runbook: %s" % problem, file=sys.stderr)
    if problems:
        return 1
    print("runbook correcto: %d secciones y todos los comandos citados existen" % len(REQUIRED_SECTIONS))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
