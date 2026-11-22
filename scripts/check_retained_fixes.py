"""Run durable semantic contracts before merging upstream into this fork."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import yaml

ROOT=Path(__file__).resolve().parents[1]
REQUIRED_CONTRACTS={
    'legacy-realtime-volume-shares':{'tests/test_mail_data_integrity.py'},
    'us-completed-session-provenance':{'tests/test_mail_data_integrity.py'},
    'bounded-market-work-and-checkpoints':{'tests/test_mail_recovery_bounds.py','tests/test_market_review_outer_guard.py'},
    'truthful-missing-market-statistics':{'tests/test_market_stats_missing.py'},
    'unused-feishu-sdk-lazy-loading':{'tests/test_feishu_sdk_lazy_loading.py'},
}


def collect_tests(root=ROOT):
    data=json.loads((root/'docs/retained-fixes.json').read_text(encoding='utf-8'))
    if data.get('version')!=1 or not data.get('fixes'):
        raise ValueError('Retained-fix registry missing or unsupported')
    tests=set();seen=set()
    for item in data['fixes']:
        if not item.get('id') or item['id'] in seen:
            raise ValueError('Retained-fix IDs must be unique and nonempty')
        seen.add(item['id'])
        if not REQUIRED_CONTRACTS.get(item['id'],set())<=set(item.get('tests',[])):
            raise ValueError('Required semantic regression binding was removed')
        if item.get('status') not in {'retained','upstream_verified'}:
            raise ValueError('Unknown retained-fix status')
        if item['status']=='upstream_verified':
            evidence=item.get('upstream_resolution') or {}
            if not all(evidence.get(key) for key in ['url','commit','verification']):
                raise ValueError('Retiring a patch requires upstream evidence and verification')
        if not item.get('tests'):
            raise ValueError('Semantic tests remain required after an upstream fix')
        for name in item['tests']:
            path=Path(name)
            if path.is_absolute() or '..' in path.parts or not name.startswith('tests/') or not (root/path).is_file():
                raise ValueError('Retained semantic test missing or outside tests/')
            tests.add(name)
    if not REQUIRED_CONTRACTS.keys()<=seen:
        raise ValueError('Retained contract was removed; retirement must preserve the entry and tests')
    workflow=yaml.load((root/'.github/workflows/00-daily-analysis.yml').read_text(encoding='utf-8'),Loader=yaml.BaseLoader)
    triggers=workflow.get('on') if isinstance(workflow,dict) else None
    if (isinstance(triggers,dict) and 'schedule' in triggers) or (isinstance(triggers,(str,list)) and 'schedule' in triggers):
        raise ValueError('Daily GitHub sender schedule must remain disabled for this VPS-managed fork')
    return sorted(tests)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--list',action='store_true');args=parser.parse_args()
    tests=collect_tests()
    if args.list:
        print('\n'.join(tests));return 0
    return subprocess.run([sys.executable,'-m','pytest','-q','-o','addopts=','--timeout=120','--timeout-method=thread',*tests],cwd=ROOT).returncode


if __name__=='__main__':raise SystemExit(main())
