from datetime import datetime, timezone
from unittest.mock import Mock

import pandas as pd
import pytest

from data_provider.yfinance_fetcher import YfinanceFetcher
from src.core.trading_calendar import get_hk_completed_cash_sessions
from src.market_analyzer import MarketAnalyzer


@pytest.fixture(autouse=True)
def no_external_sina(monkeypatch):
    monkeypatch.setattr(YfinanceFetcher, "_get_hk_sina_history", staticmethod(lambda _: pd.DataFrame()))


def bars(*rows):
    return pd.DataFrame(
        [row[1:] for row in rows],
        index=pd.DatetimeIndex([row[0] for row in rows], tz="Asia/Hong_Kong"),
        columns=["Close", "Open", "High", "Low"],
    )


NOV20 = ("2026-11-20", 3511.14, 3590, 3610, 3500)
NOV23 = ("2026-11-23", 3508.0, 3443.76, 3524.74, 3395.38)
NOV24 = ("2026-11-24", 3466.87, 3579.83, 3603.70, 3439.34)
NOV25 = ("2026-11-25", 3530, 3510, 3600, 3412)


@pytest.mark.parametrize("at,expected", [
    ("2026-11-23T10:00:00+00:00", ("2026-11-20", "2026-11-23")),
    ("2026-11-24T08:24:59+00:00", ("2026-11-20", "2026-11-23")),
    ("2026-11-24T08:25:00+00:00", ("2026-11-23", "2026-11-24")),
    ("2026-12-24T04:10:00+00:00", ("2026-12-22", "2026-12-23")),
    ("2026-12-24T04:25:00+00:00", ("2026-12-23", "2026-12-24")),
    ("2026-12-25T10:00:00+00:00", ("2026-12-23", "2026-12-24")),
    ("2026-12-28T10:00:00+00:00", ("2026-12-24", "2026-12-28")),
])
def test_hk_uses_auction_end_and_publication_window(at, expected):
    days = get_hk_completed_cash_sessions(datetime.fromisoformat(at))
    assert tuple(day.isoformat() for day in days) == expected


def select(history, at):
    yf = Mock()
    yf.Ticker.return_value.history.return_value = history
    return YfinanceFetcher()._get_hk_main_indices(yf, current_time=datetime.fromisoformat(at))


@pytest.mark.parametrize("at,target,previous,display_pct", [
    ("2026-11-23T10:00:00+00:00", NOV23, NOV20, "-0.09"),
    ("2026-11-24T10:00:00+00:00", NOV24, NOV23, "-1.17"),
])
def test_hstech_replays_both_incidents(at, target, previous, display_pct):
    rows = select(bars(NOV25, NOV23, NOV20, NOV24), at)
    hstech = next(row for row in rows if row["code"] == "HSTECH")
    assert hstech["cash_session_date"] == target[0]
    assert hstech["previous_cash_session_date"] == previous[0]
    assert f'{hstech["change_pct"]:+.2f}' == display_pct
    assert hstech["amplitude"] == pytest.approx((target[3] - target[4]) / previous[1] * 100)


@pytest.mark.parametrize("rows", [
    (NOV24,), (NOV20, NOV24), (NOV23, NOV23, NOV24), (NOV23, NOV25),
    (NOV23, (NOV24[0], float("inf"), *NOV24[2:])),
])
def test_unverified_bar_pair_is_not_published(rows):
    assert not select(bars(*rows), "2026-11-24T10:00:00+00:00")


def test_real_flat_close_is_not_treated_as_missing():
    result = select(bars(NOV23, (NOV24[0], NOV23[1], *NOV24[2:])), "2026-11-24T10:00:00+00:00")
    assert result[0]["change_pct"] == 0.0


