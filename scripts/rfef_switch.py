"""Interruptor de la herramienta RFEF y cuenta de anomalias seguidas (US-038; ADR-011, ADR-012 §6).

Solo biblioteca estandar, Python 3.9.
  - `scripts/rfef/config.json` (versionado) lleva `"enabled"`. Con `false` la herramienta no hace ninguna peticion.
  - La herramienta lo pone a `false` ante 403/429, un robots.txt que veta la ruta o 2 anomalias seguidas. Nunca lo pone a `true`:
    solo el humano lo reactiva editando el fichero.
  - La cuenta de anomalias seguidas es estado local del operador (fuera del repositorio, junto a las respuestas crudas): una
    ejecucion en verde la reinicia.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

CONFIG_PATH = os.path.join("scripts", "rfef", "config.json")
STREAK_FILE = "anomaly-streak.json"
ANOMALY_LIMIT = 2


class SwitchError(Exception):
    """El interruptor no se puede leer o escribir: por seguridad no se hace ninguna peticion."""


class DisabledError(Exception):
    """`enabled` es false: la herramienta termina sin peticiones."""


def _load(feed: str) -> Dict[str, Any]:
    path = os.path.join(feed, CONFIG_PATH)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, ValueError) as error:
        raise SwitchError("no se puede leer %s (%s): no se hace ninguna peticion" % (CONFIG_PATH, error))
    if not isinstance(config, dict) or not isinstance(config.get("enabled"), bool):
        raise SwitchError('%s debe ser un objeto con "enabled" true o false: no se hace ninguna peticion' % CONFIG_PATH)
    return config


def ensure_enabled(feed: str) -> None:
    config = _load(feed)
    if not config["enabled"]:
        reason = config.get("disabledReason")
        raise DisabledError("la herramienta esta deshabilitada (\"enabled\": false en %s)%s. No se ha hecho ninguna peticion. "
                            "Solo el humano la reactiva editando ese fichero." % (CONFIG_PATH, ": " + reason if reason else ""))


def disable(feed: str, reason: str) -> None:
    """Pone `enabled` a false (nunca a true) y anota el motivo."""
    config = _load(feed)
    config["enabled"] = False
    config["disabledReason"] = reason
    path = os.path.join(feed, CONFIG_PATH)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except OSError as error:
        raise SwitchError("no se ha podido poner enabled a false en %s: %s" % (CONFIG_PATH, error))


def _streak_path(state_dir: str) -> str:
    return os.path.join(state_dir, STREAK_FILE)


def read_streak(state_dir: str) -> int:
    try:
        with open(_streak_path(state_dir), "r", encoding="utf-8") as handle:
            value = json.load(handle).get("streak", 0)
    except (OSError, ValueError, AttributeError):
        return 0
    return value if isinstance(value, int) and value > 0 else 0


def _write_streak(state_dir: str, value: int) -> None:
    os.makedirs(state_dir, exist_ok=True)
    with open(_streak_path(state_dir), "w", encoding="utf-8") as handle:
        json.dump({"streak": value}, handle)


def record_anomaly(state_dir: str) -> int:
    """Suma una anomalia y devuelve la cuenta de seguidas."""
    value = read_streak(state_dir) + 1
    _write_streak(state_dir, value)
    return value


def reset_streak(state_dir: str) -> None:
    if read_streak(state_dir):
        _write_streak(state_dir, 0)
