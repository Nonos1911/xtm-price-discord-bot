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

from pool_data import (
    GECKOTERMINAL_BASE,
    MEXC_KLINE_BASE,
    WXTM_NETWORK,
    WXTM_POOL_ADDRESS,
    parse_mexc_1h_change,
    parse_wxtm_pool_response,
)
from settings import (
    parse_alerts_paused,
    parse_upward_alert_threshold,
    threshold_for_direction,
)


COINGECKO_BASE = os.getenv("COINGECKO_API_BASE", "https://api.coingecko.com/api/v3").rstrip("/")
DISCORD_BASE = "https://discord.com/api/v10"
CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "1370700962695610430")
ALERT_CHANNEL_ID = os.getenv("ALERT_CHANNEL_ID", "1163364187796426776")
ALERT_THRESHOLD_PERCENT = parse_upward_alert_threshold(os.getenv("ALERT_THRESHOLD_PERCENT"))
OBSOLETE_TEST_LABEL = "[TEST SIMULATION 2026-09-20 12:20:17 UTC]"
PRICE_EMBED_TITLE = "💱 Prix XTM / wXTM"
ALERT_PREFIX = "Alerte "
LEGACY_ALERT_PREFIX = "Alert "
ALERT_PREFIXES = (ALERT_PREFIX, LEGACY_ALERT_PREFIX)
ALERT_MILESTONE_FOOTER = "Palier @everyone notifié : "
ALERT_PERCENT_RE = re.compile(r"(?:XTM|wXTM)\s*([+-])\s*(\d+(?:\.\d+)?)%")
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
    return {
        "price": last,
        "volume": volume,
        "market": ticker.get("market", {}).get("name", "inconnu"),
        "base": ticker.get("base"),
        "target": ticker.get("target"),
    }


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
                "change_1h": None,
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
    change_1h = None
    if ticker and preferred_market.lower() == "mexc":
        base = str(ticker.get("base", "")).strip().upper()
        target = str(ticker.get("target", "")).strip().upper()
        if base and target:
            try:
                kline_query = urllib.parse.urlencode(
                    {"symbol": f"{base}{target}", "interval": "1m", "limit": "100"}
                )
                klines = api_json(f"{MEXC_KLINE_BASE}/api/v3/klines?{kline_query}")
                change_1h = parse_mexc_1h_change(klines, ticker["price"])
            except Exception as exc:
                print(f"Variation {label} sur 1 h indisponible via MEXC : {exc}", file=sys.stderr)
    return {
        "label": label,
        "price": ticker["price"] if ticker else None,
        "currency": "USDT",
        "change": coin.get("usd_24h_change"),
        "change_1h": change_1h,
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
    change_1h = quote.get("change_1h")
    change_1h_text = "variation 1 h indisponible" if change_1h is None else f"{change_1h:+.2f} % sur 1 h"
    currency = "USDT" if is_wxtm else quote.get("currency", "USDT")
    lines = [f"**{formatted} {currency}**", change_text, change_1h_text]
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


def format_alert_delta_lines(
    changes: dict[str, float], deltas: dict[str, float]
) -> str:
    """Render independent color/delta rows so XTM cannot inherit wXTM's trend."""
    lines = []
    for label in ("XTM", "wXTM"):
        if label not in changes:
            continue
        delta = deltas.get(label)
        rounded = None if delta is None else (0.0 if round(delta, 2) == 0 else round(delta, 2))
        if rounded is None or rounded == 0:
            marker, value = "🟡", "~ 0.00%"
        elif rounded > 0:
            marker, value = "🟢", f"+ {rounded:.2f}%"
        else:
            marker, value = "🔴", f"- {abs(rounded):.2f}%"
        lines.append(f"{marker} {value} — **{label}**")
    return "\n".join(lines)


def format_milestone_footer(notified_milestone: int, *, upward: bool) -> str:
    sign = "+" if upward else "-"
    future = list(range(notified_milestone + 10, 301, 10))
    upcoming = future[:3]
    if upcoming:
        next_text = " / ".join(f"{sign}{milestone}%" for milestone in upcoming)
        if len(future) > len(upcoming):
            next_text += " …"
    else:
        next_text = "aucun (limite 300%)"
    return (
        f"{ALERT_MILESTONE_FOOTER}{sign}{notified_milestone}%\n"
        f"Prochains paliers @everyone : {next_text}"
    )


def variation_trend_sign(current: float, previous: float | None) -> str:
    if previous is None:
        return "~"
    if current > previous:
        return "+"
    if current < previous:
        return "-"
    return "~"


def format_trend_prefix(sign: str) -> str:
    return {"+": "🟢 +", "-": "🔴 -", "~": "🟡 ~ 0.00%"}.get(sign, "🟡 ~ 0.00%")


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
        alert_starts = [title.find(prefix, 1) for prefix in ALERT_PREFIXES]
        valid_starts = [start for start in alert_starts if start >= 0]
        if valid_starts:
            return title[min(valid_starts):]
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
    threshold: float | None = None,
    previous_changes: dict[str, float] | None = None,
    previous_alert_changes: dict[str, float] | None = None,
    test_label: str | None = None,
    show_trend_for_test: bool = False,
) -> dict[str, Any]:
    effective_threshold = threshold_for_direction(
        upward, ALERT_THRESHOLD_PERCENT if threshold is None else threshold
    )
    alert_quotes = []
    if quotes is not None:
        for quote in quotes:
            if quote["change"] is not None:
                magnitude = min(300.0, max(effective_threshold, abs(float(quote["change"]))))
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
    description = ""
    if test_label is None or show_trend_for_test:
        description = format_alert_delta_lines(current_display_changes, deltas)
    if test_label:
        display_title = f"{test_label} {display_title}"
    embed = {
        "title": display_title,
        "color": 2829617,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if description:
        embed["description"] = description
    if quotes is not None:
        fields = []
        for quote in alert_quotes:
            value = price_text(quote)
            fields.append({"name": quote["label"], "value": value, "inline": False})
        embed["fields"] = fields
    if notified_milestone is not None:
        embed["footer"] = {"text": format_milestone_footer(notified_milestone, upward=upward)}
    return embed


def alert_quotes_for_direction(
    quotes: list[dict[str, Any]], *, upward: bool, threshold: float | None = None
) -> list[dict[str, Any]]:
    """Return only XTM/wXTM quotes that currently meet the directional threshold."""
    effective_threshold = threshold_for_direction(
        upward, ALERT_THRESHOLD_PERCENT if threshold is None else threshold
    )
    by_label = {quote.get("label"): quote for quote in quotes}
    affected: list[dict[str, Any]] = []
    for label in ("XTM", "wXTM"):
        quote = by_label.get(label)
        if quote is None or quote.get("change") is None:
            continue
        change = float(quote["change"])
        triggered = change >= effective_threshold if upward else change <= -effective_threshold
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


def format_alert_text(
    change: float, *, upward: bool, threshold: float | None = None, label: str = "XTM"
) -> str:
    effective_threshold = threshold_for_direction(
        upward, ALERT_THRESHOLD_PERCENT if threshold is None else threshold
    )
    magnitude = min(300.0, max(effective_threshold, abs(float(change))))
    percent = f"{magnitude:.2f}".rstrip("0").rstrip(".")
    if upward:
        return f"{ALERT_PREFIX}{label} +{percent}%"
    return f"{ALERT_PREFIX}{label} -{percent}%  GO BUY"


def format_group_alert_text(
    quotes: list[dict[str, Any]], *, upward: bool, threshold: float | None = None
) -> str:
    """Build one alert title listing each asset that crossed the same threshold."""
    components = [
        format_alert_text(
            float(quote["change"]), upward=upward, threshold=threshold,
            label=str(quote["label"]),
        )
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
    return title.startswith(ALERT_PREFIXES) and ("+" in title if upward else "-" in title)


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
    threshold: float | None = None,
    test_label: str | None = None,
    notify_everyone: bool = True,
    previous_changes: dict[str, float] | None = None,
    show_trend_for_test: bool = False,
) -> str:
    """Publish a fresh alert each run, removing older copies and pinging at 10% steps."""
    effective_threshold = threshold_for_direction(
        upward, ALERT_THRESHOLD_PERCENT if threshold is None else threshold
    )
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
    ping_quote = max(
        (quote for quote in quotes if quote.get("change") is not None),
        key=lambda quote: abs(float(quote["change"])),
        default=None,
    ) if should_notify else None
    created_messages = []
    for quote in quotes:
        label = str(quote["label"])
        asset_text = format_alert_text(
            float(quote["change"]), upward=upward,
            threshold=effective_threshold, label=label,
        )
        asset_embed = build_alert_embed(
            asset_text,
            color,
            [quote],
            notified_milestone=stored_milestone,
            upward=upward,
            threshold=effective_threshold,
            previous_changes=previous_changes,
            previous_alert_changes=previous_alert_changes,
            test_label=test_label,
            show_trend_for_test=show_trend_for_test,
        )
        ping_this_asset = ping_quote is not None and label == str(ping_quote["label"])
        payload: dict[str, Any] = {
            "embeds": [asset_embed],
            "allowed_mentions": {"parse": ["everyone"]} if ping_this_asset else {"parse": []},
        }
        if ping_this_asset:
            payload["content"] = "@everyone"
        created_messages.append(api_json(
            f"{DISCORD_BASE}/channels/{ALERT_CHANNEL_ID}/messages",
            headers=headers,
            method="POST",
            body=payload,
        ))
    removed = 0
    for old_message in matching:
        api_json(
            f"{DISCORD_BASE}/channels/{ALERT_CHANNEL_ID}/messages/{old_message['id']}",
            headers=headers,
            method="DELETE",
        )
        removed += 1
    published = ", ".join(
        f"{quote['label']} ({message['id']})"
        for quote, message in zip(quotes, created_messages)
    )
    suffix = (
        f"; {removed} ancienne alerte{'s' if removed != 1 else ''} supprimée"
        f"{'s' if removed != 1 else ''}"
        if removed
        else ""
    )
    ping = f"; @everyone palier {('+' if upward else '-')}{current_milestone}%" if should_notify else ""
    return f"Alertes séparées publiées : {published}{suffix}{ping}"


def clear_alert_if_below_threshold(
    quotes: list[dict[str, Any]], *, upward: bool, threshold: float | None = None
) -> str:
    """Delete the production alert after all tracked changes leave its threshold zone."""
    effective_threshold = threshold_for_direction(
        upward, ALERT_THRESHOLD_PERCENT if threshold is None else threshold
    )
    if not alert_changes_are_complete(quotes):
        return "données 24 h incomplètes; alerte conservée"
    if alert_quotes_for_direction(quotes, upward=upward, threshold=effective_threshold):
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
        f"car le seuil de {effective_threshold if upward else -effective_threshold:g} % n'est plus atteint"
    )


