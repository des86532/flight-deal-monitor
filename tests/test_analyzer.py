from datetime import date, datetime, timedelta

from flight_monitor import analyzer, db, serp, skyscanner, watches
from flight_monitor.google import GoogleResult
from flight_monitor.watches import Combo

NOW = datetime(2026, 11, 1, 12, 0)


def _seed(path, prices, days=5):
    with db.connect(path) as c:
        c.execute("INSERT INTO watches VALUES(1,'TPE','NRT','2026-12-01','2026-12-31',5,5,1,'x')")
        for i in range(len(prices) + 1):
            c.execute(
                "INSERT INTO combos VALUES(?,1,'NRT',?,?,?,NULL)",
                (f"k{i}", f"2026-12-{i + 1:02d}", f"2026-12-{i + 5:02d}", days),
            )
        for i, p in enumerate(prices, start=1):
            c.execute(
                "INSERT INTO quotes(combo_key, price, url, checked_at) VALUES(?,?,?,?)",
                (f"k{i}", p, "u", db.now_iso(NOW - timedelta(days=1))),
            )
    return Combo("k0", 1, "NRT", date(2026, 12, 1), date(2026, 12, 5), days)


def test_baseline_candidate_with_five_samples(dbpath):
    combo = _seed(dbpath, [16000, 15500, 15000, 15800, 16200])
    mean = analyzer.baseline(dbpath, combo, NOW)
    assert mean == 15700
    assert analyzer.is_candidate(11000, mean)
    assert not analyzer.is_candidate(14000, mean)


def test_baseline_needs_five_samples(dbpath):
    combo = _seed(dbpath, [16000, 15500, 15000, 15800])
    mean = analyzer.baseline(dbpath, combo, NOW)
    assert mean is None
    assert not analyzer.is_candidate(11000, mean)


def test_baseline_ignores_old_quotes_and_uses_latest_per_combo(dbpath):
    combo = _seed(dbpath, [16000] * 5)
    with db.connect(dbpath) as c:
        c.execute("INSERT INTO quotes(combo_key, price, url, checked_at) VALUES('k1',20000,'u',?)", (db.now_iso(NOW - timedelta(hours=1)),))
        c.execute("INSERT INTO quotes(combo_key, price, url, checked_at) VALUES('k2',1,'u',?)", (db.now_iso(NOW - timedelta(days=40)),))
    # k1 最新 20000；k2 的 40 天前報價不計，仍取其 1 天前的 16000
    assert analyzer.baseline(dbpath, combo, NOW) == (20000 + 16000 * 4) / 5


def test_cooldown(dbpath):
    combo = _seed(dbpath, [16000] * 5)
    assert analyzer.cooldown_ok(dbpath, combo.key, 12000)
    analyzer.record_notification(dbpath, combo, 12000, 12000, NOW)
    assert not analyzer.cooldown_ok(dbpath, combo.key, 11600)
    assert analyzer.cooldown_ok(dbpath, combo.key, 11500)


def test_throttle_three_per_24h(dbpath):
    combo = _seed(dbpath, [16000] * 5)
    for h in (1, 2, 3):
        analyzer.record_notification(dbpath, combo, 10000, 10000, NOW - timedelta(hours=h))
    assert not analyzer.throttle_ok(dbpath, 1, NOW)
    assert analyzer.throttle_ok(dbpath, 1, NOW + timedelta(hours=22))  # 最舊那則已超過 24 小時


def _eval(settings, combo, verification, price=11000, mean=15700.0):
    return analyzer.evaluate_candidate(
        settings, combo, "TPE", GoogleResult(price, "長榮航空", 0, "g"), mean, now=NOW,
        verify=lambda *a, **k: verification,
        compare=lambda *a, **k: skyscanner.SkyResult("blocked", None, "s"),
    )


def test_official_price_not_cheap_suppresses_notification(settings, dbpath):
    combo = _seed(dbpath, [16000] * 5)
    v = serp.Verification("ok", official_price=15000, seller="長榮航空", price_level="typical")
    assert _eval(settings, combo, v) is None


def test_official_low_level_or_cheap_notifies_with_official_price(settings, dbpath):
    combo = _seed(dbpath, [16000] * 5)
    deal, _ = _eval(settings, combo, serp.Verification("ok", official_price=15000, seller="x", price_level="low"))
    assert deal.main_price == 15000
    deal, _ = _eval(settings, combo, serp.Verification("ok", official_price=11350, seller="x", price_level="typical"))
    assert deal.main_price == 11350


def test_unverified_still_notifies_with_google_price(settings, dbpath):
    combo = _seed(dbpath, [16000] * 5)
    deal, sky = _eval(settings, combo, serp.Verification("skipped", reason="今日查證額度已用完"))
    assert deal.main_price == 11000 and sky.status == "blocked"


def test_evaluate_stops_before_verifying_when_throttled(settings, dbpath):
    combo = _seed(dbpath, [16000] * 5)
    for h in (1, 2, 3):
        analyzer.record_notification(dbpath, combo, 20000, 20000, NOW - timedelta(hours=h))

    def boom(*a, **k):
        raise AssertionError("不該呼叫 serp")

    assert analyzer.evaluate_candidate(
        settings, combo, "TPE", GoogleResult(11000, "x", 0, "g"), 15700.0, now=NOW, verify=boom, compare=boom
    ) is None
