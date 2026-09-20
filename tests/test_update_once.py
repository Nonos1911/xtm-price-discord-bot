import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import update_once
from update_once import build_alert_embed, build_embed, build_price_embed, format_alert_text, price_text, select_usdt_ticker, xtm_alert_triggered, xtm_buy_alert_triggered


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
    assert embed["footer"]["text"] == "Mise à jour toutes les minutes"
    assert build_price_embed([], "Snapshot du prix toutes les 4 heures")["footer"]["text"] == "Snapshot du prix toutes les 4 heures"


def test_xtm_alert_is_positive_and_inclusive():
    assert not xtm_alert_triggered([{"label": "XTM", "change": 9.99}])
    assert xtm_alert_triggered([{"label": "XTM", "change": 10.0}])
    assert not xtm_alert_triggered([{"label": "XTM", "change": -12.0}])
    assert xtm_buy_alert_triggered([{"label": "XTM", "change": -10.0}])
    assert not xtm_buy_alert_triggered([{"label": "XTM", "change": -9.99}])
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
            {"label": "wXTM", "price": 0.002, "change": -2.0, "market": "Gate", "error": None},
        ],
    )
    assert embed["color"] == 15158332
    assert [field["name"] for field in embed["fields"]] == ["XTM", "wXTM"]
    assert "0.001 USDT" in embed["fields"][0]["value"]
    assert "0.002 USDT" in embed["fields"][1]["value"]
    capped_embed = build_alert_embed(
        "Alert XTM-300%  GO BUY",
        15158332,
        [{"label": "XTM", "price": 0.001, "change": -450.0, "market": "MEXC", "error": None}],
    )
    assert "-300.00 % sur 24 h" in capped_embed["fields"][0]["value"]


def test_update_discord_reuses_one_price_message_and_removes_duplicates(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    responses = iter(
        [
            {"id": "bot-id"},
            [
                {"id": "new-price", "author": {"id": "bot-id"}, "embeds": [{"title": "💱 Prix XTM / wXTM"}]},
                {"id": "old-price", "author": {"id": "bot-id"}, "embeds": [{"title": "💱 Prix XTM / wXTM"}]},
                {"id": "alert", "author": {"id": "bot-id"}, "embeds": [{"title": "Alert XTM+10%"}]},
            ],
            {"id": "new-price"},
            None,
        ]
    )

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    result = update_once.update_discord({"title": "💱 Prix XTM / wXTM"})

    assert result == "message new-price mis à jour; 1 ancien(s) supprimé(s)"
    assert calls[2][1] == "PATCH"
    assert calls[3][1] == "DELETE"


def test_upsert_alert_edits_same_message_without_pinging_again(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    responses = iter([
        {"id": "bot-id"},
        [{"id": "alert-message", "author": {"id": "bot-id"}, "content": "@everyone Alert XTM+10%", "mention_everyone": True, "embeds": [{"title": "Alert XTM+10%"}]}],
        {"id": "alert-message"},
    ])

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    result = update_once.upsert_alert("Alert XTM+12.5%", 5763719, [], upward=True)

    assert result == "alerte Alert XTM+12.5% mise à jour dans le message alert-message"
    assert calls[2][1] == "PATCH"
    assert calls[2][2]["content"] == "@everyone Alert XTM+12.5%"
    assert calls[2][2]["allowed_mentions"] == {"parse": []}
    assert calls[2][2]["embeds"][0]["title"] == "Alert XTM+12.5%"


def test_upsert_alert_creates_one_message_with_everyone_ping(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    responses = iter([{"id": "bot-id"}, [], {"id": "new-alert"}])

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    update_once.upsert_alert("Alert XTM-10%  GO BUY", 15158332, [], upward=False)

    assert calls[2][1] == "POST"
    assert calls[2][2]["content"] == "@everyone Alert XTM-10%  GO BUY"
    assert calls[2][2]["allowed_mentions"] == {"parse": ["everyone"]}


def test_upsert_alert_adds_everyone_once_to_legacy_message(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    calls = []
    responses = iter([
        {"id": "bot-id"},
        [{"id": "legacy-alert", "author": {"id": "bot-id"}, "content": "Alert XTM+10%", "mention_everyone": False, "embeds": [{"title": "Alert XTM+10%"}]}],
        {"id": "legacy-alert"},
    ])

    def fake_api_json(url, *, headers=None, method="GET", body=None):
        calls.append((url, method, body))
        return next(responses)

    monkeypatch.setattr(update_once, "api_json", fake_api_json)
    update_once.upsert_alert("Alert XTM+20%", 5763719, [], upward=True)

    assert calls[2][1] == "PATCH"
    assert calls[2][2]["content"] == "@everyone Alert XTM+20%"
    assert calls[2][2]["allowed_mentions"] == {"parse": ["everyone"]}


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

    assert calls[2][2]["content"] == "[TEST 4 MIN] Alert XTM+10%"
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
