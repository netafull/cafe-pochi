import json
import unittest

import helpers
from helpers import FakeSite, TempData, prod, run_chain, TULLYS, KOMEDA
import crawl
import synth as S

D1, D2, D3 = "2026-11-03", "2026-11-04", "2026-11-05"
LINEUP = f"{TULLYS}/menu/seasonal_lineup/"

DRINK = "/menu/drink/coffee/26test_latte.html"
FOOD = "/menu/food/pastry/26test_pie.html"
BEANS = "/menu/beans/varietal/26_test_beans.html"
GOODS = "/menu/goods/mug/test_mug.html"


def tullys_site(items=None, details=None, press=None):
    items = items if items is not None else [
        (DRINK, "テスト季節ラテ", "2026-10-07 11:09:12"),
        (FOOD, "テスト季節パイ", "2026-10-07 10:13:41"),
        (BEANS, "【店舗限定】テスト豆100％", "2026-10-14 10:24:48"),
        (GOODS, "テストマグ", "2026-10-14 10:24:48"),
    ]
    site = FakeSite({f"{TULLYS}/robots.txt": "User-Agent: Googlebot\nSitemap: https://www.tullys.co.jp/sitemap.xml"})
    site.set(LINEUP, S.tullys_lineup_html(items))
    site.set(TULLYS + DRINK, S.tullys_detail_html("テスト季節ラテ", prices=(("Short", 630), ("Tall", 690)), allergens=("卵", "乳"),
                                                  energy=(("HOT", "Short", 231), ("HOT", "Tall", 302), ("COLD", "Tall", 236))))
    site.set(TULLYS + FOOD, S.tullys_detail_html("テスト季節パイ", prices=(("1個", 390),), allergens=("卵", "小麦"), bare_kcal=279))
    for k, v in (details or {}).items():
        site.set(TULLYS + k, v)
    site.set(f"{TULLYS}/company/pressrelease/2026_index.html", S.tullys_press_html(press if press is not None else [
        ("26/10/08", "新作「テスト季節ラテ」を10月7日より発売", "/company/pressrelease/2026/10/a.html"),
        ("26/10/01", "関係のないお知らせ", "/company/pressrelease/2026/10/b.html"),
    ]))
    return site


def tl(site, day):
    return run_chain(crawl.crawl_tullys, site, day)


