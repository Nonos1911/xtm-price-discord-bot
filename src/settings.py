"""Validated alert configuration shared by the live worker and one-shot job."""

from __future__ import annotations

import math


UPWARD_ALERT_THRESHOLDS = (10.0, 20.0, 30.0, 40.0)
DEFAULT_UPWARD_ALERT_THRESHOLD = 10.0
DOWNWARD_ALERT_THRESHOLD = 30.0


def parse_upward_alert_threshold(value: str | None) -> float:
    """Accept only the manual upward alert steps offered in workflow settings."""
    if value is None or not value.strip():
        return DEFAULT_UPWARD_ALERT_THRESHOLD
    try:
        threshold = float(value)
    except ValueError as exc:
        raise ValueError("ALERT_THRESHOLD_PERCENT doit être 10, 20, 30 ou 40") from exc
    if not math.isfinite(threshold) or threshold not in UPWARD_ALERT_THRESHOLDS:
        allowed = ", ".join(f"{step:g}" for step in UPWARD_ALERT_THRESHOLDS)
        raise ValueError(f"ALERT_THRESHOLD_PERCENT doit être l'un de : {allowed}")
    return threshold


def parse_alerts_paused(value: str | None) -> bool:
    """Parse an explicit pause switch; reject typos rather than silently resume."""
    if value is None or not value.strip():
        return False
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on", "pause", "paused"}:
        return True
    if normalized in {"0", "false", "no", "off", "resume", ""}:
        return False
    raise ValueError("ALERTS_PAUSED doit être true ou false")


def threshold_for_direction(upward: bool, upward_threshold: float) -> float:
    """Only the upward trigger is adjustable; the downside is fixed at -30%."""
    return upward_threshold if upward else DOWNWARD_ALERT_THRESHOLD
