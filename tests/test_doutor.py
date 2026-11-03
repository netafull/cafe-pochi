import unittest

import helpers
from helpers import FakeSite, TempData, prod, run_chain, DOUTOR, DOUTOR_ALLERGY
import crawl
import synth as S

D1, D2, D3, D4 = "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"
CATS = ["hotdrink", "icedrink", "milano", "hotdog", "hotsandwich", "toast", "morning", "sweets"]

CARD_DRINK = "20260908084729"   # 季節のおすすめにも載る(蜜芋オレ風)
CARD_CREPE = "20261009160520"   # NEW印
CARD_REG = "20140818181442"     # 定番(季節のおすすめにも並んで載る)
CARD_NEW_ONLY = "20261020111111"  # NEW印だが季節のおすすめには無い


def season_html(extra_blocks=None, drop=()):
    blocks = [
        [S.doutor_item("テストクレープ", "¥560", None, None).replace("¥560", "¥560"),
         S.doutor_item("定番ミルクレープ", "¥440")],
        [S.doutor_item("テストオレ ～安納芋使用～", "¥680", size="M")],
        [S.doutor_item_noprice("今年はおまけ付き！"),
         S.doutor_item("テストクリスマスケーキ（5号）", area=[("販売価格", "¥4,300"), ("宅配価格", "¥5,300")]),
         S.doutor_item_reservation("店頭受取", ("予約承り期間", "お渡し期間"),
                                   ("2026年11月1日(日)～12月10日(木)", "2026年12月14日(月)～12月25日(金)")),
         S.doutor_item_noprice("■ドトール バリューカードキャンペーン")],
        [S.doutor_item("テスト豆ブレンド", "¥1,380", size="コーヒー豆<br>（200g）")],
    ]
    blocks = [[i for i in b if not any(d in i for d in drop)] for b in blocks]
    return S.doutor_season_html([b for b in blocks if b] + (extra_blocks or []))


def card_pages(cards=None):
    cards = cards or {
        "sweets": [(CARD_CREPE, "テストクレープ", True), (CARD_REG, "定番ミルクレープ", False)],
        "icedrink": [(CARD_DRINK, "テストオレ～安納芋使用～", False)],
        "hotdrink": [("20260101000000", "定番コーヒー", False)],
    }
    return {c: S.doutor_cards_html(cards.get(c, [("2020000000000" + str(i), f"定番{c}{i}", False) for i in range(2)]))
            for c in CATS}


def doutor_site(season=None, cards=None, allergy=True, new_only=False):
    site = FakeSite({f"{DOUTOR}/robots.txt": "<html><body>not robots</body></html>",
                     f"{DOUTOR_ALLERGY}/robots.txt": "User-agent: *\nAllow: /"})
    site.set(f"{DOUTOR}/dcs/menu/season.html", season if season is not None else season_html())
    pages = card_pages(cards)
    if new_only:
        pages["morning"] = S.doutor_cards_html([(CARD_NEW_ONLY, "テスト新商品モーニング", True), ("20200000000009", "定番モーニング", False)])
        site.set(f"{DOUTOR}/dcs/menu/detail/{CARD_NEW_ONLY}.html", S.doutor_detail_html("テスト新商品モーニング", "¥ 590"))
    for c, html in pages.items():
        site.set(f"{DOUTOR}/dcs/menu/list/{c}.html", html)
    if allergy:
        base = f"{DOUTOR_ALLERGY}/allergy_check/doutor_coffee_shop"
        site.set(f"{base}/search/?menu_category=2-1-", S.doutor_allergy_list_html([("11001132", "アイステストオレ ～安納芋使用～"), ("11001999", "別のドリンク")]))
        site.set(f"{base}/search/?menu_category=2-2-", S.doutor_allergy_list_html([("11002001", "別のフード")]))
        site.set(f"{base}/search/?menu_category=2-3-", S.doutor_allergy_list_html([("11003073", "テストクレープ"), ("11003011", "定番ミルクレープ")]))
        site.set(f"{base}/item/detail/11001132/", S.doutor_allergy_detail_html("アイステストオレ ～安納芋使用～", circle=("乳", "ごま"), tabs=[("M（R）サイズ", 329)]))
        site.set(f"{base}/item/detail/11003073/", S.doutor_allergy_detail_html("テストクレープ", circle=("卵", "乳"), triangle=("えび",), tabs=[(None, 331)]))
        site.set(f"{base}/item/detail/11003011/", S.doutor_allergy_detail_html("定番ミルクレープ", circle=("卵",), tabs=[(None, 280)]))
    return site