class TestTullys(unittest.TestCase):
    def test_records_drink_and_food_only(self):
        with TempData():
            _, st = tl(tullys_site(), D1)
            ids = {p["product_id"] for p in crawl.load_chain_products("tullys")}
            self.assertEqual(ids, {"drink-coffee-26test_latte", "food-pastry-26test_pie"})  # 豆・グッズは記録しない
            self.assertEqual(len(st["tullys"]["skipped_non_food"]), 2)

    def test_detail_fields(self):
        with TempData():
            tl(tullys_site(), D1)
            p = prod("tullys", "drink-coffee-26test_latte")
            self.assertEqual((p["price"], p["price_max"]), (630, 690))
            self.assertEqual([v["label"] for v in p["price_variants"]], ["Short", "Tall"])
            self.assertEqual(p["nutrition"]["kcal"], 302)  # HOT Tall
            self.assertEqual(len(p["nutrition_variants"]), 3)
            self.assertEqual(p["allergens"]["contains"], ["卵", "乳"])
            self.assertEqual(p["listed_at"], "2026-10-07")
            self.assertEqual(p["official_url"], TULLYS + DRINK)
            f = prod("tullys", "food-pastry-26test_pie")
            self.assertEqual((f["price"], f["nutrition"]["kcal"]), (390, 279))

    def test_launch_date_from_press_title(self):
        with TempData():
            tl(tullys_site(), D1)
            p = prod("tullys", "drink-coffee-26test_latte")
            self.assertEqual(p["launch_date"], "2026-10-07")
            self.assertEqual(p["news_url"], TULLYS + "/company/pressrelease/2026/10/a.html")
            self.assertIsNone(prod("tullys", "food-pastry-26test_pie")["launch_date"])

    def test_no_description_or_image_saved(self):
        with TempData() as base:
            tl(tullys_site(), D1)
            for f in (base / "products" / "tullys").glob("*.json"):
                t = f.read_text(encoding="utf-8")
                for bad in ("image", "description", ".jpg", "thumbnail"):
                    self.assertNotIn(bad, t)

    def test_detail_404_still_records_product(self):
        site = tullys_site()
        site.routes.pop(TULLYS + DRINK)
        with TempData():
            pending, st = tl(site, D1)
            p = prod("tullys", "drink-coffee-26test_latte")
            self.assertIsNotNone(p)
            self.assertIsNone(p["nutrition"])
            self.assertEqual(st["tullys"]["failed"], 1)
            self.assertIn("drink-coffee-26test_latte", pending["tullys"])
            # 次回、詳細が取れれば埋まる
            site.set(TULLYS + DRINK, S.tullys_detail_html("テスト季節ラテ", prices=(("Tall", 690),), energy=(("HOT", "Tall", 302),)))
            tl(site, D2)
            self.assertEqual(prod("tullys", "drink-coffee-26test_latte")["nutrition"]["kcal"], 302)

    def test_end_after_two_days_and_detail_not_refetched_daily(self):
        site = tullys_site()
        with TempData():
            tl(site, D1)
            n = site.count(DRINK)
            tl(site, D2)
            self.assertEqual(site.count(DRINK), n)  # 7日たつまで詳細は取り直さない
            only_food = tullys_site(items=[(FOOD, "テスト季節パイ", "2026-10-07 10:13:41")])
            tl(only_food, D2)
            self.assertEqual(prod("tullys", "drink-coffee-26test_latte")["status"], "on_sale")
            tl(only_food, D3)
            self.assertEqual(prod("tullys", "drink-coffee-26test_latte")["status"], "ended")

    def test_recheck_after_a_week_tracks_price_change(self):
        with TempData():
            tl(tullys_site(), D1)
            site = tullys_site(details={DRINK: S.tullys_detail_html("テスト季節ラテ", prices=(("Tall", 720),), energy=(("HOT", "Tall", 302),))})
            tl(site, "2026-11-12")
            p = prod("tullys", "drink-coffee-26test_latte")
            self.assertEqual(p["price"], 720)
            self.assertEqual([h["price"] for h in p["price_history"]], [630, 720])

    def test_empty_lineup_is_failure_not_end(self):
        with TempData():
            tl(tullys_site(), D1)
            site = tullys_site(items=[])
            _, st = tl(site, D2)
            self.assertFalse(st["tullys"]["list_ok"])
            tl(site, D3)
            self.assertTrue(all(p["status"] == "on_sale" for p in crawl.load_chain_products("tullys")))

    def test_blocked(self):
        site = tullys_site()
        site.blocked_hosts.add("tullys.co.jp")
        with TempData():
            _, st = tl(site, D1)
            self.assertTrue(st["tullys"]["blocked"])

    def test_robots_with_only_googlebot_group_allows_us(self):
        with TempData():
            site = tullys_site()
            tl(site, D1)
            self.assertGreater(len(site.calls), 2)


# ---------------------------------------------------------------------------

M1, M2, M3 = "90001", "90002", "90003"


def komeda_site(items=None, allergy=None, notices=None, wrap="dict"):
    items = items if items is not None else [
        {"id": M1, "name": "テストケーキ（単品）", "price": "530～590円"},
        {"id": M2, "name": "テストバーガー", "price": "950～1020円", "large_type": "1-4"},
        {"id": M3, "name": "隠しメニュー", "hidden": True},
    ]
    site = FakeSite()
    site.set(f"{KOMEDA}/v1/hp/menu?brand_type=1&category=903", S.komeda_menu_json(items, wrap))
    for i in (M1, M2, M3):
        site.set(f"{KOMEDA}/v1/hp/menu-allergy/{i}", (allergy or {}).get(i) or S.komeda_allergy_json("300" if i == M1 else "620", ("卵", "乳成分")))
    site.set(f"{KOMEDA}/v1/hp/notice?limit=20&type=2&offset=0", notices if notices is not None else S.komeda_notices_json([
        ("季節のケーキ販売のお知らせ", "2026.10.20", "<div>10月28日（水）より、季節限定ケーキが登場！<br>◆テストケーキ 530円～590円</div>"),
        ("バーガー販売のお知らせ", "2026.10.06", "<div>「テストバーガー」を10月14日（水）より販売</div>"),
    ]))
    return site


