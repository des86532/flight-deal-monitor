import json

import pytest
from fast_flights import FlightsNotFound

from flight_monitor.google import parse_html


def _entry(price, airline, segments=1):
    return [[None, [airline], [None] * segments], [[None, price]]]


def _html(payload) -> str:
    return f'<html><script class="ds:1">AF_initDataCallback({{key: \'ds:1\', data:{json.dumps(payload)}, sideChannel: {{}}}});</script></html>'


def test_includes_best_group_which_fast_flights_parser_skips():
    payload = [None, None, [[_entry(9363, "酷航")]], [[_entry(11560, "樂桃航空"), _entry(12092, "捷星", 2)]]]
    assert sorted(parse_html(_html(payload))) == [(9363, "酷航", 0), (11560, "樂桃航空", 0), (12092, "捷星", 1)]


def test_no_results_groups_is_empty():
    assert parse_html(_html([None, None, None, None])) == []


def test_empty_payload_and_error_are_not_found():
    with pytest.raises(FlightsNotFound):
        parse_html(_html([]))
    with pytest.raises(FlightsNotFound):
        parse_html('<script class="ds:1">data:[3,null], errorHasStatus: true,\n</script>')


def test_missing_script_raises_so_scanner_counts_a_failure():
    with pytest.raises(ValueError):
        parse_html("<html>blocked</html>")
