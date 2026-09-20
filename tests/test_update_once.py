import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import update_once
from main import format_alert_delta_lines as format_live_alert_delta_lines
from main import format_milestone_footer as format_live_milestone_footer
from update_once import (
    alert_milestone,
    alert_changes_are_complete,
    alert_quotes_for_direction,
    change_since_previous,
    build_alert_embed,
    build_embed,
    build_price_embed,
    extract_alert_changes,
    extract_previous_price_changes,
    format_alert_delta_lines,
    format_milestone_footer,
    format_alert_text,
    format_group_alert_text,
    price_text,
    select_usdt_ticker,
    variation_trend_sign,
)
from pool_data import (
    MEXC_KLINE_BASE,
    WXTM_NETWORK,
    WXTM_POOL_ADDRESS,
    parse_mexc_1h_change,
    parse_wxtm_pool_response,
)
from settings import parse_alerts_paused, parse_upward_alert_threshold

POOL_RESPONSE = {
    "data": {
        "id": "eth_0x530581e8b4dff575d96af96cbfb74d0cc4ed0ec0cb7c953f491c7a60a787412d",
        "attributes": {
            "address": "0x530581e8b4dff575d96af96cbfb74d0cc4ed0ec0cb7c953f491c7a60a787412d",
            "name": "wXTM / ETH 3%",
            "base_token_price_usd": "0.002428",
            "price_change_percentage": {"h1": "2.50", "h24": "17.33"},
        },
    }
}


def test_select_usdt_ticker():
    selected = select_usdt_ticker([
        {"target": "USDT", "last": "0.01", "volume": 10, "market": {"name": "MEXC"}},
        {"target": "USDT", "last": "0.02", "volume": 20, "market": {"name": "Gate"}},
    ], "MEXC")
    assert selected["price"] == 0.01
    assert selected["market"] == "MEXC"


def test_select_usdt_ticker_keeps_pair_symbols_for_hourly_candles():
    selected = select_usdt_ticker([{
        "base": "XTM",
        "target": "USDT",
        "last": "0.01",
        "volume": 10,
        "market": {"name": "MEXC"},
    }], "MEXC")
    assert selected["base"] == "XTM"
    assert selected["target"] == "USDT"


def test_embed_has_both_assets():
    embed = build_embed([
        {"label": "XTM", "price": 0.001, "change": 1.5, "market": "MEXC", "error": None},
        {"label": "wXTM", "price": 0.002, "currency": "USD", "change": -2.0, "market": "Uniswap V4 (Ethereum)", "error": None},
    ])
    assert [field["name"] for field in embed["fields"]] == ["XTM", "wXTM"]
    assert "USDT" in price_text({"price": 0.001, "change": 1.5, "market": "MEXC", "error": None})
    assert "0.00123 USDT" in price_text({"label": "XTM", "price": 0.00123456, "currency": "USDT", "change": 1.5, "market": "MEXC", "error": None})
    assert "0.00243 USDT" in price_text({"label": "wXTM", "price": 0.002428, "currency": "USD", "change": 1.5, "market": "Uniswap", "error": None})
    assert embed["footer"]["text"] == "Mise à jour toutes les minutes"
    assert build_price_embed([], "Snapshot du prix toutes les 4 heures")["footer"]["text"] == "Snapshot du prix toutes les 4 heures"


def test_price_text_shows_hourly_variation_after_daily_variation():
    text = price_text({
        "label": "XTM", "price": 0.00123, "currency": "USDT",
        "change": 12.34, "change_1h": -0.56, "market": "MEXC", "error": None,
    })
    assert text.splitlines()[1:3] == ["+12.34 % sur 24 h", "-0.56 % sur 1 h"]


def test_previous_price_changes_are_parsed_from_the_live_price_embed():
    message = {"embeds": [{
        "title": "💱 Prix XTM / wXTM",
        "fields": [
            {"name": "XTM", "value": "**0.001 USDT**\n+3.25 % sur 24 h\nMarché : MEXC"},
            {"name": "wXTM", "value": "**0.002 USD**\n-12.50 % sur 24 h\nMarché : Uniswap"},
        ],
    }]}
    assert extract_previous_price_changes(message) == {"XTM": 3.25, "wXTM": -12.5}
    assert variation_trend_sign(3.5, 3.25) == "+"
    assert variation_trend_sign(-11.5, -12.5) == "+"
    assert variation_trend_sign(-13, -12.5) == "-"
    assert variation_trend_sign(3.25, 3.25) == "~"
    assert variation_trend_sign(3.25, None) == "~"


def test_alert_change_parser_and_delta_apply_independently_to_xtm_and_wxtm():
    message = {"embeds": [{"fields": [
        {"name": "XTM", "value": "**0.001 USDT**\n+12.75 % sur 24 h"},
        {"name": "wXTM", "value": "**0.00243 USDT**\n-17.75 % sur 24 h"},
    ]}]}
    previous = extract_alert_changes(message)

    assert previous == {"XTM": 12.75, "wXTM": -17.75}
    assert change_since_previous(12.9, previous["XTM"]) == pytest.approx(0.15)
    assert change_since_previous(-18.0, previous["wXTM"]) == pytest.approx(-0.25)