def run_fake_alert_progression() -> None:
    """Exercise one green and one red message through a four-minute fake progression."""
    upward_steps = [10.0, 25.0, 100.0, 200.0, 300.0]
    downward_steps = [30.0, 30.0, 100.0, 200.0, 300.0]
    for index in range(len(upward_steps)):
        for upward, change, color in (
            (True, upward_steps[index], 5763719),
            (False, -downward_steps[index], 15158332),
        ):
            # Fake a matching spot price from a 0.001 USDT baseline. A spot price
            # cannot fall below zero, so simulated drops beyond -100% stay at zero.
            fake_xtm_price = max(0.0, 0.001 * (1.0 + change / 100.0))
            quotes = [
                {"label": "XTM", "price": fake_xtm_price, "change": change, "market": "MEXC (test)", "error": None},
                {"label": "wXTM", "price": 0.0021, "currency": "USD", "change": 3.2, "market": "Uniswap V4 (test)", "error": None},
            ]
            affected = alert_quotes_for_direction(quotes, upward=upward, threshold=10.0)
            text = format_group_alert_text(affected, upward=upward, threshold=10.0)
            result = upsert_alert(
                text,
                color,
                affected,
                upward=upward,
                threshold=10.0,
                test_label="[TEST FICTIF 4 MIN]",
                notify_everyone=True,
            )
            print(f"Minute {index}: {result}")
        if index < len(upward_steps) - 1:
            print("Attente de 60 secondes avant la prochaine variation simulée.", flush=True)
            time.sleep(60)