def dt(site, day):
    return run_chain(crawl.crawl_doutor, site, day)


class TestDoutorFirstRun(unittest.TestCase):
    def test_records_season_items_and_excludes_non_products(self):
        with TempData():
            _, st = dt(doutor_site(), D1)
            names = {p["name"] for p in crawl.load_chain_products("doutor")}
            self.assertEqual(names, {"テストクレープ", "定番ミルクレープ", "テストオレ ～安納芋使用～", "テストクリスマスケーキ（5号）"})
            self.assertEqual(st["doutor"]["skipped_goods"], ["テスト豆ブレンド"])  # 豆・ドリップ等は記録しない
            self.assertTrue(st["doutor"]["list_ok"])

    def test_ids_card_vs_name(self):
        with TempData():
            dt(doutor_site(), D1)
            self.assertEqual(prod("doutor", CARD_CREPE)["name"], "テストクレープ")
            self.assertEqual(prod("doutor", CARD_DRINK)["name"], "テストオレ ～安納芋使用～")  # 副題・全角半角の揺れを吸収して照合
            cake = [p for p in crawl.load_chain_products("doutor") if "ケーキ" in p["name"]][0]
            self.assertTrue(cake["product_id"].startswith("sx-"))
            self.assertEqual(cake["id_kind"], "name")
            self.assertTrue(cake["official_url"].endswith("/dcs/menu/season.html"))
            self.assertEqual(prod("doutor", CARD_CREPE)["official_url"], f"{DOUTOR}/dcs/menu/detail/{CARD_CREPE}.html")

    def test_prices_and_reservation(self):
        with TempData():
            dt(doutor_site(), D1)
            cake = [p for p in crawl.load_chain_products("doutor") if "ケーキ" in p["name"]][0]
            self.assertEqual(cake["price"], 4300)
            self.assertEqual([v["price"] for v in cake["price_variants"]], [4300, 5300])
            self.assertEqual(cake["reservation"][0]["channel"], "店頭受取")
            self.assertIn("12月25日(金)", cake["planned_end_text"])
            self.assertEqual(prod("doutor", CARD_DRINK)["price"], 680)

    def test_nutrition_and_allergens_from_allergy_site(self):
        with TempData():
            dt(doutor_site(), D1)
            p = prod("doutor", CARD_CREPE)
            self.assertEqual(p["nutrition"]["kcal"], 331)
            self.assertEqual(p["allergens"]["contains"], ["卵", "乳"])
            self.assertEqual(p["allergens"]["partial"], [{"name": "えび", "mark": "△"}])
            d = prod("doutor", CARD_DRINK)
            self.assertEqual(d["nutrition"]["kcal"], 329)  # 「アイス」接頭辞つきの商品に照合
            self.assertEqual(d["nutrition_variants"], None)
            cake = [p for p in crawl.load_chain_products("doutor") if "ケーキ" in p["name"]][0]
            self.assertIsNone(cake["nutrition"])  # 照合できない商品は公式リンクで案内

    def test_launch_date_is_null(self):
        with TempData():
            dt(doutor_site(), D1)
            self.assertTrue(all(p["launch_date"] is None for p in crawl.load_chain_products("doutor")))

    def test_new_card_only_product_gets_price_from_detail(self):
        with TempData():
            dt(doutor_site(new_only=True), D1)
            p = prod("doutor", CARD_NEW_ONLY)
            self.assertEqual(p["price"], 590)
            self.assertEqual(p["limited_kind"], "新商品（NEW）")

    def test_no_description_or_image_saved(self):
        with TempData() as base:
            dt(doutor_site(), D1)
            for f in (base / "products" / "doutor").glob("*.json"):
                t = f.read_text(encoding="utf-8")
                for bad in ("image", "description", ".jpg", "説明文のダミー"):
                    self.assertNotIn(bad, t)

    def test_allergy_lists_fetched_once_and_not_daily(self):
        site = doutor_site()
        with TempData():
            dt(site, D1)
            n = site.count("search/?menu_category")
            self.assertEqual(n, 3)
            dt(site, D2)
            self.assertEqual(site.count("search/?menu_category"), 3)  # 取得済みの商品だけの日は、一覧を取り直さない


