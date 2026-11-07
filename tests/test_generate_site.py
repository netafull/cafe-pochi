import json
import re
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
import generate_site as g


def item(**kw):
    base = {
        "chain": "starbucks", "product_id": "9001", "name": "テストラテ", "category": "ビバレッジ",
        "price": 600, "price_max": 700, "price_note": "サイズにより異なります", "price_variants": None,
        "launch_date": "2026-09-02", "launch_date_source": "press_release",
        "news_title": "テストの見出し", "news_url": "https://stories.example/p1", "news_date": "2026-08-30",
        "planned_end_text": None, "listed_at": None,
        "nutrition": {"kcal": 246, "protein_g": 5.9, "fat_g": 14.0, "carbs_g": 24.5, "sugar_g": 23.8,
                      "fiber_g": 0.7, "salt_g": 0.41, "other": {"飽和脂肪酸": "7.9g"}},
        "nutrition_label": "Tall", "nutrition_variants": None, "nutrition_note": "ミルクは標準の値です",
        "nutrition_source": {"label": "テスト栄養ページ", "url": "https://nutrition.example/"},
        "allergens": {"contains": ["乳", "小麦"], "partial": []},
        "allergen_source": {"label": "テストアレルゲンページ", "url": "https://allergen.example/"},
        "allergen_note": None,
        "limited_kind": "季節のおすすめ", "stores_note": "スターバックス コーヒー",
        "official_url": "https://menu.starbucks.co.jp/9001",
        "first_seen_at": "2026-11-03T07:00:00+09:00", "last_seen_at": "2026-11-03",
        "status": "on_sale", "ended_observed_at": None,
        "price_history": [{"date": "2026-11-03", "price": 600, "price_max": 700}],
    }
    base.update(kw)
    return base


class TestSeo(unittest.TestCase):
    def test_title_with_kcal(self):
        self.assertEqual(g.seo_title(item()), "スターバックス「テストラテ」のカロリー・価格・栄養成分")

    def test_title_without_nutrition(self):
        self.assertEqual(g.seo_title(item(nutrition=None, chain="komeda", name="新作")),
                         "コメダ珈琲店「新作」の価格・販売期間")

    def test_description_has_chain_kcal_price(self):
        d = g.seo_description(item())
        self.assertIn("スターバックス", d)
        self.assertIn("246kcal", d)
        self.assertIn("600〜700円", d)
        self.assertLessEqual(len(d), 200)

    def test_page_title_has_site_name(self):
        h = g.render_item_page(item())
        self.assertIn("<title>スターバックス「テストラテ」のカロリー・価格・栄養成分｜カフェポチ</title>", h)


def with_caffeine(value, **kw):
    n = item()["nutrition"]
    n["other"] = dict(n["other"], カフェイン=value)
    return item(nutrition=n, **kw)


class TestCaffeineSeo(unittest.TestCase):
    def test_title_and_description_with_caffeine(self):
        it = with_caffeine("117mg")
        self.assertEqual(g.seo_title(it), "スターバックス「テストラテ」のカロリー・カフェイン・価格")
        self.assertIn("、246kcal・カフェイン117mg（Tall）、", g.seo_description(it))

    def test_zero_caffeine_is_shown(self):
        it = with_caffeine("0mg")
        self.assertIn("カロリー・カフェイン・価格", g.seo_title(it))
        self.assertIn("カフェイン0mg（Tall）", g.seo_description(it))

    def test_without_caffeine_unchanged(self):
        self.assertEqual(g.seo_title(item()), "スターバックス「テストラテ」のカロリー・価格・栄養成分")
        self.assertNotIn("カフェイン", g.seo_description(item()))
        self.assertNotIn("カフェイン", g.seo_title(item(nutrition={"kcal": 100.0, "other": {"カフェイン": "多め"}})))

    def test_no_label_has_no_parens(self):
        d = g.seo_description(with_caffeine("50mg", nutrition_label=None))
        self.assertIn("246kcal・カフェイン50mg、", d)

    def test_page_title(self):
        h = g.render_item_page(with_caffeine("117mg"))
        self.assertIn("<title>スターバックス「テストラテ」のカロリー・カフェイン・価格｜カフェポチ</title>", h)


def dup_pair():
    a = item(product_id="1", launch_date="2026-09-02")
    b = item(product_id="2", launch_date="2026-11-02", price=650, price_max=None)
    return [a, b]


