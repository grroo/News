"""T09 integration regressions. Provider behavior remains offline."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from section_cache import fingerprint
from usage_ledger import ledger, record_run
import selection


class ReleaseTests(unittest.TestCase):
    def test_missing_key_guard_exits_nonzero_without_publishing(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'output'
            env = {key:value for key,value in os.environ.items() if key not in {
                'ANTHROPIC_API_KEY','OPENAI_API_KEY','SCHEDULED_SLOT','DEPLOY_ONLY','MOCK_REQUESTED',
                'REQUIRE_RESERVATION','REQUEST_ID','RESERVATION_ID','GENERATE_REQUESTED'}}
            env.update(GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_OUTPUT=str(output))
            proc = subprocess.run([sys.executable, str(ROOT/'scripts/check_slot.py')], env=env, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn('Missing ANTHROPIC_API_KEY', proc.stdout)
            self.assertIn('publish=false', output.read_text())

    def test_editorial_order_mode_version_and_policy_invalidate_cache(self):
        items = [{'key': k, 'title': k, 'summary': 'text'} for k in ['a', 'b']]
        cfg = {'editorial_selection': True}
        base = fingerprint('news', items, cfg)
        self.assertNotEqual(base, fingerprint('news', items[::-1], cfg))
        self.assertNotEqual(base, fingerprint('news', items, {'editorial_selection': False}))
        self.assertNotEqual(base, fingerprint('news', items, {**cfg, 'selection': {'publisher_cap': 2}}))
        with patch.object(selection, 'SELECTION_VERSION', 'next'):
            self.assertNotEqual(base, fingerprint('news', items, cfg))
        self.assertEqual(base, fingerprint('news', [{**i, 'new': False} for i in items], cfg))
        # Legacy same-set new-flag reordering is publication bookkeeping (T04).
        self.assertEqual(fingerprint('news', items, {}), fingerprint('news', items[::-1], {}))

    def test_safe_release_defaults_and_workflow_interfaces(self):
        cfg = yaml.safe_load((ROOT/'config.yml').read_text())
        self.assertEqual((cfg['provider'], cfg['model']), ('anthropic', 'claude-haiku-4-5'))
        self.assertIs(cfg['editorial_selection'], False)
        workflow = yaml.safe_load((ROOT/'.github/workflows/build.yml').read_text())
        self.assertIn('inputs.request_id', workflow['run-name'])
        steps = workflow['jobs']['build']['steps']
        guard = next(s for s in steps if s.get('id') == 'slot')
        self.assertIn('OPENAI_API_KEY', guard['env'])
        for file, job in [('build.yml', 'build'), ('deploy-pages.yml', 'deploy')]:
            steps = yaml.safe_load((ROOT/'.github/workflows'/file).read_text())['jobs'][job]['steps']
            checkout = next(s for s in steps if s.get('uses', '').startswith('actions/checkout@'))
            self.assertEqual(checkout['with']['ref'], 'main')
            assemble = next(s['run'] for s in steps if 'Assemble' in s.get('name', ''))
            for private in ['seen.json', 'feed-cache.json', 'section-cache.json', 'usage-history.json']:
                self.assertIn('_site/data/'+private, assemble)

    def test_usage_history_survives_no_change_deduplicates_and_marks_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'history.json'
            known = ledger([{'model': 'claude-haiku-4-5', 'attempts': 1, 'input_tokens': 100, 'output_tokens': 50}])
            first = record_run(path, '1:1', '2026-09-23T17:00:00Z', known)
            self.assertEqual(first['known_cost_usd'], 0.00035)
            self.assertEqual(record_run(path, '1:1', '2026-09-23T17:00:00Z', known), first)
            record_run(path, '2:1', '2026-09-23T18:00:00Z', ledger([]))
            last = record_run(path, '3:1', '2026-09-23T19:00:00Z', ledger([{'model':'claude-haiku-4-5','attempts':2,'unknown':True}]))
            self.assertEqual(last['recorded_runs'], 3)
            self.assertEqual(last['unknown_cost_runs'], 1)
            self.assertEqual(last['known_cost_usd'], first['known_cost_usd'])
            self.assertEqual(last['attempts'], 3)

    def test_changed_feed_regenerates_only_affected_section_and_stale_run_cannot_replace(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root = Path(tmp)
            subprocess.run([sys.executable, str(ROOT/'tests/make_fixtures.py'), '--output-dir', str(root/'fixtures')], check=True, capture_output=True)
            def run(hour, **extra):
                spec = {'root':str(root), 'now': f'2026-09-04T{hour}:00:00Z',
                        'inputs':{'request_id':f'req-{hour}', 'reservation_id':'res-test'}, **extra}
                done = subprocess.run([sys.executable, str(ROOT/'tests/release/build_driver.py')], input=json.dumps(spec), text=True, capture_output=True, check=True)
                return json.loads(done.stdout)
            first = run('17')
            self.assertEqual(len(first['calls']), 3)
            again = run('18')
            self.assertEqual(again['calls'], [])
            self.assertEqual(again['edition']['edition_id'], first['edition']['edition_id'])
            feed = root/'fixtures/f001.xml'
            feed.write_text(feed.read_text().replace('</title>', ' changed</title>'))
            changed = run('19')
            self.assertEqual(changed['calls'], ['SECTION: news'])
            self.assertNotEqual(changed['edition']['edition_id'], first['edition']['edition_id'])
            before = (root/'briefing.json').read_bytes()
            old = run('16')
            self.assertEqual(old['calls'], [])
            self.assertEqual((root/'briefing.json').read_bytes(), before)
            retry = run('20', inputs={'deploy_only': True})
            self.assertEqual(retry['calls'], [])
            self.assertFalse(retry['allowed'])
            self.assertTrue(retry['publish'])
            missing = run('21', missing_key=True)
            self.assertFalse(missing['allowed'])
            self.assertFalse(missing['publish'])
            self.assertEqual((root/'briefing.json').read_bytes(), before)
            partial = run('22', fail_sport=True, config_changes={'prompt_version': 2})
            self.assertEqual(partial['calls'].count('SECTION: sport'), 2)
            self.assertEqual(partial['edition']['quality']['overall'], 'degraded')
            self.assertEqual(partial['edition']['sections']['sport']['last_success_at'],
                             first['edition']['sections']['sport']['last_success_at'])
            self.assertEqual(partial['edition']['sections']['news']['state'], 'healthy')
