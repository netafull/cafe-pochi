import datetime
import json
import unittest

import helpers  # noqa: F401
import parsers as P
import synth


class TestCommon(unittest.TestCase):
    def test_normalize_name_drops_marks_and_spaces(self):
        self.assertEqual(P.normalize_name("フラペチーノ® ＆ ティー"), P.normalize_name("フラペチーノ&ティー"))
        self.assertEqual(P.normalize_name("アールグレイ&amp;チョコ"), P.normalize_name("アールグレイ チョコ"))

    def test_parse_yen(self):
        self.assertEqual(P.parse_yen("￥1,380"), 1380)
        self.assertEqual(P.parse_yen("¥ 560"), 560)
        self.assertEqual(P.parse_yen("680円"), 680)
        self.assertIsNone(P.parse_yen("価格"))

    def test_launch_date_from_text(self):
        ref = datetime.date(2026, 10, 26)
        self.assertEqual(P.launch_date_from_text("3種を11月2日（月）より発売", ref), "2026-11-02")
        self.assertEqual(P.launch_date_from_text("10月28日(水)から登場", ref), "2026-10-28")
        # 終わりの日付は拾わない
        self.assertIsNone(P.launch_date_from_text("12月25日まで予約受付", ref))
        self.assertEqual(P.launch_date_from_text("12月25日まで予約、11月2日より発売", ref), "2026-11-02")
        # 年またぎ
        self.assertEqual(P.launch_date_from_text("1月5日より発売", datetime.date(2026, 12, 20)), "2027-01-05")
        self.assertEqual(P.launch_date_from_text("12月20日より発売", datetime.date(2027, 1, 5)), "2026-12-20")
        self.assertIsNone(P.launch_date_from_text("おいしいお知らせ", ref))

    def test_stable_id(self):
        self.assertEqual(P.stable_id("sx", "ミルクレープ"), P.stable_id("sx", "ミルク　レープ"))
        self.assertNotEqual(P.stable_id("sx", "A商品"), P.stable_id("sx", "B商品"))


class TestStarbucksParsers(unittest.TestCase):
    def test_list_page(self):
        pages = synth.sbux_list_pages([synth.sbux_item("111", "テスト ラテ", 600, 700)])
        d = P.parse_sbux_list(pages[0])
        self.assertEqual(d["count"], 1)
        self.assertEqual((d["items"][0]["price_min"], d["items"][0]["price_max"]), (600, 700))
        # 画像URLなどは読まない
        self.assertNotIn("image_url", d["items"][0])

    def test_list_structure_error(self):
        with self.assertRaises(ValueError):
            P.parse_sbux_list(json.dumps({"items": []}))
        with self.assertRaises(ValueError):
            P.parse_sbux_list(json.dumps({"count": 1, "item": [{"item_name": "x"}]}))

    def test_nutrition_standard_milk_and_sizes(self):
        nut = P.parse_sbux_nutrition(synth.sbux_nutrition_json(
            beverage_c01=[synth.sbux_product("111", "テスト ラテ", {"SHORT": 200, "TALL": 250, "GRANDE": 300})],
            food_c01=[synth.sbux_product("222", "テストスコーン", {"STANDARD": 400})]))
        v = P.sbux_nutrition_variants(nut["111"])
        self.assertEqual([x["label"] for x in v], ["Short", "Tall", "Grande"])
        self.assertEqual(v[1]["nutrition"]["kcal"], 250)  # soy(+30)ではなく milk が標準
        self.assertEqual(P.sbux_representative(v)["label"], "Tall")
        f = P.sbux_nutrition_variants(nut["222"])
        self.assertEqual(len(f), 1)
        self.assertIsNone(f[0]["label"])
        self.assertTrue(nut["111"]["seasonal"])

    def test_no_change_milk_is_preferred(self):
        prod = synth.sbux_product("1", "x", {"TALL": 100})
        prod["nutrition_by_milk"]["TALL"].append({**prod["nutrition_by_milk"]["TALL"][0], "_milk_type": "no_change", "calory": 77})
        nut = P.parse_sbux_nutrition(synth.sbux_nutrition_json(beverage_c01=[prod]))
        self.assertEqual(P.sbux_nutrition_variants(nut["1"])[0]["nutrition"]["kcal"], 77)

    def test_nutrition_structure_error(self):
        with self.assertRaises(ValueError):
            P.parse_sbux_nutrition(json.dumps({"x": 1}))
        with self.assertRaises(ValueError):
            P.parse_sbux_nutrition(json.dumps({"STARBUCKS": {"beverage": {}, "food": {}}}))

    def test_allergens_standard_milk_row(self):
        al = P.parse_sbux_allergens(synth.sbux_allergens_json(
            bev=[synth.sbux_allergen_product("111", "x", milk=1, wheat=1)]))
        self.assertEqual(al["111"]["contains"], ["乳", "小麦"])  # soy 行の大豆は含めない

    def test_feed(self):
        feed = P.parse_wp_feed(synth.wp_feed([("見出し &amp; テスト", "https://x/1", "Mon, 02 Nov 2026 04:00:00 +0000", "概要")]))
        self.assertEqual(feed[0]["title"], "見出し & テスト")
        self.assertEqual(feed[0]["date"], "2026-11-02")
        with self.assertRaises(ValueError):
            P.parse_wp_feed("<html></html>")


