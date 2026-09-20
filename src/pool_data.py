"""Shared GeckoTerminal pool configuration and response validation."""

from __future__ import annotations

import time
from typing import Any


GECKOTERMINAL_BASE = "https://api.geckoterminal.com/api/v2"
WXTM_NETWORK = "eth"
WXTM_POOL_ADDRESS = "0x530581e8b4dff575d96af96cbfb74d0cc4ed0ec0cb7c953f491c7a60a787412d"
WXTM_MARKET = "Uniswap V4 (Ethereum)"
MEXC_KLINE_BASE = "https://api.mexc.com"


def parse_mexc_1h_change(
    klines: list[list[Any]], current_price: float, *, now_ms: int | None = None
) -> float | None:
    """Compute a rolling one-hour change from MEXC 1-minute close candles.

    The reference candle must be within five minutes of the one-hour target and
    the newest candle must be no more than five minutes old; otherwise report
    the hourly change as unavailable instead of presenting stale data.
    """
    try:
        current = float(current_price)
    except (TypeError, ValueError):
        return None
    if current <= 0:
        return None

    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    target = now - 60 * 60 * 1000
    candles: list[tuple[int, float]] = []
    for candle in klines:
        try:
            opened_at = int(candle[0])
            close = float(candle[4])
        except (IndexError, TypeError, ValueError):
            continue
        if close > 0:
            candles.append((opened_at, close))
    if not candles:
        return None

    candles.sort(key=lambda candle: candle[0])
    if now - candles[-1][0] > 5 * 60 * 1000:
        return None
    reference = [candle for candle in candles if candle[0] <= target]
    if not reference:
        return None
    reference_time, reference_price = reference[-1]
    if target - reference_time > 5 * 60 * 1000:
        return None
    return (current / reference_price - 1) * 100


def parse_wxtm_pool_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the wXTM USD price and 24-hour change from the configured pool."""
    data = payload.get("data") or {}
    attributes = data.get("attributes") or {}
    address = attributes.get("address")
    if address and str(address).lower() != WXTM_POOL_ADDRESS.lower():
        raise ValueError(f"GeckoTerminal a répondu avec un autre pool : {address}")

    try:
        price = float(attributes["base_token_price_usd"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Le pool Uniswap ne fournit pas de prix USD valide") from exc
    if price <= 0:
        raise ValueError("Le pool Uniswap fournit un prix non positif")

    raw_change = (attributes.get("price_change_percentage") or {}).get("h24")
    try:
        change = float(raw_change) if raw_change is not None else None
    except (TypeError, ValueError):
        change = None

    return {
        "price": price,
        "currency": "USD",
        "change": change,
        "change_1h": _percentage((attributes.get("price_change_percentage") or {}).get("h1")),
        "market": WXTM_MARKET,
        "updated": None,
        "error": None,
    }


def _percentage(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
