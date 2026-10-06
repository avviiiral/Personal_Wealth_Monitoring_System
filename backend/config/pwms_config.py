"""Centralized non-secret PWMS configuration loaded from JSON."""
import json
import os
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).resolve().with_name("pwms_config.json")
with CONFIG_PATH.open("r", encoding="utf-8") as handle:
    CONFIG: dict[str, Any] = json.load(handle)

def _deep_merge(target: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value

raw_override = os.environ.get("PWMS_CONFIG_OVERRIDES", "").strip()
if raw_override:
    try:
        override = json.loads(raw_override)
        if not isinstance(override, dict):
            raise ValueError("override must be an object")
        _deep_merge(CONFIG, override)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("PWMS_CONFIG_OVERRIDES must be valid JSON.") from exc

def get(section: str, key: str, default: Any = None) -> Any:
    return CONFIG.get(section, {}).get(key, default)
