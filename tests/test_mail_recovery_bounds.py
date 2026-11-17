import ast
import pathlib
import signal
import sys
import time
import types
import unittest
from contextlib import contextmanager
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]

def extract(file,name,scope):
    tree=ast.parse((ROOT/file).read_text(encoding='utf-8'))
    nodes=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name]
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),nodes[0]],type_ignores=[])
    exec(compile(ast.fix_missing_locations(module),str(ROOT/file),'exec'),scope)
    return scope[name]

class RecoveryTests(unittest.TestCase):
    def test_no_analyzer_template_is_partial(self):
        self._template_case(None)

    def test_all_models_failed_template_is_partial(self):
        self._template_case(types.SimpleNamespace(is_available=lambda:True))

    def _template_case(self,analyzer):
        scope={'logger':types.SimpleNamespace(info=lambda *a:None,warning=lambda *a:None),
               'record_llm_run':lambda **k:None,'record_llm_run_started':lambda **k:None,'time':time}
        fn=extract('src/market_analyzer.py','generate_market_review',scope)
        obj=types.SimpleNamespace(analyzer=analyzer,_get_analyzer_generation_backend_config_error=lambda:None,
            _log_context=lambda:'region=cn',_generate_template_review=lambda *a:'synthetic template',
            _build_review_prompt=lambda *a:'synthetic prompt',_get_analyzer_generation_backend_identity=lambda:('synthetic','model'),
            _resolve_recorded_provider=lambda **k:'synthetic',
            _generate_market_review_with_metadata=lambda *a,**k:types.SimpleNamespace(text=None,diagnostics={'reason':'all_models_failed'},model='model',provider='synthetic',backend='synthetic',usage=None))
        report=fn(obj,None,[])
        self.assertIn('[dsa-generation-status]: # (fallback_template)',report)
        coerce=extract('src/core/market_review.py','_coerce_market_review_payload',{'MARKET_REVIEW_REPORT_TYPE':'market_review'})
        payload=coerce(types.SimpleNamespace(structured_payload={'version':1}),region='cn',report=report)
        self.assertEqual(payload['status'],'partial')
    @unittest.skipUnless(hasattr(signal,'setitimer'),'Linux native deadline')
    def test_actual_short_outer_deadline_propagates(self):
        from src.market_deadline import market_deadline, run_market_region_bounded, MarketDeadlineExceeded
        with self.assertRaises(MarketDeadlineExceeded):
            with market_deadline(.03,'market review'):
                run_market_region_bounded(lambda:time.sleep(2),region='us',seconds=.5)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.,0.))

    @unittest.skipUnless(hasattr(signal,'setitimer'),'Linux native deadline')
    def test_slow_checkpoint_is_bounded_and_next_region_runs(self):
        from src import market_deadline as d
        original=d.market_deadline
        def short(seconds,stage):return original(.03,stage)
        with patch.object(d,'market_deadline',short):
            self.assertIsNone(d.run_market_checkpoint(lambda:time.sleep(2),region='cn'))
        self.assertEqual(d.run_market_region_bounded(lambda:'us-ok',region='us'),'us-ok')
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.,0.))
    def test_region_must_not_swallow_enclosing_deadline(self):
        from src import market_deadline
        outer=market_deadline.MarketDeadlineExceeded('synthetic outer')
        outer.stage='market review'
        @contextmanager
        def expired(*args):
            raise outer
            yield
        with patch.object(market_deadline,'market_deadline',expired):
            with self.assertRaises(market_deadline.MarketDeadlineExceeded):
                market_deadline.run_market_region_bounded(lambda:None,region='us')
    def test_chip_call_is_bounded_and_timeout_degrades(self):
        calls=[];api=object()
        def bounded(func,**kw):
            calls.append((func,kw));raise TimeoutError('synthetic timeout')
        scope={'_is_us_code':lambda c:False,'_is_hk_code':lambda c:False,'_is_etf_code':lambda c:False,
               '_akshare_call_with_timeout':bounded,'_AKSHARE_CHIP_CALL_TIMEOUT':30.0,'logger':types.SimpleNamespace(info=lambda *a:None,error=lambda *a:None,warning=lambda *a:None)}
        method=extract('data_provider/akshare_fetcher.py','get_chip_distribution',scope)
        dummy=types.SimpleNamespace(_set_random_user_agent=lambda:None,_enforce_rate_limit=lambda:None)
        with patch.dict(sys.modules,{'akshare':types.SimpleNamespace(stock_cyq_em=api)}):
            self.assertIsNone(method(dummy,'synthetic'))
        self.assertEqual(len(calls),1)
        self.assertIs(calls[0][0],api)
        self.assertEqual(calls[0][1]['timeout'],30)
        self.assertEqual(calls[0][1]['call_name'],'ak.stock_cyq_em')

    @unittest.skipUnless(hasattr(signal,'setitimer'),'Linux native deadline')
    def test_region_timeout_cannot_starve_following_region(self):
        from src.market_deadline import run_market_region_bounded
        from src import market_deadline
        handler=signal.getsignal(signal.SIGALRM)
        with patch.object(market_deadline,'MARKET_REGION_TIMEOUT_SECONDS',0.03):
            result=run_market_region_bounded(lambda:time.sleep(2),region='cn')
            self.assertIn('未完成',result.report)
            self.assertEqual(run_market_region_bounded(lambda:'hk-ok',region='hk'),'hk-ok')
        self.assertEqual(signal.getsignal(signal.SIGALRM),handler)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.,0.))

    def test_region_provider_error_is_explicit_not_success(self):
        from src.market_deadline import run_market_region_bounded
        def fail():raise ValueError('synthetic provider failure')
        result=run_market_region_bounded(fail,region='us')
        self.assertIn('未完成',result.report)
        self.assertEqual(result.structured_payload['status'],'partial')

    def test_partial_context_is_not_good100(self):
        scope={'datetime':__import__('datetime').datetime,'MARKET_REVIEW_REPORT_TYPE':'market_review','MARKET_REVIEW_HISTORY_CODE':'market'}
        fn=extract('src/core/market_review.py','_build_market_review_context_overview',scope)
        value=fn(region='cn',report_language='zh',diagnostic_snapshot=None,partial=True)
        self.assertEqual(value['blocks'][0]['status'],'partial')
        self.assertNotEqual(value['data_quality']['level'],'good')
        self.assertLess(value['data_quality']['overall_score'],100)

    def test_checkpoint_file_failure_does_not_drop_other_markets(self):
        self._checkpoint_case(fail_file=True)

    def test_each_completed_market_is_checkpointed_before_next(self):
        self._checkpoint_case()

    def _checkpoint_case(self,fail_file=False):
        writes=[];persisted=[];entered=[]
        def reveal(*args):raise
        class Analyzer:
            def __init__(self,**kw):self.region=kw['region']
            def run_daily_review_with_snapshot(self):
                if entered:
                    self_test.assertIn(entered[-1],persisted)
                entered.append(self.region)
                return types.SimpleNamespace(report=self.region+' review')
        self_test=self
        scope={'logger':types.SimpleNamespace(info=lambda *a:None,warning=lambda *a:None,exception=reveal),
          '_get_market_review_text':lambda l:{'root_title':'root','separator':'next','cn_title':'cn','hk_title':'hk','us_title':'us'},
          '_resolve_market_review_regions':lambda r:r.split(','),'_MARKET_REVIEW_MARKETS':[('cn','cn_title','cn'),('hk','hk_title','hk'),('us','us_title','us')],
          'MarketAnalyzer':Analyzer,'_collect_market_light_snapshot':lambda *a,**k:None,
          '_coerce_market_review_payload':lambda *a,**k:{'markdown_report':k['report']},
          '_persist_market_review_history':lambda **k:persisted.append(k['region']),
          '_build_combined_market_review_payload':lambda **k:{'markdown_report':k['review_report']},
          '_render_market_review_payload_markdown':lambda payload,**k:payload['markdown_report'],
          '_render_market_review_merge_markdown':lambda payload,**k:k['review_report'],
          '_record_market_review_notification_run':lambda **k:None,
          'datetime':__import__('datetime').datetime,'GenerationError':type('GenerationError',(Exception,),{})}
        fn=extract('src/core/market_review.py','run_market_review',scope)
        def save(text,name):
            writes.append(name)
            if fail_file and name.startswith('market_review_cn_'):raise OSError('synthetic disk failure')
            return name
        notifier=types.SimpleNamespace(save_report_to_file=save)
        result=fn(notifier,config=types.SimpleNamespace(market_review_region='cn,hk,us',report_language='zh'),send_notification=False,merge_notification=True,query_id='synthetic')
        self.assertTrue(result)
        self.assertEqual(persisted,['cn','hk','us','cn,hk,us'])
        self.assertEqual(len(writes),4)

if __name__=='__main__':unittest.main()
