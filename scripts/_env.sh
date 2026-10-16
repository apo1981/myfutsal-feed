# Entorno comun de los scripts del feed. Se carga con `. "$(dirname "$0")/_env.sh"`; no se ejecuta solo.
# Define FEED (raiz del repositorio del feed) y PY (python a usar, sin escribir bytecode).
# Python 3.9 o superior y solo biblioteca estandar; el venv solo aporta check-jsonschema.

FEED=$(cd "$(dirname "$0")/.." && pwd)
export PYTHONWARNINGS=ignore

# Venv con check-jsonschema (ADR-006 §2): FEED_TOOLS_VENV, luego <feed>/.venv (CI), luego ../.venv-tools (local).
TOOLS_VENV=""
for candidate in "${FEED_TOOLS_VENV:-}" "$FEED/.venv" "$FEED/../.venv-tools"; do
    if [ -n "$candidate" ] && [ -x "$candidate/bin/check-jsonschema" ]; then
        TOOLS_VENV=$(cd "$candidate" && pwd)
        break
    fi
done

if [ -n "$TOOLS_VENV" ]; then
    PY="$TOOLS_VENV/bin/python"
else
    PY=python3
fi

# Comprueba que check-jsonschema esta disponible; si no, explica como crear el venv.
require_tools() {
    if [ -z "$TOOLS_VENV" ]; then
        echo "FALLO: falta el venv con check-jsonschema." >&2
        echo "  En el repositorio de la app: scripts/bootstrap-contract-tools (crea /.venv-tools, necesita red una vez)." >&2
        echo "  O en este repositorio: python3 -m venv .venv && .venv/bin/pip install --require-hashes --only-binary=:all: -r requirements-ci.txt" >&2
        exit 2
    fi
}

# Ejecuta un modulo de scripts/ con el python elegido (sin bytecode, sin entorno ni site de usuario).
run_python() {
    "$PY" -B -E -s "$@"
}
