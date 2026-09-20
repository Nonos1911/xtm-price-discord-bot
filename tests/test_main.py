import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from main import (
    CoinGeckoClient,
    PriceQuote,
    alert_milestone,
    alert_quotes_for_direction,
    format_change,
    format_group_alert_text,
    format_price,
    is_alert_title,
    message_notified_milestone,
    parse_coin_config,
    select_usdt_ticker,
)
from pool_data import GECKOTERMINAL_BASE, WXTM_NETWORK, WXTM_POOL_ADDRESS


def test_parse_coin_config():
    assert parse_coin_config("minotari:XTM:MEXC,wrapped-minotari:wXTM:Uniswap V4") == [
        ("minotari", "XTM", "MEXC"),
        ("wrapped-minotari", "wXTM", "Uniswap V4"),
    ]


def test_select_usdt_ticker_prefers_volume():
    tickers = [
        {"target": "USDT", "last": "0.001", "volume": 10, "market": {"name": "MEXC"}},
        {"target": "USDT", "last": "0.002", "volume": 100, "market": {"name": "Gate"}},
        {"target": "BTC", "last": "1", "volume": 100000, "market": {"name": "wrong"}},
    ]
    assert select_usdt_ticker(tickers, "MEXC")["_last_float"] == 0.001
    assert select_usdt_ticker(tickers, "MEXC")["market"]["name"] == "MEXC"


def test_formatters():
    assert format_price(0.00123) == "0.00123 USDT"
    assert format_price(0.00123, "USD") == "0.00123 USD"
    assert format_change(-2.5) == "-2.50 % sur 24 h"


def test_worker_alerts_include_only_affected_tokens_and_keep_one_direction_key():
    xtm = PriceQuote("minotari", "XTM", 0.001, 4.0, "MEXC", "MEXC", 1)
    wxtm = PriceQuote("wrapped-minotari", "wXTM", 0.002, 17.19, "Uniswap V4 (Ethereum)", "Uniswap V4", 1, currency="USD")

    upward = alert_quotes_for_direction([xtm, wxtm], upward=True, threshold=10)
    downward = alert_quotes_for_direction([xtm, wxtm], upward=False, threshold=10)

    assert [quote.label for quote in upward] == ["wXTM"]
    assert format_group_alert_text(upward, upward=True, threshold=10) == "Alert wXTM+17.19%"
    assert downward == []
    assert is_alert_title("Alert wXTM+17.19%", upward=True)
    assert is_alert_title("Alert XTM+10% | wXTM+17.19%", upward=True)
    assert is_alert_title("Alert wXTM-10%  GO BUY", upward=False)
    assert not is_alert_title("Alert wXTM-10%  GO BUY", upward=True)


def test_worker_alerts_can_trigger_both_assets_and_both_directions_independently():
    quotes = [
        PriceQuote("minotari", "XTM", 0.001, 10.0, "MEXC", "MEXC", 1),
        PriceQuote("wrapped-minotari", "wXTM", 0.002, -17.19, "Uniswap V4 (Ethereum)", "Uniswap V4", 1, currency="USD"),
    ]

    rising = alert_quotes_for_direction(quotes, upward=True, threshold=10)
    falling = alert_quotes_for_direction(quotes, upward=False, threshold=10)

    assert [quote.label for quote in rising] == ["XTM"]
    assert [quote.label for quote in falling] == ["wXTM"]
    assert format_group_alert_text(falling, upward=False, threshold=10) == "Alert wXTM-17.19%  GO BUY"


def test_worker_everyone_notifications_follow_ten_percent_steps():
    quote = PriceQuote("wrapped-minotari", "wXTM", 0.002, 29.99, "Uniswap V4 (Ethereum)", "Uniswap V4", 1, currency="USD")
    assert alert_milestone([quote]) == 20
    assert alert_milestone([PriceQuote("minotari", "XTM", 0.001, -300.0, "MEXC", "MEXC", 1)]) == 300

    marked = SimpleNamespace(
        mention_everyone=False,
        embeds=[SimpleNamespace(footer=SimpleNamespace(text="Palier @everyone notifié : +20%"), title="Alert wXTM+29.99%")],
    )
    assert message_notified_milestone(marked, upward=True) == 20

    legacy = SimpleNamespace(
        mention_everyone=True,
        embeds=[SimpleNamespace(footer=SimpleNamespace(text=None), title="Alert wXTM+29.99%")],
    )
    assert message_notified_milestone(legacy, upward=True) == 20


def test_wxtm_quote_uses_configured_uniswap_pool():
    pool_response = {
        "data": {
            "attributes": {
                "address": WXTM_POOL_ADDRESS,
                "base_token_price_usd": "0.002428",
                "price_change_percentage": {"h24": "17.33"},
            }
        }
    }
    calls = []

    class FakeResponse:
        status = 200
        headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        async def json(self):
            return pool_response

    class FakeSession:
        def get(self, url, *, headers):
            calls.append((url, headers))
            return FakeResponse()

    client = CoinGeckoClient(session=FakeSession(), base_url="https://example.invalid", api_key=None, plan="demo")
    quote = asyncio.run(client.quote("wrapped-minotari", "wXTM", "Uniswap V4"))

    assert calls == [(
        f"{GECKOTERMINAL_BASE}/networks/{WXTM_NETWORK}/pools/{WXTM_POOL_ADDRESS}",
        {"Accept": "application/json;version=20230302", "User-Agent": "xtm-price-discord-bot/1.0"},
    )]
    assert quote.price == 0.002428
    assert quote.currency == "USD"
    assert quote.change_24h == 17.33
    assert quote.market == "Uniswap V4 (Ethereum)"
