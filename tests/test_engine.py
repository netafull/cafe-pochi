"""巡回の共通部分(販売状況の追跡・歯止め・急減の検知・実行要約・連続日数・設定)のテスト。ネットワーク不使用。"""
import json
import unittest
from unittest import mock

import helpers
from helpers import FakeSite, TempData, run_chain, BlockedError
import crawl
import synth as S


def new_prod(**kw):
    p = crawl.base_product("starbucks", "1", "テスト", "2026-11-03T07:00:00+09:00", "2026-11-03")
    crawl.set_initial_price(p, 500, None, "2026-11-03")
    p.update(kw)
    return p


class TestConfig(unittest.TestCase):
    def test_user_agent_is_plain_and_honest(self):
        self.assertEqual(crawl.UA, "CafePochi/1.0 (+https://cafe.netaful.jp/)")
        self.assertNotIn("Mozilla", crawl.UA)

    def test_intervals_at_least_one_second_and_limits_per_chain(self):
        chains = [c["slug"] for c in crawl.CONFIG["chains"]]
        self.assertEqual(sorted(chains), sorted(crawl.CRAWLERS))
        for c in chains:
            self.assertGreaterEqual(max(crawl.INTERVALS.get(c, 2), crawl.MIN_INTERVAL), 1)
            self.assertIn(c, crawl.MAX_REQUESTS)

    def test_throttle_never_below_one_second(self):
        with mock.patch.dict(crawl.INTERVALS, {"starbucks": 0}):
            t = crawl.Throttle()
            with mock.patch.object(crawl.time, "sleep") as sl, mock.patch.object(crawl.time, "monotonic", side_effect=[0.0, 0.0, 0.2, 0.2]):
                t.wait("starbucks")
                t.wait("starbucks")
            slept = sl.call_args.args[0]
            self.assertGreaterEqual(slept, 0.8 - 1e-9)

    def test_getter_enforces_request_cap(self):
        g = crawl.Getter()
        g.throttle.wait = lambda chain: None
        with mock.patch.dict(crawl.MAX_REQUESTS, {"komeda": 2}), mock.patch.object(crawl, "fetch", return_value="x"):
            g("komeda", "https://a/1")
            g("komeda", "https://a/2")
            with self.assertRaises(RuntimeError):
                g("komeda", "https://a/3")

    def test_ads_and_ga_start_empty(self):
        self.assertEqual(crawl.CONFIG["adsense_client_id"], "")
        self.assertEqual(crawl.CONFIG["adsense_ad_slot"], "")
        self.assertEqual(crawl.CONFIG["ga_measurement_id"], "")
        self.assertEqual(crawl.CONFIG["top_title"], "カフェポチ｜季節限定メニューのカロリー・価格アーカイブ")
        self.assertEqual(crawl.CONFIG["site_title"], "カフェポチ")

    def test_chain_colors_have_dark_variants(self):
        for c in crawl.CONFIG["chains"]:
            self.assertRegex(c["color"], r"^#[0-9A-Fa-f]{6}$")
            self.assertRegex(c["color_dark"], r"^#[0-9A-Fa-f]{6}$")


class TestTouchSeen(unittest.TestCase):
    def test_price_history_only_on_change(self):
        p = new_prod()
        crawl.touch_seen(p, 500, None, "2026-11-04")
        self.assertEqual(len(p["price_history"]), 1)
        crawl.touch_seen(p, 550, 600, "2026-11-05")
        self.assertEqual(p["price_history"][-1], {"date": "2026-11-05", "price": 550, "price_max": 600})
        self.assertEqual((p["price"], p["price_max"]), (550, 600))
        crawl.touch_seen(p, None, None, "2026-11-06")  # 価格が取れない日は変えない
        self.assertEqual(p["price"], 550)

    def test_relaunch_moves_last_season_to_seasons(self):
        p = new_prod(launch_date="2026-11-02", news_title="見出し", news_url="https://x")
        p["status"], p["ended_observed_at"] = "ended", "2026-12-20"
        crawl.touch_seen(p, 500, None, "2027-11-01")
        self.assertEqual(p["status"], "on_sale")
        self.assertEqual(p["seasons"][0]["launch_date"], "2026-11-02")
        self.assertEqual(p["seasons"][0]["ended_observed_at"], "2026-12-20")
        self.assertIsNone(p["launch_date"])
        self.assertEqual(p["relaunches"][0]["reappeared_at"], "2027-11-01")

    def test_missing_counts_reset_when_back(self):
        p = new_prod(missed_runs=1, last_miss_date="2026-11-04")
        crawl.touch_seen(p, 500, None, "2026-11-05")
        self.assertEqual((p["missed_runs"], p["last_miss_date"]), (0, None))


