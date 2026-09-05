import json
import sys
import tempfile
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build
from feeds import parse_feed


def item(i, source='Other', new=True, published='2026-09-05T12:00:00+00:00'):
    return dict(key=str(i), title=f'Story {i}', url=f'https://example.com/{i}',
                source=source, feed_name=source, summary='Source summary.', via=None,
                published=published, new=new)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {'max_candidates': 35, 'item_targets': {'sport': 8}, 'interests': 'PSG first',
                    'source_preferences': {'sport': {'CulturePSG': {'candidate_slots': 8, 'min_new_items': 2}}}}

    def test_priority_survives_large_newer_google_pool(self):
        others = [item(i) for i in range(100)]
        priority = [item(100+i, 'CulturePSG', published='2026-09-05T08:00:00+00:00') for i in range(12)]
        candidates = build.select_candidates(others+priority, self.cfg, 'sport')
        self.assertEqual(len(candidates), 35)
        self.assertEqual(sum(x['source']=='CulturePSG' for x in candidates), 8)
        chosen = build.enforce_preferences([build.published_item(x) for x in others[:8]], candidates,
                                            self.cfg['source_preferences']['sport'], 8)
        self.assertEqual(len(chosen), 8)
        self.assertEqual(sum(x['source']=='CulturePSG' for x in chosen), 2)

    def test_old_priority_stories_are_not_forced_again(self):
        old = item(1, 'CulturePSG', new=False)
        fresh = item(2)
        chosen = build.enforce_preferences([fresh], [old, fresh], self.cfg['source_preferences']['sport'], 8)
        self.assertEqual(chosen, [fresh])

    def test_priority_quota_cannot_exceed_item_limit(self):
        candidates = [item(i, 'CulturePSG') for i in range(10)]
        chosen = build.enforce_preferences([], candidates, self.cfg['source_preferences']['sport'], 1)
        self.assertEqual(len(chosen), 1)

    def test_direct_priority_copy_wins_regardless_of_arrival(self):
        direct = item(1, 'CulturePSG')
        google = {**item(2, 'CulturePSG'), 'feed_name':'Team: PSG', 'via':'Google News', 'title':'Story 1 - CulturePSG'}
        for order in [[direct,google],[google,direct]]:
            self.assertEqual(build.dedupe(order, preferred=['CulturePSG']), [direct])

    def test_publisher_is_preserved_from_google_rss(self):
        rss = b'<rss><channel><item><title>PSG update</title><link>https://news.google.com/rss/articles/abc</link><source url="https://www.culturepsg.com">CulturePSG</source></item></channel></rss>'
        parsed = parse_feed(rss, 'Team: PSG')[0]
        self.assertEqual(parsed['source'], 'CulturePSG')
        self.assertEqual(parsed['feed_name'], 'Team: PSG')
        self.assertEqual(parsed['via'], 'Google News')

    def test_unsafe_feed_links_are_rejected(self):
        rss = b'<rss><channel><item><title>X</title><link>javascript:alert(1)</link></item></channel></rss>'
        self.assertEqual(build.dedupe(parse_feed(rss,'Test')), [])

    def test_only_valid_citations_and_unique_articles_are_published(self):
        candidates = [item(0),item(1)]
        response = {'items':[{'id':0},{'id':0},{'id':-1},{'id':True},{'id':99}],
                    'briefing':[{'text':'Supported bullet', 'source_ids':[0,0,-1,True,99]},
                                {'text':'Unsupported bullet','source_ids':[99]}, 'Uncited legacy text']}
        with patch.object(build,'call_claude',return_value=response):
            result = build.llm_section('news', candidates, self.cfg, 'fake')
        self.assertEqual(len(result['items']),1)
        self.assertEqual(len(result['briefing']),1)
        self.assertEqual(len(result['briefing'][0]['sources']),1)
        self.assertEqual(result['reviewed_count'],2)

    def test_failed_feed_can_use_fallback_and_reports_it(self):
        fetcher = build.Fetcher(None)
        rss = b'<rss><channel><item><title>Finance</title><link>https://news.google.com/rss/articles/abc</link><source>Les Echos</source></item></channel></rss>'
        with patch.object(fetcher,'get',side_effect=[None,rss]):
            items = build.fetch_all(fetcher,[{'name':'Les Echos','url':'https://example.com/rss','fallback_query':'site:lesechos.fr'}], 'finance')
        self.assertEqual(len(items),1)
        self.assertEqual(fetcher.feed_health[0]['status'],'fallback')
        self.assertEqual(fetcher.feed_health[0]['section'],'finance')

    def test_broken_feed_is_reported_instead_of_silent_empty(self):
        fetcher = build.Fetcher(None)
        with patch.object(fetcher,'get',return_value=b'<html>Not RSS</html>'):
            self.assertEqual(build.fetch_all(fetcher,[{'name':'Test','url':'https://example.com/rss'}]),[])
        self.assertEqual(fetcher.feed_health[0]['status'],'unavailable')

    def test_valid_empty_feed_is_available_not_broken(self):
        fetcher = build.Fetcher(None)
        with patch.object(fetcher,'get',return_value=b'<rss><channel></channel></rss>'):
            self.assertEqual(build.fetch_all(fetcher,[{'name':'Test','url':'https://example.com/rss'}]),[])
        self.assertEqual(fetcher.feed_health[0]['status'],'empty')


class HistoryTests(unittest.TestCase):
    def test_legacy_seen_migration_keeps_only_published_articles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); past=root/'past'; past.mkdir()
            (root/'seen.json').write_text(json.dumps({'published':'2026-09-04','never-selected':'2026-09-04'}))
            (root/'briefing.json').write_text(json.dumps({'generated_at':'2026-09-05T07:00:00+00:00','sections':{'news':{'items':[{'key':'published'}]}}}))
            with patch.multiple(build, SEEN_PATH=root/'seen.json', OUT_PATH=root/'briefing.json', PAST_DIR=past):
                seen=build.load_seen()
                self.assertEqual(set(seen),{'published'})
                build.save_seen(seen,datetime(2026,9,5,tzinfo=timezone.utc))
                self.assertEqual(build.load_seen(),seen)
                self.assertEqual(json.loads((root/'seen.json').read_text())['version'],2)

    def test_offline_pipeline_tracks_only_displayed_items(self):
        repo=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=repo) as tmp:
            root=Path(tmp); past=root/'past'
            fixtures=root/'fixtures'
            subprocess.run([sys.executable,str(repo/'tests/make_fixtures.py'),'--output-dir',str(fixtures)],check=True,capture_output=True)
            paths=dict(DATA_DIR=root, PAST_DIR=past, SEEN_PATH=root/'seen.json', OUT_PATH=root/'briefing.json')
            argv=['build.py','--mock','--fixtures',str(fixtures),'--now','2026-09-04T17:00:00+00:00']
            with patch.multiple(build,**paths),patch.object(sys,'argv',argv):
                build.main()
                result=json.loads((root/'briefing.json').read_text())
                selected={it['key'] for s in result['sections'].values() for it in s['items']}
                self.assertEqual(set(build.load_seen()),selected)
                self.assertEqual(result['schedule']['hours'],[7,13,19])
                self.assertTrue(result['feed_health'])
                self.assertTrue(all(f['status']=='ok' for f in result['feed_health']))
                self.assertTrue(all(s.get('reviewed_count',0)==0 for s in result['sections'].values()))
                self.assertTrue(all(s['items'] for s in result['sections'].values()))

if __name__ == '__main__':
    unittest.main()
