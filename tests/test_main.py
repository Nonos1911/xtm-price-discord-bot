import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from main import format_change, format_price, parse_coin_config, select_usdt_ticker


def test_parse_coin_config():
    assert parse_coin_config("minotari:XTM:MEXC,wrapped-minotari:wXTM:Gate") == [
        ("minotari", "XTM", "MEXC"),
        ("wrapped-minotari", "wXTM", "Gate"),
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
    assert format_change(-2.5) == "-2.50 % sur 24 h"
