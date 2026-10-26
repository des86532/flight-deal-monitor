from datetime import date

from flight_monitor.messages import Deal, format_deal

BASE = dict(
    origin="TPE", destination="NRT", depart=date(2026, 12, 12), ret=date(2026, 12, 16),
    google_price=11200, airlines="長榮航空", stops=0, url="https://g.example/?a=1&b=2",
)


def test_official_price_line_and_header():
    t = format_deal(Deal(**BASE, serp_status="ok", official_price=11350, seller="長榮航空",
                         sky_status="ok", sky_price=11050, mean=15800, price_level="low", watch_id=3))
    assert "監控 #3" in t
    assert "2026/12/12（六）– 12/16（三）共 5 天" in t
    assert "官網價（長榮航空）NT$ 11,350" in t
    assert "Skyscanner NT$ 11,050" in t
    assert "均價 NT$ 15,800（低 28%）" in t and "Google 判定：偏低" in t
    assert "a=1&amp;b=2" in t


def test_skipped_and_blocked():
    t = format_deal(Deal(**BASE, serp_status="skipped", serp_reason="今日查證額度已用完", sky_status="blocked"))
    assert "官網價未確認：今日查證額度已用完" in t
    assert "Skyscanner 被擋，未比價" in t
    assert "均價" not in t  # 無基準價（probe）省略該行
