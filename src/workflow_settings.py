"""Load Tari tracker settings and export them as GitHub Actions step outputs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from settings import parse_upward_alert_threshold


DEFAULTS = {"paused": False, "upward_threshold_percent": 10}


def resolve_settings(payload: Any) -> dict[str, bool | int]:
    if not isinstance(payload, dict):
        raise ValueError("alert_settings.json doit contenir un objet JSON")
    raw_threshold = payload.get("upward_threshold_percent", DEFAULTS["upward_threshold_percent"])
    threshold = parse_upward_alert_threshold(str(raw_threshold))
    paused = payload.get("paused", DEFAULTS["paused"])
    if not isinstance(paused, bool):
        raise ValueError("paused doit être un booléen JSON")
    return {"paused": paused, "upward_threshold_percent": int(threshold)}


def load_settings(path: Path) -> dict[str, bool | int]:
    if not path.exists():
        return dict(DEFAULTS)
    try:
        return resolve_settings(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Impossible de lire alert_settings.json : {exc}") from exc


def write_github_outputs(output_path: Path, settings: dict[str, bool | int]) -> None:
    with output_path.open("a", encoding="utf-8", newline="\n") as output:
        output.write(f"threshold={settings['upward_threshold_percent']}\n")
        output.write(f"paused={str(settings['paused']).lower()}\n")


def main() -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    if not output_path:
        raise SystemExit("GITHUB_OUTPUT est absent; ce script doit être exécuté par GitHub Actions")
    settings = load_settings(Path("alert_settings.json"))
    write_github_outputs(Path(output_path), settings)


if __name__ == "__main__":
    main()
