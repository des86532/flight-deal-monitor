from datetime import date, datetime, timedelta

import pytest

from flight_monitor import db, watches
from flight_monitor.watches import WatchArgError, WatchLimitError, WatchSpec, expand_combos, parse_watch_args

TODAY = date(2026, 11, 1)


def test_parse_ok():
    spec = parse_watch_args(["nrt,kix", "2026-12-01", "2027-02-28", "4-6"], TODAY)
    assert spec.destinations == ("NRT", "KIX")
    assert (spec.days_min, spec.days_max) == (4, 6)


@pytest.mark.parametrize(
    "args",
    [
        ["TOKYO", "2026-12-01", "2027-02-28", "4-6"],
        ["NRT", "2026-12-10", "2026-12-01", "4-6"],
        ["NRT", "2026-12-01", "2027-02-28", "6-4"],
        ["NRT", "2026-12-01", "2027-02-28", "1"],
        ["NRT", "2026-12-01", "2026-10-30", "3"],  # date_to 已過
        ["NRT", "12/01", "2027-02-28", "3"],
    ],
)
def test_parse_rejects(args):
    with pytest.raises(WatchArgError) as e:
        parse_watch_args(args, TODAY)
    assert "/watch NRT,KIX" in str(e.value)


def _spec(d_from="2026-12-01", d_to="2026-12-20", n=5, dest=("NRT",)):
    return WatchSpec("TPE", dest, date.fromisoformat(d_from), date.fromisoformat(d_to), n, n)


def test_expand_whole_trip_inside_range():
    combos = expand_combos(_spec(), 1, TODAY)
    assert len(combos) == 16
    assert (combos[0].depart, combos[0].ret) == (date(2026, 12, 1), date(2026, 12, 5))
    assert (combos[-1].depart, combos[-1].ret) == (date(2026, 12, 16), date(2026, 12, 20))


def test_expand_skips_past_departures():
    combos = expand_combos(_spec(), 1, date(2026, 12, 10))
    assert [c.depart.day for c in combos] == [11, 12, 13, 14, 15, 16]


def test_scan_order_prefers_null_then_oldest_and_skips_fresh(dbpath):
    now = datetime(2026, 11, 1, 12, 0)
    # 手動建立 6 個 combo：3 NULL、2 組 7 小時前、1 組 1 小時前
    with db.connect(dbpath) as c:
        c.execute("INSERT INTO watches VALUES(1,'TPE','NRT','2026-12-01','2026-12-31',2,2,1,'x')")
        rows = [
            (1, None), (2, None), (3, None),
            (4, db.now_iso(now - timedelta(hours=7))),
            (5, db.now_iso(now - timedelta(hours=8))),
            (6, db.now_iso(now - timedelta(hours=1))),
        ]
        for i, last in rows:
            c.execute(
                "INSERT INTO combos VALUES(?,1,'NRT',?,?,2,?)",
                (f"k{i}", f"2026-12-{10 + i:02d}", f"2026-12-{11 + i:02d}", last),
            )
    picked = []
    while (nxt := watches.next_combo(dbpath, now)) is not None:
        picked.append(nxt[0].key)
        watches.mark_checked(dbpath, nxt[0].key, now)
    assert picked == ["k1", "k2", "k3", "k5", "k4"]  # k6 一小時前查過，不會被選到


def test_add_watch_limits(dbpath, monkeypatch):
    monkeypatch.setattr(watches, "MAX_ACTIVE_COMBOS", 20)
    wid, n = watches.add_watch(dbpath, _spec(), TODAY)
    assert (wid, n) == (1, 16)
    with pytest.raises(WatchLimitError, match="組合數 16，加上現有 16 超過上限 20"):
        watches.add_watch(dbpath, _spec(), TODAY)
    with pytest.raises(WatchLimitError, match="沒有可查的日期組合"):
        watches.add_watch(dbpath, _spec(d_from="2026-12-01", d_to="2026-12-03", n=5), TODAY)


def test_cleanup_stops_watch_when_all_dates_passed(dbpath):
    watches.add_watch(dbpath, _spec(d_to="2026-12-05"), TODAY)  # 單一 combo 12/01-12/05
    assert watches.cleanup_expired(dbpath, date(2026, 11, 30)) == []
    assert watches.cleanup_expired(dbpath, date(2026, 12, 1)) == [1]
    assert watches.list_active_watches(dbpath) == []
