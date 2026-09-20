import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import update_once
from update_once import (
    alert_milestone,
    alert_changes_are_complete,
    alert_quotes_for_direction,
    build_alert_embed,
    build_embed,
    build_price_embed,
    extract_previous_price_changes,
    format_alert_text,
    format_group_alert_text,
    price_text,
    select_usdt_ticker,
    variation_trend_sign,
)
from pool_data import WXTM_NETWORK, WXTM_POOL_ADDRESS, parse_wxtm_pool_response

POOL_RESPONSE = {
    "data": {
        "id": "eth_0x530581e8b4dff575d96af96cbfb74d0cc4ed0ec0cb7c953f491c7a60a787412d",
        "attributes": {
            "address": "0x530581e8b4dff575d96af96cbfb74d0cc4ed0ec0cb7c953f491c7a60a787412d",
            "name": "wXTM / ETH 3%",
            "base_token_price_usd": "0.002428",
            "price_change_percentage": {"h24": "17.33"},
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


def test_embed_has_both_assets():
    embed = build_embed([
        {"label": "XTM", "price": 0.001, "change": 1.5, "market": "MEXC", "error": None},
        {"label": "wXTM", "price": 0.002, "currency": "USD", "change": -2.0, "market": "Uniswap V4 (Ethereum)", "error": None},
    ])
    assert [field["name"] for field in embed["fields"]] == ["XTM", "wXTM"]
    assert "USDT" in price_text({"price": 0.001, "change": 1.5, "market": "MEXC", "error": None})
    assert "0.00243 USDT" in price_text({"label": "wXTM", "price": 0.002428, "currency": "USD", "change": 1.5, "market": "Uniswap", "error": None})
    assert embed["footer"]["text"] == "Mise à jour toutes les minutes"
    assert build_price_embed([], "Snapshot du prix toutes les 4 heures")["footer"]["text"] == "Snapshot du prix toutes les 4 heures"


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
    assert variation_trend_sign(3.25, 3.25) == "="
    assert variation_trend_sign(3.25, None) == "?"


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
    assert format_group_alert_text(one, upward=True) == "Alert wXTM+17.19%"
    assert format_group_alert_text(both, upward=True) == "Alert XTM+10% | wXTM+17.19%"
    assert format_group_alert_text(
        [{"label": "wXTM", "change": -17.19}], upward=False
    ) == "Alert wXTM-17.19%  GO BUY"
    embed = build_alert_embed(
        format_group_alert_text(both, upward=True),
        5763719,
        [
            {"label": "XTM", "price": 0.001, "change": 10.0, "market": "MEXC", "error": None},
            {"label": "wXTM", "price": 0.002, "currency": "USD", "change": 17.19, "market": "Uniswap V4 (Ethereum)", "error": None},
        ],
    )
    assert [field["name"] for field in embed["fields"]] == ["XTM", "wXTM"]
    assert embed["title"] == "Alert XTM+10% | wXTM+17.19%"
    assert "description" not in embed


def test_wxtm_only_alert_embed_excludes_unaffected_xtm():
    quotes = [
        {"label": "XTM", "price": 0.0022, "change": 7.54, "market": "MEXC", "error": None},
        {"label": "wXTM", "price": 0.002428, "currency": "USD", "change": 18.70, "market": "Uniswap V4 (Ethereum)", "error": None},
    ]
    affected = alert_quotes_for_direction(quotes, upward=True)
    embed = build_alert_embed(format_group_alert_text(affected, upward=True), 5763719, affected)

    assert embed["title"] == "Alert wXTM+18.7%"
    assert [field["name"] for field in embed["fields"]] == ["wXTM"]
    assert "0.00243 USDT" in embed["fields"][0]["value"]
    trending_embed = build_alert_embed(
        "Alert wXTM+18.7%",
        5763719,
        affected,
        previous_changes={"wXTM": 18.8},
    )
    assert trending_embed["title"] == "🔴- Alert wXTM+18.7%"
    assert "```" not in trending_embed["fields"][0]["value"]
    positive_trend_embed = build_alert_embed(
        "Alert wXTM+18.7%",
        5763719,
        affected,
        previous_changes={"wXTM": 18.6},
    )
    assert positive_trend_embed["title"] == "🟢+ Alert wXTM+18.7%"
    assert format_alert_text(-10, upward=False) == "Alert XTM-10%  GO BUY"
    assert format_alert_text(10, upward=True) == "Alert XTM+10%"
    assert format_alert_text(15.678, upward=True) == "Alert XTM+15.68%"
    assert format_alert_text(-15.678, upward=False) == "Alert XTM-15.68%  GO BUY"
    assert format_alert_text(450, upward=True) == "Alert XTM+300%"
    assert format_alert_text(-450, upward=False) == "Alert XTM-300%  GO BUY"
    embed = build_alert_embed(
        format_alert_text(-10, upward=False),
        15158332,
        [
            {"label": "XTM", "price": 0.001, "change": -10.0, "market": "MEXC", "error": None},
            {"label": "wXTM", "price": 0.002, "currency": "USD", "change": -2.0, "market": "Uniswap V4 (Ethereum)", "error": None},
        ],
    )
    assert embed["color"] == 15158332
    assert [field["name"] for field in embed["fields"]] == ["XTM", "wXTM"]
    assert "0.001 USDT" in embed["fields"][0]["value"]
    assert "0.00200 USDT" in embed["fields"][1]["value"]
    capped_embed = build_alert_embed(
        "Alert XTM-300%  GO BUY",
        15158332,
        [{"label": "XTM", "price": 0.001, "change": -450.0, "market": "MEXC", "error": None}],
    )
    assert "-300.00 % sur 24 h" in capped_embed["fields"][0]["value"]
    wxtm_capped = build_alert_embed(
        "Alert wXTM-300%  GO BUY",
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
        "market": "Uniswap V4 (Ethereum)",
        "updated": None,
        "error": None,
    }


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
                {"id": "alert", "author": {"id": "bot-id"}, "embeds": [{"title": "Alert XTM+10%"}]},
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
        [{"id": "alert-message", "author": {"id": "bot-id"}, "content": "@everyone Alert XTM+10%", "mention_everyone": True, "embeds": [{"title": "Alert XTM+10%"}]}],
        {"id": "fresh-alert"},
        None,
    ])

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    result = update_once.upsert_alert(
        "Alert wXTM+12.5%", 5763719,
        [{"label": "wXTM", "price": 0.002, "currency": "USD", "change": 12.5, "market": "Uniswap V4 (Ethereum)", "error": None}],
        upward=True,
    )

    assert result == "nouveau message d'alerte fresh-alert publié; 1 ancienne(s) alerte(s) supprimée(s)"
    assert calls[2][1] == "POST"
    assert "content" not in calls[2][2]
    assert calls[2][2]["allowed_mentions"] == {"parse": []}
    assert calls[2][2]["embeds"][0]["title"] == "Alert wXTM+12.5%"
    assert "description" not in calls[2][2]["embeds"][0]
    assert calls[2][2]["embeds"][0]["footer"]["text"] == "Palier @everyone notifié : +10%"
    assert calls[3][1] == "DELETE"
    assert calls[3][0].endswith("/alert-message")


def test_upsert_alert_creates_one_message_with_everyone_ping(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    responses = iter([{"id": "bot-id"}, [], {"id": "new-alert"}])

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    update_once.upsert_alert(
        "Alert XTM-10%  GO BUY", 15158332,
        [{"label": "XTM", "price": 0.001, "change": -10, "market": "MEXC", "error": None}],
        upward=False,
    )

    assert calls[2][1] == "POST"
    assert calls[2][2]["content"] == "@everyone"
    assert calls[2][2]["allowed_mentions"] == {"parse": ["everyone"]}
    assert calls[2][2]["embeds"][0]["footer"]["text"] == "Palier @everyone notifié : -10%"


def test_upsert_alert_pings_again_only_when_next_ten_percent_milestone_is_reached(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    responses = iter([
        {"id": "bot-id"},
        [{
            "id": "previous-alert",
            "author": {"id": "bot-id"},
            "mention_everyone": False,
            "embeds": [{"title": "Alert wXTM+19.7%", "footer": {"text": "Palier @everyone notifié : +10%"}}],
        }],
        {"id": "milestone-20-alert"},
        None,
    ])

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    update_once.upsert_alert(
        "Alert wXTM+20.1%", 5763719,
        [{"label": "wXTM", "price": 0.002, "currency": "USD", "change": 20.1, "market": "Uniswap V4 (Ethereum)", "error": None}],
        upward=True,
    )

    assert calls[2][1] == "POST"
    assert calls[2][2]["content"] == "@everyone"
    assert calls[2][2]["allowed_mentions"] == {"parse": ["everyone"]}
    assert calls[2][2]["embeds"][0]["footer"]["text"] == "Palier @everyone notifié : +20%"
    assert calls[3][1] == "DELETE"


def test_upsert_alert_adds_everyone_once_to_legacy_message(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    responses = iter([
        {"id": "bot-id"},
        [{"id": "legacy-alert", "author": {"id": "bot-id"}, "content": "Alert XTM+10%", "mention_everyone": False, "embeds": [{"title": "Alert XTM+10%"}]}],
        {"id": "fresh-alert"},
        None,
    ])

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    update_once.upsert_alert(
        "Alert XTM+20%", 5763719,
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
            {"id": "green", "author": {"id": "bot-id"}, "embeds": [{"title": "🔴- Alert wXTM+16.67%"}]},
            {"id": "red", "author": {"id": "bot-id"}, "embeds": [{"title": "🟢+ Alert XTM-12%  GO BUY"}]},
            {"id": "test", "author": {"id": "bot-id"}, "embeds": [{"title": "[TEST] Alert XTM+20%"}]},
            {"id": "other-bot", "author": {"id": "other"}, "embeds": [{"title": "Alert XTM+20%"}]},
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
        "Alert XTM+10%", 5763719, [], upward=True, test_label="[TEST 4 MIN]", notify_everyone=False
    )

    assert "content" not in calls[2][2]
    assert calls[2][2]["embeds"][0]["title"] == "[TEST 4 MIN] Alert XTM+10%"
    assert calls[2][2]["allowed_mentions"] == {"parse": []}


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
        "Alert XTM+10%", "Alert XTM-10%  GO BUY",
        "Alert XTM+25%", "Alert XTM-25%  GO BUY",
        "Alert XTM+100%", "Alert XTM-100%  GO BUY",
        "Alert XTM+200%", "Alert XTM-200%  GO BUY",
        "Alert XTM+300%", "Alert XTM-300%  GO BUY",
    ]
    assert [alert[1] for alert in alerts] == [5763719, 15158332] * 5
    assert all(alert[4]["test_label"] == "[TEST FICTIF 4 MIN]" for alert in alerts)
    assert all(alert[4]["notify_everyone"] is True for alert in alerts)
    assert [alert[0] for alert in alerts[::2]] == [
        "Alert XTM+10%", "Alert XTM+25%", "Alert XTM+100%", "Alert XTM+200%", "Alert XTM+300%"
    ]
    assert [alert[2] for alert in alerts[::2]] == [10, 25, 100, 200, 300]
    assert [alert[2] for alert in alerts[1::2]] == [-10, -25, -100, -200, -300]
    assert [alert[2] for alert in alerts] == [10, -10, 25, -25, 100, -100, 200, -200, 300, -300]
    assert [round(alert[3], 8) for alert in alerts[::2]] == [0.0011, 0.00125, 0.002, 0.003, 0.004]
    assert [round(alert[3], 8) for alert in alerts[1::2]] == [0.0009, 0.00075, 0.0, 0.0, 0.0]
    assert pauses == [60, 60, 60, 60]


def test_verify_fake_progression_checks_one_mentioned_box_per_direction(monkeypatch, capsys):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    messages = [
        {
            "id": "green-id",
            "author": {"id": "bot-id"},
            "content": "@everyone [TEST FICTIF 4 MIN] Alert XTM+300%",
            "mention_everyone": True,
            "embeds": [{"title": "[TEST FICTIF 4 MIN] Alert XTM+300%", "color": 5763719,
                        "fields": [{"name": "XTM", "value": "**0.004 USDT**"}]}],
        },
        {
            "id": "red-id",
            "author": {"id": "bot-id"},
            "content": "@everyone [TEST FICTIF 4 MIN] Alert XTM-300%  GO BUY",
            "mention_everyone": True,
            "embeds": [{"title": "[TEST FICTIF 4 MIN] Alert XTM-300%  GO BUY", "color": 15158332,
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
