"""Negative checks for the fork's upstream-sync safety gate."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('retained_fix_policy',ROOT/'scripts/check_retained_fixes.py')
policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)

class RetainedFixPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for name in ['docs/retained-fixes.json','.github/workflows/00-daily-analysis.yml']:
            target=self.root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
        self.registry=json.loads((self.root/'docs/retained-fixes.json').read_text())
        for item in self.registry['fixes']:
            for name in item['tests']:
                target=self.root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('# synthetic contract fixture')
    def save(self):
        (self.root/'docs/retained-fixes.json').write_text(json.dumps(self.registry))
    def test_current_manifest_is_valid(self):self.assertTrue(policy.collect_tests(self.root))
    def test_removing_an_unresolved_contract_is_rejected(self):
        self.registry['fixes'].pop(0);self.save()
        with self.assertRaises(ValueError):policy.collect_tests(self.root)
    def test_swapping_a_regression_for_an_unrelated_test_is_rejected(self):
        self.registry['fixes'][0]['tests']=['tests/unrelated.py'];(self.root/'tests/unrelated.py').write_text('pass');self.save()
        with self.assertRaises(ValueError):policy.collect_tests(self.root)
    def test_reindented_sender_cron_is_rejected(self):
        (self.root/'.github/workflows/00-daily-analysis.yml').write_text("name: sender\non:\n    schedule:\n      - cron: '0 10 * * *'\n")
        with self.assertRaises(ValueError):policy.collect_tests(self.root)
    def test_upstream_verified_still_needs_evidence_and_tests(self):
        self.registry['fixes'][0]['status']='upstream_verified';self.save()
        with self.assertRaises(ValueError):policy.collect_tests(self.root)
        self.registry['fixes'][0]['upstream_resolution']={'url':'https://github.com/example/project/commit/'+'a'*40,'commit':'a'*40,'verification':'semantic regressions passed'};self.save()
        self.assertTrue(policy.collect_tests(self.root))

if __name__=='__main__':unittest.main()