def test_hk_report_replaces_contradictory_index_commentary_and_marks_partial(monkeypatch):
    at = datetime(2026, 11, 24, 10, tzinfo=timezone.utc)
    rows = select(bars(NOV23, NOV24), at.isoformat())
    analyst = MarketAnalyzer(region="hk")
    monkeypatch.setattr(analyst.data_manager, "get_main_indices", lambda **_: rows[1:2])
    overview = analyst.get_market_overview(current_time=at)
    report = analyst._inject_data_into_review(
        "### 二、指数结构\n恒生科技指数相对抗跌，涨跌幅 +0.00%，缺乏开盘、最高、最低。\n"
        "### 五、消息催化\n保留新闻摘要。", overview,
    )
    payload = analyst.build_market_review_payload(overview, [], report)
    assert "相对抗跌" not in report and "+0.00%" not in report and "缺乏开盘" not in report
    assert "2026-11-24" in report and "2026-11-23" in report
    assert "-1.17%" in report and "4.69%" in report and "保留新闻摘要" in report
    assert payload["status"] == "partial"
    assert payload["indices"][0]["previous_cash_session_date"] == "2026-11-23"


def test_single_yahoo_bar_cannot_be_its_own_previous_close():
    yf = Mock()
    yf.Ticker.return_value.history.return_value = bars(NOV24).assign(Volume=1)
    assert not YfinanceFetcher()._get_hk_main_indices(yf)


def test_hk_fallback_uses_complete_pair_without_mixing_prices(monkeypatch):
    yf = Mock()
    yf.Ticker.return_value.history.return_value = bars(
        (NOV24[0], 9999, 9950, 10010, 9930),
    )
    with_sina = {"HSTECH": bars(NOV23, NOV24)}
    monkeypatch.setattr(
        YfinanceFetcher, "_get_hk_sina_history",
        staticmethod(lambda code: with_sina.get(code, pd.DataFrame())), raising=False,
    )
    rows = YfinanceFetcher()._get_hk_main_indices(
        yf, current_time=datetime(2026, 11, 24, 10, tzinfo=timezone.utc),
    )
    assert rows and [row["code"] for row in rows] == ["HSTECH"]
    assert rows[0]["current"] == 3466.87
    assert rows[0]["prev_close"] == 3508.0
    assert rows[0]["source"] == "sina"


def test_hk_mail_payload_keeps_verified_aggregate_and_no_fabricated_missing_data(monkeypatch):
    from src.core.market_review import _render_market_review_payload_markdown

    at = datetime(2026, 11, 24, 10, tzinfo=timezone.utc)
    by_code = {
        "^HSI": bars(
            ("2026-11-23", 22383.74, 22168.19, 22436.18, 21998.05),
            ("2026-11-24", 22219.65, 22627.29, 22647.50, 22183.16),
        ),
        "^HSCE": bars(
            ("2026-11-23", 7527.98, 7472.52, 7550.51, 7406.35),
            ("2026-11-24", 7452.56, 7587.92, 7602.30, 7435.02),
        ),
        "HSTECH.HK": bars(NOV24),
    }
    yf = Mock()
    yf.Ticker.side_effect = lambda symbol: Mock(history=Mock(return_value=by_code[symbol]))
    monkeypatch.setattr(
        YfinanceFetcher, "_get_hk_sina_history",
        staticmethod(lambda code: bars(NOV23, NOV24) if code == "HSTECH" else pd.DataFrame()),
    )
    analyst = MarketAnalyzer(region="hk")
    fetched = YfinanceFetcher()._get_hk_main_indices(yf, current_time=at)
    monkeypatch.setattr(analyst.data_manager, "get_main_indices", lambda **_: fetched)
    overview = analyst.get_market_overview(current_time=at)
    generated = analyst._inject_data_into_review("## 港股复盘\n\n### 二、指数结构\n错误数字", overview)
    payload = analyst.build_market_review_payload(overview, [], generated)
    mail = _render_market_review_payload_markdown(payload, wrapper_title="港股复盘")
    assert "有效指数 3/3" in mail and "错误数字" not in mail
    assert "三项指数中表现最弱：恒生科技指数（-1.17%）" in mail
    assert "主要指数平均涨跌幅 -0.97%" in mail
    assert "3579.83 | 3603.70 | 3439.34 | 4.69%" in mail
    assert payload["indices"][1]["source"] == "sina"

    fetched.clear()
    overview = analyst.get_market_overview(current_time=at)
    generated = analyst._inject_data_into_review("## 港股复盘\n\n### 二、指数结构\n+0.00%", overview)
    payload = analyst.build_market_review_payload(overview, [], generated)
    assert "有效指数 0/3" in generated and "+0.00%" not in generated
    assert not payload["indices"] and "market_light" not in payload
    assert payload["warnings"] == ["hk_cash_indices_unavailable"]
