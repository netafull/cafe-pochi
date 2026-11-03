import json
import unittest

import helpers
from helpers import FakeSite, TempData, prod, run_chain, SBUX_LIST, SBUX_PRODUCT, SBUX_STORIES, BlockedError
import crawl
import synth as S

D1, D2, D3 = "2026-11-03", "2026-11-04", "2026-11-05"

# 創作した商品: A=季節のおすすめ(飲み物・新作一覧にもある) B=新作一覧と栄養にある(季節のおすすめではない)
# C=グッズ(栄養JSONに無い) D=季節のおすすめだが新作一覧に無い(価格なし) E=季節のおすすめ・メニュー商品ではない
A, B, C, DD, E, F = "1000000000001", "1000000000002", "1000000000003", "1000000000004", "1000000000005", "1000000000006"


def make_site(list_items=None, c01_bev=None, c01_food=None, other_bev=None, feed=None, bodies=None,
              allergens=True, nut_override=None):
    items = list_items if list_items is not None else [
        S.sbux_item(A, "テスト季節ラテ", 600, 700),
        S.sbux_item(B, "テスト新作ティー", 520),
        S.sbux_item(C, "テスト タンブラー", 3800, methods=("STARBUCKS_COFFEE", "ONLINE_STORE")),
        S.sbux_item(F, "テスト季節スコーン", 290),
    ]
    site = FakeSite()
    for start, js in S.sbux_list_pages(items).items():
        site.set(f"{SBUX_LIST}/api/v1/list?tag=new&start={start}", js)
    site.set(f"{SBUX_LIST}/robots.txt", "User-agent: *\nDisallow: /search*")
    site.set(f"{SBUX_PRODUCT}/robots.txt", "User-agent: *\nDisallow:/line/qr/*")
    site.set(f"{SBUX_STORIES}/robots.txt", "User-agent: *\nDisallow: /search/")
    c01_bev = c01_bev if c01_bev is not None else [
        S.sbux_product(A, "テスト季節ラテ", {"SHORT": 200, "TALL": 250, "GRANDE": 300}),
        S.sbux_product(DD, "テスト季節ラテ（アイス）", {"TALL": 180}),
        S.sbux_product(E, "テスト非メニュー", {"TALL": 5}, is_menu=False),
    ]
    c01_food = c01_food if c01_food is not None else [S.sbux_product(F, "テスト季節スコーン", {"STANDARD": 400})]
    other_bev = other_bev if other_bev is not None else [S.sbux_product(B, "テスト新作ティー", {"TALL": 90})]
    site.set(f"{SBUX_PRODUCT}/allergy/json/nutritions.json",
             nut_override or S.sbux_nutrition_json(c01_bev, c01_food, other_bev))
    if allergens:
        site.set(f"{SBUX_PRODUCT}/allergy/json/allergens.json", S.sbux_allergens_json(
            bev=[S.sbux_allergen_product(A, "テスト季節ラテ", milk=1), S.sbux_allergen_product(DD, "x", milk=1)],
            food=[S.sbux_allergen_product(F, "テスト季節スコーン", egg=1, wheat=1)]))
    feed = feed if feed is not None else [
        ("テスト新作ティーを11月2日（月）より発売", "https://stories.example/p1", "Mon, 02 Nov 2026 01:00:00 +0000", "概要A"),
        ("ホリデーシーズンのお知らせ 3種を11月2日（月）より発売", "https://stories.example/p2", "Sun, 01 Nov 2026 01:00:00 +0000", "概要B"),
    ]
    site.set(f"{SBUX_STORIES}/press/press-releases/feed/", S.wp_feed(feed))
    for url, text in (bodies or {}).items():
        site.set(url, text)
    return site


def sb(site, day):
    return run_chain(crawl.crawl_starbucks, site, day)