class TestRatioGuard(unittest.TestCase):
    def test_sudden_drop_is_ignored_for_two_days_then_accepted(self):
        with TempData():
            self.assertTrue(crawl.ratio_ok("komeda", 20, "2026-11-01"))
            self.assertFalse(crawl.ratio_ok("komeda", 5, "2026-11-02"))
            self.assertFalse(crawl.ratio_ok("komeda", 5, "2026-11-02"))  # 同じ日は数えない
            self.assertFalse(crawl.ratio_ok("komeda", 5, "2026-11-03"))
            self.assertTrue(crawl.ratio_ok("komeda", 5, "2026-11-04"))  # 3日続いたので本当の入れ替えとして受け入れる
            self.assertTrue(crawl.ratio_ok("komeda", 5, "2026-11-05"))

    def test_small_lists_are_exempt(self):
        with TempData():
            self.assertTrue(crawl.ratio_ok("tullys", 6, "2026-11-01"))
            self.assertTrue(crawl.ratio_ok("tullys", 2, "2026-11-02"))

    def test_drop_does_not_end_products(self):
        with TempData():
            # 10品が一覧にあり、翌日から2品に急減しても、3日目までは終了判定しない
            ids = [(f"8{i:04d}", f"テスト{i}") for i in range(10)]
            full = FakeSite()
            def site(items):
                s = FakeSite()
                s.set("https://eu.komeda.co.jp/v1/hp/menu?brand_type=1&category=903",
                      S.komeda_menu_json([{"id": i, "name": n} for i, n in items]))
                s.set("https://eu.komeda.co.jp/v1/hp/notice?limit=20&type=2&offset=0", S.komeda_notices_json([]))
                for i, _ in ids:
                    s.set(f"https://eu.komeda.co.jp/v1/hp/menu-allergy/{i}", S.komeda_allergy_json())
                return s
            run_chain(crawl.crawl_komeda, site(ids), "2026-11-01")
            low = site(ids[:2])
            for d in ("2026-11-02", "2026-11-03"):
                _, st = run_chain(crawl.crawl_komeda, low, d)
                self.assertFalse(st["komeda"]["list_ok"])
            self.assertTrue(all(p["status"] == "on_sale" for p in crawl.load_chain_products("komeda")))
            run_chain(crawl.crawl_komeda, low, "2026-11-04")  # 3日目に受け入れ(1回目の消失として数える)
            run_chain(crawl.crawl_komeda, low, "2026-11-05")
            ended = [p for p in crawl.load_chain_products("komeda") if p["status"] == "ended"]
            self.assertEqual(len(ended), 8)


class TestRobots(unittest.TestCase):
    def run_robots(self, text, path):
        site = FakeSite({"https://h.example/robots.txt": text} if text is not None else {})
        st = {"list_requests": 0}
        r = crawl.Robots(site, "starbucks", st)
        return r.allowed("https://h.example", path), st, site

    def test_disallow_is_respected(self):
        ok, st, site = self.run_robots("User-agent: *\nDisallow: /private/", "/private/x")
        self.assertFalse(ok)
        self.assertEqual(st["list_requests"], 1)

    def test_404_and_html_mean_allow_all(self):
        self.assertTrue(self.run_robots(None, "/anything")[0])
        self.assertTrue(self.run_robots("<html><body>x</body></html>", "/anything")[0])

    def test_fetched_once_per_host(self):
        site = FakeSite({"https://h.example/robots.txt": "User-agent: *\nDisallow:"})
        r = crawl.Robots(site, "starbucks", {"list_requests": 0})
        r.allowed("https://h.example", "/a")
        r.allowed("https://h.example", "/b")
        self.assertEqual(site.count("robots.txt"), 1)

    def test_blocked_robots_raises(self):
        site = FakeSite()
        site.blocked_hosts.add("h.example")
        with self.assertRaises(BlockedError):
            crawl.Robots(site, "starbucks", {"list_requests": 0}).allowed("https://h.example", "/a")


