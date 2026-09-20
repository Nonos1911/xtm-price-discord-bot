"""One-shot updater for GitHub Actions (no VPS, no persistent process)."""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from pool_data import GECKOTERMINAL_BASE, WXTM_NETWORK, WXTM_POOL_ADDRESS, parse_wxtm_pool_response


COINGECKO_BASE = os.getenv("COINGECKO_API_BASE", "https://api.coingecko.com/api/v3").rstrip("/")
DISCORD_BASE = "https://discord.com/api/v10"
CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "1370700962695610430")
ALERT_CHANNEL_ID = os.getenv("ALERT_CHANNEL_ID", "1163364187796426776")
ALERT_THRESHOLD_PERCENT = float(os.getenv("ALERT_THRESHOLD_PERCENT", "10"))
PRICE_EMBED_TITLE = "💱 Prix XTM / wXTM"
ALERT_PREFIX = "Alert "
ALERT_MILESTONE_FOOTER = "Palier @everyone notifié : "
ALERT_PERCENT_RE = re.compile(r"(?:XTM|wXTM)([+-])(\d+(?:\.\d+)?)%")
PRICE_CHANGE_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%\s*sur 24 h", re.IGNORECASE)
COINS = [("minotari", "XTM", "MEXC"), ("wrapped-minotari", "wXTM", "Uniswap V4")]


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
    if label == "wXTM":
        try:
            response = api_json(
                f"{GECKOTERMINAL_BASE}/networks/{WXTM_NETWORK}/pools/{WXTM_POOL_ADDRESS}",
                headers={"Accept": "application/json;version=20230302"},
            )
            quote = parse_wxtm_pool_response(response)
            return {"label": label, **quote}
        except Exception as exc:
            return {
                "label": label,
                "price": None,
                "currency": "USD",
                "change": None,
                "market": "Uniswap V4 (Ethereum)",
                "updated": None,
                "error": f"pool Uniswap indisponible : {exc}",
            }

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
        "currency": "USDT",
        "change": coin.get("usd_24h_change"),
        "market": ticker["market"] if ticker else None,
        "updated": coin.get("last_updated_at"),
        "error": None if ticker else f"marché {preferred_market} indisponible",
    }


def price_text(quote: dict[str, Any]) -> str:
    if quote["price"] is None:
        return f"Indisponible — {quote['error']}"
    price = quote["price"]
    label = quote.get("label")
    is_wxtm = label == "wXTM"
    formatted = (
        f"{price:.5f}"
        if is_wxtm or label == "XTM"
        else f"{price:,.4f}".replace(",", " ") if price >= 1
        else f"{price:.10f}".rstrip("0").rstrip(".")
    )
    change = quote["change"]
    change_text = "variation 24 h indisponible" if change is None else f"{change:+.2f} % sur 24 h"
    currency = "USDT" if is_wxtm else quote.get("currency", "USDT")
    lines = [f"**{formatted} {currency}**", change_text]
    lines.append(f"Marché : {quote['market']}")
    return "\n".join(lines)


def extract_previous_price_changes(message: dict[str, Any]) -> dict[str, float]:
    """Read the prior 24 h percentages from the bot's persistent price embed."""
    for embed in message.get("embeds", []):
        if embed.get("title") != PRICE_EMBED_TITLE:
            continue
        changes: dict[str, float] = {}
        for field in embed.get("fields", []):
            match = PRICE_CHANGE_RE.search(str(field.get("value", "")))
            if match:
                changes[str(field.get("name", ""))] = float(match.group(1))
        return changes
    return {}


def extract_alert_changes(message: dict[str, Any]) -> dict[str, float]:
    """Read each asset's displayed 24 h change from a prior alert message."""
    changes: dict[str, float] = {}
    for embed in message.get("embeds", []):
        for field in embed.get("fields", []):
            match = PRICE_CHANGE_RE.search(str(field.get("value", "")))
            if match:
                changes.setdefault(str(field.get("name", "")), float(match.group(1)))
    return changes


def change_since_previous(current: float | None, previous: float | None) -> float | None:
    """Return the change in the displayed 24 h percentage, in percentage points."""
    if current is None or previous is None:
        return None
    return current - previous


def calculate_alert_deltas(
    current_changes: dict[str, float],
    previous_alert_changes: dict[str, float],
    previous_changes: dict[str, float] | None = None,
) -> dict[str, float]:
    """Compare each alert's 24 h percentage with its previous alert or reading."""
    deltas: dict[str, float] = {}
    for label, current in current_changes.items():
        previous = previous_alert_changes.get(label)
        if previous is None and previous_changes is not None:
            previous = previous_changes.get(label)
        delta = change_since_previous(current, previous)
        if delta is not None:
            deltas[label] = delta
    return deltas


