"""Discord bot that publishes XTM and wXTM prices every 5 minutes.

The service is designed to run on an always-on cloud worker. It creates a new
Discord message on every refresh.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import discord
from discord.ext import tasks
from pool_data import (
    GECKOTERMINAL_BASE,
    MEXC_KLINE_BASE,
    WXTM_NETWORK,
    WXTM_POOL_ADDRESS,
    parse_mexc_1h_change,
    parse_wxtm_pool_response,
)
from settings import (
    DEFAULT_UPWARD_ALERT_THRESHOLD,
    parse_alerts_paused,
    parse_upward_alert_threshold,
    threshold_for_direction,
)


LOGGER = logging.getLogger("xtm-price-bot")
ALERT_PREFIX = "Alerte "
LEGACY_ALERT_PREFIX = "Alert "
ALERT_PREFIXES = (ALERT_PREFIX, LEGACY_ALERT_PREFIX)
ALERT_MILESTONE_FOOTER = "Palier @everyone notifié : "
ALERT_PERCENT_RE = re.compile(r"(?:XTM|wXTM)\s*([+-])\s*(\d+(?:\.\d+)?)%")
PRICE_EMBED_TITLE = "💱 Prix XTM / wXTM"
PRICE_CHANGE_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%\s*sur 24 h", re.IGNORECASE)


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} doit être un entier") from exc


def parse_coin_config(raw: str) -> list[tuple[str, str, str]]:
    """Parse ``coin_id:label:market`` entries from COINS."""

    result: list[tuple[str, str, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        parts = [part.strip() for part in item.split(":")]
        if len(parts) != 3 or not all(parts):
            raise ValueError(
                "COINS doit utiliser le format coin-gecko-id:label:marche, séparé par des virgules"
            )
        result.append((parts[0], parts[1], parts[2]))
    if not result:
        raise ValueError("COINS ne peut pas être vide")
    return result


def select_usdt_ticker(tickers: list[dict[str, Any]], preferred_market: str) -> dict[str, Any] | None:
    """Return the preferred USDT ticker with a numeric last price."""

    candidates: list[dict[str, Any]] = []
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
        ticker = dict(ticker)
        ticker["_last_float"] = last
        ticker["_volume_float"] = volume
        candidates.append(ticker)
    return max(candidates, key=lambda item: item["_volume_float"], default=None)


def format_price(value: float | None, currency: str = "USDT", *, label: str | None = None) -> str:
    if value is None:
        return "indisponible"
    if label == "wXTM":
        return f"{value:.5f} USDT"
    if label == "XTM":
        return f"{value:.5f} {currency}"
    if value >= 1:
        return f"{value:,.4f} {currency}".replace(",", " ")
    return f"{value:.10f}".rstrip("0").rstrip(".") + f" {currency}"


def format_change(value: float | None, *, hours: int = 24) -> str:
    if value is None:
        return f"variation {hours} h indisponible"
    return f"{value:+.2f} % sur {hours} h"


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
    quotes: list["PriceQuote"], previous_changes: dict[str, float]
) -> str:
    available = [quote for quote in quotes if quote.change_24h is not None]
    if not available:
        return "~"
    leading = max(available, key=lambda quote: abs(float(quote.change_24h)))
    return variation_trend_sign(
        float(leading.change_24h), previous_changes.get(leading.label)
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


def extract_previous_price_changes(embed: discord.Embed) -> dict[str, float]:
    if embed.title != PRICE_EMBED_TITLE:
        return {}
    changes: dict[str, float] = {}
    for field in embed.fields:
        match = PRICE_CHANGE_RE.search(field.value)
        if match:
            changes[field.name] = float(match.group(1))
    return changes


def extract_alert_changes(messages: list[discord.Message]) -> dict[str, float]:
    """Read each asset's displayed 24 h change from the latest matching alert."""
    changes: dict[str, float] = {}
    for message in messages:
        for embed in message.embeds:
            for field in embed.fields:
                match = PRICE_CHANGE_RE.search(field.value)
                if match:
                    changes.setdefault(field.name, float(match.group(1)))
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


