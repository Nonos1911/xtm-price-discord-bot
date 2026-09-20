"""Shared GeckoTerminal pool configuration and response validation."""

from __future__ import annotations

from typing import Any


GECKOTERMINAL_BASE = "https://api.geckoterminal.com/api/v2"
WXTM_NETWORK = "eth"
WXTM_POOL_ADDRESS = "0x530581e8b4dff575d96af96cbfb74d0cc4ed0ec0cb7c953f491c7a60a787412d"
WXTM_MARKET = "Uniswap V4 (Ethereum)"


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
        "market": WXTM_MARKET,
        "updated": None,
        "error": None,
    }