def format_alert_delta_title(
    text: str, deltas: dict[str, float], *, asset_count: int
) -> str:
    """Show each asset's delta directly beside its color marker."""
    if not deltas:
        return text
    rounded_deltas = {
        label: (0.0 if round(delta, 2) == 0 else round(delta, 2))
        for label, delta in deltas.items()
    }
    if all(delta == 0 for delta in rounded_deltas.values()):
        return f"🟡 ~ — {text}"
    dominant_delta = max(rounded_deltas.values(), key=abs)
    marker = "🟢" if dominant_delta > 0 else "🔴" if dominant_delta < 0 else "🟡"
    parts = []
    for label, delta in rounded_deltas.items():
        if delta == 0:
            value = "~"
            parts.append(f"{label} {value}" if asset_count > 1 else value)
            continue
        sign = "+" if delta > 0 else "-" if delta < 0 else "~"
        value = f"{sign}{abs(delta):.2f}%"
        parts.append(f"{label} {value}" if asset_count > 1 else value)
    return f"{marker} {' | '.join(parts)} — {text}"


def variation_trend_sign(current: float, previous: float | None) -> str:
    if previous is None:
        return "~"
    if current > previous:
        return "+"
    if current < previous:
        return "-"
    return "~"


def format_trend_prefix(sign: str) -> str:
    return {"+": "🟢 +", "-": "🔴 -", "~": "🟡 ~"}.get(sign, "🟡 ~")


def alert_trend_sign(
    quotes: list[dict[str, Any]], previous_changes: dict[str, float]
) -> str:
    available = [quote for quote in quotes if quote.get("change") is not None]
    if not available:
        return "~"
    leading = max(available, key=lambda quote: abs(float(quote["change"])))
    return variation_trend_sign(
        float(leading["change"]), previous_changes.get(str(leading["label"]))
    )


def strip_trend_prefix(title: str) -> str:
    if title.startswith(("🟢", "🔴", "🟡")):
        alert_start = title.find(ALERT_PREFIX, 1)
        if alert_start >= 0:
            return title[alert_start:]
    for prefix in ("🟢 + ", "🔴 - ", "🟡 ~ ", "🟢+ ", "🔴- ", "⚪= ", "⚪? "):
        if title.startswith(prefix):
            return title[len(prefix):]
    return title


def build_embed(quotes: list[dict[str, Any]]) -> dict[str, Any]:
    return build_price_embed(quotes, "Mise à jour toutes les minutes")