class TestDoutorTracking(unittest.TestCase):
    def test_end_requires_absence_from_season_and_category_lists(self):
        with TempData():
            dt(doutor_site(), D1)
            # 季節のおすすめから消えたが、カテゴリ一覧には残っている(定番になった) -> 終了しない
            gone_season = doutor_site(season=season_html(drop=("定番ミルクレープ",)))
            for d in (D2, D3):
                dt(gone_season, d)
            self.assertEqual(prod("doutor", CARD_REG)["status"], "on_sale")
            # カテゴリ一覧からも消えた -> 2日続けて(別の日)で終了
            gone_both = doutor_site(season=season_html(drop=("定番ミルクレープ",)),
                                    cards={"sweets": [(CARD_CREPE, "テストクレープ", True)],
                                           "icedrink": [(CARD_DRINK, "テストオレ～安納芋使用～", False)]})
            dt(gone_both, D3)
            self.assertEqual(prod("doutor", CARD_REG)["status"], "on_sale")
            dt(gone_both, D4)
            self.assertEqual(prod("doutor", CARD_REG)["status"], "ended")

    def test_season_only_product_end_and_name_id_is_kept(self):
        with TempData():
            dt(doutor_site(), D1)
            cake_id = [p["product_id"] for p in crawl.load_chain_products("doutor") if "ケーキ" in p["name"]][0]
            no_cake = doutor_site(season=season_html(drop=("クリスマスケーキ",)))
            dt(no_cake, D2)
            self.assertEqual(prod("doutor", cake_id)["status"], "on_sale")
            dt(no_cake, D3)
            self.assertEqual(prod("doutor", cake_id)["status"], "ended")

    def test_category_failure_does_not_end_anything(self):
        with TempData():
            dt(doutor_site(), D1)
            site = doutor_site(season=season_html(drop=("定番ミルクレープ", "クリスマスケーキ")))
            site.routes[f"{DOUTOR}/dcs/menu/list/toast.html"] = OSError("down")
            for d in (D2, D3, D4):
                _, st = dt(site, d)
                self.assertFalse(st["doutor"]["list_ok"])
            self.assertTrue(all(p["status"] == "on_sale" for p in crawl.load_chain_products("doutor")))

    def test_season_structure_change_is_list_failed(self):
        with TempData():
            dt(doutor_site(), D1)
            _, st = dt(doutor_site(season="<html><body>リニューアル</body></html>"), D2)
            self.assertFalse(st["doutor"]["list_ok"])
            self.assertEqual(crawl.collect_problems(st)["list_failed"], ["doutor"])

    def test_empty_category_is_failure(self):
        with TempData():
            site = doutor_site()
            site.set(f"{DOUTOR}/dcs/menu/list/toast.html", "<html></html>")
            _, st = dt(site, D1)
            self.assertFalse(st["doutor"]["list_ok"])

    def test_blocked(self):
        site = doutor_site()
        site.blocked_hosts.add("www.doutor.co.jp")
        with TempData():
            _, st = dt(site, D1)
            self.assertTrue(st["doutor"]["blocked"])

    def test_price_change_history(self):
        with TempData():
            dt(doutor_site(), D1)
            html = season_html().replace("¥680", "¥700")
            dt(doutor_site(season=html), D2)
            p = prod("doutor", CARD_DRINK)
            self.assertEqual([h["price"] for h in p["price_history"]], [680, 700])

    def test_unknown_allergy_legend_is_not_recorded_as_contains(self):
        site = doutor_site()
        base = f"{DOUTOR_ALLERGY}/allergy_check/doutor_coffee_shop"
        site.set(f"{base}/item/detail/11003073/", S.doutor_allergy_detail_html("テストクレープ", extra_kind="mystery", tabs=[(None, 331)]))
        with TempData():
            dt(site, D1)
            p = prod("doutor", CARD_CREPE)
            self.assertEqual(p["nutrition"]["kcal"], 331)
            self.assertIsNone(p["allergens"])  # 凡例を確認できない記号は「含む」の判定を保留し、公式リンクで案内する


if __name__ == "__main__":
    unittest.main()
