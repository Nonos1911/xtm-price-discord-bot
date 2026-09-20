"""Discord bot that publishes XTM and wXTM prices every 5 minutes.

The service is designed to run on an always-on cloud worker. It creates a new
Discord message on every refresh.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import discord
from discord.ext import tasks


LOGGER = logging.getLogger("xtm-price-bot")
UP_ALERT_PREFIX = "Alert XTM+"
BUY_ALERT_PREFIX = "Alert XTM-"


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


def format_price(value: float | None) -> str:
    if value is None:
        return "indisponible"
    if value >= 1:
        return f"{value:,.4f} USDT".replace(",", " ")
    return f"{value:.10f}".rstrip("0").rstrip(".") + " USDT"


def format_change(value: float | None) -> str:
    if value is None:
        return "variation 24 h indisponible"
    return f"{value:+.2f} % sur 24 h"


def format_alert_text(change: float, *, upward: bool, threshold: float) -> str:
    magnitude = min(300.0, max(threshold, abs(float(change))))
    percent = f"{magnitude:.2f}".rstrip("0").rstrip(".")
    if upward:
        return f"{UP_ALERT_PREFIX}{percent}%"
    return f"{BUY_ALERT_PREFIX}{percent}%  GO BUY"


@dataclass(frozen=True)
class PriceQuote:
    coin_id: str
    label: str
    price_usdt: float | None
    change_24h: float | None
    market: str | None
    preferred_market: str
    updated_at: int | None
    error: str | None = None


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

    async def quote(self, coin_id: str, label: str, preferred_market: str) -> PriceQuote:
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
            return PriceQuote(
                coin_id=coin_id,
                label=label,
                price_usdt=ticker["_last_float"] if ticker else None,
                change_24h=coin_data.get("usd_24h_change"),
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
                price_usdt=None,
                change_24h=None,
                market=None,
                preferred_market=preferred_market,
                updated_at=None,
                error=str(exc),
            )


class PriceBot(discord.Client):
    def __init__(self, *, channel_id: int, alert_channel_id: int, coins: list[tuple[str, str, str]], state_file: Path, interval_minutes: int, alert_threshold: float, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.channel_id = channel_id
        self.alert_channel_id = alert_channel_id
        self.coins = coins
        self.state_file = state_file
        self.interval_minutes = interval_minutes
        self.alert_threshold = alert_threshold
        self.alert_task: asyncio.Task[None] | None = None
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
        if self.alert_task and not self.alert_task.done():
            self.alert_task.cancel()
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

    async def upsert_alert(self, text: str, colour: discord.Colour, quotes: list[PriceQuote], *, upward: bool) -> None:
        channel = await self.resolve_channel(self.alert_channel_id)
        alert_embed = discord.Embed(
            title=text,
            description=text,
            colour=colour,
            timestamp=datetime.now(timezone.utc),
        )
        for quote in quotes:
            if quote.price_usdt is None:
                value = f"Indisponible — {quote.error or 'aucun marché USDT'}"
            else:
                display_change = quote.change_24h
                if quote.label == "XTM" and display_change is not None:
                    magnitude = min(300.0, max(self.alert_threshold, abs(display_change)))
                    display_change = -magnitude if display_change < 0 else magnitude
                value = f"**{format_price(quote.price_usdt)}**\n{format_change(display_change)}"
                if quote.market:
                    value += f"\nMarché : {quote.market}"
            alert_embed.add_field(name=quote.label, value=value, inline=True)

        prefix = UP_ALERT_PREFIX if upward else BUY_ALERT_PREFIX
        bot_id = self.user.id if self.user else None
        history = getattr(channel, "history", None)
        existing = None
        if bot_id is not None and history is not None:
            async for message in history(limit=100):
                if message.author.id != bot_id:
                    continue
                if any(embed.title and embed.title.startswith(prefix) for embed in message.embeds):
                    existing = message
                    break

        content = f"@everyone {text}"
        if existing is not None:
            await existing.edit(
                content=content,
                embed=alert_embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            LOGGER.info("Alerte mise à jour dans le même message (%s)", existing.id)
        else:
            await channel.send(  # type: ignore[attr-defined]
                content=content,
                embed=alert_embed,
                allowed_mentions=discord.AllowedMentions(everyone=True),
            )
            LOGGER.info("Nouvelle alerte publiée : %s", text)

    def start_alert_if_needed(self, quotes: list[PriceQuote]) -> None:
        xtm_quote = next((quote for quote in quotes if quote.label == "XTM"), None)
        if not xtm_quote or xtm_quote.change_24h is None:
            return
        if xtm_quote.change_24h >= self.alert_threshold:
            text = format_alert_text(xtm_quote.change_24h, upward=True, threshold=self.alert_threshold)
            colour, upward = discord.Colour.green(), True
        elif xtm_quote.change_24h <= -self.alert_threshold:
            text = format_alert_text(xtm_quote.change_24h, upward=False, threshold=self.alert_threshold)
            colour, upward = discord.Colour.red(), False
        else:
            LOGGER.info("Variation XTM sous le seuil de 10 %; alertes existantes laissées inchangées")
            return
        if self.alert_task is None or self.alert_task.done():
            self.alert_task = asyncio.create_task(self.upsert_alert(text, colour, quotes, upward=upward))
            LOGGER.warning("Alerte %s déclenchée à %.2f %% sur 24 h", text, xtm_quote.change_24h)

    async def publish_prices(self) -> None:
        if self.price_client is None:
            return
        channel = await self.resolve_channel(self.channel_id)
        quotes = await asyncio.gather(*(self.price_client.quote(coin_id, label, preferred_market) for coin_id, label, preferred_market in self.coins))
        if not any(quote.price_usdt is not None for quote in quotes):
            LOGGER.error("Aucun prix USDT disponible; le message Discord n'est pas remplacé")
            return

        embed = discord.Embed(
            title="💱 Prix XTM / wXTM",
            description="Cours en USDT récupérés sur CoinGecko.",
            colour=discord.Colour.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        for quote in quotes:
            if quote.price_usdt is None:
                value = f"Indisponible — {quote.error or 'aucun marché USDT'}"
            else:
                value = f"**{format_price(quote.price_usdt)}**\n{format_change(quote.change_24h)}"
                if quote.market:
                    value += f"\nMarché : {quote.market}"
            embed.add_field(name=quote.label, value=value, inline=True)
        embed.set_footer(text=f"Nouveau message toutes les {self.interval_minutes} minutes")
        await channel.send(embed=embed)  # type: ignore[attr-defined]
        self.start_alert_if_needed(quotes)
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
    coins = parse_coin_config(os.getenv("COINS", "minotari:XTM:MEXC,wrapped-minotari:wXTM:Gate"))
    alert_channel_id = env_int("ALERT_CHANNEL_ID", 1163364187796426776)
    alert_threshold = float(os.getenv("ALERT_THRESHOLD_PERCENT", "10"))
    state_file = Path(os.getenv("STATE_FILE", "/data/state.json"))
    intents = discord.Intents.none()
    bot = PriceBot(
        channel_id=channel_id,
        alert_channel_id=alert_channel_id,
        coins=coins,
        state_file=state_file,
        interval_minutes=interval,
        alert_threshold=alert_threshold,
        intents=intents,
    )
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