def format_alert_delta_lines(changes: dict[str, float], deltas: dict[str, float]) -> str:
    """Give XTM and wXTM independent color and movement rows."""
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


def format_alert_text(
    change: float,
    *,
    upward: bool,
    threshold: float | None = None,
    label: str = "XTM",
) -> str:
    threshold = threshold or (DEFAULT_UPWARD_ALERT_THRESHOLD if upward else 10.0)
    magnitude = min(300.0, max(threshold, abs(float(change))))
    percent = f"{magnitude:.2f}".rstrip("0").rstrip(".")
    if upward:
        return f"{ALERT_PREFIX}{label} +{percent}%"
    return f"{ALERT_PREFIX}{label} -{percent}%  GO BUY"


def alert_quotes_for_direction(
    quotes: list["PriceQuote"], *, upward: bool, threshold: float | None = None
) -> list["PriceQuote"]:
    threshold = threshold or (DEFAULT_UPWARD_ALERT_THRESHOLD if upward else 10.0)
    by_label = {quote.label: quote for quote in quotes}
    affected: list[PriceQuote] = []
    for label in ("XTM", "wXTM"):
        quote = by_label.get(label)
        if quote is None or quote.change_24h is None:
            continue
        triggered = quote.change_24h >= threshold if upward else quote.change_24h <= -threshold
        if triggered:
            affected.append(quote)
    return affected


def alert_changes_are_complete(quotes: list["PriceQuote"], labels: set[str]) -> bool:
    """Avoid deleting a live alert when any configured 24 h change is unavailable."""
    by_label = {quote.label: quote for quote in quotes}
    return bool(labels) and all(
        label in by_label and by_label[label].change_24h is not None
        for label in labels
    )


def format_group_alert_text(
    quotes: list["PriceQuote"], *, upward: bool, threshold: float | None = None
) -> str:
    threshold = threshold or (DEFAULT_UPWARD_ALERT_THRESHOLD if upward else 10.0)
    components = [
        format_alert_text(
            quote.change_24h, upward=upward, threshold=threshold, label=quote.label
        ).removeprefix(ALERT_PREFIX).removesuffix("  GO BUY")
        for quote in quotes
        if quote.change_24h is not None
    ]
    suffix = "  GO BUY" if not upward else ""
    return f"{ALERT_PREFIX}{' | '.join(components)}{suffix}"


def is_alert_title(title: str, *, upward: bool) -> bool:
    title = strip_trend_prefix(title)
    return title.startswith(ALERT_PREFIXES) and ("+" in title if upward else "-" in title)


def alert_milestone(quotes: list["PriceQuote"]) -> int:
    changes = [abs(quote.change_24h) for quote in quotes if quote.change_24h is not None]
    if not changes:
        return 0
    magnitude = min(300.0, max(changes))
    return int(math.floor((magnitude + 1e-9) / 10.0) * 10)


def message_notified_milestone(message: discord.Message, *, upward: bool) -> int:
    sign = "+" if upward else "-"
    for embed in message.embeds:
        footer_text = embed.footer.text or ""
        marker = re.search(r"Palier @everyone notifié : ([+-])(\d+)%", footer_text)
        if marker and marker.group(1) == sign:
            return int(marker.group(2))

    if not message.mention_everyone:
        return 0
    values = [
        float(match.group(2))
        for embed in message.embeds
        for match in ALERT_PERCENT_RE.finditer(embed.title or "")
        if match.group(1) == sign
    ]
    return alert_milestone([PriceQuote("", "", None, value, None, "", None) for value in values])


@dataclass(frozen=True)
class PriceQuote:
    coin_id: str
    label: str
    price: float | None
    change_24h: float | None
    market: str | None
    preferred_market: str
    updated_at: int | None
    error: str | None = None
    currency: str = "USDT"
    change_1h: float | None = None


class CoinGeckoClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key: str | None, plan: str) -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.plan = plan.lower().strip()

    @property
    def headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        header_name = "x-cg-pro-api-key" if self.plan == "pro" else "x-cg-demo-api-key"
        return {header_name: self.api_key}

    async def get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with self.session.get(url, params=params, headers=self.headers) as response:
                    if response.status == 429:
                        retry_after = response.headers.get("Retry-After")
                        delay = float(retry_after) if retry_after else 2.0 * (attempt + 1)
                        await asyncio.sleep(min(delay, 20.0))
                        continue
                    response.raise_for_status()
                    return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"CoinGecko indisponible: {last_error}") from last_error

    async def get_wxtm_pool_json(self) -> dict[str, Any]:
        url = f"{GECKOTERMINAL_BASE}/networks/{WXTM_NETWORK}/pools/{WXTM_POOL_ADDRESS}"
        headers = {"Accept": "application/json;version=20230302", "User-Agent": "xtm-price-discord-bot/1.0"}
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with self.session.get(url, headers=headers) as response:
                    if response.status == 429:
                        retry_after = response.headers.get("Retry-After")
                        delay = float(retry_after) if retry_after else 2.0 * (attempt + 1)
                        await asyncio.sleep(min(delay, 20.0))
                        continue
                    response.raise_for_status()
                    return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"Pool wXTM/ETH indisponible : {last_error}") from last_error

    async def get_mexc_1h_change(self, symbol: str, current_price: float) -> float | None:
        """Compute a rolling one-hour return using public MEXC 1-minute candles."""
        url = f"{MEXC_KLINE_BASE}/api/v3/klines"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with self.session.get(
                    url,
                    params={"symbol": symbol, "interval": "1m", "limit": "100"},
                    headers={"User-Agent": "xtm-price-discord-bot/1.0"},
                ) as response:
                    if response.status == 429:
                        retry_after = response.headers.get("Retry-After")
                        delay = float(retry_after) if retry_after else 2.0 * (attempt + 1)
                        await asyncio.sleep(min(delay, 20.0))
                        continue
                    response.raise_for_status()
                    return parse_mexc_1h_change(await response.json(), current_price)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"Chandelles MEXC 1 m indisponibles pour {symbol}: {last_error}") from last_error

    async def quote(self, coin_id: str, label: str, preferred_market: str) -> PriceQuote:
        if label == "wXTM":
            try:
                pool_quote = parse_wxtm_pool_response(await self.get_wxtm_pool_json())
                return PriceQuote(
                    coin_id=coin_id,
                    label=label,
                    price=pool_quote["price"],
                    change_24h=pool_quote["change"],
                    change_1h=pool_quote["change_1h"],
                    market=pool_quote["market"],
                    preferred_market=preferred_market,
                    updated_at=None,
                    error=None,
                    currency=pool_quote["currency"],
                )
            except Exception as exc:
                LOGGER.warning("Impossible de récupérer wXTM sur Uniswap : %s", exc)
                return PriceQuote(
                    coin_id=coin_id,
                    label=label,
                    price=None,
                    change_24h=None,
                    market="Uniswap V4 (Ethereum)",
                    preferred_market=preferred_market,
                    updated_at=None,
                    error=str(exc),
                    currency="USD",
                )

        try:
            simple, tickers = await asyncio.gather(
                self.get_json(
                    "/simple/price",
                    {
                        "ids": coin_id,
                        "vs_currencies": "usd",
                        "include_24hr_change": "true",
                        "include_last_updated_at": "true",
                    },
                ),
                self.get_json(
                    f"/coins/{coin_id}/tickers",
                    {"include_exchange_logo": "false", "page": "1"},
                ),
            )
            coin_data = simple.get(coin_id, {})
            ticker = select_usdt_ticker(tickers.get("tickers", []), preferred_market)
            change_1h = None
            if ticker and preferred_market.lower() == "mexc":
                base = str(ticker.get("base", "")).strip().upper()
                target = str(ticker.get("target", "")).strip().upper()
                if base and target and ticker["_last_float"] > 0:
                    try:
                        change_1h = await self.get_mexc_1h_change(
                            f"{base}{target}", ticker["_last_float"]
                        )
                    except Exception as exc:
                        LOGGER.warning("Variation XTM sur 1 h indisponible via MEXC : %s", exc)
            return PriceQuote(
                coin_id=coin_id,
                label=label,
                price=ticker["_last_float"] if ticker else None,
                change_24h=coin_data.get("usd_24h_change"),
                change_1h=change_1h,
                market=(ticker.get("market", {}).get("name") if ticker else None),
                preferred_market=preferred_market,
                updated_at=coin_data.get("last_updated_at"),
                error=None if ticker else f"marché {preferred_market} indisponible",
            )
        except Exception as exc:  # keep one failed asset from hiding the other
            LOGGER.warning("Impossible de récupérer %s: %s", coin_id, exc)
            return PriceQuote(
                coin_id=coin_id,
                label=label,
                price=None,
                change_24h=None,
                market=None,
                preferred_market=preferred_market,
                updated_at=None,
                error=str(exc),
            )


