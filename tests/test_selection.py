import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import selection


def item(i, source="Other", title=None, summary="", published="2026-09-05T12:00:00+00:00", new=True, via=None, feed_name=None):
    return {
        "key": f"k{i}",
        "title": title or f"Story {i}",
        "url": f"https://example.com/{i}",
        "source": source,
        "feed_name": feed_name or source,
        "summary": summary,
        "via": via,
        "published": published,
        "new": new,
    }


INTERESTS = """
For News: geopolitics, France, Italy, the EU, central banks, AI, insurance and Zurich.
For Sport: PSG, Ligue 1, Champions League, France rugby, Counter-Strike and Vitality.
For Finance: equities, rates, the ECB and Fed, credit, FX, insurance and Zurich. Crypto only if notable.
"""


class LegacySelectionTests(unittest.TestCase):
    def test_flag_off_keeps_recency_cutoff_and_culturepsg_slots(self):
        cfg = {
            "max_candidates": 35,
            "editorial_selection": False,
            "source_preferences": {"sport": {"CulturePSG": {"candidate_slots": 8, "min_new_items": 2}}},
        }
        others = [item(i, published=f"2026-09-05T{12 + (i % 7):02d}:00:00+00:00") for i in range(100)]
        priority = [item(100 + i, "CulturePSG", published="2026-09-05T08:00:00+00:00") for i in range(12)]
        picked = selection.select_candidates(others + priority, cfg, "sport")
        self.assertEqual([row["key"] for row in picked], [row["key"] for row in selection._select_legacy(others + priority, cfg, "sport")])
        self.assertEqual(sum(row["source"] == "CulturePSG" for row in picked), 8)
        self.assertNotIn("selection_reason", picked[0])


