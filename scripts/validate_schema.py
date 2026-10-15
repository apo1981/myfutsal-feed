"""Valida ficheros del feed contra el esquema con check-jsonschema (US-007a, criterio 3).

Una sola ejecucion de la herramienta por esquema. Nombra cada fichero que falla, con la ruta JSON
del error. Solo biblioteca estandar, compatible con Python 3.9. No escribe nada.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List

SEASON_SCHEMA = "league-feed-v1.season.schema.json"
INDEX_SCHEMA = "league-feed-v1.index.schema.json"
USAGE_ERROR = 2


def is_index_document(path: str) -> bool:
    name = os.path.basename(path)
    return name == "index.json" or name.startswith("index-")


def run_tool(tool: str, schema: str, files: List[str]) -> Dict[str, Any]:
    command = [tool, "--no-cache", "--output-format", "json", "--schemafile", schema] + files
    proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {"status": "error", "errors": [], "parse_errors": [{"filename": "?", "message": proc.stderr.strip() or proc.stdout.strip()}]}


def problems_in(report: Dict[str, Any]) -> List[str]:
    found = ["FALLO esquema: %s: %s %s" % (e["filename"], e.get("path", "$"), e["message"]) for e in report.get("errors", [])]
    found += ["FALLO esquema: %s: no se pudo leer: %s" % (e.get("filename", "?"), e.get("message", "")) for e in report.get("parse_errors", [])]
    return found


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Validacion contra el esquema (US-007a)")
    parser.add_argument("--feed", required=True)
    parser.add_argument("--tool", required=True, help="ruta de check-jsonschema")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)
    if not args.files:
        print("uso: scripts/validate-schema <fichero>...", file=sys.stderr)
        return USAGE_ERROR
    groups = (
        (INDEX_SCHEMA, [f for f in args.files if is_index_document(f)]),
        (SEASON_SCHEMA, [f for f in args.files if not is_index_document(f)]),
    )
    problems = []  # type: List[str]
    for schema, files in groups:
        if files:
            problems += problems_in(run_tool(args.tool, os.path.join(args.feed, "schema", schema), files))
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
