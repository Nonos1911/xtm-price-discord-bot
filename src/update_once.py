"""One-shot updater for GitHub Actions (no VPS, no persistent process)."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any


COINGECKO_BASE = os.getenv("COINGECKO_API_BASE", "https://api.coingecko.com/api/v3").rstrip("/")
DISCORD_BASE = "https://discord.com/api/v10"
CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "1370700962695610430")
ALERT_CHANNEL_ID = os.getenv("ALERT_CHANNEL_ID", "1163364187796426776")
ALERT_THRESHOLD_PERCENT = float(os.getenv("ALERT_THRESHOLD_PERCENT", "10"))
PRICE_EMBED_TITLE = "💱 Prix XTM / wXTM"
ALERT_UP_PREFIX = "Alert XTM+"
ALERT_DOWN_PREFIX = "Alert XTM-"
COINS = [("minotari", "XTM", "MEXC"), ("wrapped-minotari", "wXTM", "Gate")]


def api_json(url: str, *, headers: dict[str, str] | None = None, method: str = "GET", body: Any = None) -> Any:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request_headers = {"User-Agent": "xtm-price-discord-bot/1.0", **(headers or {})}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, data=payload, headers=request_headers, method=method)
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read().decode("utf-8")
                return json.loads(data) if data else None
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After", "3")
                time.sleep(min(float(retry_after), 20))
                continue
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} sur {url}: {detail[:500]}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"API indisponible: {last_error}") from last_error


def select_usdt_ticker(tickers: list[dict[str, Any]], preferred_market: str) -> dict[str, Any] | None:
    candidates = []
    for ticker in tickers:
        if str(ticker.get("target", "")).upper() != "USDT":
            continue
        market = ticker.get("market", {})
        market_name = str(market.get("name", "")).strip().lower()
        market_id = str(market.get("identifier", "")).strip().lower()
        preferred = preferred_market.strip().lower()
        aliases = {preferred}
        if preferred == "mexc":
            aliases.add("mxc")
        if preferred == "gate":
            aliases.add("gate-io")
        if market_name not in aliases and market_id not in aliases:
            continue
        try:
            last = float(ticker["last"])
        except (KeyError, TypeError, ValueError):
            continue
        if last <= 0:
            continue
        try:
            volume = float(ticker.get("volume") or 0)
        except (TypeError, ValueError):
            volume = 0
        candidates.append((volume, last, ticker))
    if not candidates:
        return None
    volume, last, ticker = max(candidates, key=lambda item: item[0])
    return {"price": last, "volume": volume, "market": ticker.get("market", {}).get("name", "inconnu")}


def get_quote(coin_id: str, label: str, preferred_market: str) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {
            "ids": coin_id,
            "vs_currencies": "usd",
            "include_24hr_change": "true",
            "include_last_updated_at": "true",
        }
    )
    auth_headers: dict[str, str] = {}
    api_key = os.getenv("COINGECKO_API_KEY")
    if api_key:
        auth_headers["x-cg-pro-api-key" if os.getenv("COINGECKO_API_PLAN", "demo").lower() == "pro" else "x-cg-demo-api-key"] = api_key

    simple = api_json(f"{COINGECKO_BASE}/simple/price?{query}", headers=auth_headers)
    tickers_query = urllib.parse.urlencode({"include_exchange_logo": "false", "page": "1"})
    tickers = api_json(f"{COINGECKO_BASE}/coins/{coin_id}/tickers?{tickers_query}", headers=auth_headers)
    ticker = select_usdt_ticker(tickers.get("tickers", []), preferred_market)
    coin = simple.get(coin_id, {})
    return {
        "label": label,
        "price": ticker["price"] if ticker else None,
        "change": coin.get("usd_24h_change"),
        "market": ticker["market"] if ticker else None,
        "updated": coin.get("last_updated_at"),
        "error": None if ticker else f"marché {preferred_market} indisponible",
    }


def price_text(quote: dict[str, Any]) -> str:
    if quote["price"] is None:
        return f"Indisponible — {quote['error']}"
    price = quote["price"]
    formatted = f"{price:,.4f}".replace(",", " ") if price >= 1 else f"{price:.10f}".rstrip("0").rstrip(".")
    change = quote["change"]
    change_text = "variation 24 h indisponible" if change is None else f"{change:+.2f} % sur 24 h"
    return f"**{formatted} USDT**\n{change_text}\nMarché : {quote['market']}"


def build_embed(quotes: list[dict[str, Any]]) -> dict[str, Any]:
    return build_price_embed(quotes, "Mise à jour toutes les minutes")


def build_price_embed(quotes: list[dict[str, Any]], footer_text: str) -> dict[str, Any]:
    return {
        "title": PRICE_EMBED_TITLE,
        "description": "Cours en USDT récupérés sur CoinGecko.",
        "color": 5793266,
        "fields": [{"name": quote["label"], "value": price_text(quote), "inline": True} for quote in quotes],
        "footer": {"text": footer_text},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def update_discord(embed: dict[str, Any]) -> str:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN est absent")
    headers = {"Authorization": f"Bot {token}"}
    payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}
    bot = api_json(f"{DISCORD_BASE}/users/@me", headers=headers)
    messages = api_json(
        f"{DISCORD_BASE}/channels/{CHANNEL_ID}/messages?limit=100",
        headers=headers,
    )
    price_messages = [
        message
        for message in messages
        if message.get("author", {}).get("id") == bot.get("id")
        and any(embed_item.get("title") == PRICE_EMBED_TITLE for embed_item in message.get("embeds", []))
    ]
    if price_messages:
        current = price_messages[0]
        updated = api_json(
            f"{DISCORD_BASE}/channels/{CHANNEL_ID}/messages/{current['id']}",
            headers=headers,
            method="PATCH",
            body=payload,
        )
        removed = 0
        for duplicate in price_messages[1:]:
            api_json(
                f"{DISCORD_BASE}/channels/{CHANNEL_ID}/messages/{duplicate['id']}",
                headers=headers,
                method="DELETE",
            )
            removed += 1
        suffix = f"; {removed} ancien(s) supprimé(s)" if removed else ""
        return f"message {updated['id']} mis à jour{suffix}"
    created = api_json(f"{DISCORD_BASE}/channels/{CHANNEL_ID}/messages", headers=headers, method="POST", body=payload)
    return f"message {created['id']} créé"


def publish_snapshot(embed: dict[str, Any]) -> str:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN est absent")
    headers = {"Authorization": f"Bot {token}"}
    payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}
    created = api_json(
        f"{DISCORD_BASE}/channels/{ALERT_CHANNEL_ID}/messages",
        headers=headers,
        method="POST",
        body=payload,
    )
    return f"snapshot {created['id']} publié dans mog-post"


def build_alert_embed(text: str, color: int, quotes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    embed = {
        "title": text,
        "description": text,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if quotes is not None:
        alert_quotes = []
        for quote in quotes:
            if quote["label"] == "XTM" and quote["change"] is not None:
                magnitude = min(300.0, max(ALERT_THRESHOLD_PERCENT, abs(float(quote["change"]))))
                bounded_change = -magnitude if float(quote["change"]) < 0 else magnitude
                quote = {**quote, "change": bounded_change}
            alert_quotes.append(quote)
        embed["fields"] = [
            {"name": quote["label"], "value": price_text(quote), "inline": True}
            for quote in alert_quotes
        ]
    return embed


def xtm_alert_triggered(quotes: list[dict[str, Any]]) -> bool:
    xtm_quote = next((quote for quote in quotes if quote["label"] == "XTM"), None)
    return bool(xtm_quote and xtm_quote["change"] is not None and xtm_quote["change"] >= ALERT_THRESHOLD_PERCENT)


def xtm_buy_alert_triggered(quotes: list[dict[str, Any]]) -> bool:
    xtm_quote = next((quote for quote in quotes if quote["label"] == "XTM"), None)
    return bool(xtm_quote and xtm_quote["change"] is not None and xtm_quote["change"] <= -ALERT_THRESHOLD_PERCENT)


def format_alert_text(change: float, *, upward: bool) -> str:
    magnitude = min(300.0, max(ALERT_THRESHOLD_PERCENT, abs(float(change))))
    percent = f"{magnitude:.2f}".rstrip("0").rstrip(".")
    if upward:
        return f"{ALERT_UP_PREFIX}{percent}%"
    return f"{ALERT_DOWN_PREFIX}{percent}%  GO BUY"


def upsert_alert(
    text: str,
    color: int,
    quotes: list[dict[str, Any]],
    *,
    upward: bool,
    test_label: str | None = None,
    notify_everyone: bool = True,
) -> str:
    """Create an alert once, then edit that same bot-authored message on later runs."""
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN est absent")
    headers = {"Authorization": f"Bot {token}"}
    display_text = f"{test_label} {text}" if test_label else text
    content = f"@everyone {display_text}" if notify_everyone else display_text
    payload: dict[str, Any] = {"content": content, "embeds": [build_alert_embed(display_text, color, quotes)]}
    bot = api_json(f"{DISCORD_BASE}/users/@me", headers=headers)
    messages = api_json(
        f"{DISCORD_BASE}/channels/{ALERT_CHANNEL_ID}/messages?limit=100",
        headers=headers,
    )
    prefix = ALERT_UP_PREFIX if upward else ALERT_DOWN_PREFIX
    identity = f"{test_label} {prefix}" if test_label else prefix
    matching = [
        message
        for message in messages
        if message.get("author", {}).get("id") == bot.get("id")
        and (
            str(message.get("content", "")).removeprefix("@everyone ").startswith(identity)
            or any(
                str(embed.get("title", "")).startswith(identity)
                for embed in message.get("embeds", [])
            )
        )
    ]

    if matching:
        current = matching[0]  # Discord returns channel history newest-first.
        # Edits keep the visible text, but must never ping the server a second time.
        payload["allowed_mentions"] = {"parse": []}
        updated = api_json(
            f"{DISCORD_BASE}/channels/{ALERT_CHANNEL_ID}/messages/{current['id']}",
            headers=headers,
            method="PATCH",
            body=payload,
        )
        return f"alerte {text} mise à jour dans le message {updated['id']}"

    payload["allowed_mentions"] = {"parse": ["everyone"]} if notify_everyone else {"parse": []}
    created = api_json(
        f"{DISCORD_BASE}/channels/{ALERT_CHANNEL_ID}/messages",
        headers=headers,
        method="POST",
        body=payload,
    )
    return f"alerte {text} créée dans le message {created['id']}"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    quotes = [get_quote(coin_id, label, preferred_market) for coin_id, label, preferred_market in COINS]
    embed = build_embed(quotes)
    if "--dry-run" in sys.argv:
        print(json.dumps(embed, indent=2, ensure_ascii=False))
        return 0
    if not any(quote["price"] is not None for quote in quotes):
        raise RuntimeError("Aucun prix USDT disponible; aucun message n'a été modifié")
    if os.getenv("SNAPSHOT_ONLY", "false").strip().lower() == "true":
        snapshot = build_price_embed(quotes, "Snapshot du prix toutes les 4 heures")
        print(publish_snapshot(snapshot))
        return 0
    print(update_discord(embed))
    if os.getenv("TEST_ALERTS", "").strip().lower() == "both_once":
        print("Test manuel: envoi unique des alertes verte et rouge dans mog-post")
        print(upsert_alert(format_alert_text(ALERT_THRESHOLD_PERCENT, upward=True), 5763719, quotes, upward=True))
        print(upsert_alert(format_alert_text(-ALERT_THRESHOLD_PERCENT, upward=False), 15158332, quotes, upward=False))
        return 0
    xtm_quote = next(quote for quote in quotes if quote["label"] == "XTM")
    if xtm_alert_triggered(quotes):
        print(f"Variation XTM détectée: {xtm_quote['change']:.2f} %; lancement de l'alerte")
        print(upsert_alert(format_alert_text(xtm_quote["change"], upward=True), 5763719, quotes, upward=True))
    elif xtm_buy_alert_triggered(quotes):
        print(f"Variation XTM détectée: {xtm_quote['change']:.2f} %; lancement de l'alerte achat")
        print(upsert_alert(format_alert_text(xtm_quote["change"], upward=False), 15158332, quotes, upward=False))
    else:
        print("Variation XTM sous le seuil de 10 %; alertes existantes laissées inchangées")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
