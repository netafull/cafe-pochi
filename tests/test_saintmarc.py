import datetime
import unittest

import helpers
from helpers import FakeSite, TempData, prod, run_chain
import crawl
import parsers
import synth as S

BASE = "https://www.saint-marc-hd.com"
D1, D2 = "2026-11-28", "2026-11-29"
CATS = ["bakery_okayama", "drink", "dessert", "hotsand2", "pasta",
        "lunchmarc-cafe", "morningmarc-cafe", "seasonal"]


def category_pages(sand_set=True):
    pages = {
        "bakery_okayama": S.saintmarc_category_html([
            S.saintmarc_item("7001", "テストチョコクロ", [(None, 460)]),
            S.saintmarc_item("7002", "定番パン", [(None, 300)], limited=False),
            S.saintmarc_item("7003", "テストサンド", [(None, 440)]),
            S.saintmarc_item("7012", "テストBOX (テスト監修チョコ)", [(None, 1500)]),
        ]),
        "drink": S.saintmarc_category_html([
            S.saintmarc_item("7004", "テストショコラ", [("S", 720), ("M", 780)]),
            S.saintmarc_item("7005", "定番コーヒー", [("S", 350)], limited=False),
        ]),
        "dessert": S.saintmarc_category_html([
            S.saintmarc_item("7006", "定番プリン", [(None, 400)], limited=False)]),
        "hotsand2": S.saintmarc_category_html([
            S.saintmarc_item("7007", "定番ホットサンド", [(None, 500)], limited=False)]),
        "pasta": S.saintmarc_category_html([
            S.saintmarc_item("7008", "定番パスタ", [(None, 900)], limited=False)]),
        "lunchmarc-cafe": S.saintmarc_category_html([
            S.saintmarc_item("7009", "テストサンド", [("セット", 740)])] if sand_set else [
            S.saintmarc_item("7010", "定番ランチ", [(None, 800)], limited=False)]),
        "morningmarc-cafe": S.saintmarc_category_html([
            S.saintmarc_item("7011", "定番モーニング", [(None, 390)], limited=False)]),
        "seasonal": S.saintmarc_category_html([
            S.saintmarc_item("7001", "テストチョコクロ", [(None, 460)]),
            S.saintmarc_item("7004", "テストショコラ", [("S", 720), ("M", 780)]),
        ]),
    }
    return pages


def saintmarc_site(pages=None):
    site = FakeSite({f"{BASE}/robots.txt": "<html><body>not robots</body></html>"})
    for cat, html in (pages or category_pages()).items():
        site.set(f"{BASE}/saintmarccafe/menu/saintmarccafe/category/{cat}/", html)
    site.set(f"{BASE}/saintmarccafe/news/", S.saintmarc_news_list_html([
        S.saintmarc_news_card("9001", "【11/26(木)発売】冬だけの「テストチョコクロ」", "2026.11.24"),
        S.saintmarc_news_card("9002", "新しい店舗がオープン", "2026.11.20", category="店舗情報"),
        S.saintmarc_news_card("9003", "【9/18(金)開始】BOX購入でプレゼントキャンペーン", "2026.09.15"),
    ]))
    site.set(f"{BASE}/saintmarccafe/news/9001/", S.saintmarc_article_html(
        "【11/26(木)発売】冬だけの「テストチョコクロ」",
        "テストチョコクロが11月26日（木）より期間限定発売。"
        "販売期間：11月26日（木）〜12月25日（金）販売店舗：全国のサンマルクカフェ"))
    # 通常版BOXのキャンペーン記事(括弧の修飾語を落とした短縮名だけで一致してしまう記事)
    site.set(f"{BASE}/saintmarccafe/news/9003/", S.saintmarc_article_html(
        "【9/18(金)開始】BOX購入でプレゼントキャンペーン",
        "秋の新商品が入ったテストBOXご購入でプレゼント。9月18日（金）から実施します"))
    return site


def sm(site, day):
    return run_chain(crawl.crawl_saintmarc, site, day)