class TestTullysParsers(unittest.TestCase):
    def test_lineup(self):
        items = P.parse_tullys_lineup(synth.tullys_lineup_html([
            ("/menu/drink/coffee/a.html", "テストラテ", "2026-10-07 11:09:12")]))
        self.assertEqual(items[0]["path"], "/menu/drink/coffee/a.html")
        self.assertEqual(items[0]["listed_at"], "2026-10-07")

    def test_detail_prices_allergens_energy(self):
        d = P.parse_tullys_detail(synth.tullys_detail_html(
            "テストラテ", prices=(("Short", 630), ("Tall", 690)), allergens=("卵", "乳"),
            energy=(("HOT", "Short", 231), ("HOT", "Tall", 302), ("COLD", "Tall", 236))))
        self.assertEqual(d["prices"], [{"label": "Short", "price": 630}, {"label": "Tall", "price": 690}])
        self.assertEqual(d["allergens"], {"contains": ["卵", "乳"], "none_listed": False})
        self.assertEqual([(e["label"], e["kcal"]) for e in d["energy"]],
                         [("HOT Short", 231.0), ("HOT Tall", 302.0), ("COLD Tall", 236.0)])

    def test_detail_bare_kcal_and_no_allergen_items(self):
        d = P.parse_tullys_detail(synth.tullys_detail_html("テストパイ", prices=(("1個", 390),), bare_kcal=279, allergens=("卵", "小麦")))
        self.assertEqual(d["energy"], [{"label": "", "kcal": 279.0}])
        d = P.parse_tullys_detail(synth.tullys_detail_html("テスト水", allergens=()))
        self.assertEqual(d["allergens"], {"contains": [], "none_listed": True})

    def test_detail_without_allergy_section_is_none(self):
        d = P.parse_tullys_detail(synth.tullys_detail_html("テスト豆", allergy_section=False, energy=()))
        self.assertIsNone(d["allergens"])
        self.assertEqual(d["energy"], [])

    def test_press(self):
        rows = P.parse_tullys_press(synth.tullys_press_html([("26/10/08", "テスト「ペルー」を10月14日(水)より発売", "/p/1.html")]))
        self.assertEqual(rows[0]["date"], "2026-10-08")
        self.assertEqual(P.launch_date_from_text(rows[0]["title"], datetime.date(2026, 10, 8)), "2026-10-14")


class TestKomedaParsers(unittest.TestCase):
    def test_price_range(self):
        self.assertEqual(P.komeda_price_range("680～740円"), (680, 740))
        self.assertEqual(P.komeda_price_range("530円～590円（税込）"), (530, 590))
        self.assertEqual(P.komeda_price_range("950円"), (950, 950))
        self.assertIsNone(P.komeda_price_range(""))

    def test_menu_dict_response(self):
        d = P.parse_komeda_menu(synth.komeda_menu_json([{"id": "1", "name": "テストケーキ"}]))
        self.assertEqual(d["items"][0]["price_min"], 530)
        self.assertEqual(d["categories"]["1-3"], "デザート")

    def test_menu_structure_errors(self):
        # レスポンスは dict。list だけ・menus 無し・id/name 無しは構造変更の疑い
        with self.assertRaises(ValueError):
            P.parse_komeda_menu(synth.komeda_menu_json([{"id": "1", "name": "x"}], wrap="list"))
        with self.assertRaises(ValueError):
            P.parse_komeda_menu(json.dumps({"data": []}))
        with self.assertRaises(ValueError):
            P.parse_komeda_menu(json.dumps({"menus": [{"name": "x"}]}))

    def test_allergy(self):
        d = P.parse_komeda_allergy(synth.komeda_allergy_json("237", ("卵", "大豆"), "注記です"))
        self.assertEqual(d, {"kcal": 237.0, "contains": ["卵", "大豆"], "note": "注記です"})
        with self.assertRaises(ValueError):
            P.parse_komeda_allergy(json.dumps({"x": 1}))

    def test_notices(self):
        n = P.parse_komeda_notices(synth.komeda_notices_json([("お知らせ", "2026.10.20", "<div>10月28日（水）より、<br>登場</div>")]))
        self.assertEqual(n[0]["date"], "2026-10-20")
        self.assertIn("10月28日", n[0]["text"])