class TestStarbucksFirstRun(unittest.TestCase):
    def test_records_food_and_drink_only_not_goods(self):
        with TempData():
            _, st = sb(make_site(), D1)
            self.assertTrue(st["starbucks"]["list_ok"])
            ids = {p["product_id"] for p in crawl.load_chain_products("starbucks")}
            # 季節のおすすめ(A,F)・新作一覧∩栄養(B)・季節のおすすめだが新作一覧に無い(DD)。グッズ(C)・非メニュー(E)は記録しない
            self.assertEqual(ids, {A, B, F, DD})
            self.assertEqual(st["starbucks"]["new_products"], 4)

    def test_first_run_records_everything_unlike_baseline_method(self):
        with TempData() as base:
            sb(make_site(), D1)
            self.assertEqual(len(crawl.load_chain_products("starbucks")), 4)
            self.assertEqual(set(json.loads((base / "baseline_ids.json").read_text())["starbucks"]), {A, B, F, DD})

    def test_nutrition_standard_milk_tall_and_allergens(self):
        with TempData():
            sb(make_site(), D1)
            p = prod("starbucks", A)
            self.assertEqual(p["nutrition"]["kcal"], 250)  # Tall・標準のミルク
            self.assertEqual(p["nutrition_label"], "Tall")
            self.assertEqual([v["label"] for v in p["nutrition_variants"]], ["Short", "Tall", "Grande"])
            self.assertEqual(p["allergens"]["contains"], ["乳"])
            self.assertEqual(prod("starbucks", F)["allergens"]["contains"], ["卵", "小麦"])
            self.assertEqual(prod("starbucks", F)["limited_kind"], "季節のおすすめ")
            self.assertEqual(prod("starbucks", B)["limited_kind"], "新作")

    def test_price_range_and_price_missing(self):
        with TempData():
            sb(make_site(), D1)
            p = prod("starbucks", A)
            self.assertEqual((p["price"], p["price_max"]), (600, 700))
            # 新作一覧に無い商品は、名前(ホット/アイス表記を除く)が一致するものから価格を借りる。無ければ価格なし
            self.assertEqual(prod("starbucks", DD)["price"], 600)
            self.assertEqual(p["official_url"], f"https://menu.starbucks.co.jp/{A}")

    def test_no_description_or_image_saved(self):
        with TempData() as base:
            sb(make_site(), D1)
            for f in (base / "products" / "starbucks").glob("*.json"):
                text = f.read_text(encoding="utf-8")
                for bad in ("image", "description", "photo", ".jpg", "sku_images"):
                    self.assertNotIn(bad, text, f"{f.name} に {bad} が入っている")

    def test_launch_date_from_press_headline(self):
        with TempData():
            sb(make_site(), D1)
            p = prod("starbucks", B)
            self.assertEqual(p["launch_date"], "2026-11-02")
            self.assertEqual(p["launch_date_source"], "press_release")
            self.assertEqual(p["news_url"], "https://stories.example/p1")
            self.assertIsNone(prod("starbucks", A)["launch_date"])  # 見出しに名前が無い

    def test_article_body_fetch_is_capped_and_used(self):
        feed = [(f"お知らせ{i} 11月2日（月）より発売", f"https://stories.example/p{i}", "Mon, 02 Nov 2026 01:00:00 +0000", "概要")
                for i in range(7)]
        bodies = {f"https://stories.example/p{i}": "<html><body>11月2日（月）より。" + ("テスト季節ラテ登場" if i == 1 else "") + "</body></html>"
                  for i in range(7)}
        site = make_site(feed=feed, bodies=bodies)
        with TempData():
            sb(site, D1)
            fetched = [u for u in site.calls if u.startswith("https://stories.example/")]
            self.assertLessEqual(len(set(fetched)), crawl.CONFIG["starbucks_article_fetch_max"])
            self.assertEqual(prod("starbucks", A)["launch_date"], "2026-11-02")

    def test_request_budget(self):
        site = make_site()
        with TempData():
            sb(site, D1)
        self.assertLessEqual(len(site.calls), crawl.MAX_REQUESTS["starbucks"])
        self.assertEqual(site.count("api/v1/list"), 1)  # 4件なので1ページ