def remove_obsolete_test_alert() -> int:
    """Remove only the prior one-off test message whose long label was replaced."""
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN est absent")
    headers = {"Authorization": f"Bot {token}"}
    bot = api_json(f"{DISCORD_BASE}/users/@me", headers=headers)
    messages = api_json(
        f"{DISCORD_BASE}/channels/{ALERT_CHANNEL_ID}/messages?limit=100",
        headers=headers,
    )
    prefix = f"{OBSOLETE_TEST_LABEL} "
    matching = [
        message
        for message in messages
        if message.get("author", {}).get("id") == bot.get("id")
        and any(
            str(embed.get("title", "")).startswith(prefix)
            for embed in message.get("embeds", [])
        )
    ]
    for message in matching:
        api_json(
            f"{DISCORD_BASE}/channels/{ALERT_CHANNEL_ID}/messages/{message['id']}",
            headers=headers,
            method="DELETE",
        )
    return len(matching)


def remove_test_alerts(test_label: str) -> int:
    """Clean only this bot's test-labeled alerts before a deterministic test run."""
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN est absent")
    headers = {"Authorization": f"Bot {token}"}
    bot = api_json(f"{DISCORD_BASE}/users/@me", headers=headers)
    messages = api_json(
        f"{DISCORD_BASE}/channels/{ALERT_CHANNEL_ID}/messages?limit=100",
        headers=headers,
    )
    prefix = f"{test_label} "
    matching = [
        message
        for message in messages
        if message.get("author", {}).get("id") == bot.get("id")
        and any(str(embed.get("title", "")).startswith(prefix) for embed in message.get("embeds", []))
    ]
    for message in matching:
        api_json(
            f"{DISCORD_BASE}/channels/{ALERT_CHANNEL_ID}/messages/{message['id']}",
            headers=headers,
            method="DELETE",
        )
    return len(matching)