class TestDoutorParsers(unittest.TestCase):
    def test_name_keys_handle_subtitles_and_width(self):
        a = P.doutor_name_keys("蜜芋オレ ～種子島産安納芋使用～")
        b = P.doutor_name_keys("蜜芋オレ~種子島産安納芋使用~")
        c = P.doutor_name_keys("蜜芋オレ")
        self.assertTrue(a & b)
        self.assertTrue(a & c)
        self.assertFalse(P.doutor_name_keys("ミルクレープ") & P.doutor_name_keys("パリパリミルクレープ"))

    def test_season_items_and_exclusions(self):
        html = synth.doutor_season_html([
            [synth.doutor_item("テストモーニング", "¥570～")],
            [synth.doutor_item("テストオレ", "¥680", size="M")],
            [synth.doutor_item_noprice("今年はピック付！"),
             synth.doutor_item("テストケーキ", area=[("販売価格", "¥4,300"), ("宅配価格", "¥5,300")]),
             synth.doutor_item_reservation("店頭受取", ("予約承り期間", "お渡し期間"),
                                           ("2026年11月1日(日)～12月10日(木)", "2026年12月14日(月)～12月25日(金)")),
             synth.doutor_item_noprice("■ドトール バリューカードキャンペーン")],
            [synth.doutor_item("テスト豆", "¥1,380", size="コーヒー豆<br>（200g）")],
        ])
        r = P.parse_doutor_season(html, ["コーヒー豆", "ドリップ"])
        names = [i["name"] for i in r["items"]]
        self.assertEqual(names, ["テストモーニング", "テストオレ", "テストケーキ"])
        self.assertEqual(r["skipped"], ["テスト豆"])
        self.assertTrue(r["items"][0]["prices"][0]["from"])
        self.assertEqual(r["items"][1]["prices"][0]["label"], "M")
        cake = r["items"][2]
        self.assertEqual([p["price"] for p in cake["prices"]], [4300, 5300])
        self.assertEqual(cake["reservation"][0]["channel"], "店頭受取")
        self.assertEqual(cake["reservation"][0]["periods"][1]["label"], "お渡し期間")

    def test_season_no_blocks_is_structure_error(self):
        with self.assertRaises(ValueError):
            P.parse_doutor_season("<html></html>")

    def test_cards_and_detail(self):
        cards = P.parse_doutor_cards(synth.doutor_cards_html([("20261009160520", "テストクレープ", True), ("20140818181442", "定番", False)]))
        self.assertEqual([(c["id"], c["is_new"]) for c in cards], [("20261009160520", True), ("20140818181442", False)])
        d = P.parse_doutor_detail(synth.doutor_detail_html("テストクレープ", "¥ 560"))
        self.assertEqual(d["price"], 560)  # HTMLコメントの中の価格は読まない

    def test_allergy_list_and_detail_legend(self):
        lst = P.parse_doutor_allergy_list(synth.doutor_allergy_list_html([("11003073", "テストクレープ")]))
        self.assertEqual(lst, [{"id": "11003073", "name": "テストクレープ"}])
        d = P.parse_doutor_allergy_detail(synth.doutor_allergy_detail_html(
            "テストクレープ", circle=("卵", "乳"), triangle=("えび",), line=("かに",)))
        self.assertEqual(d["allergens"]["contains"], ["卵", "乳"])  # circle(●)=含む
        self.assertEqual(d["allergens"]["partial"], [{"name": "えび", "mark": "△"}])  # triangle(△)=条件つき
        self.assertEqual(d["allergens"]["unknown"], [])
        self.assertEqual(d["variants"][0]["nutrition"]["kcal"], 250.0)

    def test_allergy_unknown_class_is_not_treated_as_contains(self):
        d = P.parse_doutor_allergy_detail(synth.doutor_allergy_detail_html("x", extra_kind="mystery"))
        self.assertEqual(d["allergens"]["unknown"], ["未知の品目"])
        self.assertNotIn("未知の品目", d["allergens"]["contains"])

    def test_allergy_size_tabs(self):
        d = P.parse_doutor_allergy_detail(synth.doutor_allergy_detail_html("x", tabs=[("Mサイズ", 300), ("Lサイズ", 380)]))
        self.assertEqual([(v["label"], v["nutrition"]["kcal"]) for v in d["variants"]], [("Mサイズ", 300.0), ("Lサイズ", 380.0)])


if __name__ == "__main__":
    unittest.main()
