import unittest

import helpers  # noqa: F401
import fetch_netaful_reviews as nr
import generate_site as g


class TestTags(unittest.TestCase):
    def test_tag_ids_from_config(self):
        # スターバックスだけタグがある(2026-11-03に /wp-json/wp/v2/tags?search= で確認)。他の3社はタグ無し
        self.assertEqual(nr.TAGS, {"starbucks": 1062, "tullys": None, "komeda": None, "doutor": None})

    def test_tagless_chains_use_article_search_with_title_filter(self):
        self.assertEqual(nr.SEARCH_TERMS, {"starbucks": "スターバックス", "tullys": "タリーズ", "komeda": "コメダ", "doutor": "ドトール"})
        self.assertTrue(nr.title_passes_filter("tullys", "【タリーズ】新作ドリンク"))
        self.assertFalse(nr.title_passes_filter("tullys", "満足度が高いコーヒーチェーンランキング"))
        self.assertTrue(nr.title_passes_filter("komeda", "コメダ珈琲店の新作"))
        self.assertFalse(nr.title_passes_filter("doutor", "セブンの新商品"))
        self.assertTrue(nr.title_passes_filter("starbucks", "【スタバ】新作フラペチーノ"))

    def test_all_chains_always_filtered(self):
        self.assertEqual(nr.TITLE_ALWAYS, {"starbucks", "tullys", "komeda", "doutor"})

    def test_search_url_orders_by_date(self):
        seen = {}
        def fake_get(url):
            seen["url"] = url
            return b"[]", {"X-WP-TotalPages": "1"}
        saved = nr.http_get
        nr.http_get = fake_get
        try:
            nr.fetch_tag_page(None, 1, 50, "タリーズ")
        finally:
            nr.http_get = saved
        self.assertIn("orderby=date", seen["url"])
        self.assertIn("search=", seen["url"])
        self.assertNotIn("tags=", seen["url"])


class TestMatching(unittest.TestCase):
    def test_names_match_strictness(self):
        n = nr.normalize_name
        self.assertTrue(nr.names_match(n("パンプキンプディングラテ"), n("パンプキンプディングラテ（HOT）")))
        self.assertFalse(nr.names_match(n("ラテ"), n("パンプキンラテ")))  # 6文字未満の部分一致は不可
        self.assertTrue(nr.names_match(n("シロノワール"), n("シロノワール")))

    def test_registered_mark_ignored(self):
        n = nr.normalize_name
        self.assertEqual(n("フラペチーノ® ティー"), n("フラペチーノ ティー"))

    def test_extract_name_from_title(self):
        a = nr.extract_article({"id": 1, "date": "2026-01-01T00:00:00", "link": "x",
                                "title": {"rendered": "【スタバ】「ロイヤル ミルク ティー」を飲んだ"}})
        self.assertEqual(a["product_name"], "ロイヤル ミルク ティー")

    def test_effective_date_prefers_launch_then_listed_then_first_seen(self):
        self.assertEqual(nr.product_effective_date({"launch_date": "2026-10-01", "listed_at": "2026-09-01", "first_seen_at": "2026-11-03T07:00"}), "2026-10-01")
        self.assertEqual(nr.product_effective_date({"listed_at": "2026-09-01", "first_seen_at": "2026-11-03T07:00"}), "2026-09-01")
        self.assertEqual(nr.product_effective_date({"first_seen_at": "2026-11-03T07:00:00+09:00"}), "2026-11-03")

    def test_keyword_longest_match_wins(self):
        kw = g.match_keyword_for_name("パリパリチョコキャラメルミルクレープ")
        self.assertEqual(kw["label"], "ミルクレープ")  # キャラメル(5)・チョコ(2)よりミルクレープ(6)が具体的
        self.assertEqual(g.match_keyword_for_name("パンプキン スープチーノ")["label"], "パンプキン・かぼちゃ")
        self.assertIsNone(g.match_keyword_for_name("zzz"))

    def test_related_articles_prefer_same_chain(self):
        arts = {
            1: {"id": 1, "chain": "tullys", "title": "タリーズのパンプキン", "date": "2026-02-01"},
            2: {"id": 2, "chain": "starbucks", "title": "スタバのパンプキン", "date": "2025-02-01"},
        }
        it = {"chain": "starbucks", "product_id": "9", "name": "パンプキン スープチーノ"}
        label, got = g.select_related_articles(it, arts)
        self.assertEqual(label, "パンプキン・かぼちゃ")
        self.assertEqual([a["id"] for a in got], [2, 1])


if __name__ == "__main__":
    unittest.main()