class TestSameName(unittest.TestCase):
    def test_qualifier_by_launch_month(self):
        q = g.name_qualifiers(dup_pair())
        self.assertEqual(q[("starbucks", "1")], "2026年9月発売")
        self.assertEqual(q[("starbucks", "2")], "2026年11月発売")

    def test_same_month_falls_back_to_day_then_price(self):
        a = item(product_id="1", launch_date="2026-11-02")
        b = item(product_id="2", launch_date="2026-11-20")
        q = g.name_qualifiers([a, b])
        self.assertEqual(q[("starbucks", "2")], "2026年11月20日発売")
        c = item(product_id="3", launch_date="2026-11-02", price=500, price_max=None)
        d = item(product_id="4", launch_date="2026-11-02", price=600, price_max=None)
        q = g.name_qualifiers([c, d])
        self.assertEqual(q[("starbucks", "3")], "500円")

    def test_indistinguishable_gets_none(self):
        a = item(product_id="1", launch_date="2026-11-02", price_max=None, stores_note="X", category="A")
        b = item(product_id="2", launch_date="2026-11-02", price_max=None, stores_note="X", category="A")
        q = g.name_qualifiers([a, b])
        self.assertEqual(q, {("starbucks", "1"): None, ("starbucks", "2"): None})
        h = g.render_item_page(a, [a, b])
        self.assertIn("スターバックス「テストラテ」のカロリー", h)  # 補足なし
        self.assertIn("同じ名前の商品", h)  # リンクは出す

    def test_name_match_is_nfkc_and_space_insensitive(self):
        a = item(product_id="1", name="ＡＢ ラテ", launch_date="2026-09-02")
        b = item(product_id="2", name="AB　ラテ", launch_date="2026-11-02")
        self.assertEqual(len(g.name_qualifiers([a, b])), 2)

    def test_other_chain_is_not_same_name(self):
        a = item(product_id="1")
        b = item(product_id="2", chain="komeda")
        self.assertEqual(g.name_qualifiers([a, b]), {})

    def test_page_for_duplicate(self):
        a, b = dup_pair()
        h = g.render_item_page(a, [a, b])
        self.assertIn("<title>スターバックス「テストラテ」（2026年9月発売）のカロリー・価格・栄養成分｜カフェポチ</title>", h)
        self.assertIn("「テストラテ」（2026年9月発売）、246kcal", h)
        self.assertIn('<h1>テストラテ <span class="sub">（2026年9月発売）</span></h1>', h)
        self.assertEqual(h.count("<h1"), 1)
        self.assertIn("<h2>同じ名前の商品</h2>", h)
        self.assertIn('href="/items/starbucks-2.html">テストラテ（2026年11月発売）</a>', h)
        self.assertIn('<link rel="canonical" href="https://cafe.netaful.jp/items/starbucks-1.html">', h)

    def test_non_duplicate_page_unchanged(self):
        a = item(product_id="1")
        b = item(product_id="2", name="べつの商品")
        h = g.render_item_page(a, [a, b])
        self.assertIn("<title>スターバックス「テストラテ」のカロリー・価格・栄養成分｜カフェポチ</title>", h)
        self.assertIn("<h1>テストラテ</h1>", h)
        self.assertNotIn("同じ名前の商品", h)


class TestRelated(unittest.TestCase):
    def make(self, n=20):
        return [item(product_id=str(i), name=f"商品{i}", launch_date=f"2026-09-{i + 1:02d}") for i in range(n)]

    def test_max_six_excludes_self_and_same_name(self):
        items = self.make() + [item(product_id="99", name="商品10", launch_date="2026-09-11")]
        rel = g.select_related_items(items[10], items)
        self.assertEqual(len(rel), 6)
        self.assertNotIn("10", [r["product_id"] for r in rel])
        self.assertNotIn("99", [r["product_id"] for r in rel])  # 同名

    def test_nearest_on_both_sides(self):
        items = self.make()
        ids = sorted(int(r["product_id"]) for r in g.select_related_items(items[10], items))
        self.assertEqual(ids, [7, 8, 9, 11, 12, 13])

    def test_deterministic_and_far_items_do_not_change_links(self):
        items = self.make()
        before = [r["product_id"] for r in g.select_related_items(items[3], items)]
        again = [r["product_id"] for r in g.select_related_items(items[3], list(reversed(items)))]
        self.assertEqual(before, again)
        newer = items + [item(product_id="500", name="新商品", launch_date="2027-01-01")]
        self.assertEqual(before, [r["product_id"] for r in g.select_related_items(items[3], newer)])

    def test_same_category_first_then_fill_from_chain(self):
        items = [item(product_id=str(i), name=f"商品{i}", launch_date=f"2026-09-{i + 1:02d}",
                      category="A" if i < 3 else "B") for i in range(10)]
        rel = g.select_related_items(items[0], items)
        self.assertEqual(len(rel), 6)
        self.assertTrue({"1", "2"} <= {r["product_id"] for r in rel})  # 同カテゴリは必ず入る

    def test_other_chain_excluded_and_few(self):
        a = item(product_id="1")
        b = item(product_id="2", name="他社", chain="komeda")
        self.assertEqual(g.select_related_items(a, [a, b]), [])

    def test_page_has_plain_links(self):
        items = self.make()
        h = g.render_item_page(items[5], items)
        self.assertIn("<h2>同じ時期のスターバックスの商品</h2>", h)
        self.assertEqual(len(re.findall(r'href="/items/starbucks-\d+\.html"', h)), 6)
        self.assertLess(h.index("同じ時期のスターバックス"), h.index("<h2>補足と出典</h2>"))


