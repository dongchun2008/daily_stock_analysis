"""Offline checks of the actual prompt/report methods, without provider imports."""
import ast
import pathlib
from types import SimpleNamespace
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def extract(name):
    path = ROOT / 'src/market_analyzer.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MarketAnalyzer')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), method], type_ignores=[])
    scope = {}
    exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), scope)
    return scope[name], method


def fixture(method, language, up=0, down=0, flat=0, amount=0):
    analyzer = SimpleNamespace(region='cn', profile=SimpleNamespace(has_market_stats=True, has_sector_rankings=True))
    for node in ast.walk(method):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'self':
            name = node.func.attr
            value = language if name in {'_get_review_language', '_get_output_language'} else ''
            setattr(analyzer, name, lambda *args, value=value: value)
    overview = SimpleNamespace(date='2026-11-06', indices=[], top_sectors=[], bottom_sectors=[], top_concepts=[], bottom_concepts=[], up_count=up, down_count=down, flat_count=flat, limit_up_count=0, limit_down_count=0, total_amount=amount)
    return analyzer, overview


class MissingStatisticsTests(unittest.TestCase):
    def test_final_report_keeps_statistics_notice_with_unexpected_heading(self):
        inject, _ = extract('_inject_data_into_review')
        insert, _ = extract('_insert_after_section')
        stats, method = extract('_build_stats_block')
        for language in ('zh', 'en'):
            with self.subTest(language=language):
                analyzer, overview = fixture(method, language)
                analyzer._get_review_language = lambda: language
                analyzer._build_stats_block = lambda ov: stats(analyzer, ov)
                analyzer._build_indices_block = lambda ov: ''
                analyzer._build_sector_block = lambda ov: ''
                analyzer._insert_after_section = insert
                inject.__globals__.update(_CHINESE_SECTION_PATTERNS={'market_summary': '### 市场总结'},
                                          _ENGLISH_SECTION_PATTERNS={'market_summary': '### Market Summary'})
                result = inject(analyzer, '### 今日概览\nSynthetic review.', overview)
                self.assertIn('统计未获取成功' if language == 'zh' else 'statistics unavailable', result)
                self.assertIn('Synthetic review.', result)

    def test_missing_stats_are_explicit_in_prompt_without_factual_zeros(self):
        func, method = extract('_build_review_prompt')
        for language in ('zh', 'en'):
            with self.subTest(language=language):
                analyzer, overview = fixture(method, language)
                prompt = func(analyzer, overview, [])
                self.assertNotIn('上涨: 0 家' if language == 'zh' else 'Advancers: 0', prompt)
                self.assertNotIn('两市成交额: 0 亿元' if language == 'zh' else 'Turnover: 0', prompt)
                self.assertIn('统计未获取成功' if language == 'zh' else 'statistics unavailable', prompt)

    def test_valid_stats_still_reach_prompt_including_flat_only_market(self):
        func, method = extract('_build_review_prompt')
        for up, down, flat in ((3, 2, 1), (0, 0, 6)):
            for language in ('zh', 'en'):
                with self.subTest(language=language, up=up):
                    analyzer, overview = fixture(method, language, up=up, down=down, flat=flat, amount=42)
                    prompt = func(analyzer, overview, [])
                    self.assertIn(f'上涨: {up} 家' if language == 'zh' else f'Advancers: {up}', prompt)
                    self.assertNotIn('统计未获取成功' if language == 'zh' else 'statistics unavailable', prompt)

    def test_report_block_explicitly_marks_missing_statistics(self):
        func, method = extract('_build_stats_block')
        for language in ('zh', 'en'):
            with self.subTest(language=language):
                analyzer, overview = fixture(method, language)
                report = func(analyzer, overview)
                self.assertIn('统计未获取成功' if language == 'zh' else 'statistics unavailable', report)
                self.assertNotIn('Advancers 0', report)


if __name__ == '__main__':
    unittest.main()
