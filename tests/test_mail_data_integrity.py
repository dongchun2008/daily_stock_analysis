"""Deterministic regressions for retained mail-data repair contracts."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


class TestLegacyQuoteUnits:
    @pytest.mark.parametrize('raw,expected',[
        ('1000',1000),('1000.0',1000),(123456789,123456789),
        ('0',0),(None,None),('NaN',None),('Infinity',None),('-1',None),
        ('1.5',None),(str(2**63),None),
    ])
    def test_legacy_volume_is_shares_or_missing_and_keeps_price(self,raw,expected):
        from data_provider.tushare_fetcher import TushareFetcher
        fetcher=TushareFetcher.__new__(TushareFetcher)
        fetcher._api=MagicMock()
        fetcher._invoke_pro_api=MagicMock(side_effect=RuntimeError('synthetic permission limit'))
        fake=MagicMock()
        fake.get_realtime_quotes.return_value=pd.DataFrame([{
            'name':'synthetic','price':'10.50','pre_close':'10','volume':raw,
            'amount':'1000','high':'11','low':'9','open':'10',
        }])
        with patch.dict('sys.modules',{'tushare':fake}):quote=fetcher.get_realtime_quote('600001')
        assert quote is not None and quote.price==10.5
        assert quote.volume==expected


class TestCompletedCashSessions:
    @pytest.mark.parametrize('when,latest,previous',[
        ('2026-11-23T10:00:00+00:00','2026-11-20','2026-11-19'),
        ('2026-11-23T20:59:00+00:00','2026-11-20','2026-11-19'),
        ('2026-11-23T21:14:59+00:00','2026-11-20','2026-11-19'),
        ('2026-11-23T21:15:00+00:00','2026-11-23','2026-11-20'),
        ('2026-07-06T20:14:59+00:00','2026-07-02','2026-07-01'),
        ('2026-07-06T20:15:00+00:00','2026-07-06','2026-07-02'),
        ('2026-11-27T18:14:59+00:00','2026-11-25','2026-11-24'),
        ('2026-11-27T18:15:00+00:00','2026-11-27','2026-11-25'),
        ('2026-11-22T18:00:00+08:00','2026-11-20','2026-11-19'),
    ])
    def test_real_calendar_close_guard_holidays_dst_and_early_close(self,when,latest,previous):
        from src.core import trading_calendar as calendar
        fn=getattr(calendar,'get_us_completed_cash_sessions',None)
        assert callable(fn),'US data requires an explicit completed-session selector'
        assert fn(datetime.fromisoformat(when))==(date.fromisoformat(previous),date.fromisoformat(latest))

    def test_calendar_failure_does_not_guess_a_date(self):
        from src.core import trading_calendar as calendar
        fn=getattr(calendar,'get_us_completed_cash_sessions',None)
        assert callable(fn)
        with patch.object(calendar,'_XCALS_AVAILABLE',False):assert fn() is None


def history(labels=('2026-11-19','2026-11-20','2026-11-23'),closes=(100.,102.,999.)):
    return pd.DataFrame({'Close':closes,'Open':closes,'High':closes,'Low':closes,'Volume':[100.]*len(labels)},index=pd.DatetimeIndex(labels))


class TestYahooCompletedBars:
    def test_nonfinite_optional_fields_are_missing_and_json_safe(self):
        import json
        from data_provider.yfinance_fetcher import YfinanceFetcher
        frame=history();frame.loc[pd.Timestamp('2026-11-20'),['Open','High','Low','Volume']]=[float('nan'),float('inf'),float('nan'),float('inf')]
        yf=MagicMock();yf.Ticker.return_value.history.return_value=frame
        result=YfinanceFetcher.__new__(YfinanceFetcher)._get_us_main_indices(yf,current_time=datetime(2026,11,23,10,tzinfo=timezone.utc))
        cash=[row for row in result if row['code']=='SPX'][0]
        assert cash['current']==102. and cash['open'] is None and cash['high'] is None and cash['amplitude'] is None
        json.dumps(result,allow_nan=False)

    @pytest.mark.parametrize('vix_labels',[('2026-11-20','2026-11-24'),('2026-11-17','2026-11-18')])
    def test_future_and_stale_vix_are_rejected_at_provider_entry(self,vix_labels):
        from data_provider.yfinance_fetcher import YfinanceFetcher
        yf=MagicMock()
        def ticker(symbol):
            obj=MagicMock();obj.history.return_value=(history(vix_labels,(10.,11.)) if symbol=='^VIX' else history())
            return obj
        yf.Ticker.side_effect=ticker
        rows=YfinanceFetcher.__new__(YfinanceFetcher)._get_us_main_indices(yf,current_time=datetime(2026,11,23,10,tzinfo=timezone.utc))
        assert {row['code'] for row in rows}=={'SPX','IXIC','DJI'}

    def test_excludes_partial_current_bar_and_emits_cash_date(self):
        from data_provider.yfinance_fetcher import YfinanceFetcher
        fetcher=YfinanceFetcher.__new__(YfinanceFetcher);yf=MagicMock()
        yf.Ticker.return_value.history.return_value=history()
        result=fetcher._get_us_main_indices(yf,current_time=datetime(2026,11,23,10,tzinfo=timezone.utc))
        assert {row['code'] for row in result}=={'SPX','IXIC','DJI','VIX'}
        for row in result:
            if row['code']!='VIX':
                assert row['current']==102. and row['prev_close']==100.
                assert row['change_pct']==2. and row['cash_session_date']=='2026-11-20'
            else:assert row['observation_date']=='2026-11-23' and 'cash_session_date' not in row

    @pytest.mark.parametrize('frame',[
        history(labels=('2026-11-18','2026-11-19','2026-11-23')),
        history(labels=('2026-11-19','2026-11-20','2026-11-20')),
        history(closes=(100.,float('nan'),999.)),
        history(closes=(0.,102.,999.)),
    ])
    def test_missing_duplicate_invalid_bars_never_become_valid_cash_quote(self,frame):
        from data_provider.yfinance_fetcher import YfinanceFetcher
        fetcher=YfinanceFetcher.__new__(YfinanceFetcher);yf=MagicMock()
        yf.Ticker.return_value.history.return_value=frame
        result=fetcher._get_us_main_indices(yf,current_time=datetime(2026,11,23,10,tzinfo=timezone.utc))
        assert all(row['code']=='VIX' for row in (result or []))


class TestReportProvenance:
    def test_legacy_us_snapshot_cannot_be_compared_as_verified_cash_history(self):
        from src.services.market_light_service import _extract_region_snapshot
        snapshot={'trade_date':'2026-11-20'}
        context={'market_light_snapshots':{'us':snapshot,'cn':snapshot}}
        assert _extract_region_snapshot(context,'us') is None
        assert _extract_region_snapshot(context,'cn')==snapshot
        context['market_review_payload']={'region':'us','cash_session_date':'2026-11-20'}
        assert _extract_region_snapshot(context,'us')==snapshot
        context['market_review_payload']['cash_session_date']='2026-11-19'
        assert _extract_region_snapshot(context,'us') is None
        for bad in [None,'bad',{}, {'cash_session_date':None},{'cash_session_date':'not-a-date'}]:
            context['market_review_payload']={'markets':{'us':bad}}
            assert _extract_region_snapshot(context,'us') is None
            assert _extract_region_snapshot(context,'cn')==snapshot
        context['market_review_payload']={'markets':{'us':{'cash_session_date':'2026-11-20'}}}
        assert _extract_region_snapshot(context,'us')==snapshot

    def test_missing_cash_provenance_stays_partial_through_template_and_context(self):
        from src.market_analyzer import MarketOverview
        from src.core.market_review import _coerce_market_review_payload,_build_combined_market_review_payload
        from src.services.daily_market_context import _extract_summary
        analyzer=self.analyzer('en');overview=MarketOverview(date='2026-11-23')
        payload=analyzer.build_market_review_payload(overview,[],'### Market Summary\nRisk-on, position cap 90%.')
        assert payload['status']=='partial' and payload['warnings']==['us_cash_indices_unavailable']
        assert 'unavailable' in _extract_summary(payload,None) and '90%' not in _extract_summary(payload,None)
        coerced=_coerce_market_review_payload(SimpleNamespace(structured_payload=payload),region='us',report='[dsa-generation-status]: # (fallback_template)')
        assert set(coerced['warnings'])=={'us_cash_indices_unavailable','llm_template_fallback'}
        for payloads in [{'us':coerced},{'cn':{'region':'cn'},'us':coerced}]:
            combined=_build_combined_market_review_payload(review_report='synthetic',payloads=payloads,region=','.join(payloads),language='en',root_title='Synthetic')
            assert combined['status']=='partial'
            assert {'us_cash_indices_unavailable','llm_template_fallback'}<=set(combined['warnings'])

    def test_unverified_public_snapshot_does_not_issue_neutral_score(self):
        from src.market_analyzer import MarketOverview
        with pytest.raises(ValueError,match='unavailable'):
            self.analyzer().build_market_light_snapshot(MarketOverview(date='2026-11-23'))

    def test_payload_keeps_canonical_index_names_and_partial_warning(self):
        from src.market_analyzer import MarketIndex,MarketOverview
        analyzer=self.analyzer();overview=MarketOverview(date='2026-11-23')
        overview.cash_session_date='2026-11-20'
        index=MarketIndex(code='SPX',name='S&P 500',current=100.,change_pct=1.);index.cash_session_date='2026-11-20';overview.indices=[index]
        payload=analyzer.build_market_review_payload(overview,[],'### Review\nsynthetic')
        assert payload['indices'][0]['name']=='S&P 500'
        assert payload['status']=='partial' and 'cash_index_coverage_incomplete' in payload['warnings']

    def test_unavailable_equities_have_factual_summary_and_preserved_fallback_warning(self):
        from src.market_analyzer import MarketOverview
        from src.core.market_review import _coerce_market_review_payload
        analyzer=self.analyzer();overview=MarketOverview(date='2026-11-23')
        report=analyzer._inject_data_into_review('### Market Summary\nSynthetic high risk position cap 10%',overview)
        payload=analyzer.build_market_review_payload(overview,[],report)
        assert payload['status']=='partial' and 'us_cash_indices_unavailable' in payload['warnings']
        assert '不可用' in payload['summary'] and 'position cap' not in payload['summary']
        assert '不可用' in report.splitlines()[0]
        coerced=_coerce_market_review_payload(SimpleNamespace(structured_payload=payload),region='us',report='[dsa-generation-status]: # (fallback_template)\n'+report)
        assert {'us_cash_indices_unavailable','llm_template_fallback'}<=set(coerced['warnings'])

    @staticmethod
    def analyzer(language='zh'):
        from src.market_analyzer import MarketAnalyzer
        from src.core.market_profile import get_profile
        from src.core.market_strategy import get_market_strategy_blueprint
        analyzer=MarketAnalyzer.__new__(MarketAnalyzer)
        analyzer.region='us';analyzer.profile=get_profile('us')
        analyzer.strategy=get_market_strategy_blueprint('us')
        analyzer.config=SimpleNamespace(report_language=language)
        return analyzer

    @pytest.mark.parametrize('language',['zh','en'])
    def test_unverified_cash_rows_and_vix_do_not_create_market_signal(self,language):
        from src.market_analyzer import MarketIndex,MarketOverview
        analyzer=self.analyzer(language)
        overview=MarketOverview(date='2026-11-23',indices=[MarketIndex(code='SPX',name='synthetic',change_pct=9.),MarketIndex(code='VIX',name='synthetic VIX',change_pct=-50.)])
        assert analyzer._get_directional_index_changes(overview)==[]
        assert not analyzer._has_review_signal(overview)
        assert ('不可用' if language=='zh' else 'unavailable') in analyzer._build_stats_block(overview)

    @pytest.mark.parametrize('language',['zh','en'])
    def test_cash_date_and_separate_vix_observation_reach_report(self,language):
        from src.market_analyzer import MarketIndex,MarketOverview
        analyzer=self.analyzer(language)
        overview=MarketOverview(date='2026-11-23')
        overview.cash_session_date='2026-11-20';overview.data_collected_at='2026-11-23T10:00:00+00:00'
        equity=MarketIndex(code='SPX',name='synthetic',change_pct=2.);equity.cash_session_date='2026-11-20'
        vix=MarketIndex(code='VIX',name='synthetic VIX',current=15.,change_pct=-20.);vix.observation_date='2026-11-20'
        overview.indices=[equity,vix]
        payload=analyzer.build_market_review_payload(overview,[],'### Synthetic heading\nSynthetic body')
        assert payload['indices'][0]['name']=='synthetic'
        assert payload['status']=='partial' and payload['warnings']==['cash_index_coverage_incomplete']
        assert analyzer._get_directional_index_changes(overview)==[2.]
        block=analyzer._build_indices_block(overview)
        assert '2026-11-20' in block and '2026-11-23T10:00:00+00:00' in block
        assert ('精确报价时间未知' if language=='zh' else 'timestamp unavailable') in block
        assert ('不计入股指均值' if language=='zh' else 'excluded from equity averages') in block
        report=analyzer._inject_data_into_review('### Synthetic heading\nSynthetic body',overview)
        assert '2026-11-20' in report and 'Synthetic body' in report