class EditorialSelectionTests(unittest.TestCase):
    def cfg(self, **extra):
        base = {
            "max_candidates": 35,
            "editorial_selection": True,
            "interests": INTERESTS,
            "selection": {"publisher_cap": 6, "min_topic_slots": 8, "excerpt_chars": 140},
            "source_preferences": {"sport": {"CulturePSG": {"candidate_slots": 8, "min_new_items": 2}}},
        }
        base.update(extra)
        return base

    def test_older_relevant_stories_survive_a_high_volume_feed(self):
        noise = [
            item(i, f"Wire {i % 40}", title=f"Celebrity outing {i}", published=f"2026-09-05T18:{i % 60:02d}:00+00:00")
            for i in range(400)
        ]
        important = [
            item(900 + i, "Le Monde", title=title, summary="A markets-moving development.", published="2026-09-04T09:00:00+00:00")
            for i, title in enumerate((
                "France and Italy press the EU on insurance rules",
                "ECB holds rates as Zurich outlines reinsurance demand",
                "Geopolitics: central banks watch the European Union",
            ))
        ]
        legacy = selection._select_legacy(noise + important, self.cfg(), "news")
        editorial = selection.select_candidates(noise + important, self.cfg(), "news")
        legacy_keys = {row["key"] for row in legacy}
        editorial_keys = {row["key"] for row in editorial}
        self.assertTrue(all(row["key"] not in legacy_keys for row in important))
        self.assertTrue(all(row["key"] in editorial_keys for row in important))
        self.assertGreater(
            sum(row.get("selection_reason", "").startswith("topic:") and not row["selection_reason"].startswith("topic:0") for row in editorial),
            sum((row.get("title") or "").startswith("France") or "ECB" in (row.get("title") or "") or "Geopolitics" in (row.get("title") or "") for row in legacy),
        )
        self.assertEqual(len(editorial), 35)

    def test_publisher_aliases_share_one_cap_without_banning_ft(self):
        stories = []
        for i, source in enumerate(("FT", "Financial Times", "ft.com") * 12):
            stories.append(item(
                i, source, title=f"ECB rates move {i} for insurers",
                summary="Credit and FX context.", published=f"2026-09-05T10:{i % 60:02d}:00+00:00",
            ))
        stories += [
            item(200 + i, "Reuters", title=f"Fed credit markets note {i}", published="2026-09-05T11:00:00+00:00")
            for i in range(10)
        ]
        picked = selection.select_candidates(stories, self.cfg(), "finance")
        ft = [row for row in picked if selection.normalize_publisher(row) == "financial times"]
        self.assertEqual(len(ft), 6)
        self.assertGreater(len(ft), 0)
        self.assertTrue(any(row["source"] == "Reuters" for row in picked))
        self.assertLessEqual(max(len(row["summary"]) for row in picked), 140)

    def test_sport_preferences_hold_and_google_copy_does_not_replace_the_direct_story(self):
        gossip = [item(i, f"Desk {i % 12}", title=f"Transfer gossip {i}", published="2026-09-05T20:00:00+00:00") for i in range(40)]
        notebooks = [item(50 + i, "CulturePSG", title=f"CulturePSG notebook {i}", published="2026-09-05T08:00:00+00:00") for i in range(12)]
        reserved = selection.select_candidates(gossip + notebooks, self.cfg(), "sport")
        self.assertEqual(sum(row["source"] == "CulturePSG" for row in reserved), 8)
        direct = item(1, "CulturePSG", title="PSG beat Marseille 2-1", published="2026-09-05T08:00:00+00:00")
        google = item(
            2, "CulturePSG", title="PSG beat Marseille 2-1 - CulturePSG", via="Google News",
            feed_name="Team: PSG", published="2026-09-05T08:05:00+00:00",
        )
        follow = item(
            3, "CulturePSG", title="Hakimi injured after PSG beat Marseille 2-1",
            published="2026-09-05T15:00:00+00:00",
        )
        picked = selection.select_candidates(gossip + [google, direct, follow], self.cfg(), "sport")
        titles = [row["title"] for row in picked]
        self.assertIn("PSG beat Marseille 2-1", titles)
        self.assertNotIn("PSG beat Marseille 2-1 - CulturePSG", titles)
        self.assertIn("Hakimi injured after PSG beat Marseille 2-1", titles)
        self.assertTrue(any(row["title"] == "PSG beat Marseille 2-1" and not row.get("via") for row in picked))

    def test_finance_crypto_cap_is_explicit_and_does_not_drop_macro(self):
        crypto = [item(i, "CoinDesk", title=f"Bitcoin memecoin move {i}", published="2026-09-05T18:00:00+00:00") for i in range(20)]
        macro = [item(100 + i, "Reuters", title=f"ECB rates and credit {i}", published="2026-09-05T09:00:00+00:00") for i in range(8)]
        cfg = self.cfg()
        cfg["selection"] = {**cfg["selection"], "publisher_cap": 12}
        picked = selection.select_candidates(crypto + macro, cfg, "finance")
        crypto_count = sum("bitcoin" in row["title"].lower() or "memecoin" in row["title"].lower() for row in picked)
        self.assertLessEqual(crypto_count, 2)
        self.assertGreaterEqual(sum("ECB" in row["title"] for row in picked), 8)

    def test_sparse_section_returns_what_exists_in_a_stable_order(self):
        rows = [
            item(2, title="Quiet note", published="2026-09-05T08:00:00+00:00"),
            item(1, title="Earlier note", published="2026-09-05T07:00:00+00:00"),
        ]
        cfg = self.cfg()
        first = [row["key"] for row in selection.select_candidates(rows, cfg, "news")]
        second = [row["key"] for row in selection.select_candidates(list(reversed(rows)), cfg, "news")]
        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)

    def test_long_feed_excerpt_is_bounded_and_order_is_deterministic(self):
        rows = [
            item(i, "Reuters", title=f"ECB insurance review {i}", summary="x" * 500, published=f"2026-09-05T09:{i:02d}:00+00:00")
            for i in range(12)
        ]
        cfg = self.cfg()
        first = selection.select_candidates(rows, cfg, "finance")
        second = selection.select_candidates(list(reversed(rows)), cfg, "finance")
        self.assertEqual([row["key"] for row in first], [row["key"] for row in second])
        self.assertTrue(all(len(row["summary"]) <= 140 for row in first))
        self.assertTrue(all("topic:" in row["selection_reason"] for row in first))


if __name__ == "__main__":
    unittest.main()