def test_alert_thresholds_are_inclusive_for_both_assets():
    quotes = [
        {"label": "XTM", "change": 9.99},
        {"label": "wXTM", "change": 17.19},
    ]
    assert [quote["label"] for quote in alert_quotes_for_direction(quotes, upward=True)] == ["wXTM"]
    assert alert_quotes_for_direction(quotes, upward=False) == []
    quotes = [
        {"label": "XTM", "change": -10.0},
        {"label": "wXTM", "change": -9.99},
    ]
    assert [quote["label"] for quote in alert_quotes_for_direction(quotes, upward=False)] == ["XTM"]
    assert alert_quotes_for_direction([{"label": "XTM", "change": 10.0}], upward=True)
    assert alert_quotes_for_direction([{"label": "wXTM", "change": -10.0}], upward=False)
    assert alert_quotes_for_direction([{"label": "unknown", "change": 500}], upward=True) == []


def test_manual_threshold_changes_upside_only(monkeypatch):
    monkeypatch.setattr(update_once, "ALERT_THRESHOLD_PERCENT", 20.0)
    quotes = [
        {"label": "XTM", "change": 15.0},
        {"label": "wXTM", "change": 20.0},
    ]
    assert [q["label"] for q in alert_quotes_for_direction(quotes, upward=True)] == ["wXTM"]
    assert alert_quotes_for_direction([{"label": "XTM", "change": -10.0}], upward=False)
    assert format_group_alert_text([quotes[1]], upward=True) == "Alerte wXTM +20%"


def test_pause_keeps_price_updates_but_skips_alert_creation_and_cleanup(monkeypatch):
    monkeypatch.setenv("ALERTS_PAUSED", "true")
    monkeypatch.setenv("TEST_ALERTS", "none")
    monkeypatch.setenv("SNAPSHOT_ONLY", "false")
    quotes = [
        {"label": "XTM", "price": 0.001, "change": 25.0, "change_1h": 1.0,
         "currency": "USDT", "market": "MEXC", "error": None},
        {"label": "wXTM", "price": 0.002, "change": 5.0, "change_1h": -1.0,
         "currency": "USD", "market": "Uniswap V4", "error": None},
    ]
    monkeypatch.setattr(update_once, "get_quote", lambda *_args: quotes.pop(0))
    updated = []
    monkeypatch.setattr(update_once, "update_discord", lambda embed, **_kwargs: updated.append(embed) or "updated")
    monkeypatch.setattr(update_once, "upsert_alert", lambda *_args, **_kwargs: pytest.fail("pause must block alert posts"))
    monkeypatch.setattr(update_once, "clear_alert_if_below_threshold", lambda *_args, **_kwargs: pytest.fail("pause must preserve existing alerts"))

    assert update_once.main() == 0
    assert len(updated) == 1


def test_pause_blocks_simulated_alert_sends_too(monkeypatch):
    monkeypatch.setenv("ALERTS_PAUSED", "true")
    monkeypatch.setenv("TEST_ALERTS", "both_once")
    monkeypatch.setattr(update_once, "run_both_alert_tests", lambda *_args: pytest.fail("paused tests must not post"))

    assert update_once.main() == 0


def test_alert_data_must_be_complete_before_existing_alerts_can_be_cleared():
    assert alert_changes_are_complete([
        {"label": "XTM", "change": 4.9},
        {"label": "wXTM", "change": -3.2},
    ])
    assert not alert_changes_are_complete([
        {"label": "XTM", "change": 4.9},
        {"label": "wXTM", "change": None},
    ])
    assert not alert_changes_are_complete([{"label": "XTM", "change": 4.9}])


def test_group_alert_names_only_affected_assets_and_can_handle_both():
    one = [{"label": "wXTM", "change": 17.19}]
    both = [
        {"label": "XTM", "change": 10.0},
        {"label": "wXTM", "change": 17.19},
    ]
    assert format_group_alert_text(one, upward=True) == "Alerte wXTM +17.19%"
    assert format_group_alert_text(both, upward=True) == "Alerte XTM +10% | wXTM +17.19%"
    assert format_group_alert_text(
        [{"label": "wXTM", "change": -17.19}], upward=False
    ) == "Alerte wXTM -17.19%  GO BUY"
    embed = build_alert_embed(
        format_group_alert_text(both, upward=True),
        5763719,
        [
            {"label": "XTM", "price": 0.001, "change": 10.0, "market": "MEXC", "error": None},
            {"label": "wXTM", "price": 0.002, "currency": "USD", "change": 17.19, "market": "Uniswap V4 (Ethereum)", "error": None},
        ],
    )
    assert [field["name"] for field in embed["fields"]] == ["XTM", "wXTM"]
    assert embed["title"] == "Alerte XTM +10% | wXTM +17.19%"
    assert embed["description"] == "🟡 ~ 0.00% — **XTM**\n🟡 ~ 0.00% — **wXTM**"
    assert all(field["inline"] is False for field in embed["fields"])


def test_alert_embed_shows_change_since_previous_for_each_asset():
    quotes = [
        {"label": "XTM", "price": 0.001, "change": 12.75, "market": "MEXC", "error": None},
        {"label": "wXTM", "price": 0.00243, "currency": "USD", "change": 17.75, "market": "Uniswap V4", "error": None},
    ]
    embed = build_alert_embed(
        "Alerte XTM +12.75% | wXTM +17.75%",
        15158332,
        quotes,
        previous_alert_changes={"XTM": 12.5, "wXTM": 18.0},
    )

    assert embed["title"] == "Alerte XTM +12.75% | wXTM +17.75%"
    assert embed["description"] == "🟢 + 0.25% — **XTM**\n🔴 - 0.25% — **wXTM**"
    assert all("Entre alertes" not in field["value"] for field in embed["fields"])


