import json
from datetime import date, datetime
from pathlib import Path

import httpx

from flight_monitor import db, serp

FX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 1, 12, 0)
TODAY = NOW.date().isoformat()


def _fx(name):
    return json.loads((FX / name).read_text())


def _client(responses, counter):
    def handler(req: httpx.Request) -> httpx.Response:
        counter.append(dict(req.url.params))
        return httpx.Response(200, json=responses[len(counter) - 1])

    return httpx.Client(transport=httpx.MockTransport(handler))


def _verify(settings, client):
    return serp.verify(settings, "TPE", "NRT", date(2026, 12, 12), date(2026, 12, 16), client=client, now=NOW)


def test_picks_lowest_airline_direct_price_and_chains_tokens(settings, dbpath):
    calls = []
    v = _verify(settings, _client([_fx("serp_step1.json"), _fx("serp_step2.json"), _fx("serp_step3.json")], calls))
    assert (v.status, v.official_price, v.seller, v.price_level) == ("ok", 11350, "EVA Air", "low")
    assert v.url == "https://www.google.com/travel/flights?booking=1"
    assert calls[1]["departure_token"] == "DEP_TOKEN_CHEAP"
    assert calls[2]["booking_token"] == "BOOK_TOKEN_CHEAP"
    assert db.usage_today(dbpath, TODAY) == (3, 1)


def test_only_ota_options_is_no_direct(settings):
    step3 = _fx("serp_step3.json")
    step3["booking_options"] = [step3["booking_options"][0], step3["booking_options"][3]]
    v = _verify(settings, _client([_fx("serp_step1.json"), _fx("serp_step2.json"), step3], []))
    assert v.status == "no_direct"


def test_api_error_field(settings):
    v = _verify(settings, _client([{"error": "Invalid API key."}], []))
    assert (v.status, v.reason) == ("error", "Invalid API key.")


def test_daily_limit_skips_without_http(settings, dbpath):
    for _ in range(2):
        db.add_serp_verification(dbpath, TODAY)
    calls = []
    v = _verify(settings, _client([], calls))
    assert (v.status, v.reason) == ("skipped", "今日查證額度已用完")
    assert calls == []


def test_monthly_limit_skips_without_http(settings, dbpath):
    for _ in range(240):
        db.add_serp_call(dbpath, "2026-11-03")
    calls = []
    v = _verify(settings, _client([], calls))
    assert (v.status, v.reason) == ("skipped", "本月 SerpApi 額度已用完")
    assert calls == []