class TestParsers(unittest.TestCase):
    def test_category_items(self):
        items = parsers.parse_saintmarc_category(category_pages()["drink"])
        self.assertEqual(len(items), 2)
        choc = items[0]
        self.assertEqual(choc["menu_id"], "7004")
        self.assertEqual(choc["name"], "テストショコラ")
        self.assertTrue(choc["limited"])
        self.assertEqual(choc["prices"], [{"label": "S", "price": 720}, {"label": "M", "price": 780}])
        self.assertFalse(items[1]["limited"])
        self.assertEqual(set(choc), {"menu_id", "name", "limited", "prices"})  # 説明文は持たない

    def test_category_no_blocks(self):
        with self.assertRaises(ValueError):
            parsers.parse_saintmarc_category("<html><body>なし</body></html>")

    def test_news_list(self):
        cards = parsers.parse_saintmarc_news_list(saintmarc_site().routes[f"{BASE}/saintmarccafe/news/"])
        self.assertEqual(len(cards), 3)
        self.assertEqual(cards[0]["date"], "2026-11-24")
        self.assertEqual(cards[0]["category"], "商品情報")
        self.assertEqual(cards[0]["url"], f"{BASE}/saintmarccafe/news/9001/")

    def test_article_and_planned_end(self):
        art = parsers.parse_saintmarc_news_article(
            saintmarc_site().routes[f"{BASE}/saintmarccafe/news/9001/"], datetime.date(2026, 11, 24))
        self.assertEqual(art["title"], "【11/26(木)発売】冬だけの「テストチョコクロ」")
        self.assertEqual(art["planned_end"], "2026年12月25日")

    def test_planned_end_year_rollover(self):
        end = parsers.saintmarc_planned_end(
            "販売期間:12月20日(日)〜1月10日(日)", datetime.date(2026, 12, 15))
        self.assertEqual(end, "2027年1月10日")


class TestCrawl(unittest.TestCase):
    def test_first_run_records_limited_only(self):
        with TempData():
            _, st = sm(saintmarc_site(), D1)
            self.assertTrue(st["saintmarc"]["list_ok"])
            names = {p["name"] for p in crawl.load_chain_products("saintmarc")}
            self.assertEqual(names, {"テストチョコクロ", "テストサンド", "テストショコラ",
                                     "テストBOX (テスト監修チョコ)"})

    def test_same_name_merged_with_set_price(self):
        with TempData():
            sm(saintmarc_site(), D1)
            sand = prod("saintmarc", "7003")
            self.assertEqual(sand["price"], 440)
            self.assertEqual(sand["price_max"], 740)
            self.assertEqual(sand["price_variants"],
                             [{"label": None, "price": 440}, {"label": "セット", "price": 740}])
            self.assertIsNone(prod("saintmarc", "7009"))  # セット側のIDでは作らない

    def test_launch_and_planned_end_from_news(self):
        with TempData():
            sm(saintmarc_site(), D1)
            choc = prod("saintmarc", "7001")
            self.assertEqual(choc["launch_date"], "2026-11-26")
            self.assertEqual(choc["planned_end_text"], "2026年12月25日")
            self.assertEqual(choc["stores_note"], "店舗により価格が異なる場合があります")
            self.assertIsNone(choc["nutrition"])  # 栄養成分は付けない(PDFのみのため)

    def test_short_name_only_body_match_rejected(self):
        # 「テストBOX (テスト監修チョコ)」が通常版BOXのキャンペーン記事に短縮名だけで
        # 一致しても、発売日を付けない(別商品の記事のため)
        with TempData():
            sm(saintmarc_site(), D1)
            box = prod("saintmarc", "7012")
            self.assertIsNone(box["launch_date"])
            self.assertIsNone(box["planned_end_text"])

    def test_second_run_no_duplicates(self):
        with TempData():
            site = saintmarc_site()
            sm(site, D1)
            _, st = sm(site, D2)
            self.assertEqual(st["saintmarc"]["new_products"], 0)
            self.assertEqual(len(crawl.load_chain_products("saintmarc")), 4)


if __name__ == "__main__":
    unittest.main()