def km(site, day):
    return run_chain(crawl.crawl_komeda, site, day)


class TestKomeda(unittest.TestCase):
    def test_records_non_hidden(self):
        with TempData():
            site = komeda_site()
            _, st = km(site, D1)
            self.assertEqual({p["product_id"] for p in crawl.load_chain_products("komeda")}, {M1, M2})
            self.assertTrue(st["komeda"]["list_ok"])
            self.assertEqual(site.count("menu-allergy"), 2)

    def test_price_range_kcal_allergens(self):
        with TempData():
            km(komeda_site(), D1)
            p = prod("komeda", M1)
            self.assertEqual((p["price"], p["price_max"]), (530, 590))
            self.assertEqual(p["nutrition"]["kcal"], 300)
            self.assertEqual(p["allergens"]["contains"], ["卵", "乳成分"])
            self.assertEqual(p["category"], "デザート")
            self.assertEqual(p["limited_kind"], "季節限定")
            self.assertEqual(p["official_url"], "https://www.komeda.co.jp/menu/?brand=1")

    def test_launch_date_from_notice_body_and_title(self):
        with TempData():
            km(komeda_site(), D1)
            self.assertEqual(prod("komeda", M1)["launch_date"], "2026-10-28")  # 本文の「10月28日（水）より」
            self.assertEqual(prod("komeda", M2)["launch_date"], "2026-10-14")

    def test_allergy_not_refetched_daily(self):
        site = komeda_site()
        with TempData():
            km(site, D1)
            n = site.count("menu-allergy")
            km(site, D2)
            self.assertEqual(site.count("menu-allergy"), n)
            km(site, "2026-11-12")  # 7日以上たったので再確認
            self.assertEqual(site.count("menu-allergy"), n + 2)

    def test_allergy_failure_records_without_nutrition(self):
        site = komeda_site()
        site.routes[f"{KOMEDA}/v1/hp/menu-allergy/{M1}"] = OSError("down")
        with TempData():
            _, st = km(site, D1)
            self.assertIsNone(prod("komeda", M1)["nutrition"])
            self.assertEqual(st["komeda"]["failed"], 1)

    def test_structure_change_is_list_failed_and_does_not_end(self):
        with TempData():
            km(komeda_site(), D1)
            for wrap, day in (("list", D2), ("list", D3)):
                _, st = km(komeda_site(wrap=wrap), day)
                self.assertFalse(st["komeda"]["list_ok"])
                self.assertEqual(crawl.collect_problems(st)["list_failed"], ["komeda"])
            self.assertTrue(all(p["status"] == "on_sale" for p in crawl.load_chain_products("komeda")))

    def test_empty_menus_is_failure(self):
        with TempData():
            km(komeda_site(), D1)
            _, st = km(komeda_site(items=[]), D2)
            self.assertFalse(st["komeda"]["list_ok"])

    def test_end_after_two_days(self):
        with TempData():
            km(komeda_site(), D1)
            one = komeda_site(items=[{"id": M2, "name": "テストバーガー", "price": "950～1020円", "large_type": "1-4"}])
            km(one, D2)
            self.assertEqual(prod("komeda", M1)["status"], "on_sale")
            _, st = km(one, D3)
            self.assertEqual(prod("komeda", M1)["status"], "ended")
            self.assertEqual(st["komeda"]["ended"], 1)

    def test_no_description_saved(self):
        with TempData() as base:
            km(komeda_site(), D1)
            for f in (base / "products" / "komeda").glob("*.json"):
                t = f.read_text(encoding="utf-8")
                for bad in ("description", "photo", ".jpg", "contents"):
                    self.assertNotIn(bad, t)

    def test_burst_guard_small_list(self):
        with TempData():
            km(komeda_site(), D1)
            many = [{"id": f"91{i:03d}", "name": f"テスト新{i}"} for i in range(10)] + [{"id": M1, "name": "テストケーキ（単品）"}]
            site = komeda_site(items=many)
            for i in range(10):
                site.set(f"{KOMEDA}/v1/hp/menu-allergy/91{i:03d}", S.komeda_allergy_json())
            _, st = km(site, D2)
            self.assertIn("suspected_restructure", st["komeda"])
            self.assertEqual(st["komeda"]["new_products"], 0)


if __name__ == "__main__":
    unittest.main()