def test_live_bot_and_scheduler_format_xtm_and_wxtm_alerts_identically():
    changes = {"XTM": 22.0, "wXTM": 12.0}
    deltas = {"XTM": 10.0, "wXTM": -2.0}
    assert format_alert_delta_lines(changes, deltas) == format_live_alert_delta_lines(changes, deltas)
    assert format_alert_delta_lines(changes, deltas) == (
        "🟢 + 10.00% — **XTM**\n🔴 - 2.00% — **wXTM**"
    )
    assert format_milestone_footer(20, upward=True) == format_live_milestone_footer(20, upward=True)
    assert format_milestone_footer(20, upward=True).startswith(
        "Palier @everyone notifié : +20%\nProchains paliers @everyone : +30% / +40% / +50%"
    )

    assert update_once._is_alert_title(
        "🟢 + 0.25% — Alert XTM+10%", upward=True, test_label=None
    )
    assert update_once._is_alert_title(
        "Alerte wXTM +13.84%", upward=True, test_label=None
    )


def test_wxtm_only_alert_embed_excludes_unaffected_xtm():
    quotes = [
        {"label": "XTM", "price": 0.0022, "change": 7.54, "market": "MEXC", "error": None},
        {"label": "wXTM", "price": 0.002428, "currency": "USD", "change": 18.70, "market": "Uniswap V4 (Ethereum)", "error": None},
    ]
    affected = alert_quotes_for_direction(quotes, upward=True)
    embed = build_alert_embed(format_group_alert_text(affected, upward=True), 5763719, affected)

    assert embed["title"] == "Alerte wXTM +18.7%"
    assert [field["name"] for field in embed["fields"]] == ["wXTM"]
    assert "0.00243 USDT" in embed["fields"][0]["value"]
    trending_embed = build_alert_embed(
        "Alerte wXTM +18.7%",
        5763719,
        affected,
        previous_changes={"wXTM": 18.8},
    )
    assert trending_embed["title"] == "Alerte wXTM +18.7%"
    assert trending_embed["description"] == "🔴 - 0.10% — **wXTM**"
    assert "```" not in trending_embed["fields"][0]["value"]
    positive_trend_embed = build_alert_embed(
        "Alerte wXTM +18.7%",
        5763719,
        affected,
        previous_changes={"wXTM": 18.6},
    )
    assert positive_trend_embed["description"] == "🟢 + 0.10% — **wXTM**"
    neutral_trend_embed = build_alert_embed(
        "Alerte wXTM +18.7%",
        5763719,
        affected,
        previous_changes={"wXTM": 18.7},
    )
    assert neutral_trend_embed["description"] == "🟡 ~ 0.00% — **wXTM**"
    assert format_alert_text(-10, upward=False) == "Alerte XTM -10%  GO BUY"
    assert format_alert_text(10, upward=True) == "Alerte XTM +10%"
    assert format_alert_text(15.678, upward=True) == "Alerte XTM +15.68%"
    assert format_alert_text(-15.678, upward=False) == "Alerte XTM -15.68%  GO BUY"
    assert format_alert_text(450, upward=True) == "Alerte XTM +300%"
    assert format_alert_text(-450, upward=False) == "Alerte XTM -300%  GO BUY"
    embed = build_alert_embed(
        format_alert_text(-10, upward=False),
        15158332,
        [
            {"label": "XTM", "price": 0.001, "change": -10.0, "market": "MEXC", "error": None},
            {"label": "wXTM", "price": 0.002, "currency": "USD", "change": -2.0, "market": "Uniswap V4 (Ethereum)", "error": None},
        ],
    )
    assert embed["color"] == 2829617
    assert [field["name"] for field in embed["fields"]] == ["XTM", "wXTM"]
    assert "0.00100 USDT" in embed["fields"][0]["value"]
    assert "0.00200 USDT" in embed["fields"][1]["value"]
    capped_embed = build_alert_embed(
        "Alerte XTM -300%  GO BUY",
        15158332,
        [{"label": "XTM", "price": 0.001, "change": -450.0, "market": "MEXC", "error": None}],
    )
    assert "-300.00 % sur 24 h" in capped_embed["fields"][0]["value"]
    wxtm_capped = build_alert_embed(
        "Alerte wXTM -300%  GO BUY",
        15158332,
        [{"label": "wXTM", "price": 0.002, "currency": "USD", "change": -450.0, "market": "Uniswap V4 (Ethereum)", "error": None}],
    )
    assert "-300.00 % sur 24 h" in wxtm_capped["fields"][0]["value"]


def test_parse_geckoterminal_wxtm_pool_response_uses_usd_and_pool_variation():
    quote = parse_wxtm_pool_response(POOL_RESPONSE)
    assert quote == {
        "price": 0.002428,
        "currency": "USD",
        "change": 17.33,
        "change_1h": 2.5,
        "market": "Uniswap V4 (Ethereum)",
        "updated": None,
        "error": None,
    }


def test_mexc_one_hour_change_uses_recent_one_minute_candle_close():
    now_ms = 10_000_000
    target_ms = now_ms - 60 * 60 * 1000
    candles = [
        [target_ms - 60_000, "5", "5", "5", "5.0"],
        [now_ms - 60_000, "5.4", "5.5", "5.3", "5.4"],
    ]
    assert parse_mexc_1h_change(candles, 5.5, now_ms=now_ms) == pytest.approx(10.0)
    assert parse_mexc_1h_change(candles[:1], 5.5, now_ms=now_ms) is None