def run_color_badge_progression_3min() -> None:
    """Refresh paired positive/negative test alerts for three full minutes."""
    removed = remove_obsolete_test_alert()
    print(f"Ancienne alerte de test renommée supprimée: {removed}", flush=True)
    upward_progressions = [(12.0, 14.0), (15.0, 17.0), (18.0, 20.0), (22.0, 24.0)]
    downward_progressions = [(32.0, 34.0), (35.0, 37.0), (38.0, 40.0), (42.0, 44.0)]
    previous_by_direction = {
        True: {"XTM": 0.0, "wXTM": 0.0},
        False: {"XTM": 0.0, "wXTM": 0.0},
    }
    for minute in range(len(upward_progressions)):
        for upward, color in ((True, 5763719), (False, 15158332)):
            xtm_change, wxtm_change = (
                upward_progressions[minute]
                if upward
                else downward_progressions[minute]
            )
            sign = 1 if upward else -1
            quotes = [
                {
                    "label": "XTM",
                    "price": 0.001 * (1 + sign * xtm_change / 100),
                    "change": sign * xtm_change,
                    "market": "MEXC (simulation)",
                    "error": None,
                },
                {
                    "label": "wXTM",
                    "price": 0.002 * (1 + sign * wxtm_change / 100),
                    "currency": "USD",
                    "change": sign * wxtm_change,
                    "market": "Uniswap V4 (Ethereum, simulation)",
                    "error": None,
                },
            ]
            affected = alert_quotes_for_direction(quotes, upward=upward)
            text = format_group_alert_text(affected, upward=upward)
            preview = build_alert_embed(
                text,
                color,
                affected,
                upward=upward,
                previous_changes=previous_by_direction[upward],
                test_label="[test]",
                show_trend_for_test=True,
            )
            print(
                f"Minute {minute}: {preview['description']} — {preview['title']}",
                flush=True,
            )
            print(upsert_alert(
                text,
                color,
                affected,
                upward=upward,
                test_label="[test]",
                notify_everyone=False,
                previous_changes=previous_by_direction[upward],
                show_trend_for_test=True,
            ), flush=True)
            previous_by_direction[upward] = {
                "XTM": sign * xtm_change,
                "wXTM": sign * wxtm_change,
            }
        if minute < len(upward_progressions) - 1:
            print("Attente de 60 secondes avant la prochaine actualisation simulée.", flush=True)
            time.sleep(60)