class TestStarbucksTracking(unittest.TestCase):
    def test_end_needs_two_different_days(self):
        with TempData():
            sb(make_site(), D1)
            gone = [S.sbux_item(A, "テスト季節ラテ", 600, 700), S.sbux_item(B, "テスト新作ティー", 520), S.sbux_item(C, "テスト タンブラー", 3800)]
            site2 = make_site(list_items=gone, c01_food=[])
            _, st = sb(site2, D2)  # F(季節スコーン)が一覧から消えた1日目
            self.assertEqual(prod("starbucks", F)["status"], "on_sale")
            sb(site2, D2)  # 同じ日の再実行は数えない
            self.assertEqual(prod("starbucks", F)["status"], "on_sale")
            _, st = sb(site2, D3)
            p = prod("starbucks", F)
            self.assertEqual((p["status"], p["ended_observed_at"]), ("ended", D3))
            self.assertEqual(st["starbucks"]["ended"], 1)

    def test_incomplete_list_never_ends_anything(self):
        items = [S.sbux_item(f"20000000000{i:02d}", f"テスト商品{i}", 500) for i in range(15)]
        nut_bev = [S.sbux_product(it["item_code"], it["item_name"], {"TALL": 100}) for it in items]
        with TempData():
            site = make_site(list_items=items, c01_bev=nut_bev, c01_food=[], other_bev=[])
            sb(site, D1)
            self.assertEqual(len(crawl.load_chain_products("starbucks")), 15)
            site.routes[f"{SBUX_LIST}/api/v1/list?tag=new&start=10"] = OSError("down")  # 2ページ目が取れない
            for d in (D2, D3):
                _, st = sb(site, d)
                self.assertFalse(st["starbucks"]["list_ok"])
            self.assertTrue(all(p["status"] == "on_sale" for p in crawl.load_chain_products("starbucks")))

    def test_multi_page_list(self):
        items = [S.sbux_item(f"20000000000{i:02d}", f"テスト商品{i}", 500) for i in range(23)]
        nut = [S.sbux_product(it["item_code"], it["item_name"], {"TALL": 100}) for it in items]
        site = make_site(list_items=items, c01_bev=nut, c01_food=[], other_bev=[])
        with TempData():
            _, st = sb(site, D1)
            self.assertEqual(site.count("api/v1/list"), 3)
            self.assertEqual(st["starbucks"]["new_products"], 23)

    def test_nutrition_json_failure_records_nothing(self):
        with TempData():
            site = make_site(nut_override="{not json")
            _, st = sb(site, D1)
            self.assertFalse(st["starbucks"]["list_ok"])
            self.assertEqual(crawl.load_chain_products("starbucks"), [])
            self.assertEqual(crawl.collect_problems(st)["list_failed"], ["starbucks"])

    def test_list_structure_change_is_list_failed(self):
        with TempData():
            sb(make_site(), D1)
            site = make_site()
            site.set(f"{SBUX_LIST}/api/v1/list?tag=new&start=0", json.dumps({"result": []}))
            _, st = sb(site, D2)
            self.assertFalse(st["starbucks"]["list_ok"])
            self.assertTrue(all(p["status"] == "on_sale" for p in crawl.load_chain_products("starbucks")))

    def test_blocked_stops(self):
        with TempData():
            site = make_site()
            site.blocked_hosts.add("menu.starbucks.co.jp")
            _, st = sb(site, D1)
            self.assertTrue(st["starbucks"]["blocked"])
            self.assertEqual(crawl.collect_problems(st)["blocked"], ["starbucks"])

    def test_allergen_json_failure_still_records_and_fills_later(self):
        with TempData():
            site = make_site(allergens=False)
            _, st = sb(site, D1)
            self.assertEqual(prod("starbucks", A)["allergens"], None)
            self.assertEqual(st["starbucks"]["new_products"], 4)
            sb(make_site(), D2)
            self.assertEqual(prod("starbucks", A)["allergens"]["contains"], ["乳"])

    def test_price_change_goes_to_history(self):
        with TempData():
            sb(make_site(), D1)
            items = [S.sbux_item(A, "テスト季節ラテ", 650, 750), S.sbux_item(B, "テスト新作ティー", 520), S.sbux_item(F, "テスト季節スコーン", 290)]
            sb(make_site(list_items=items), D2)
            p = prod("starbucks", A)
            self.assertEqual((p["price"], p["price_max"]), (650, 750))
            self.assertEqual([h["price"] for h in p["price_history"]], [600, 650])

    def test_relaunch_keeps_last_season(self):
        with TempData():
            sb(make_site(), D1)
            few = [S.sbux_item(A, "テスト季節ラテ", 600, 700), S.sbux_item(B, "テスト新作ティー", 520)]
            for d in ("2026-11-04", "2026-11-05"):
                sb(make_site(list_items=few, c01_food=[]), d)
            self.assertEqual(prod("starbucks", F)["status"], "ended")
            sb(make_site(), "2026-12-01")
            p = prod("starbucks", F)
            self.assertEqual(p["status"], "on_sale")
            self.assertEqual(p["relaunches"][0]["reappeared_at"], "2026-12-01")
            self.assertEqual(p["seasons"][0]["ended_observed_at"], "2026-11-05")
            self.assertIsNone(p["launch_date"])  # 昨季の発売日を今季として出さない


class TestStarbucksBurst(unittest.TestCase):
    def test_second_run_burst_is_held_but_first_run_is_not(self):
        with TempData() as base:
            sb(make_site(), D1)
            many = [S.sbux_item(f"30000000000{i:02d}", f"テスト大量{i}", 500) for i in range(30)]
            nut = [S.sbux_product(it["item_code"], it["item_name"], {"TALL": 100}) for it in many]
            site = make_site(list_items=many, c01_bev=nut, c01_food=[], other_bev=[])
            pending, st = sb(site, D2)
            self.assertEqual(st["starbucks"]["new_products"], 0)
            self.assertFalse(st["starbucks"]["list_ok"])
            self.assertIn("suspected_restructure", st["starbucks"])
            self.assertEqual(len(crawl.load_chain_products("starbucks")), 4)
            self.assertTrue(all(p["status"] == "on_sale" for p in crawl.load_chain_products("starbucks")))

    def test_seasonal_launch_under_threshold_is_recorded(self):
        with TempData():
            sb(make_site(), D1)
            more = [S.sbux_item(A, "テスト季節ラテ", 600, 700), S.sbux_item(B, "テスト新作ティー", 520), S.sbux_item(F, "テスト季節スコーン", 290)]
            more += [S.sbux_item(f"30000000000{i:02d}", f"テスト新作{i}", 500) for i in range(12)]
            nut = [S.sbux_product(it["item_code"], it["item_name"], {"TALL": 100}) for it in more[3:]]
            nut += [S.sbux_product(A, "テスト季節ラテ", {"TALL": 250})]
            site = make_site(list_items=more, c01_bev=nut, c01_food=[S.sbux_product(F, "テスト季節スコーン", {"STANDARD": 400})],
                             other_bev=[S.sbux_product(B, "テスト新作ティー", {"TALL": 90})])
            _, st = sb(site, D2)
            self.assertTrue(st["starbucks"]["list_ok"])
            self.assertEqual(st["starbucks"]["new_products"], 12)  # ホリデー入れ替えの12品は歯止めにかからない


if __name__ == "__main__":
    unittest.main()
