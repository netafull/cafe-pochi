import unittest

import helpers
from helpers import FakeSite, TempData, prod, run_chain, DOUTOR, DOUTOR_ALLERGY
import crawl
import parsers
import synth as S

D1, D2 = "2026-11-28", "2026-11-29"
CATS = ["hotdrink", "icedrink", "panini", "rice", "pasta", "soup", "morning", "sweets", "alcohol"]

CARD_PANINI = "20261102160408"   # 季節のおすすめにも載る
CARD_NEW_ONLY = "20261102160458"  # NEW印だが季節のおすすめには無い
CARD_REG = "20111201210053"       # 定番


def season_html():
    return S.excelsior_season_html([
        [S.excelsior_item("テストパニーニ", ((None, "¥600"),)),
         S.excelsior_item("テストチャイティー", (("R", "¥670"), ("L", "¥730")))],
        [S.excelsior_item_noprice("キャンペーンの案内")],
    ])


def excelsior_site(season=None, allergy=True, new_only=True):
    site = FakeSite({f"{DOUTOR}/robots.txt": "<html><body>not robots</body></html>",
                     f"{DOUTOR_ALLERGY}/robots.txt": "User-agent: *\nAllow: /"})
    site.set(f"{DOUTOR}/exc/menu/recommend.html", season if season is not None else season_html())
    cards = {"panini": [(CARD_PANINI, "テストパニーニ", False), (CARD_REG, "定番パニーニ", False)],
             "icedrink": [("20260101000001", "定番アイスコーヒー", False)]}
    if new_only:
        cards["panini"] = [(CARD_PANINI, "テストパニーニ", False),
                           (CARD_NEW_ONLY, "テスト新作パニーニ", True),
                           (CARD_REG, "定番パニーニ", False)]
        site.set(f"{DOUTOR}/exc/menu/detail/{CARD_NEW_ONLY}.html",
                 S.excelsior_detail_html("テスト新作パニーニ", ((None, "¥550"),)))
    for c in CATS:
        site.set(f"{DOUTOR}/exc/menu/list/{c}.html",
                 S.excelsior_cards_html(cards.get(c, [("2020000000000" + str(i), f"定番{c}{i}", False) for i in range(2)])))
    if allergy:
        base = f"{DOUTOR_ALLERGY}/allergy_check/excelsior_caffe"
        site.set(f"{base}/search/?menu_category=2-1-", S.excelsior_allergy_list_html([("21001001", "テストチャイティー"), ("21001003", "別のドリンク")]))
        site.set(f"{base}/search/?menu_category=2-2-", S.excelsior_allergy_list_html([("21002001", "テストパニーニ"), ("21002002", "テスト新作パニーニ")]))
        site.set(f"{base}/search/?menu_category=2-3-", S.excelsior_allergy_list_html([("21003001", "別のデザート")]))
        site.set(f"{base}/item/detail/21001001/", S.doutor_allergy_detail_html("テストチャイティー", circle=("乳",), tabs=[("Rサイズ", 200), ("Lサイズ", 240)]))
        site.set(f"{base}/item/detail/21002001/", S.doutor_allergy_detail_html("テストパニーニ", circle=("小麦", "乳"), tabs=[(None, 450)]))
        site.set(f"{base}/item/detail/21002002/", S.doutor_allergy_detail_html("テスト新作パニーニ", circle=("卵",), tabs=[(None, 380)]))
    return site


def ex(site, day):
    return run_chain(crawl.crawl_excelsior, site, day)


class TestExcelsiorParsers(unittest.TestCase):
    def test_season(self):
        r = parsers.parse_excelsior_season(season_html(), ["コーヒー豆"])
        self.assertEqual([i["name"] for i in r["items"]], ["テストパニーニ", "テストチャイティー"])
        chai = r["items"][1]
        self.assertEqual([(p["label"], p["price"]) for p in chai["prices"]], [("R", 670), ("L", 730)])

    def test_season_no_blocks(self):
        with self.assertRaises(ValueError):
            parsers.parse_excelsior_season("<html><body>なし</body></html>")

    def test_cards(self):
        cards = parsers.parse_excelsior_cards(S.excelsior_cards_html([(CARD_PANINI, "テストパニーニ", True)]))
        self.assertEqual(cards, [{"id": CARD_PANINI, "name": "テストパニーニ", "is_new": True}])

    def test_detail_sizes(self):
        d = parsers.parse_excelsior_detail(S.excelsior_detail_html("テストチャイティー", (("R", "¥ 670"), ("L", "¥ 730"))))
        self.assertEqual(d["name"], "テストチャイティー")
        self.assertEqual([(p["label"], p["price"]) for p in d["prices"]], [("R", 670), ("L", 730)])
        self.assertEqual(d["price"], 670)

    def test_allergy_list(self):
        rows = parsers.parse_excelsior_allergy_list(S.excelsior_allergy_list_html([("21001001", "テストチャイティー")]))
        self.assertEqual(rows, [{"id": "21001001", "name": "テストチャイティー"}])


class TestExcelsiorCrawl(unittest.TestCase):
    def test_first_run_records_season_and_new(self):
        with TempData():
            _, st = ex(excelsior_site(), D1)
            self.assertEqual(st["excelsior"]["new_products"], 3)
            names = {p["name"] for p in crawl.load_chain_products("excelsior")}
            self.assertEqual(names, {"テストパニーニ", "テストチャイティー", "テスト新作パニーニ"})

    def test_card_id_and_prices(self):
        with TempData():
            ex(excelsior_site(), D1)
            p = prod("excelsior", CARD_PANINI)
            self.assertEqual(p["id_kind"], "detail")
            self.assertEqual(p["price"], 600)
            self.assertEqual(p["official_url"], f"https://www.doutor.co.jp/exc/menu/detail/{CARD_PANINI}.html")
            chai = next(p for p in crawl.load_chain_products("excelsior") if p["name"] == "テストチャイティー")
            self.assertEqual(chai["price"], 670)
            self.assertEqual(chai["price_variants"], [{"label": "R", "price": 670}, {"label": "L", "price": 730}])

    def test_new_only_price_from_detail(self):
        with TempData():
            ex(excelsior_site(), D1)
            p = prod("excelsior", CARD_NEW_ONLY)
            self.assertEqual(p["limited_kind"], "新商品（NEW）")
            self.assertEqual(p["price"], 550)

    def test_nutrition_and_allergens(self):
        with TempData():
            ex(excelsior_site(), D1)
            p = prod("excelsior", CARD_PANINI)
            self.assertIsNotNone(p["nutrition"])
            self.assertEqual(p["nutrition"]["kcal"], 450)
            self.assertEqual(p["allergens"]["contains"], ["小麦", "乳"])
            chai = next(p for p in crawl.load_chain_products("excelsior") if p["name"] == "テストチャイティー")
            self.assertEqual(len(chai["nutrition_variants"]), 2)
            self.assertEqual(chai["nutrition_source"]["url"],
                             "https://allergy.doutor.co.jp/allergy_check/excelsior_caffe/")

    def test_launch_date_is_null(self):
        with TempData():
            ex(excelsior_site(), D1)
            for p in crawl.load_chain_products("excelsior"):
                self.assertIsNone(p["launch_date"])

    def test_second_run_no_duplicates(self):
        with TempData():
            site = excelsior_site()
            ex(site, D1)
            _, st = ex(site, D2)
            self.assertEqual(st["excelsior"]["new_products"], 0)
            self.assertEqual(len(crawl.load_chain_products("excelsior")), 3)


if __name__ == "__main__":
    unittest.main()