def run_milestone_22_test() -> None:
    """Ping at +10%, then verify a second @everyone at +20% / +22% market change."""
    test_label = "[test]"
    removed = remove_test_alerts(test_label)
    print(f"Anciennes alertes {test_label} supprimées avant le test: {removed}", flush=True)
    before = [
        {"label": "XTM", "price": 0.00112, "change": 12.0, "market": "MEXC (simulation)", "error": None},
        {"label": "wXTM", "price": 0.00228, "currency": "USD", "change": 14.0, "market": "Uniswap V4 (simulation)", "error": None},
    ]
    text_before = format_group_alert_text(
        alert_quotes_for_direction(before, upward=True, threshold=10.0),
        upward=True,
        threshold=10.0,
    )
    print("Simulation +10% : premier @everyone; les deux actifs restent au-dessus de +10%.", flush=True)
    print(upsert_alert(
        text_before,
        5763719,
        before,
        upward=True,
        threshold=10.0,
        test_label=test_label,
        notify_everyone=True,
        previous_changes={"XTM": 0.0, "wXTM": 0.0},
        show_trend_for_test=True,
    ), flush=True)

    print("Attente de 20 secondes pour laisser apparaître le premier test dans Discord.", flush=True)
    time.sleep(20)
    after = [
        {"label": "XTM", "price": 0.00122, "change": 22.0, "market": "MEXC (simulation)", "error": None},
        {"label": "wXTM", "price": 0.00224, "currency": "USD", "change": 12.0, "market": "Uniswap V4 (simulation)", "error": None},
    ]
    text_after = format_group_alert_text(
        alert_quotes_for_direction(after, upward=True, threshold=10.0),
        upward=True,
        threshold=10.0,
    )
    preview = build_alert_embed(
        text_after,
        5763719,
        after,
        upward=True,
        threshold=10.0,
        previous_alert_changes={"XTM": 12.0, "wXTM": 14.0},
        test_label=test_label,
        show_trend_for_test=True,
    )
    print(f"Simulation +22% : {preview['description']} — {preview['title']}", flush=True)
    print(upsert_alert(
        text_after,
        5763719,
        after,
        upward=True,
        threshold=10.0,
        test_label=test_label,
        notify_everyone=True,
        show_trend_for_test=True,
    ), flush=True)