def test_get_quote_xtm_requests_mexc_hourly_candles(monkeypatch):
    calls = []

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append(url)
        if "/simple/price?" in url:
            return {"minotari": {"usd": 0.001, "usd_24h_change": 5.0, "last_updated_at": 123}}
        if "/tickers?" in url:
            return {"tickers": [{
                "base": "XTM", "target": "USDT", "last": "0.001", "volume": 100,
                "market": {"name": "MEXC"},
            }]}
        return [[0, "0", "0", "0", "0.001"]]

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    monkeypatch.setattr(update_once, "parse_mexc_1h_change", lambda candles, price: 1.25)
    quote = update_once.get_quote("minotari", "XTM", "MEXC")

    assert quote["change_1h"] == 1.25
    assert f"{MEXC_KLINE_BASE}/api/v3/klines?" in calls[2]
    assert "symbol=XTMUSDT" in calls[2]
    assert "interval=1m" in calls[2]


def test_parse_geckoterminal_rejects_a_different_pool():
    wrong_pool = {
        "data": {
            "attributes": {
                **POOL_RESPONSE["data"]["attributes"],
                "address": "0x0000000000000000000000000000000000000000000000000000000000000000",
            }
        }
    }
    try:
        parse_wxtm_pool_response(wrong_pool)
    except ValueError as exc:
        assert "autre pool" in str(exc)
    else:
        raise AssertionError("un pool différent doit être refusé")


def test_get_quote_for_wxtm_calls_only_the_configured_uniswap_pool(monkeypatch):
    calls = []

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, headers))
        return POOL_RESPONSE

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    quote = update_once.get_quote("wrapped-minotari", "wXTM", "Uniswap V4")

    assert calls == [(
        f"https://api.geckoterminal.com/api/v2/networks/{WXTM_NETWORK}/pools/{WXTM_POOL_ADDRESS}",
        {"Accept": "application/json;version=20230302"},
    )]
    assert quote["price"] == 0.002428
    assert quote["currency"] == "USD"
    assert quote["change"] == 17.33
    assert quote["market"] == "Uniswap V4 (Ethereum)"