class TestRunSummary(unittest.TestCase):
    def test_fetch_failure_is_list_failed(self):
        def down(chain, url):
            raise OSError("down")
        _, stats = run_chain(crawl.crawl_komeda, down, "2026-11-03")
        self.assertEqual(crawl.collect_problems(stats), {"blocked": [], "list_failed": ["komeda"], "errors": []})

    def test_blocked_is_not_list_failed(self):
        def blocked(chain, url):
            raise BlockedError("HTTP 403")
        _, stats = run_chain(crawl.crawl_komeda, blocked, "2026-11-03")
        self.assertEqual(crawl.collect_problems(stats), {"blocked": ["komeda"], "list_failed": [], "errors": []})

    def test_suspected_restructure_is_not_list_failed(self):
        stats = {"starbucks": {"list_ok": False, "suspected_restructure": {"count": 40}}}
        self.assertEqual(crawl.collect_problems(stats)["list_failed"], [])

    def test_healthy_chain_has_no_problem_and_detail_failure_is_only_counted(self):
        stats = {"tullys": {"list_ok": True, "failed": 3}}
        self.assertEqual(crawl.collect_problems(stats), {"blocked": [], "list_failed": [], "errors": []})
        with TempData() as base:
            crawl.save_run_summary(stats)
            data = json.loads((base / "run_summary.json").read_text())
        self.assertEqual(data["detail_failed"], {"tullys": 3})

    def test_summary_file_has_new_fields(self):
        stats = {"tullys": {"list_ok": False, "blocked": True, "failed": 2},
                 "doutor": {"list_ok": False, "failed": 1},
                 "komeda": {"list_ok": False, "error": "RuntimeError: x"},
                 "starbucks": {"list_ok": True, "new_products": 1}}
        with TempData() as base:
            crawl.save_run_summary(stats, {"doutor": 2})
            data = json.loads((base / "run_summary.json").read_text())
        self.assertEqual(data["blocked"], ["tullys"])
        self.assertEqual(data["list_failed"], ["doutor"])
        self.assertEqual(data["errors"], ["komeda"])
        self.assertEqual(data["streaks"], {"doutor": 2})
        self.assertEqual(data["new"]["starbucks"], 1)

    def test_main_records_exception_as_error_and_streak(self):
        def boom(getter, pending, stats, today=None):
            raise RuntimeError("bug")
        with TempData() as base, mock.patch.dict(crawl.CRAWLERS, {"tullys": boom}), mock.patch.object(crawl, "Getter", mock.MagicMock()):
            self.assertEqual(crawl.main(["tullys"]), 0)
            data = json.loads((base / "run_summary.json").read_text())
            pend = json.loads((base / "pending.json").read_text())
        self.assertEqual(data["errors"], ["tullys"])
        self.assertEqual(data["streaks"], {"tullys": 1})
        self.assertEqual(pend["_health"]["tullys"]["days"], 1)

    def test_main_rejects_unknown_chain(self):
        self.assertEqual(crawl.main(["mcdonalds"]), 2)


class TestHealthStreak(unittest.TestCase):
    def test_streak_counts_by_date(self):
        pending = {}
        bad = {"doutor": {"list_ok": False}}
        self.assertEqual(crawl.update_health(pending, bad, "2026-11-01"), {"doutor": 1})
        self.assertEqual(crawl.update_health(pending, bad, "2026-11-01"), {"doutor": 1})  # 同日の再実行
        self.assertEqual(crawl.update_health(pending, bad, "2026-11-02"), {"doutor": 2})
        self.assertEqual(crawl.update_health(pending, bad, "2026-11-03"), {"doutor": 3})
        self.assertEqual(crawl.update_health(pending, bad, "2026-11-06"), {"doutor": 1})  # 空いたら数え直し

    def test_recovery_clears_and_untouched_kept(self):
        pending = {}
        crawl.update_health(pending, {"tullys": {"blocked": True}, "doutor": {"list_ok": False}}, "2026-11-01")
        crawl.update_health(pending, {"tullys": {"list_ok": True}}, "2026-11-02")
        self.assertNotIn("tullys", pending["_health"])
        self.assertEqual(pending["_health"]["doutor"]["days"], 1)
        crawl.update_health(pending, {"doutor": {"list_ok": True}}, "2026-11-02")
        self.assertNotIn("_health", pending)


class TestBurstFilterUnit(unittest.TestCase):
    def test_thresholds_per_chain(self):
        self.assertGreater(crawl.burst_threshold("starbucks", 36), crawl.burst_threshold("komeda", 10))
        # 全件が新規になる(ID体系の変更)のは、どのチェーンでも閾値を超える
        for chain, size in (("starbucks", 36), ("tullys", 9), ("komeda", 10), ("doutor", 8)):
            self.assertGreater(size, crawl.burst_threshold(chain, size), chain)

    def test_first_run_exempt_and_escalation_days(self):
        with TempData():
            stats = {"komeda": crawl.new_stats()}
            items = {f"n{i}": {} for i in range(30)}
            self.assertEqual(crawl.burst_filter("komeda", items, None, {}, stats, "2026-11-01"), items)
            pending = {}
            for n, d in enumerate(("2026-11-02", "2026-11-03", "2026-11-04"), start=1):
                crawl.burst_filter("komeda", items, {"old"}, pending, stats, d)
                self.assertEqual(pending["komeda"][crawl.BURST_KEY]["consecutive_days"], n)
            self.assertFalse(stats["komeda"]["list_ok"])


if __name__ == "__main__":
    unittest.main()