def run_positive_pair_test() -> str:
    """Post one tagged, green XTM+wXTM simulation without touching live alerts."""
    quotes = [
        {"label": "XTM", "price": 0.00108, "change": 10.8, "market": "MEXC (simulation)", "error": None},
        {
            "label": "wXTM",
            "price": 0.00243,
            "currency": "USD",
            "change": 13.84,
            "market": "Uniswap V4 (Ethereum, simulation)",
            "error": None,
        },
    ]
    affected = alert_quotes_for_direction(quotes, upward=True, threshold=10.0)
    text = format_group_alert_text(affected, upward=True, threshold=10.0)
    test_label = f"[TEST SIMULATION {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC]"
    return upsert_alert(
        text,
        5763719,
        affected,
        upward=True,
        threshold=10.0,
        test_label=test_label,
        notify_everyone=True,
    )


def run_both_alert_tests(quotes: list[dict[str, Any]]) -> list[str]:
    """Post tagged green/red simulations without touching production alerts."""
    results = []
    test_label = "[TEST SIMULATION BOTH ±10%]"
    for upward, color in ((True, 5763719), (False, 15158332)):
        threshold = threshold_for_direction(upward, ALERT_THRESHOLD_PERCENT)
        simulated = [
            {**quote, "change": threshold if upward else -threshold}
            for quote in quotes
            if quote["label"] in {"XTM", "wXTM"}
        ]
        affected = alert_quotes_for_direction(simulated, upward=upward, threshold=threshold)
        text = format_group_alert_text(affected, upward=upward, threshold=threshold)
        results.append(upsert_alert(
            text,
            color,
            affected,
            upward=upward,
            threshold=threshold,
            test_label=test_label,
            notify_everyone=True,
        ))
    return results


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
        ("Alerte XTM +300%", 2829617, "0.004 USDT"),
        ("Alerte XTM -300%  GO BUY", 2829617, "0 USDT"),
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
    test_mode = os.getenv("TEST_ALERTS", "").strip().lower()
    alerts_paused = parse_alerts_paused(os.getenv("ALERTS_PAUSED"))
    if alerts_paused and test_mode not in {"", "none", "verify_test"}:
        print("Alertes de mog-post en pause : aucun test d'alerte n'est publié.", flush=True)
        return 0
    if test_mode == "verify_test":
        verify_fake_alert_progression()
        return 0
    if test_mode == "progression_3min":
        run_color_badge_progression_3min()
        return 0
    if test_mode == "milestone_22":
        run_milestone_22_test()
        return 0
    if test_mode == "progression_4min":
        print("Test fictif sur quatre minutes : alertes marquées TEST, sans @everyone.", flush=True)
        run_fake_alert_progression()
        return 0
    if test_mode == "positive_pair_once":
        print("Simulation XTM +10.80 % / wXTM +13.84 % avec un ping @everyone, sans modifier les alertes réelles.", flush=True)
        print(run_positive_pair_test())
        return 0
    quotes = [get_quote(coin_id, label, preferred_market) for coin_id, label, preferred_market in COINS]
    if test_mode == "both_once":
        print("Tests vert/rouge simulés et étiquetés TEST; aucune alerte de production ne sera modifiée.", flush=True)
        for result in run_both_alert_tests(quotes):
            print(result)
        return 0
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
    if alerts_paused:
        print("Alertes de mog-post en pause : les cours ont été actualisés; les alertes existantes sont conservées.", flush=True)
        return 0
    for upward, color in ((True, 5763719), (False, 15158332)):
        threshold = threshold_for_direction(upward, ALERT_THRESHOLD_PERCENT)
        affected = alert_quotes_for_direction(quotes, upward=upward, threshold=threshold)
        if not affected:
            print(clear_alert_if_below_threshold(quotes, upward=upward, threshold=threshold))
            continue
        text = format_group_alert_text(affected, upward=upward, threshold=threshold)
        description = ", ".join(f"{quote['label']} {float(quote['change']):+.2f}%" for quote in affected)
        print(f"Variation 24 h détectée ({description}); lancement de l'alerte")
        print(upsert_alert(
            text, color, affected, upward=upward, threshold=threshold,
            previous_changes=previous_changes,
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