class TestHeadingsAndBreadcrumbs(unittest.TestCase):
    def test_item_page_single_h1_and_h2_sections(self):
        h = g.render_item_page(item())
        self.assertEqual(h.count("<h1"), 1)
        self.assertIn('<div class="site-title">', h)
        self.assertNotIn("<h3>", h)
        for t in ("<h2>栄養成分（Tall）</h2>", "<h2>補足と出典</h2>"):
            self.assertIn(t, h)

    def test_top_keeps_site_h1(self):
        h = g.render_top([item()])
        self.assertEqual(h.count("<h1"), 1)
        self.assertNotIn('class="site-title"', h)

    def test_list_pages_have_one_h1(self):
        for h in (g.render_grid_page("テスト", "d", [item()], "https://cafe.netaful.jp/chains/starbucks/"),
                  g.render_grid_page("テスト", "d", [], "https://cafe.netaful.jp/chains/starbucks/"),
                  g.render_archive_index([item(status="ended", ended_observed_at="2026-10-01")]),
                  g.render_archive_index([]), g.render_chains_top()):
            self.assertEqual(h.count("<h1"), 1)

    def test_breadcrumb_jsonld_on_item(self):
        h = g.render_item_page(item())
        m = re.search(r'<script type="application/ld\+json">(\{"@context": "https://schema.org", "@type": "BreadcrumbList".*?)</script>', h)
        data = json.loads(m.group(1))
        els = data["itemListElement"]
        self.assertEqual([e["position"] for e in els], [1, 2, 3])
        self.assertEqual(els[0]["item"], "https://cafe.netaful.jp/")
        self.assertEqual(els[1]["item"], "https://cafe.netaful.jp/chains/starbucks/")
        self.assertEqual(els[2]["item"], "https://cafe.netaful.jp/items/starbucks-9001.html")
        self.assertNotIn('"Product"', h)

    def test_breadcrumb_escapes_script_end(self):
        ld = g.breadcrumb_ld([("a</script>", "https://x/")])
        self.assertEqual(ld.count("</script>"), 1)  # 末尾の閉じタグだけ


class TestChainSeo(unittest.TestCase):
    def test_title_and_description(self):
        items = [item(product_id=str(i), name=f"新作{i}", launch_date=f"2026-10-{i + 1:02d}") for i in range(5)]
        t, d = g.chain_seo(g.CHAINS[0], items)
        self.assertEqual(t, "スターバックスの季節限定メニュー一覧（発売日順）")
        self.assertIn("スターバックスの季節限定メニュー5件（販売中5件）を発売日順に記録。", d)
        self.assertIn("「新作4」「新作3」「新作2」", d)
        self.assertNotIn("新作1", d)
        self.assertIn("カロリー・価格・栄養成分・アレルゲンを掲載", d)
        self.assertLessEqual(len(d), 140)

    def test_facts_only_what_exists(self):
        it = item(nutrition=None, allergens=None)
        _, d = g.chain_seo(g.CHAINS[0], [it])
        self.assertNotIn("栄養成分", d)
        self.assertNotIn("アレルゲン", d)
        self.assertIn("価格", d)

    def test_long_names_drop_to_fewer(self):
        items = [item(product_id=str(i), name="長い名前" * 8 + str(i), launch_date=f"2026-10-{i + 1:02d}")
                 for i in range(3)]
        _, d = g.chain_seo(g.CHAINS[0], items)
        self.assertLessEqual(len(d), 130)

    def test_chain_page_title_and_breadcrumb(self):
        c = g.CHAINS[0]
        t, d = g.chain_seo(c, [item()])
        url = "https://cafe.netaful.jp/chains/starbucks/"
        h = g.render_grid_page(c["name"], d, [item()], url, seo_title=t,
                               extra_head=g.breadcrumb_ld([("トップ", "https://cafe.netaful.jp/"), (c["name"], url)]))
        self.assertIn("<title>スターバックスの季節限定メニュー一覧（発売日順）｜カフェポチ</title>", h)
        self.assertIn('"BreadcrumbList"', h)


class TestLastmod(unittest.TestCase):
    def test_uses_change_dates_not_last_seen(self):
        it = item(first_seen_at="2026-11-03T07:00:00+09:00", last_seen_at="2026-11-08")
        self.assertEqual(g.latest_change_date(it), "2026-11-03")

    def test_picks_latest_of_changes(self):
        it = item(price_history=[{"date": "2026-11-03", "price": 1}, {"date": "2026-11-05", "price": 2}],
                  nutrition_changes=[{"date": "2026-11-06"}], ended_observed_at="2026-11-07", status="ended",
                  relaunches=[{"ended_observed_at": "2026-10-01", "reappeared_at": "2026-11-09"}])
        self.assertEqual(g.latest_change_date(it), "2026-11-09")

    def test_sitemap_lastmod(self):
        a = item(product_id="1")
        b = item(product_id="2", status="ended", ended_observed_at="2026-11-06",
                 first_seen_at="2026-10-01T00:00:00+09:00", price_history=[])
        sm = g.generate_sitemap([a, b], ["2026-09"])
        self.assertIn("items/starbucks-1.html</loc><lastmod>2026-11-03</lastmod>", sm)
        self.assertIn("items/starbucks-2.html</loc><lastmod>2026-11-06</lastmod>", sm)
        self.assertIn("https://cafe.netaful.jp/</loc><lastmod>2026-11-06</lastmod>", sm)
        self.assertIn("archive/</loc><lastmod>2026-11-06</lastmod>", sm)
        self.assertIn("chains/komeda/</loc></url>", sm)  # 商品が無いチェーンは lastmod なし
        self.assertNotIn(g.datetime.datetime.now(g.JST).date().isoformat(), sm)


