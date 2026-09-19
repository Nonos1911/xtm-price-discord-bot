import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from update_once import BUY_ALERT_TEXT, build_alert_embed, build_embed, price_text, select_usdt_ticker, xtm_alert_triggered, xtm_buy_alert_triggered


def test_select_usdt_ticker():
    selected = select_usdt_ticker([
        {"target": "USDT", "last": "0.01", "volume": 10, "market": {"name": "MEXC"}},
        {"target": "USDT", "last": "0.02", "volume": 20, "market": {"name": "Gate"}},
    ], "MEXC")
    assert selected["price"] == 0.01
    assert selected["market"] == "MEXC"


def test_embed_has_both_assets():
    embed = build_embed([
        {"label": "XTM", "price": 0.001, "change": 1.5, "market": "MEXC", "error": None},
        {"label": "wXTM", "price": 0.002, "change": -2.0, "market": "Gate", "error": None},
    ])
    assert [field["name"] for field in embed["fields"]] == ["XTM", "wXTM"]
    assert "USDT" in price_text({"price": 0.001, "change": 1.5, "market": "MEXC", "error": None})


def test_xtm_alert_is_positive_and_inclusive():
    assert not xtm_alert_triggered([{"label": "XTM", "change": 9.99}])
    assert xtm_alert_triggered([{"label": "XTM", "change": 10.0}])
    assert not xtm_alert_triggered([{"label": "XTM", "change": -12.0}])
    assert xtm_buy_alert_triggered([{"label": "XTM", "change": -10.0}])
    assert not xtm_buy_alert_triggered([{"label": "XTM", "change": -9.99}])
    assert BUY_ALERT_TEXT == "Alert XTM-10%  GO BUY"
    embed = build_alert_embed(
        BUY_ALERT_TEXT,
        15158332,
        [
            {"label": "XTM", "price": 0.001, "change": -10.0, "market": "MEXC", "error": None},
            {"label": "wXTM", "price": 0.002, "change": -2.0, "market": "Gate", "error": None},
        ],
    )
    assert embed["color"] == 15158332
    assert [field["name"] for field in embed["fields"]] == ["XTM", "wXTM"]
    assert "0.001 USDT" in embed["fields"][0]["value"]
    assert "0.002 USDT" in embed["fields"][1]["value"]