class PriceBot(discord.Client):
    def __init__(self, *, channel_id: int, alert_channel_id: int, coins: list[tuple[str, str, str]], state_file: Path, interval_minutes: int, alert_threshold: float, alerts_paused: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.channel_id = channel_id
        self.alert_channel_id = alert_channel_id
        self.coins = coins
        self.state_file = state_file
        self.interval_minutes = interval_minutes
        self.alert_threshold = alert_threshold
        self.alerts_paused = alerts_paused
        self.alert_tasks: dict[bool, asyncio.Task[None] | None] = {True: None, False: None}
        self.http_session: aiohttp.ClientSession | None = None
        self.price_client: CoinGeckoClient | None = None
        self._state: dict[str, Any] = self.load_state()

    def load_state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
        temporary.replace(self.state_file)

    async def setup_hook(self) -> None:
        self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        self.price_client = CoinGeckoClient(
            self.http_session,
            os.getenv("COINGECKO_API_BASE", "https://api.coingecko.com/api/v3"),
            os.getenv("COINGECKO_API_KEY") or None,
            os.getenv("COINGECKO_API_PLAN", "demo"),
        )

    async def close(self) -> None:
        if self.price_loop.is_running():
            self.price_loop.cancel()
        for task in self.alert_tasks.values():
            if task and not task.done():
                task.cancel()
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        await super().close()

    async def on_ready(self) -> None:
        LOGGER.info("Connecté à Discord en tant que %s", self.user)
        if not self.price_loop.is_running():
            await self.publish_prices()
            self.price_loop.start()

    @tasks.loop(minutes=5)
    async def price_loop(self) -> None:
        await self.publish_prices()

    @price_loop.before_loop
    async def before_price_loop(self) -> None:
        await self.wait_until_ready()
        self.price_loop.change_interval(minutes=self.interval_minutes)

    async def resolve_channel(self, channel_id: int) -> discord.abc.Messageable:
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await super().fetch_channel(channel_id)
        return channel

    async def upsert_alert(self, text: str, colour: discord.Colour, quotes: list[PriceQuote], *, upward: bool, previous_changes: dict[str, float] | None = None) -> None:
        channel = await self.resolve_channel(self.alert_channel_id)
        bot_id = self.user.id if self.user else None
        history = getattr(channel, "history", None)
        existing_messages = []
        if bot_id is not None and history is not None:
            async for message in history(limit=100):
                if message.author.id != bot_id:
                    continue
                if any(embed.title and is_alert_title(embed.title, upward=upward) for embed in message.embeds):
                    existing_messages.append(message)
        previous_alert_changes = extract_alert_changes(existing_messages)

        current_milestone = alert_milestone(quotes)
        previous_milestone = max(
            (message_notified_milestone(message, upward=upward) for message in existing_messages),
            default=0,
        )
        should_notify = current_milestone > previous_milestone
        stored_milestone = max(previous_milestone, current_milestone)
        ping_quote = max(
            (quote for quote in quotes if quote.change_24h is not None),
            key=lambda quote: abs(float(quote.change_24h)),
            default=None,
        ) if should_notify else None

        created_messages = []
        for quote in quotes:
            if quote.change_24h is None:
                continue
            magnitude = min(300.0, max(self.alert_threshold, abs(quote.change_24h)))
            current_display_changes = {
                quote.label: -magnitude if quote.change_24h < 0 else magnitude
            }
            deltas = calculate_alert_deltas(
                current_display_changes, previous_alert_changes, previous_changes
            )
            asset_text = format_alert_text(
                quote.change_24h,
                upward=upward,
                threshold=self.alert_threshold,
                label=quote.label,
            )
            alert_embed = discord.Embed(
                title=asset_text,
                description=format_alert_delta_lines(current_display_changes, deltas),
                colour=discord.Colour(0x2B2D31),
                timestamp=datetime.now(timezone.utc),
            )
            if quote.price is None:
                value = f"Indisponible — {quote.error or 'aucun marché USDT'}"
            else:
                display_change = quote.change_24h
                if display_change is not None:
                    magnitude = min(300.0, max(self.alert_threshold, abs(display_change)))
                    display_change = -magnitude if display_change < 0 else magnitude
                value = f"**{format_price(quote.price, quote.currency, label=quote.label)}**\n{format_change(display_change)}"
                if quote.market:
                    value += f"\nMarché : {quote.market}"
            alert_embed.add_field(name=quote.label, value=value, inline=False)
            alert_embed.set_footer(text=format_milestone_footer(stored_milestone, upward=upward))
            ping_this_asset = ping_quote is not None and quote.label == ping_quote.label
            created_messages.append(await channel.send(  # type: ignore[attr-defined]
                content="@everyone" if ping_this_asset else None,
                embed=alert_embed,
                allowed_mentions=(
                    discord.AllowedMentions(everyone=True)
                    if ping_this_asset
                    else discord.AllowedMentions.none()
                ),
            ))
        deleted = 0
        for old_message in existing_messages:
            await old_message.delete()
            deleted += 1
        ping = f"; @everyone au palier {('+' if upward else '-')}{current_milestone}%" if should_notify else ""
        LOGGER.info(
            "%s alerte(s) séparée(s) publiée(s) (%s); %s ancienne(s) alerte(s) supprimée(s)%s",
            len(created_messages),
            ", ".join(str(message.id) for message in created_messages),
            deleted,
            ping,
        )

    async def clear_alert_if_below_threshold(self, quotes: list[PriceQuote], *, upward: bool) -> None:
        threshold = threshold_for_direction(upward, self.alert_threshold)
        labels = {label for _, label, _ in self.coins}
        if not alert_changes_are_complete(quotes, labels):
            LOGGER.warning("Alerte %s conservée : variation 24 h incomplète", "verte" if upward else "rouge")
            return
        if alert_quotes_for_direction(quotes, upward=upward, threshold=threshold):
            return

        channel = await self.resolve_channel(self.alert_channel_id)
        bot_id = self.user.id if self.user else None
        history = getattr(channel, "history", None)
        if bot_id is None or history is None:
            return

        removed = 0
        async for message in history(limit=100):
            if message.author.id != bot_id:
                continue
            if not any(embed.title and is_alert_title(embed.title, upward=upward) for embed in message.embeds):
                continue
            await message.delete()
            removed += 1
        if removed:
            LOGGER.info(
                "Alerte %s supprimée : retour sous le seuil de %s %% (%s message(s))",
                "verte" if upward else "rouge",
                threshold if upward else -threshold,
                removed,
            )

    def start_alert_if_needed(self, quotes: list[PriceQuote], previous_changes: dict[str, float] | None = None) -> None:
        if self.alerts_paused:
            LOGGER.info("Alertes de mog-post en pause; les alertes affichées sont conservées")
            return
        for upward, colour in ((True, discord.Colour.green()), (False, discord.Colour.red())):
            threshold = threshold_for_direction(upward, self.alert_threshold)
            affected = alert_quotes_for_direction(quotes, upward=upward, threshold=threshold)
            if not affected:
                task = self.alert_tasks[upward]
                if task is None or task.done():
                    self.alert_tasks[upward] = asyncio.create_task(
                        self.clear_alert_if_below_threshold(quotes, upward=upward)
                    )
                continue
            text = format_group_alert_text(affected, upward=upward, threshold=threshold)
            task = self.alert_tasks[upward]
            if task is None or task.done():
                self.alert_tasks[upward] = asyncio.create_task(
                    self.upsert_alert(text, colour, affected, upward=upward, previous_changes=previous_changes)
                )
                changes = ", ".join(
                    f"{quote.label} {quote.change_24h:+.2f}%"
                    for quote in affected
                    if quote.change_24h is not None
                )
                LOGGER.warning("Alerte %s déclenchée sur 24 h (%s)", text, changes)

    async def publish_prices(self) -> None:
        if self.price_client is None:
            return
        channel = await self.resolve_channel(self.channel_id)
        quotes = await asyncio.gather(*(self.price_client.quote(coin_id, label, preferred_market) for coin_id, label, preferred_market in self.coins))
        if not any(quote.price is not None for quote in quotes):
            LOGGER.error("Aucun prix valide disponible; le message Discord n'est pas remplacé")
            return

        previous_changes: dict[str, float] = {}
        old_price_messages: list[discord.Message] = []
        bot_id = self.user.id if self.user else None
        history = getattr(channel, "history", None)
        if bot_id is not None and history is not None:
            found_previous_price_embed = False
            async for old_message in history(limit=100):
                if old_message.author.id != bot_id:
                    continue
                for old_embed in old_message.embeds:
                    if old_embed.title == PRICE_EMBED_TITLE:
                        old_price_messages.append(old_message)
                        if not found_previous_price_embed:
                            previous_changes = extract_previous_price_changes(old_embed)
                        found_previous_price_embed = True
                        break

        embed = discord.Embed(
            title=PRICE_EMBED_TITLE,
            description="XTM/USDT sur MEXC ; wXTM/USDT indicatif depuis Uniswap V4 (prix USD affiché à parité).",
            colour=discord.Colour.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        for quote in quotes:
            if quote.price is None:
                value = f"Indisponible — {quote.error or 'marché indisponible'}"
            else:
                value = (
                    f"**{format_price(quote.price, quote.currency, label=quote.label)}**\n"
                    f"{format_change(quote.change_24h)}\n"
                    f"{format_change(quote.change_1h, hours=1)}"
                )
                if quote.market:
                    value += f"\nMarché : {quote.market}"
            embed.add_field(name=quote.label, value=value, inline=True)
        embed.set_footer(text=f"Nouveau message toutes les {self.interval_minutes} minutes")
        created = await channel.send(embed=embed)  # type: ignore[attr-defined]
        for old_message in old_price_messages:
            await old_message.delete()
        if old_price_messages:
            LOGGER.info(
                "Nouvelle box de prix publiée (%s); %s ancienne(s) box supprimée(s)",
                created.id,
                len(old_price_messages),
            )
        self.start_alert_if_needed(quotes, previous_changes)
        LOGGER.info("Prix publiés dans le salon %s", self.channel_id)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN n'est pas configuré")
    channel_id = env_int("DISCORD_CHANNEL_ID", 1370700962695610430)
    interval = env_int("INTERVAL_MINUTES", 5)
    if interval < 5:
        raise SystemExit("INTERVAL_MINUTES doit être au moins égal à 5")
    coins = parse_coin_config(os.getenv("COINS", "minotari:XTM:MEXC,wrapped-minotari:wXTM:Uniswap V4"))
    alert_channel_id = env_int("ALERT_CHANNEL_ID", 1163364187796426776)
    alert_threshold = parse_upward_alert_threshold(os.getenv("ALERT_THRESHOLD_PERCENT"))
    alerts_paused = parse_alerts_paused(os.getenv("ALERTS_PAUSED"))
    state_file = Path(os.getenv("STATE_FILE", "/data/state.json"))
    intents = discord.Intents.none()
    bot = PriceBot(
        channel_id=channel_id,
        alert_channel_id=alert_channel_id,
        coins=coins,
        state_file=state_file,
        interval_minutes=interval,
        alert_threshold=alert_threshold,
        alerts_paused=alerts_paused,
        intents=intents,
    )
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