class TestMultiSizeCaffeineRow(unittest.TestCase):
    def variants(self, caf):
        def v(label, kcal, c):
            other = {"飽和脂肪酸": "1g"}
            if c:
                other["カフェイン"] = c
            return {"label": label, "nutrition": {"kcal": kcal, "protein_g": 1.0, "other": other}}
        return [v("Short", 100.0, caf[0]), v("Tall", 150.0, caf[1])]

    def test_row_added_with_missing_as_dash(self):
        h = g.render_item_page(item(nutrition_variants=self.variants(["50mg", None])))
        self.assertIn("<tr><th>カフェイン</th><td>50mg</td><td>－</td></tr>", h)
        self.assertNotIn("<th>飽和脂肪酸</th>", h)  # カフェイン以外のotherは足さない

    def test_no_row_when_no_size_has_caffeine(self):
        h = g.render_item_page(item(nutrition_variants=self.variants([None, None])))
        self.assertNotIn("<th>カフェイン</th>", h)

    def test_single_size_table_unchanged(self):
        h = g.render_item_page(with_caffeine("117mg"))
        self.assertEqual(h.count("<th>カフェイン</th>"), 1)
        self.assertNotIn("table class='multi'", h)


class TestPrice(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(g.fmt_price(item(price=690, price_max=None)), "690円（税込）")
        self.assertEqual(g.fmt_price(item(price=530, price_max=590)), "530〜590円（税込）")
        self.assertEqual(g.fmt_price(item(price=4300, price_max=None)), "4,300円（税込）")
        self.assertEqual(g.fmt_price(item(price=570, price_max=None, price_from=True)), "570円〜（税込）")
        self.assertEqual(g.fmt_price(item(price=None)), "")

    def test_no_price_page_says_check_official(self):
        h = g.render_item_page(item(price=None, price_max=None, price_history=[]))
        self.assertIn("価格は公式サイトでご確認ください", h)

    def test_price_variants_table(self):
        h = g.render_item_page(item(price=4300, price_max=None, price_variants=[{"label": "販売価格", "price": 4300}, {"label": "宅配価格", "price": 5300}]))
        self.assertIn("宅配価格", h)
        self.assertIn("5,300円（税込）", h)


class TestPeriod(unittest.TestCase):
    def test_on_sale(self):
        self.assertEqual(g.period_text(item()), "2026-09-02〜 販売中")
        self.assertIn("終了予定", g.period_text(item(planned_end_text="お渡し期間：12月25日"), detailed=True))

    def test_ended(self):
        it = item(status="ended", ended_observed_at="2026-10-21")
        self.assertEqual(g.period_text(it), "2026-09-02〜2026-10-21")
        self.assertIn("消えたことを確認した日", g.period_text(it, detailed=True))

    def test_unknown_launch_uses_recording_start(self):
        self.assertIn("2026-11-03（記録開始日）", g.period_text(item(launch_date=None)))

    def test_tullys_listed_date_is_shown_as_site_listing_date(self):
        it = item(chain="tullys", launch_date=None, listed_at="2026-10-07")
        self.assertEqual(g.period_text(it), "2026-10-07（公式サイト掲載日）〜 販売中")
        self.assertEqual(g.effective_date(it), "2026-10-07")

    def test_relaunch_ignores_last_season_listed_date(self):
        it = item(chain="tullys", launch_date=None, listed_at="2025-10-07", relaunches=[{"ended_observed_at": "2025-12-01", "reappeared_at": "2026-10-05"}])
        self.assertIn("2026-10-05（記録開始日）", g.period_text(it))


class TestItemPage(unittest.TestCase):
    def test_no_description_quote_only_facts_and_links(self):
        h = g.render_item_page(item())
        self.assertNotIn("<blockquote", h)
        self.assertIn('href="https://menu.starbucks.co.jp/9001"', h)  # 公式の商品ページ
        self.assertIn('href="https://stories.example/p1"', h)  # プレスリリース
        self.assertIn("出典：スターバックス公式サイト（2026-11-03時点）", h)

    def test_no_images_except_site_logo(self):
        h = g.render_item_page(item())
        for src in re.findall(r'<img[^>]*src="([^"]*)"', h):
            # サイトのロゴと、運営者自身のブログ(ネタフル)のレビュー記事のアイキャッチだけ。各社の公式画像は表示しない
            self.assertTrue(src == "/assets/logo.png" or src.startswith("https://netaful.jp/"), src)
            for official in ("starbucks", "tullys", "komeda", "doutor", "asset.menu"):
                self.assertNotIn(official, src)

    def test_nutrition_inline_for_few_other(self):
        h = g.render_item_page(item())
        self.assertIn("246kcal", h)
        self.assertIn("栄養成分（Tall）", h)
        self.assertIn("飽和脂肪酸", h)
        self.assertNotIn("<details><summary>そのほかの栄養成分", h)

    def test_nutrition_details_for_many_other(self):
        other = {f"項目{i}": f"{i}mg" for i in range(10)}
        h = g.render_item_page(item(nutrition={"kcal": 520.0, "other": other}))
        self.assertIn("そのほかの栄養成分（10項目）", h)

    def test_kcal_only_variants_table(self):
        v = [{"label": "HOT Short", "nutrition": {"kcal": 231.0}}, {"label": "COLD Tall", "nutrition": {"kcal": 236.0}}]
        h = g.render_item_page(item(nutrition={"kcal": 231.0}, nutrition_variants=v, nutrition_label="HOT Short"))
        self.assertIn("HOT Short", h)
        self.assertIn("236kcal", h)

    def test_multi_column_variants(self):
        n = {"kcal": 200, "protein_g": 3.0, "fat_g": 5.0, "carbs_g": 30.0, "salt_g": 0.1}
        v = [{"label": "Short", "nutrition": n}, {"label": "Tall", "nutrition": {**n, "kcal": 250}}]
        h = g.render_item_page(item(nutrition_variants=v))
        self.assertIn("class='multi'", h)
        self.assertIn("<th>Short</th>", h)

    def test_missing_nutrition_points_to_official(self):
        h = g.render_item_page(item(nutrition=None, allergens=None))
        self.assertIn("栄養成分は未取得です", h)
        self.assertIn("https://nutrition.example/", h)
        self.assertIn("アレルギー情報は未取得です", h)
        self.assertIn("https://allergen.example/", h)

    def test_allergens_contains_none_and_partial(self):
        h = g.render_item_page(item(allergens={"contains": [], "partial": [], "none_listed": True}))
        self.assertIn("公式ページに表示された品目はありません", h)
        h = g.render_item_page(item(allergens={"contains": ["卵"], "partial": [{"name": "えび", "mark": "△"}]}))
        self.assertIn("条件つきの表示（△）：えび△", h)
        self.assertIn("最新のアレルギー情報は公式サイトでご確認ください", h)

    def test_price_history_only_when_changed(self):
        self.assertNotIn("価格の履歴", g.render_item_page(item()))
        hist = [{"date": "2026-11-03", "price": 600}, {"date": "2026-11-20", "price": 650, "price_max": 750}]
        h = g.render_item_page(item(price_history=hist, price=650))
        self.assertIn("価格の履歴", h)
        self.assertIn("650〜750円", h)

    def test_launch_date_note_only_when_unknown(self):
        self.assertNotIn("発売日は", g.render_item_page(item()))
        h = g.render_item_page(item(launch_date=None, news_url=None, news_title=None))
        self.assertIn("発売日は不明のため、記録を始めた日から表示しています", h)

    def test_ended_note_only_on_ended_items(self):
        self.assertNotIn("店頭ではまだ販売", g.render_item_page(item()))
        it = item(status="ended", ended_observed_at="2026-11-11")
        self.assertIn("店頭ではまだ販売していることもあります", g.render_item_page(it))

    def test_doutor_reservation_notes(self):
        r = [{"channel": "店頭受取", "periods": [{"label": "予約承り期間", "value": "11月1日～12月10日"}, {"label": "お渡し期間", "value": "12月14日～12月25日"}]}]
        h = g.render_item_page(item(chain="doutor", reservation=r, launch_date=None))
        self.assertIn("店頭受取：予約承り期間 11月1日～12月10日／お渡し期間 12月14日～12月25日", h)

    def test_season_history_and_relaunch_notes(self):
        it = item(seasons=[{"launch_date": "2025-11-01", "ended_observed_at": "2026-01-10"}],
                  relaunches=[{"ended_observed_at": "2026-01-10", "reappeared_at": "2026-11-02"}])
        h = g.render_item_page(it)
        self.assertIn("昨季の掲載：2025-11-01〜2026-01-10", h)
        self.assertIn("2026-11-02に公式サイトの一覧へ再び登場しました", h)

    def test_no_adsense_when_unset(self):
        from unittest import mock
        with mock.patch.dict(g.CONFIG, {"adsense_client_id": "", "adsense_ad_slot": "",
                                         "ga_measurement_id": ""}):
            h = g.render_item_page(item())
        self.assertNotIn("adsbygoogle", h)
        self.assertNotIn("googletagmanager", h)

    def test_notes_come_after_nutrition(self):
        # 注意書きが先に並ぶと、カロリーまで長くスクロールすることになる
        it = item()
        h = g.render_item_page(it)
        nutrition = g.render_nutrition(it)
        self.assertTrue(nutrition)
        self.assertLess(h.index(nutrition), h.index("補足と出典"))
        self.assertLess(h.index("補足と出典"), h.index("<p class='source'>"))

    def test_nutrition_notes_are_short(self):
        h = g.render_item_page(item(nutrition_note="公式サイトで公表されているのはカロリー(kcal)のみです"))
        self.assertNotIn("公表されているのは", h)
        self.assertNotIn("転記", h)
        self.assertIn('href="https://nutrition.example', h)  # 栄養の出典は補足と出典に残す
        self.assertIn("最新のアレルギー情報は公式サイトでご確認ください", h)  # アレルゲンの案内は残す
        h = g.render_item_page(item(nutrition_note="ミルクは標準の値です"))
        self.assertIn("ミルクは標準の値です。", h)

    def test_ga4_tag_present_when_set(self):
        from unittest import mock
        with mock.patch.dict(g.CONFIG, {"ga_measurement_id": "G-TEST123"}):
            h = g.render_item_page(item())
        self.assertIn("googletagmanager.com/gtag/js?id=G-TEST123", h)


class TestChainColors(unittest.TestCase):
    def test_css_has_light_and_dark_chain_colors_and_dark_badge_text(self):
        css = g.build_css()
        for c in g.CHAINS:
            self.assertIn(f"--{c['slug']}: {c['color']};", css)
            self.assertIn(f"--{c['slug']}: {c['color_dark']};", css)
            self.assertIn(f'.item[data-chain="{c["slug"]}"] .badge', css)
        self.assertIn("--accent: #6D4C41", css)
        self.assertIn("prefers-color-scheme: dark", css)
        self.assertIn(".chain-badge { color: #17130f; }", css)


class TestAdSlots(unittest.TestCase):
    """広告(AdSense)。ヘッダー下1枠＋カード一覧の中(JSが差し込む。最大ADS_MAX_PER_GRID枠)、商品ページは1枠。"""

    def setUp(self):
        from unittest import mock
        p = mock.patch.dict(g.CONFIG, {"adsense_client_id": "ca-pub-1", "adsense_ad_slot": "2"})
        p.start()
        self.addCleanup(p.stop)
        self.items = [item(chain=c, product_id=f"{c}{i}") for c in ("starbucks", "tullys", "komeda") for i in range(3)]

    def pages(self):
        return {
            "grid": g.render_grid_page("週", "d", self.items, "https://x/"),
            "top": g.render_top(self.items),
            "item": g.render_item_page(self.items[0]),
        }

    def test_real_config_has_both_ad_values(self):
        # 片方だけだと広告が出ない。値は他のポチと共通
        real = json.loads((Path(g.ROOT) / "config.json").read_text(encoding="utf-8"))
        self.assertTrue(real["adsense_client_id"].startswith("ca-pub-"))
        self.assertTrue(real["adsense_ad_slot"])

    def test_head_script_and_vignette_opt_out(self):
        for name, h in self.pages().items():
            self.assertIn("adsbygoogle.js?client=ca-pub-1", h.split("</head>")[0], name)
            self.assertNotIn("<a href", h, name)
            self.assertNotIn("<a class", h, name)

    def test_no_server_side_ad_inside_sortable_grid(self):
        for name, h in self.pages().items():
            for m in re.finditer(r'<div class="grid" data-sortable>(.*?)\n</div>\n', h, re.S):
                self.assertNotIn("ad-slot", m.group(1), name)
                self.assertNotIn("adsbygoogle", m.group(1), name)

    def test_grid_pages_have_ad_js_template_and_constants(self):
        for name in ("grid", "top"):
            h = self.pages()[name]
            self.assertIn("function syncAds()", h, name)
            self.assertIn(f"var AD_EVERY = {g.ADS_EVERY};", h, name)
            self.assertIn(f"var AD_MAX = {g.ADS_MAX_PER_GRID};", h, name)
            self.assertEqual(h.count('<template id="ad-template">'), 1, name)
            self.assertNotIn("__ADS_", h, name)
            self.assertGreater(h.rindex("syncAds();"), h.index("var AD_EVERY"), name)
            # ヘッダー下1枠 + 雛形1つ。実際の差し込み(最大3枠)はJS
            self.assertEqual(h.split("<body>")[1].count('<ins class="adsbygoogle"'), 2, name)
        self.assertEqual((g.ADS_EVERY, g.ADS_MAX_PER_GRID), (12, 3))

    def test_item_page_has_one_ad(self):
        h = self.pages()["item"]
        self.assertEqual(h.split("<body>")[1].count('<ins class="adsbygoogle"'), 1)
        self.assertNotIn("<template", h)
        self.assertEqual(h.count("adsbygoogle || []).push"), 1)  # syncAdsの1回だけ(枠ごとのinline pushなし)
        h2 = g.render_item_page(item(nutrition=None, allergens=None, price_history=[]))
        self.assertEqual(h2.split("<body>")[1].count('<ins class="adsbygoogle"'), 1)

    def test_nothing_when_unset(self):
        from unittest import mock
        with mock.patch.dict(g.CONFIG, {"adsense_client_id": "", "adsense_ad_slot": ""}):
            for name, h in self.pages().items():
                self.assertNotIn("adsbygoogle", h.replace(g.build_css(), ""), name)
                self.assertNotIn("function syncAds", h, name)
                self.assertNotIn("<template", h, name)
                self.assertNotIn('class="ad-slot"', h, name)
                self.assertNotIn("data-google-vignette", h, name)

    def test_one_side_unset_outputs_no_slot(self):
        from unittest import mock
        for key in ("adsense_client_id", "adsense_ad_slot"):
            with mock.patch.dict(g.CONFIG, {key: ""}):
                for name, h in self.pages().items():
                    self.assertNotIn('class="ad-slot"', h, name)

    def test_no_ads_on_empty_pages(self):
        self.assertNotIn('class="ad-slot"', g.render_grid_page("週", "d", [], "https://x/"))

    def test_scripts_are_valid_javascript(self):
        import os, shutil, subprocess, tempfile
        node = shutil.which("node") or "/opt/homebrew/opt/node@22/bin/node"
        if not os.path.exists(node):
            self.skipTest("node がない")
        for m in re.finditer(r"<script>(.*?)</script>", self.pages()["top"], re.S):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
                f.write(m.group(1))
            try:
                r = subprocess.run([node, "--check", f.name], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
            finally:
                os.unlink(f.name)

    def _run_ad_plan(self, cases):
        import json, os, shutil, subprocess
        node = shutil.which("node") or "/opt/homebrew/opt/node@22/bin/node"
        if not os.path.exists(node):
            self.skipTest("node がない")
        script = (
            g.AD_PLAN_JS
            + "\nconsole.log(JSON.stringify(%s.map(function (c) { return computeAdPlan(c, %d, %d); })));"
            % (json.dumps(cases), g.ADS_EVERY, g.ADS_MAX_PER_GRID)
        )
        r = subprocess.run([node, "-e", script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_compute_ad_plan(self):
        cases = [[0], [11], [12], [13], [30], [42, 65, 34, 11, 10],
                 [13, 13, 13, 13, 13], [212], [], [13, 5, 20]]
        got = self._run_ad_plan(cases)
        self.assertEqual(got[0], [[]])
        self.assertEqual(got[1], [[]])
        self.assertEqual(got[2], [[]])          # ちょうどAD_EVERY件は置かない
        self.assertEqual(got[3], [[12]])        # 13件: 末尾の1件手前
        self.assertEqual(got[4], [[12, 24]])    # 30件: 36は範囲外
        self.assertEqual(got[5], [[12], [12], [12], [], []])  # 置ける3社に1枠ずつ
        # 13件以上が5社: 等間隔に3社(0,1,3番目)を選び1枠ずつ
        self.assertEqual(got[6], [[12], [12], [], [12], []])
        self.assertEqual(got[7], [[12, 24, 36]])  # 単一グリッドは3枠
        self.assertEqual(got[8], [])
        self.assertEqual(got[9], [[12], [], [12]])
        for plan, counts in zip(got, cases):
            self.assertLessEqual(sum(len(p) for p in plan), g.ADS_MAX_PER_GRID)
            for p, n in zip(plan, counts):
                self.assertEqual(p, sorted(set(p)))
                for i in p:
                    self.assertTrue(0 < i < n)

    def test_place_ads_never_detaches_ad_slots(self):
        # 枠をDOMから外してよいのは「余った枠を捨てる」1か所だけ。並べ替えは.itemしか動かさない
        js = g.SORT_JS
        self.assertEqual(js.count("removeChild("), 1)
        self.assertIn("slots.pop()", js)
        self.assertNotIn("innerHTML", js)
        self.assertIn("el.classList.contains('ad-slot')", js)
        self.assertNotIn("__AD_PLAN__", js)
        self.assertIn("function computeAdPlan", js)


class TestTop(unittest.TestCase):
    def test_website_jsonld(self):
        h = g.render_top([item()])
        m = re.search(r'<script type="application/ld\+json">(.*?)</script>', h, re.S)
        ld = json.loads(m.group(1))
        self.assertEqual(ld["@type"], "WebSite")
        self.assertEqual(ld["name"], "カフェポチ")
        self.assertEqual(h.count("application/ld+json"), 1)

    def test_favicon_sizes(self):
        h = g.render_top([item()])
        self.assertIn('<link rel="icon" type="image/png" sizes="96x96" href="/assets/favicon.png">', h)

    def test_favicon_file_is_96px(self):
        import struct
        data = (Path(g.ROOT) / "docs/assets/favicon.png").read_bytes()
        w, h = struct.unpack(">II", data[16:24])
        self.assertEqual((w, h), (96, 96))

    def test_groups_by_chain_and_order(self):
        a = item(product_id="1", name="古い", launch_date="2026-09-01")
        b = item(product_id="2", name="新しい", launch_date="2026-10-01")
        c = item(chain="tullys", product_id="3", name="タリーズの", launch_date="2026-09-15")
        h = g.render_top([a, b, c])
        self.assertLess(h.index("新しい"), h.index("古い"))
        self.assertLess(h.index('data-chain="starbucks"'), h.index('data-chain="tullys"'))
        self.assertNotIn("社別", h)

    def test_sortable_grid_contains_only_cards(self):
        # 広告設定済みだと全<a>にdata-google-vignetteが付くので、外してから見る
        h = g.render_top([item(), item(product_id="2")]).replace(' data-google-vignette="false"', "")
        for m in re.finditer(r'<div class="grid" data-sortable>\n(.*?)\n</div>\n', h, re.S):
            inner = m.group(1)
            self.assertNotIn("ad-slot", inner)
            self.assertNotIn("<h", inner)
            # 直下の要素は <a class="item"> だけ
            self.assertEqual(len(re.findall(r'<a class="item"', inner)), 2)

    def test_status_filter_default_on_sale_and_ended_hidden_count(self):
        ended = item(product_id="2", status="ended", ended_observed_at="2026-10-06")
        h = g.render_top([item(), ended], today=__import__("datetime").date(2026, 10, 8))
        self.assertIn('<option value="on_sale" selected>', h)
        self.assertIn("販売中の季節限定メニュー (1件)", h)

    def test_old_ended_not_on_top(self):
        old = item(product_id="2", status="ended", ended_observed_at="2026-01-01", name="昔のメニュー")
        h = g.render_top([item(), old], today=__import__("datetime").date(2026, 10, 8))
        self.assertNotIn("昔のメニュー", h)

    def test_empty_site_has_no_empty_headings(self):
        h = g.render_top([])
        self.assertNotIn("<h3>", h)

    def test_about_is_short_and_states_facts_only_policy(self):
        h = g.render_top([item()]).replace(' data-google-vignette="false"', "")
        self.assertIn('<a href="https://netaful.jp/">ネタフル</a>', h)
        self.assertIn("非公式サイト", h)
        self.assertIn("記録しているのは事実情報だけ", h)
        self.assertIn("説明文と画像は載せていません", h)
        self.assertIn("各社の公式サイトをご覧ください", h)


class TestArchive(unittest.TestCase):
    def test_empty_archive(self):
        h = g.render_archive_index([item()])
        self.assertIn("まだ販売が終わったメニューはありません", h)
        self.assertNotIn("/archive/2026", h)

    def test_months_grouped_by_start_month(self):
        e1 = item(product_id="1", status="ended", ended_observed_at="2026-10-01", launch_date="2026-09-02")
        e2 = item(product_id="2", status="ended", ended_observed_at="2026-10-02", launch_date="2026-09-20")
        e3 = item(product_id="3", status="ended", ended_observed_at="2026-10-03", launch_date="2026-08-18")
        h = g.render_archive_index([e1, e2, e3, item(product_id="4")])
        self.assertIn('/archive/2026-09/', h)
        self.assertIn('/archive/2026-08/', h)
        self.assertLess(h.index("2026-09"), h.index("2026-08"))
        self.assertIn("2026年9月に始まった季節限定メニュー<span class=\"n\">2件", h)

    def test_card_shows_period(self):
        e = item(status="ended", ended_observed_at="2026-10-21")
        self.assertIn("2026-09-02〜2026-10-21", g.render_card(e))
        self.assertIn('data-status="ended"', g.render_card(e))


class TestOutputs(unittest.TestCase):
    def test_robots(self):
        r = g.generate_robots()
        for bot in ["GPTBot", "CCBot", "Google-Extended", "Applebot-Extended", "anthropic-ai", "ClaudeBot",
                    "Bytespider", "meta-externalagent", "cohere-training-data-crawler", "Diffbot", "Omgilibot"]:
            self.assertIn(f"User-agent: {bot}", r)
        self.assertIn("User-agent: *\nAllow: /", r)
        self.assertNotIn("Googlebot", r)
        self.assertIn("Sitemap: https://cafe.netaful.jp/sitemap.xml", r)

    def test_rss_and_sitemap(self):
        rss = g.generate_rss([item()])
        self.assertIn("【スターバックス】テストラテ", rss)
        sm = g.generate_sitemap([item()], ["2026-09"])
        self.assertIn("https://cafe.netaful.jp/items/starbucks-9001.html", sm)
        self.assertIn("https://cafe.netaful.jp/archive/2026-09/", sm)

    def test_search_index(self):
        idx = g.build_search_index([item(status="ended")])
        self.assertEqual(idx[0]["s"], "ended")
        self.assertEqual(idx[0]["cn"], "スターバックス")

    def test_main_generates_and_cleans(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            saved = (g.PRODUCTS, g.DOCS)
            g.PRODUCTS, g.DOCS = tmp / "products", tmp / "docs"
            try:
                (g.PRODUCTS / "starbucks").mkdir(parents=True)
                (g.PRODUCTS / "starbucks/9001.json").write_text(json.dumps(item()), encoding="utf-8")
                ended = item(product_id="9002", status="ended", ended_observed_at="2026-10-01")
                (g.PRODUCTS / "starbucks/9002.json").write_text(json.dumps(ended), encoding="utf-8")
                (g.DOCS / "items").mkdir(parents=True)
                (g.DOCS / "items/starbucks-old.html").write_text("x")
                (g.DOCS / "archive/2025-01").mkdir(parents=True)
                g.main()
                self.assertTrue((g.DOCS / "items/starbucks-9001.html").exists())
                self.assertFalse((g.DOCS / "items/starbucks-old.html").exists())  # 古いページは掃除
                self.assertFalse((g.DOCS / "archive/2025-01").exists())
                self.assertTrue((g.DOCS / "archive/2026-09/index.html").exists())
                self.assertEqual((g.DOCS / "CNAME").read_text().strip(), "cafe.netaful.jp")
                self.assertTrue((g.DOCS / ".nojekyll").exists())
                # タリーズは0件でも空の見出しだけのページは出さない(「まだ記録がありません」の文だけ)
                tl = (g.DOCS / "chains/tullys/index.html").read_text(encoding="utf-8")
                self.assertNotIn('class="chain-group"', tl)
            finally:
                g.PRODUCTS, g.DOCS = saved


class TestUpdatedLabel(unittest.TestCase):
    """トップだけに「最終更新: 2026年10月14日 07:37」(日本時間・電書/家電/林檎ポチと同じ書式)を出す。"""

    def test_label_format_is_jst(self):
        import datetime
        utc = datetime.datetime(2026, 10, 13, 22, 37, tzinfo=datetime.timezone.utc)  # 日本時間 10/14 07:37
        self.assertEqual(g.updated_label(utc.astimezone(g.JST)), "最終更新: 2026年10月14日 07:37")

    def test_top_has_label_but_other_pages_do_not(self):
        top = g.render_top([item()])
        self.assertIn("最終更新: ", top)
        shell = g.page_shell("x", "d", "<p>b</p>", "https://example.invalid/")
        self.assertNotIn("最終更新", shell)  # 全ページに入れると毎日すべてのファイルが変わる


class TestCardLinksAreRootAbsolute(unittest.TestCase):
    """カードのリンクが /items/… になっている(階層のあるページ /chains/x/ などで404にならない)。"""

    def test_card_href_starts_with_slash(self):
        card = g.render_card(item())
        self.assertIn('href="/items/', card)
        self.assertNotIn('href="items/', card)

    def test_grid_page_has_no_relative_item_links(self):
        html = g.render_grid_page("チェーン", "d", [item()], "https://x/")
        self.assertNotIn('href="items/', html)


if __name__ == "__main__":
    unittest.main()