def test_update_discord_republishes_latest_price_box_and_removes_old_boxes(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    responses = iter(
        [
            {"id": "bot-id"},
            [
                {"id": "new-price", "author": {"id": "bot-id"}, "embeds": [{
                    "title": "💱 Prix XTM / wXTM",
                    "fields": [
                        {"name": "XTM", "value": "**0.001 USDT**\n+3.25 % sur 24 h\nMarché : MEXC"},
                        {"name": "wXTM", "value": "**0.00200 USDT**\n-12.50 % sur 24 h\nMarché : Uniswap"},
                    ],
                }]},
                {"id": "old-price", "author": {"id": "bot-id"}, "embeds": [{"title": "💱 Prix XTM / wXTM"}]},
                {"id": "alert", "author": {"id": "bot-id"}, "embeds": [{"title": "Alerte XTM+ 10%"}]},
            ],
            {"id": "fresh-price"},
            None,
            None,
        ]
    )

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    previous_changes = {}
    result = update_once.update_discord({"title": "💱 Prix XTM / wXTM"}, previous_changes=previous_changes)

    assert result == "nouvelle box de prix fresh-price publiée; 2 ancienne(s) box supprimée(s)"
    assert previous_changes == {"XTM": 3.25, "wXTM": -12.5}
    assert calls[2][1] == "POST"
    assert calls[2][2]["embeds"][0]["title"] == "💱 Prix XTM / wXTM"
    assert calls[3][1] == "DELETE"
    assert calls[3][0].endswith("/new-price")
    assert calls[4][1] == "DELETE"
    assert calls[4][0].endswith("/old-price")


def test_upsert_alert_replaces_previous_message_without_repinging_same_ten_percent_step(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    responses = iter([
        {"id": "bot-id"},
        [{"id": "alert-message", "author": {"id": "bot-id"}, "content": "@everyone Alerte XTM+ 10%", "mention_everyone": True, "embeds": [{"title": "Alerte XTM+ 10%"}]}],
        {"id": "fresh-alert"},
        None,
    ])

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    result = update_once.upsert_alert(
        "Alerte wXTM +12.5%", 5763719,
        [{"label": "wXTM", "price": 0.002, "currency": "USD", "change": 12.5, "market": "Uniswap V4 (Ethereum)", "error": None}],
        upward=True,
    )

    assert result == "Alertes séparées publiées : wXTM (fresh-alert); 1 ancienne alerte supprimée"
    assert calls[2][1] == "POST"
    assert "content" not in calls[2][2]
    assert calls[2][2]["allowed_mentions"] == {"parse": []}
    assert calls[2][2]["embeds"][0]["title"] == "Alerte wXTM +12.5%"
    assert calls[2][2]["embeds"][0]["description"] == "🟡 ~ 0.00% — **wXTM**"
    assert calls[2][2]["embeds"][0]["footer"]["text"] == (
        "Palier @everyone notifié : +10%\n"
        "Prochains paliers @everyone : +20% / +30% / +40% …"
    )
    assert calls[3][1] == "DELETE"
    assert calls[3][0].endswith("/alert-message")


def test_upsert_alert_reports_per_asset_change_from_previous_alert(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    responses = iter([
        {"id": "bot-id"},
        [{
            "id": "previous-alert",
            "author": {"id": "bot-id"},
            "embeds": [{
                "title": "Alerte XTM+ 12.50% | wXTM+ 14%",
                "fields": [
                    {"name": "XTM", "value": "**0.001 USDT**\n+12.50 % sur 24 h"},
                    {"name": "wXTM", "value": "**0.00243 USDT**\n+14.00 % sur 24 h"},
                ],
            }],
        }],
        {"id": "fresh-xtm-alert"},
        {"id": "fresh-wxtm-alert"},
        None,
    ])

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    update_once.upsert_alert(
        "Alerte XTM +12.75% | wXTM +13.75%",
        5763719,
        [
            {"label": "XTM", "price": 0.001, "change": 12.75, "market": "MEXC", "error": None},
            {"label": "wXTM", "price": 0.00243, "currency": "USD", "change": 13.75, "market": "Uniswap V4", "error": None},
        ],
        upward=True,
    )

    xtm_embed = calls[2][2]["embeds"][0]
    wxtm_embed = calls[3][2]["embeds"][0]
    assert xtm_embed["title"] == "Alerte XTM +12.75%"
    assert xtm_embed["description"] == "🟢 + 0.25% — **XTM**"
    assert wxtm_embed["title"] == "Alerte wXTM +13.75%"
    assert wxtm_embed["description"] == "🔴 - 0.25% — **wXTM**"
    assert "content" not in calls[2][2]
    assert calls[3][2]["content"] == "@everyone"
    assert update_once._is_alert_title(xtm_embed["title"], upward=True, test_label=None)
    assert update_once._is_alert_title(wxtm_embed["title"], upward=True, test_label=None)


def test_upsert_alert_creates_one_message_with_everyone_ping(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    responses = iter([{"id": "bot-id"}, [], {"id": "new-alert"}])

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    update_once.upsert_alert(
        "Alerte XTM -10%  GO BUY", 15158332,
        [{"label": "XTM", "price": 0.001, "change": -10, "market": "MEXC", "error": None}],
        upward=False,
    )

    assert calls[2][1] == "POST"
    assert calls[2][2]["content"] == "@everyone"
    assert calls[2][2]["allowed_mentions"] == {"parse": ["everyone"]}
    assert calls[2][2]["embeds"][0]["footer"]["text"] == (
        "Palier @everyone notifié : -10%\n"
        "Prochains paliers @everyone : -20% / -30% / -40% …"
    )


def test_upsert_alert_pings_again_only_when_next_ten_percent_milestone_is_reached(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    responses = iter([
        {"id": "bot-id"},
        [{
            "id": "previous-alert",
            "author": {"id": "bot-id"},
            "mention_everyone": False,
            "embeds": [{"title": "Alerte wXTM+ 19.7%", "footer": {"text": "Palier @everyone notifié : +10%\nProchains paliers @everyone : +20% / +30% / +40% …"}}],
        }],
        {"id": "milestone-20-alert"},
        None,
    ])

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    update_once.upsert_alert(
        "Alerte wXTM +22%", 5763719,
        [{"label": "wXTM", "price": 0.002, "currency": "USD", "change": 22.0, "market": "Uniswap V4 (Ethereum)", "error": None}],
        upward=True,
    )

    assert calls[2][1] == "POST"
    assert calls[2][2]["content"] == "@everyone"
    assert calls[2][2]["allowed_mentions"] == {"parse": ["everyone"]}
    assert calls[2][2]["embeds"][0]["footer"]["text"] == (
        "Palier @everyone notifié : +20%\n"
        "Prochains paliers @everyone : +30% / +40% / +50% …"
    )
    assert calls[3][1] == "DELETE"


def test_separate_asset_boxes_keep_independent_deltas_and_ping_once_at_next_milestone(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    previous_footer = {
        "text": "Palier @everyone notifié : +10%\n"
        "Prochains paliers @everyone : +20% / +30% / +40% …"
    }
    previous_messages = [
        {
            "id": "old-xtm",
            "author": {"id": "bot-id"},
            "mention_everyone": False,
            "embeds": [{
                "title": "Alerte XTM +12%",
                "fields": [{"name": "XTM", "value": "**0.00100 USDT**\n+12.00 % sur 24 h"}],
                "footer": previous_footer,
            }],
        },
        {
            "id": "old-wxtm",
            "author": {"id": "bot-id"},
            "mention_everyone": True,
            "embeds": [{
                "title": "Alerte wXTM +14%",
                "fields": [{"name": "wXTM", "value": "**0.00200 USDT**\n+14.00 % sur 24 h"}],
                "footer": previous_footer,
            }],
        },
    ]
    responses = iter([
        {"id": "bot-id"},
        previous_messages,
        {"id": "new-xtm"},
        {"id": "new-wxtm"},
        None,
        None,
    ])

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    result = update_once.upsert_alert(
        "Alerte XTM +22% | wXTM +12%",
        5763719,
        [
            {"label": "XTM", "price": 0.0011, "change": 22.0, "market": "MEXC", "error": None},
            {"label": "wXTM", "price": 0.002, "currency": "USD", "change": 12.0, "market": "Uniswap V4", "error": None},
        ],
        upward=True,
    )

    posts = [body for _, method, body in calls if method == "POST"]
    assert len(posts) == 2
    assert posts[0]["embeds"][0]["title"] == "Alerte XTM +22%"
    assert posts[0]["embeds"][0]["description"] == "🟢 + 10.00% — **XTM**"
    assert posts[1]["embeds"][0]["title"] == "Alerte wXTM +12%"
    assert posts[1]["embeds"][0]["description"] == "🔴 - 2.00% — **wXTM**"
    assert posts[0]["content"] == "@everyone"
    assert posts[0]["allowed_mentions"] == {"parse": ["everyone"]}
    assert "content" not in posts[1]
    assert posts[1]["allowed_mentions"] == {"parse": []}
    for post in posts:
        assert post["embeds"][0]["footer"]["text"] == (
            "Palier @everyone notifié : +20%\n"
            "Prochains paliers @everyone : +30% / +40% / +50% …"
        )
    assert [method for _, method, _ in calls if method == "DELETE"] == ["DELETE", "DELETE"]
    assert "@everyone palier +20%" in result


def test_upsert_alert_adds_everyone_once_to_legacy_message(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    responses = iter([
        {"id": "bot-id"},
        [{"id": "legacy-alert", "author": {"id": "bot-id"}, "content": "Alerte XTM+ 10%", "mention_everyone": False, "embeds": [{"title": "Alerte XTM+ 10%"}]}],
        {"id": "fresh-alert"},
        None,
    ])

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    update_once.upsert_alert(
        "Alerte XTM +20%", 5763719,
        [{"label": "XTM", "price": 0.001, "change": 20, "market": "MEXC", "error": None}],
        upward=True,
    )

    assert calls[2][1] == "POST"
    assert calls[2][2]["content"] == "@everyone"
    assert calls[2][2]["allowed_mentions"] == {"parse": ["everyone"]}
    assert calls[3][1] == "DELETE"


def test_clear_alert_removes_only_the_matching_production_direction(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    responses = iter([
        {"id": "bot-id"},
        [
            {"id": "green", "author": {"id": "bot-id"}, "embeds": [{"title": "🔴- Alerte wXTM+ 16.67%"}]},
            {"id": "red", "author": {"id": "bot-id"}, "embeds": [{"title": "🟢+ Alerte XTM- 12%  GO BUY"}]},
            {"id": "test", "author": {"id": "bot-id"}, "embeds": [{"title": "[TEST] Alerte XTM+ 20%"}]},
            {"id": "other-bot", "author": {"id": "other"}, "embeds": [{"title": "Alerte XTM+ 20%"}]},
        ],
        None,
    ])

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    result = update_once.clear_alert_if_below_threshold([
        {"label": "XTM", "change": 4.0},
        {"label": "wXTM", "change": -2.0},
    ], upward=True)

    assert result.startswith("alerte verte supprimée; 1 message(s) retiré(s)")
    assert len(calls) == 3
    assert calls[2][1] == "DELETE"
    assert calls[2][0].endswith("/green")


def test_clear_alert_keeps_message_when_market_data_is_incomplete(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    result = update_once.clear_alert_if_below_threshold([
        {"label": "XTM", "change": 4.0},
        {"label": "wXTM", "change": None},
    ], upward=True)
    assert result == "données 24 h incomplètes; alerte conservée"


def test_clear_alert_does_not_delete_while_that_direction_is_still_active(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    result = update_once.clear_alert_if_below_threshold([
        {"label": "XTM", "change": 12.0},
        {"label": "wXTM", "change": -2.0},
    ], upward=True)
    assert result == "seuil toujours atteint; alerte conservée"


def test_test_alert_label_is_separate_from_live_alert(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    responses = iter([{"id": "bot-id"}, [], {"id": "test-alert"}])

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    update_once.upsert_alert(
        "Alerte XTM +10%", 5763719,
        [{"label": "XTM", "price": 0.0011, "change": 10, "market": "MEXC", "error": None}],
        upward=True, test_label="[TEST 4 MIN]", notify_everyone=False
    )

    assert "content" not in calls[2][2]
    assert calls[2][2]["embeds"][0]["title"] == "[TEST 4 MIN] Alerte XTM +10%"
    assert calls[2][2]["allowed_mentions"] == {"parse": []}


def test_test_alert_can_show_color_and_delta_after_short_label():
    positive = update_once.build_alert_embed(
        "Alerte XTM +12% | wXTM +14%",
        5763719,
        [
            {"label": "XTM", "price": 0.00112, "change": 12.0, "market": "MEXC", "error": None},
            {"label": "wXTM", "price": 0.00228, "change": 14.0, "market": "Uniswap", "error": None},
        ],
        previous_changes={"XTM": 0.0, "wXTM": 0.0},
        test_label="[test]",
        show_trend_for_test=True,
    )
    negative = update_once.build_alert_embed(
        "Alerte XTM -12% | wXTM -14%  GO BUY",
        15158332,
        [
            {"label": "XTM", "price": 0.00088, "change": -12.0, "market": "MEXC", "error": None},
            {"label": "wXTM", "price": 0.00172, "change": -14.0, "market": "Uniswap", "error": None},
        ],
        previous_changes={"XTM": 0.0, "wXTM": 0.0},
        upward=False,
        test_label="[test]",
        show_trend_for_test=True,
    )

    assert positive["title"] == "[test] Alerte XTM +12% | wXTM +14%"
    assert positive["description"] == "🟢 + 12.00% — **XTM**\n🟢 + 14.00% — **wXTM**"
    assert negative["title"] == "[test] Alerte XTM -12% | wXTM -14%  GO BUY"
    assert negative["description"] == "🔴 - 12.00% — **XTM**\n🔴 - 14.00% — **wXTM**"


def test_alert_embed_keeps_xtm_and_wxtm_trend_colors_independent_above_threshold():
    quotes = [
        {"label": "XTM", "price": 0.00122, "change": 22.0, "market": "MEXC", "error": None},
        {"label": "wXTM", "price": 0.00224, "currency": "USD", "change": 12.0, "market": "Uniswap V4", "error": None},
    ]
    embed = update_once.build_alert_embed(
        "Alerte XTM +22% | wXTM +12%",
        5763719,
        quotes,
        previous_alert_changes={"XTM": 12.0, "wXTM": 14.0},
    )

    assert embed["description"] == "🟢 + 10.00% — **XTM**\n🔴 - 2.00% — **wXTM**"
    assert [field["name"] for field in embed["fields"]] == ["XTM", "wXTM"]
    assert all(field["inline"] is False for field in embed["fields"])
    assert "+22.00 % sur 24 h" in embed["fields"][0]["value"]
    assert "+12.00 % sur 24 h" in embed["fields"][1]["value"]


def test_three_minute_color_progression_keeps_both_assets_beyond_threshold(monkeypatch):
    alerts = []
    pauses = []
    removed = []

    def fake_upsert(text, color, quotes, **kwargs):
        alerts.append((text, color, [quote["change"] for quote in quotes], kwargs))
        return "test-message"

    monkeypatch.setattr(update_once, "upsert_alert", fake_upsert)
    monkeypatch.setattr(update_once, "remove_obsolete_test_alert", lambda: removed.append(True) or 1)
    monkeypatch.setattr(update_once.time, "sleep", pauses.append)
    update_once.run_color_badge_progression_3min()

    assert removed == [True]
    assert len(alerts) == 8
    assert [alert[1] for alert in alerts] == [5763719, 15158332] * 4
    assert all(alert[3]["test_label"] == "[test]" for alert in alerts)
    assert all(alert[3]["notify_everyone"] is False for alert in alerts)
    assert all(alert[3]["show_trend_for_test"] is True for alert in alerts)
    assert all(len(changes) == 2 for _, _, changes, _ in alerts)
    assert all(changes[0] >= 12 and changes[1] >= 14 for _, color, changes, _ in alerts if color == 5763719)
    assert all(changes[0] <= -12 and changes[1] <= -14 for _, color, changes, _ in alerts if color == 15158332)
    assert pauses == [60, 60, 60]


def test_milestone_22_test_simulates_second_everyone_and_opposite_asset_trends(monkeypatch):
    alerts = []
    pauses = []

    def fake_upsert(text, color, quotes, **kwargs):
        alerts.append((text, color, [quote["change"] for quote in quotes], kwargs))
        return "test-message"

    monkeypatch.setattr(update_once, "upsert_alert", fake_upsert)
    monkeypatch.setattr(update_once, "remove_test_alerts", lambda label: 1)
    monkeypatch.setattr(update_once.time, "sleep", pauses.append)

    update_once.run_milestone_22_test()

    assert len(alerts) == 2
    assert alerts[0][2] == [12.0, 14.0]
    assert alerts[0][3]["notify_everyone"] is True
    assert alerts[0][3]["previous_changes"] == {"XTM": 0.0, "wXTM": 0.0}
    assert alerts[1][2] == [22.0, 12.0]
    assert alerts[1][3]["notify_everyone"] is True
    assert alerts[1][3]["show_trend_for_test"] is True
    assert all(change >= 10 for change in alerts[1][2])
    assert pauses == [20]


def test_obsolete_timestamped_test_alert_cleanup_only_removes_that_test(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    messages = [
        {
            "id": "old-test",
            "author": {"id": "bot-id"},
            "embeds": [{"title": f"{update_once.OBSOLETE_TEST_LABEL} Alerte XTM +10.8%"}],
        },
        {
            "id": "production",
            "author": {"id": "bot-id"},
            "embeds": [{"title": "🟢 + 0.20% — Alerte wXTM +17%"}],
        },
        {
            "id": "other-author",
            "author": {"id": "someone-else"},
            "embeds": [{"title": f"{update_once.OBSOLETE_TEST_LABEL} Alerte XTM +10.8%"}],
        },
    ]

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method))
        if url.endswith("/users/@me"):
            return {"id": "bot-id"}
        if method == "GET":
            return messages
        if method == "DELETE":
            return None
        raise AssertionError(f"Unexpected API call: {method} {url}")

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    assert update_once.remove_obsolete_test_alert() == 1
    deletes = [url for url, method in calls if method == "DELETE"]
    assert deletes == [f"{update_once.DISCORD_BASE}/channels/{update_once.ALERT_CHANNEL_ID}/messages/old-test"]


def test_short_test_label_cleanup_does_not_touch_production_or_other_authors(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    messages = [
        {"id": "test-alert", "author": {"id": "bot-id"}, "embeds": [{"title": "[test] Alerte XTM +22%"}]},
        {"id": "production", "author": {"id": "bot-id"}, "embeds": [{"title": "Alerte XTM +22%"}]},
        {"id": "other-author", "author": {"id": "other"}, "embeds": [{"title": "[test] Alerte XTM +22%"}]},
    ]

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method))
        if url.endswith("/users/@me"):
            return {"id": "bot-id"}
        if method == "GET":
            return messages
        if method == "DELETE":
            return None
        raise AssertionError(f"Unexpected API call: {method} {url}")

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    assert update_once.remove_test_alerts("[test]") == 1
    deletes = [url for url, method in calls if method == "DELETE"]
    assert deletes == [f"{update_once.DISCORD_BASE}/channels/{update_once.ALERT_CHANNEL_ID}/messages/test-alert"]


def test_fake_four_minute_progression_uses_one_green_and_red_box(monkeypatch):
    alerts = []
    pauses = []

    def fake_upsert(text, color, quotes, **kwargs):
        alerts.append((text, color, quotes[0]["change"], quotes[0]["price"], kwargs))
        return "test-message"

    monkeypatch.setattr(update_once, "upsert_alert", fake_upsert)
    monkeypatch.setattr(update_once.time, "sleep", pauses.append)

    update_once.run_fake_alert_progression()

    assert [alert[0] for alert in alerts] == [
        "Alerte XTM +10%", "Alerte XTM -10%  GO BUY",
        "Alerte XTM +25%", "Alerte XTM -25%  GO BUY",
        "Alerte XTM +100%", "Alerte XTM -100%  GO BUY",
        "Alerte XTM +200%", "Alerte XTM -200%  GO BUY",
        "Alerte XTM +300%", "Alerte XTM -300%  GO BUY",
    ]
    assert [alert[1] for alert in alerts] == [5763719, 15158332] * 5
    assert all(alert[4]["test_label"] == "[TEST FICTIF 4 MIN]" for alert in alerts)
    assert all(alert[4]["notify_everyone"] is True for alert in alerts)
    assert [alert[0] for alert in alerts[::2]] == [
        "Alerte XTM +10%", "Alerte XTM +25%", "Alerte XTM +100%", "Alerte XTM +200%", "Alerte XTM +300%"
    ]
    assert [alert[2] for alert in alerts[::2]] == [10, 25, 100, 200, 300]
    assert [alert[2] for alert in alerts[1::2]] == [-10, -25, -100, -200, -300]
    assert [alert[2] for alert in alerts] == [10, -10, 25, -25, 100, -100, 200, -200, 300, -300]
    assert [round(alert[3], 8) for alert in alerts[::2]] == [0.0011, 0.00125, 0.002, 0.003, 0.004]
    assert [round(alert[3], 8) for alert in alerts[1::2]] == [0.0009, 0.00075, 0.0, 0.0, 0.0]
    assert pauses == [60, 60, 60, 60]


def test_positive_pair_test_posts_two_separate_alert_boxes_and_one_everyone_ping(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    responses = iter([
        {"id": "bot-id"},
        [{"id": "production", "author": {"id": "bot-id"}, "embeds": [{"title": "Alerte wXTM +17%"}]}],
        {"id": "simulated-xtm-alert"},
        {"id": "simulated-wxtm-alert"},
    ])

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    result = update_once.run_positive_pair_test()

    assert "simulated-xtm-alert" in result and "simulated-wxtm-alert" in result
    assert "@everyone palier +10%" in result
    posts = [call[2] for call in calls if call[1] == "POST"]
    assert len(posts) == 2
    assert posts[0]["embeds"][0]["title"].startswith("[TEST SIMULATION ")
    assert posts[0]["embeds"][0]["title"].endswith("Alerte XTM +10.8%")
    assert posts[1]["embeds"][0]["title"].endswith("Alerte wXTM +13.84%")
    assert "content" not in posts[0]
    assert posts[1]["content"] == "@everyone"
    assert posts[0]["allowed_mentions"] == {"parse": []}
    assert posts[1]["allowed_mentions"] == {"parse": ["everyone"]}
    assert all(post["embeds"][0]["color"] == 2829617 for post in posts)
    assert [post["embeds"][0]["fields"][0]["name"] for post in posts] == ["XTM", "wXTM"]
    assert "0.00108 USDT" in posts[0]["embeds"][0]["fields"][0]["value"]
    assert "0.00243 USDT" in posts[1]["embeds"][0]["fields"][0]["value"]
    assert all(method != "DELETE" for _, method, _ in calls)


def test_both_alert_simulations_are_tagged_and_never_replace_production(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    production_message = {
        "id": "production",
        "author": {"id": "bot-id"},
        "embeds": [{"title": "🟢 + 0.20% — Alerte wXTM +17%"}],
    }

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        if url.endswith("/users/@me"):
            return {"id": "bot-id"}
        if method == "GET":
            return [production_message]
        if method == "POST":
            return {"id": f"test-{sum(call[1] == 'POST' for call in calls)}"}
        raise AssertionError(f"Unexpected API call: {method} {url}")

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    results = update_once.run_both_alert_tests([
        {"label": "XTM", "price": 0.001, "change": 4.0, "market": "MEXC", "error": None},
        {"label": "wXTM", "price": 0.002, "change": 9.0, "market": "Uniswap V4", "error": None},
    ])

    assert len(results) == 2
    posts = [call[2] for call in calls if call[1] == "POST"]
    assert len(posts) == 4
    assert [post["embeds"][0]["color"] for post in posts] == [2829617] * 4
    assert all(post["embeds"][0]["title"].startswith("[TEST SIMULATION BOTH ±10%]") for post in posts)
    assert sum(post.get("content") == "@everyone" for post in posts) == 2
    assert [post["embeds"][0]["fields"][0]["name"] for post in posts] == ["XTM", "wXTM", "XTM", "wXTM"]
    assert all(call[1] != "DELETE" for call in calls)


def test_verify_fake_progression_checks_one_mentioned_box_per_direction(monkeypatch, capsys):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    messages = [
        {
            "id": "green-id",
            "author": {"id": "bot-id"},
            "content": "@everyone [TEST FICTIF 4 MIN] Alerte XTM +300%",
            "mention_everyone": True,
            "embeds": [{"title": "[TEST FICTIF 4 MIN] Alerte XTM +300%", "color": 2829617,
                        "fields": [{"name": "XTM", "value": "**0.004 USDT**"}]}],
        },
        {
            "id": "red-id",
            "author": {"id": "bot-id"},
            "content": "@everyone [TEST FICTIF 4 MIN] Alerte XTM -300%  GO BUY",
            "mention_everyone": True,
            "embeds": [{"title": "[TEST FICTIF 4 MIN] Alerte XTM -300%  GO BUY", "color": 2829617,
                        "fields": [{"name": "XTM", "value": "**0 USDT**"}]}],
        },
    ]
    calls = []

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method))
        return {"id": "bot-id"} if url.endswith("/users/@me") else messages

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    update_once.verify_fake_alert_progression()

    assert len(calls) == 2
    assert all(method == "GET" for _, method in calls)
    assert "mention_everyone=True" in capsys.readouterr().out


def test_alert_milestone_is_each_ten_percent_and_capped_at_300():
    assert alert_milestone([{"change": 17.19}]) == 10
    assert alert_milestone([{"change": 20.0}]) == 20
    assert alert_milestone([{"change": -39.99}]) == 30
    assert alert_milestone([{"change": 450.0}]) == 300
    assert alert_milestone([]) == 0