def build_price_embed(quotes: list[dict[str, Any]], footer_text: str) -> dict[str, Any]:
    return {
        "title": PRICE_EMBED_TITLE,
        "description": "XTM/USDT sur MEXC ; wXTM/USDT indicatif depuis Uniswap V4 (prix USD affiché à parité).",
        "color": 5793266,
        "fields": [{"name": quote["label"], "value": price_text(quote), "inline": True} for quote in quotes],
        "footer": {"text": footer_text},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def update_discord(embed: dict[str, Any], *, previous_changes: dict[str, float] | None = None) -> str:
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
    if previous_changes is not None:
        previous_changes.update(
            extract_previous_price_changes(price_messages[0]) if price_messages else {}
        )
    created = api_json(
        f"{DISCORD_BASE}/channels/{CHANNEL_ID}/messages",
        headers=headers,
        method="POST",
        body=payload,
    )
    removed = 0
    if price_messages:
        for old_message in price_messages:
            api_json(
                f"{DISCORD_BASE}/channels/{CHANNEL_ID}/messages/{old_message['id']}",
                headers=headers,
                method="DELETE",
            )
            removed += 1
    suffix = f"; {removed} ancienne(s) box supprimée(s)" if removed else ""
    return f"nouvelle box de prix {created['id']} publiée{suffix}"


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


def build_alert_embed(
    text: str,
    color: int,
    quotes: list[dict[str, Any]] | None = None,
    *,
    notified_milestone: int | None = None,
    upward: bool = True,
    previous_changes: dict[str, float] | None = None,
    previous_alert_changes: dict[str, float] | None = None,
) -> dict[str, Any]:
    alert_quotes = []
    if quotes is not None:
        for quote in quotes:
            if quote["change"] is not None:
                magnitude = min(300.0, max(ALERT_THRESHOLD_PERCENT, abs(float(quote["change"]))))
                bounded_change = -magnitude if float(quote["change"]) < 0 else magnitude
                quote = {**quote, "change": bounded_change}
            alert_quotes.append(quote)
    current_display_changes = {
        str(quote["label"]): float(quote["change"])
        for quote in alert_quotes
        if quote.get("change") is not None
    }
    deltas = calculate_alert_deltas(
        current_display_changes, previous_alert_changes or {}, previous_changes
    )
    display_title = text
    if not text.startswith("["):
        if deltas:
            display_title = format_alert_delta_title(
                text, deltas, asset_count=len(current_display_changes)
            )
        elif previous_changes is not None or previous_alert_changes:
            baseline = previous_changes or previous_alert_changes or {}
            display_title = f"{format_trend_prefix(alert_trend_sign(quotes or [], baseline))} {text}"
    embed = {
        "title": display_title,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if quotes is not None:
        fields = []
        for quote in alert_quotes:
            value = price_text(quote)
            fields.append({"name": quote["label"], "value": value, "inline": True})
        embed["fields"] = fields
    if notified_milestone is not None:
        sign = "+" if upward else "-"
        embed["footer"] = {"text": f"{ALERT_MILESTONE_FOOTER}{sign}{notified_milestone}%"}
    return embed


def alert_quotes_for_direction(quotes: list[dict[str, Any]], *, upward: bool) -> list[dict[str, Any]]:
    """Return only XTM/wXTM quotes that currently meet the directional threshold."""
    by_label = {quote.get("label"): quote for quote in quotes}
    affected: list[dict[str, Any]] = []
    for label in ("XTM", "wXTM"):
        quote = by_label.get(label)
        if quote is None or quote.get("change") is None:
            continue
        change = float(quote["change"])
        triggered = change >= ALERT_THRESHOLD_PERCENT if upward else change <= -ALERT_THRESHOLD_PERCENT
        if triggered:
            affected.append(quote)
    return affected


def alert_changes_are_complete(quotes: list[dict[str, Any]]) -> bool:
    """Do not clear an alert based on a partial/failed market response."""
    by_label = {quote.get("label"): quote for quote in quotes}
    labels = {label for _, label, _ in COINS}
    return bool(labels) and all(
        label in by_label and by_label[label].get("change") is not None
        for label in labels
    )


def format_alert_text(change: float, *, upward: bool, label: str = "XTM") -> str:
    magnitude = min(300.0, max(ALERT_THRESHOLD_PERCENT, abs(float(change))))
    percent = f"{magnitude:.2f}".rstrip("0").rstrip(".")
    if upward:
        return f"{ALERT_PREFIX}{label}+{percent}%"
    return f"{ALERT_PREFIX}{label}-{percent}%  GO BUY"


def format_group_alert_text(quotes: list[dict[str, Any]], *, upward: bool) -> str:
    """Build one alert title listing each asset that crossed the same threshold."""
    components = [
        format_alert_text(float(quote["change"]), upward=upward, label=str(quote["label"]))
        .removeprefix(ALERT_PREFIX)
        .removesuffix("  GO BUY")
        for quote in quotes
    ]
    suffix = "  GO BUY" if not upward else ""
    return f"{ALERT_PREFIX}{' | '.join(components)}{suffix}"


def _is_alert_title(title: str, *, upward: bool, test_label: str | None) -> bool:
    if test_label:
        marker = f"{test_label} "
        if not title.startswith(marker):
            return False
        title = title[len(marker):]
    elif title.startswith("["):
        # Do not let test alerts get reused as production alerts.
        return False
    title = strip_trend_prefix(title)
    return title.startswith(ALERT_PREFIX) and ("+" in title if upward else "-" in title)


def alert_milestone(quotes: list[dict[str, Any]]) -> int:
    """Return the highest 10%-step reached, capped to the displayed 300% range."""
    changes = [abs(float(quote["change"])) for quote in quotes if quote.get("change") is not None]
    if not changes:
        return 0
    magnitude = min(300.0, max(changes))
    return int(math.floor((magnitude + 1e-9) / 10.0) * 10)


def _message_notified_milestone(message: dict[str, Any], *, upward: bool) -> int:
    sign = "+" if upward else "-"
    for embed in message.get("embeds", []):
        footer_text = str(embed.get("footer", {}).get("text", ""))
        marker = re.search(r"Palier @everyone notifié : ([+-])(\d+)%", footer_text)
        if marker and marker.group(1) == sign:
            return int(marker.group(2))

    # Older alerts do not have our milestone footer; infer the last pinged step
    # from their percentages only if that message actually sent @everyone.
    if not message.get("mention_everyone"):
        return 0
    values = [
        float(match.group(2))
        for embed in message.get("embeds", [])
        for match in ALERT_PERCENT_RE.finditer(str(embed.get("title", "")))
        if match.group(1) == sign
    ]
    return alert_milestone([{"change": value} for value in values])


def upsert_alert(
    text: str,
    color: int,
    quotes: list[dict[str, Any]],
    *,
    upward: bool,
    test_label: str | None = None,
    notify_everyone: bool = True,
    previous_changes: dict[str, float] | None = None,
) -> str:
    """Publish a fresh alert each run, removing older copies and pinging at 10% steps."""
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN est absent")
    headers = {"Authorization": f"Bot {token}"}
    display_text = f"{test_label} {text}" if test_label else text
    bot = api_json(f"{DISCORD_BASE}/users/@me", headers=headers)
    messages = api_json(
        f"{DISCORD_BASE}/channels/{ALERT_CHANNEL_ID}/messages?limit=100",
        headers=headers,
    )
    matching = [
        message
        for message in messages
        if message.get("author", {}).get("id") == bot.get("id")
        and any(
            _is_alert_title(str(embed.get("title", "")), upward=upward, test_label=test_label)
            for embed in message.get("embeds", [])
        )
    ]
    previous_alert_changes: dict[str, float] = {}
    for old_message in matching:
        for label, change in extract_alert_changes(old_message).items():
            previous_alert_changes.setdefault(label, change)

    previous_milestone = max(
        (_message_notified_milestone(message, upward=upward) for message in matching),
        default=0,
    )
    current_milestone = alert_milestone(quotes)
    should_notify = notify_everyone and current_milestone > previous_milestone
    stored_milestone = max(previous_milestone, current_milestone) if notify_everyone else None
    payload: dict[str, Any] = {
        "embeds": [build_alert_embed(
            display_text,
            color,
            quotes,
            notified_milestone=stored_milestone,
            upward=upward,
            previous_changes=previous_changes,
            previous_alert_changes=previous_alert_changes,
        )],
        "allowed_mentions": {"parse": ["everyone"]} if should_notify else {"parse": []},
    }
    if should_notify:
        # Keep the mention separate from the alert text, which appears once in
        # the embed title. Non-ping refreshes have no message content at all.
        payload["content"] = "@everyone"
    created = api_json(
        f"{DISCORD_BASE}/channels/{ALERT_CHANNEL_ID}/messages",
        headers=headers,
        method="POST",
        body=payload,
    )
    removed = 0
    for old_message in matching:
        api_json(
            f"{DISCORD_BASE}/channels/{ALERT_CHANNEL_ID}/messages/{old_message['id']}",
            headers=headers,
            method="DELETE",
        )
        removed += 1
    suffix = f"; {removed} ancienne(s) alerte(s) supprimée(s)" if removed else ""
    ping = f"; @everyone palier {('+' if upward else '-')}{current_milestone}%" if should_notify else ""
    return f"nouveau message d'alerte {created['id']} publié{suffix}{ping}"


def clear_alert_if_below_threshold(quotes: list[dict[str, Any]], *, upward: bool) -> str:
    """Delete the production alert after all tracked changes leave its threshold zone."""
    if not alert_changes_are_complete(quotes):
        return "données 24 h incomplètes; alerte conservée"
    if alert_quotes_for_direction(quotes, upward=upward):
        return "seuil toujours atteint; alerte conservée"

    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN est absent")
    headers = {"Authorization": f"Bot {token}"}
    bot = api_json(f"{DISCORD_BASE}/users/@me", headers=headers)
    messages = api_json(
        f"{DISCORD_BASE}/channels/{ALERT_CHANNEL_ID}/messages?limit=100",
        headers=headers,
    )
    matching = [
        message
        for message in messages
        if message.get("author", {}).get("id") == bot.get("id")
        and any(
            _is_alert_title(str(embed.get("title", "")), upward=upward, test_label=None)
            for embed in message.get("embeds", [])
        )
    ]
    for message in matching:
        api_json(
            f"{DISCORD_BASE}/channels/{ALERT_CHANNEL_ID}/messages/{message['id']}",
            headers=headers,
            method="DELETE",
        )
    direction = "verte" if upward else "rouge"
    if not matching:
        return f"aucune alerte {direction} active à supprimer"
    return (
        f"alerte {direction} supprimée; {len(matching)} message(s) retiré(s) "
        f"car le seuil de ±{ALERT_THRESHOLD_PERCENT:g} % n'est plus atteint"
    )


def run_fake_alert_progression() -> None:
    """Exercise one green and one red message through a four-minute fake progression."""
    steps = [10.0, 25.0, 100.0, 200.0, 300.0]
    for index, magnitude in enumerate(steps):
        for upward, change, color in (
            (True, magnitude, 5763719),
            (False, -magnitude, 15158332),
        ):
            # Fake a matching spot price from a 0.001 USDT baseline. A spot price
            # cannot fall below zero, so simulated drops beyond -100% stay at zero.
            fake_xtm_price = max(0.0, 0.001 * (1.0 + change / 100.0))
            quotes = [
                {"label": "XTM", "price": fake_xtm_price, "change": change, "market": "MEXC (test)", "error": None},
                {"label": "wXTM", "price": 0.0021, "currency": "USD", "change": 3.2, "market": "Uniswap V4 (test)", "error": None},
            ]
            affected = alert_quotes_for_direction(quotes, upward=upward)
            text = format_group_alert_text(affected, upward=upward)
            result = upsert_alert(
                text,
                color,
                affected,
                upward=upward,
                test_label="[TEST FICTIF 4 MIN]",
                notify_everyone=True,
            )
            print(f"Minute {index}: {result}")
        if index < len(steps) - 1:
            print("Attente de 60 secondes avant la prochaine variation simulée.", flush=True)
            time.sleep(60)


def verify_fake_alert_progression() -> None:
    """Read back the final fake test alerts and assert one correct box per direction."""
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN est absent")
    headers = {"Authorization": f"Bot {token}"}
    bot = api_json(f"{DISCORD_BASE}/users/@me", headers=headers)
    messages = api_json(
        f"{DISCORD_BASE}/channels/{ALERT_CHANNEL_ID}/messages?limit=100",
        headers=headers,
    )
    expected = [
        ("Alert XTM+300%", 5763719, "0.004 USDT"),
        ("Alert XTM-300%  GO BUY", 15158332, "0 USDT"),
    ]
    for expected_title, expected_color, expected_price in expected:
        title = f"[TEST FICTIF 4 MIN] {expected_title}"
        found = [
            message
            for message in messages
            if message.get("author", {}).get("id") == bot.get("id")
            and any(embed.get("title") == title for embed in message.get("embeds", []))
        ]
        if len(found) != 1:
            raise RuntimeError(f"Résultat attendu une seule fois dans mog-post : {title} (trouvé {len(found)})")
        message = found[0]
        embed = next(embed for embed in message.get("embeds", []) if embed.get("title") == title)
        xtm_field = next(field for field in embed.get("fields", []) if field.get("name") == "XTM")
        if not message.get("mention_everyone"):
            raise RuntimeError(f"La mention @everyone n'est pas active sur {title}")
        if embed.get("color") != expected_color or expected_price not in xtm_field.get("value", ""):
            raise RuntimeError(f"Le prix ou la couleur finale ne correspond pas pour {title}")
        print(
            f"Vérifié: id={message['id']}; {message.get('content')}; "
            f"mention_everyone={message.get('mention_everyone')}; XTM={xtm_field['value']}"
        )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if os.getenv("TEST_ALERTS", "").strip().lower() == "verify_test":
        verify_fake_alert_progression()
        return 0
    if os.getenv("TEST_ALERTS", "").strip().lower() == "progression_4min":
        print("Test fictif sur quatre minutes : alertes marquées TEST, sans @everyone.", flush=True)
        run_fake_alert_progression()
        return 0
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
    previous_changes: dict[str, float] = {}
    print(update_discord(embed, previous_changes=previous_changes))
    if os.getenv("TEST_ALERTS", "").strip().lower() == "both_once":
        print("Test manuel: envoi unique des alertes verte et rouge dans mog-post")
        for upward, color in ((True, 5763719), (False, 15158332)):
            simulated = [
                {**quote, "change": ALERT_THRESHOLD_PERCENT if upward else -ALERT_THRESHOLD_PERCENT}
                for quote in quotes
                if quote["label"] in {"XTM", "wXTM"}
            ]
            affected = alert_quotes_for_direction(simulated, upward=upward)
            text = format_group_alert_text(affected, upward=upward)
            print(upsert_alert(text, color, affected, upward=upward, previous_changes=previous_changes))
        return 0
    for upward, color in ((True, 5763719), (False, 15158332)):
        affected = alert_quotes_for_direction(quotes, upward=upward)
        if not affected:
            print(clear_alert_if_below_threshold(quotes, upward=upward))
            continue
        text = format_group_alert_text(affected, upward=upward)
        description = ", ".join(f"{quote['label']} {float(quote['change']):+.2f}%" for quote in affected)
        print(f"Variation 24 h détectée ({description}); lancement de l'alerte")
        print(upsert_alert(text, color, affected, upward=upward, previous_changes=previous_changes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
