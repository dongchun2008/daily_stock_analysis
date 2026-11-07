"""Verify the actual shared-lock boundary keeps the deployment review guard."""
import ast
import pathlib
import sys
import signal
import time
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def blocked_provider():
    time.sleep(60)


def successful_provider():
    import pandas as pd
    return pd.DataFrame({'代码': ['600000', '000001'], '名称': ['浦发银行', '平安银行'],
                         '最新价': [11., 9.], '昨收': [10., 10.], '成交额': [100_000_000, 200_000_000]})


class OuterGuardTests(unittest.TestCase):
    def test_shared_lock_calls_bounded_review_and_always_releases(self):
        path = pathlib.Path(__file__).resolve().parents[1] / 'main.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_run_market_review_with_shared_lock')
        module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), func], type_ignores=[])
        calls = []
        lock = SimpleNamespace(try_acquire_market_review_lock=lambda config: 'token', release_market_review_lock=lambda token: calls.append(('release', token)))
        def bounded(review, **kwargs):
            calls.append(('bounded', kwargs['config']))
            return 'bounded result'
        scope = {'logger': SimpleNamespace(warning=lambda *args: None)}
        exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), scope)
        with patch.dict(sys.modules, {'src.core.market_review_lock': lock, 'src.market_deadline': SimpleNamespace(run_bounded_market_review=bounded)}):
            result = scope[func.name]('config', lambda **kwargs: 'unbounded result')
        self.assertEqual(result, 'bounded result')
        self.assertEqual(calls, [('bounded', 'config'), ('release', 'token')])

    @unittest.skipUnless(hasattr(signal, 'setitimer'), 'native Linux signal acceptance')
    def test_outer_alarm_reaps_inner_worker_and_releases_lock(self):
        import multiprocessing
        from data_provider.akshare_fetcher import _akshare_call_with_timeout
        from src import market_deadline
        from src.core.market_review_lock import try_acquire_market_review_lock, release_market_review_lock
        path = pathlib.Path(__file__).resolve().parents[1] / 'main.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_run_market_review_with_shared_lock')
        module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), func], type_ignores=[])
        scope = {'logger': SimpleNamespace(warning=lambda *args: None)}
        exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), scope)
        def review(**kwargs):
            return _akshare_call_with_timeout(blocked_provider, timeout=5, call_name='outer-interrupt')
        started = time.monotonic()
        original_handler = signal.getsignal(signal.SIGALRM)
        with tempfile.TemporaryDirectory() as directory, patch.object(market_deadline, 'MARKET_REVIEW_TIMEOUT_SECONDS', .3):
            config = SimpleNamespace(database_path=str(pathlib.Path(directory) / 'copy.db'))
            result = scope[func.name](config, review)
            self.assertIn('大盘复盘未完成', result)
            token = try_acquire_market_review_lock(config)
            self.assertIsNotNone(token)
            self.assertTrue(token.uses_flock)
            release_market_review_lock(token)
            self.assertEqual(scope[func.name](config, lambda **kwargs: 'recovered'), 'recovered')
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(signal.getsignal(signal.SIGALRM), original_handler)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0., 0.))
        self.assertFalse([p for p in multiprocessing.active_children() if p.name == 'akshare-outer-interrupt'])

    @unittest.skipUnless(hasattr(signal, 'setitimer'), 'native Linux provider acceptance')
    def test_native_eastmoney_timeout_then_sina_success_has_no_worker_leak(self):
        import multiprocessing
        from data_provider.akshare_fetcher import AkshareFetcher
        fetcher = AkshareFetcher(sleep_min=0, sleep_max=0)
        fetcher._market_stats_call_timeout = 30
        with patch.dict(sys.modules, {'akshare': SimpleNamespace(stock_zh_a_spot_em=blocked_provider, stock_zh_a_spot=successful_provider)}):
            result = fetcher.get_market_stats()
        self.assertEqual(result['up_count'], 1)
        self.assertEqual(result['down_count'], 1)
        self.assertEqual(result['total_amount'], 3.)
        self.assertFalse([p for p in multiprocessing.active_children() if p.name.startswith('akshare-')])


if __name__ == '__main__':
    unittest.main()
