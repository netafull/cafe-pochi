#!/usr/bin/env python3
"""スターバックス・タリーズ・コメダ珈琲店・ドトールコーヒーショップの季節限定・新作メニューを巡回し、
data/products/{starbucks|tullys|komeda|doutor}/{id}.json に1商品1ファイルで蓄積する。

このサイトの核は「販売状況の追跡」。公式サイトは販売が終わると商品を消すので、
一覧に居た商品が消えたら終了とみなして記録を残す(ファストフードポチと同じ設計)。

・既存ファイルは丸ごと上書きしない(初回取得が正)。更新するのは last_seen_at / status /
  price_history 等の追跡用の項目と、まだ空の栄養成分・アレルゲン・発売日だけ。
・**初回から、その時点で季節限定・新作として載っている商品を全部記録する**(ファストフードポチの
  baseline方式=既存品は記録しない、とは違う)。baseline_ids.json は「初回に見えた記録対象のID」を持ち、
  2回目以降の歯止め(新規が急に大量に出たら構造変更とみなして保留)の判定にだけ使う。
・終了判定は「そのチェーンの一覧取得が全部成功した回」だけ行い、さらに「連続2回(別の日)
  見つからなかった」ときに初めて status=ended にする。
・取得失敗はチェーン単位で隔離し(data/pending.json)、他のチェーンへ波及させない。
・ブロック(403・チャレンジ)されたらUA偽装などに切り替えず、止まって報告する。
・4社とも規約が複製・転載・引用を禁じているため、保存するのは事実(商品名・価格・日付・
  栄養成分・アレルゲン)だけ。説明文・画像は読み取りもしない。
"""

from __future__ import annotations

import datetime
import json
import re
import sys
import time
import unicodedata
import urllib.robotparser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import parsers  # noqa: E402
from fetch import BlockedError, NotFoundError, fetch  # noqa: E402

CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / "data"
PRODUCTS = DATA / "products"
PENDING_PATH = DATA / "pending.json"
BASELINE_PATH = DATA / "baseline_ids.json"
RUN_SUMMARY = DATA / "run_summary.json"
LIST_STATE_PATH = DATA / "list_state.json"  # チェーンごとの前回の記録対象の件数(急減の検知用)
JST = datetime.timezone(datetime.timedelta(hours=9))

UA = CONFIG.get("user_agent", "CafePochi/1.0 (+https://cafe.netaful.jp/)")
INTERVALS = CONFIG.get("request_interval_seconds", {})
MAX_REQUESTS = CONFIG.get("max_requests_per_run", {})
MIN_INTERVAL = 1.0  # どのチェーンも1秒未満にはしない
# 連続何回(別の日)見つからなければ終了扱いにするか
MISSES_TO_END = 2
# 詳細(栄養・価格)を取り直す間隔(日)と、1回の実行で取り直す最大件数
RECHECK_DAYS = int(CONFIG.get("recheck_days", 7))
RECHECK_MAX = int(CONFIG.get("recheck_max_per_run", 6))
# 記録対象の件数が前回の半分未満になったら、3日続くまで「一覧が壊れた」とみなして終了判定をしない
RATIO_MIN = 0.5
RATIO_MIN_PREV = 8
RATIO_ACCEPT_DAYS = 3


def now_iso() -> str:
    return datetime.datetime.now(JST).isoformat(timespec="seconds")


def today_jst() -> str:
    return datetime.datetime.now(JST).date().isoformat()


# ---------------------------------------------------------------------------
# 保存まわり
# ---------------------------------------------------------------------------

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, obj, **kw) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, **kw), encoding="utf-8")


def product_path(chain: str, product_id: str) -> Path:
    return PRODUCTS / chain / f"{product_id}.json"


def save_product(chain: str, product: dict) -> None:
    save_json(product_path(chain, product["product_id"]), product)


def load_product(chain: str, product_id: str) -> dict | None:
    return load_json(product_path(chain, product_id), None)


def load_chain_products(chain: str) -> list[dict]:
    out = []
    d = PRODUCTS / chain
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            p = load_json(f, None)
            if p:
                out.append(p)
    return out


class Throttle:
    """チェーンごとに最後のリクエスト時刻を覚え、間隔を守って待つ。"""

    def __init__(self):
        self.last: dict[str, float] = {}

    def wait(self, chain: str) -> None:
        interval = max(INTERVALS.get(chain, 2), MIN_INTERVAL)
        last = self.last.get(chain)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < interval:
                time.sleep(interval - elapsed)
        self.last[chain] = time.monotonic()


class Getter:
    """リクエスト数の上限つきの取得器。テストでは差し替える。素のUA(config の user_agent)で取る。"""

    def __init__(self):
        self.throttle = Throttle()
        self.counts: dict[str, int] = {}

    def __call__(self, chain: str, url: str) -> str:
        limit = MAX_REQUESTS.get(chain)
        if limit is not None and self.counts.get(chain, 0) >= limit:
            raise RuntimeError(f"{chain}: 1回の実行あたりのリクエスト上限({limit})に達しました")
        self.throttle.wait(chain)
        self.counts[chain] = self.counts.get(chain, 0) + 1
        return fetch(url, UA)


# ---------------------------------------------------------------------------
# 販売状況の追跡(チェーン共通)
# ---------------------------------------------------------------------------

def record_price(product: dict, price: int | None, price_max: int | None, today: str, relaunch: bool = False) -> bool:
    """価格(と上限)が前回の記録と違えば price_history に追記する。変わったら True。"""
    if price is None:
        return False
    pmax = price_max if (price_max is not None and price_max != price) else None
    hist = product.setdefault("price_history", [])
    last = hist[-1] if hist else None
    if last is not None and last.get("price") == price and last.get("price_max") == pmax:
        return False
    entry = {"date": today, "price": price}
    if pmax is not None:
        entry["price_max"] = pmax
    if relaunch:
        entry["relaunch"] = True
    if last is not None and last.get("date") == today:
        hist[-1] = entry  # 同じ日の再実行は上書き
    else:
        hist.append(entry)
    product["price"] = price
    product["price_max"] = pmax
    return True


def start_new_season(product: dict, old_ended: str | None) -> None:
    """終了後に同じIDが一覧へ戻ってきた(季節商品の再登場)。昨季の期間を seasons に残し、
    今季の発売日は未確定に戻す(昨季の日付を今季として出さない)。"""
    product.setdefault("seasons", []).append({
        "launch_date": product.get("launch_date"),
        "observed_start": season_ref_for_end(product),
        "ended_observed_at": old_ended,
        "news_url": product.get("news_url"),
        "news_title": product.get("news_title"),
    })
    product["launch_date"] = None
    product["launch_date_source"] = None
    for k in ("news_url", "news_title", "news_date"):
        product[k] = None
    product["launch_search_runs"] = 0


def season_ref_for_end(product: dict) -> str:
    """終わった季の観測の起点日(再登場前の relaunches 末尾、無ければ初回に見つけた日)。"""
    rel = product.get("relaunches") or []
    if rel and rel[-1].get("reappeared_at"):
        return rel[-1]["reappeared_at"]
    return (product.get("first_seen_at") or "")[:10]


def touch_seen(product: dict, entry_price: int | None, entry_price_max: int | None, today: str) -> bool:
    """一覧に居た既存商品の追跡項目を更新する。変更があれば True。

    ・last_seen_at を更新、status=on_sale、見失った回数をリセット
    ・終了後に再登場したら on_sale に戻し、relaunches / seasons に記録
    ・一覧の価格が前回と違えば price_history に追記
    """
    relaunched = False
    if product.get("status") == "ended":
        old_ended = product.get("ended_observed_at")
        start_new_season(product, old_ended)
        product.setdefault("relaunches", []).append({
            "ended_observed_at": old_ended,
            "reappeared_at": today,
        })
        product["status"] = "on_sale"
        product["ended_observed_at"] = None
        relaunched = True
    elif product.get("status") != "on_sale":
        product["status"] = "on_sale"
    if product.get("missed_runs"):
        product["missed_runs"] = 0
        product["last_miss_date"] = None
    product["last_seen_at"] = today  # 日付だけ(同じ日に複数回実行してもgitの差分が増えないように)
    record_price(product, entry_price, entry_price_max, today, relaunch=relaunched)
    return relaunched


def apply_missing(chain: str, seen_ids: set[str], today: str, stats: dict) -> None:
    """一覧取得が全部成功した回にだけ呼ぶ。一覧に居なかった販売中の商品を数え、
    連続 MISSES_TO_END 回(別の日)見つからなければ status=ended にする。"""
    for p in load_chain_products(chain):
        if p["product_id"] in seen_ids:
            continue
        if p.get("status") != "on_sale":
            continue
        if p.get("last_miss_date") == today:
            continue  # 同じ日に何度実行しても1回と数える
        p["missed_runs"] = int(p.get("missed_runs") or 0) + 1
        p["last_miss_date"] = today
        if p["missed_runs"] >= MISSES_TO_END:
            p["status"] = "ended"
            p["ended_observed_at"] = today
            stats["ended"] = stats.get("ended", 0) + 1
        save_product(chain, p)


def new_stats() -> dict:
    return {"list_requests": 0, "detail_requests": 0, "new_products": 0, "ended": 0,
            "failed": 0, "blocked": False, "list_ok": False, "limited_seen": 0}


def baseline_for(chain: str) -> set[str] | None:
    data = load_json(BASELINE_PATH, {})
    ids = data.get(chain)
    return set(ids) if ids is not None else None


def save_baseline(chain: str, ids: set[str]) -> None:
    data = load_json(BASELINE_PATH, {})
    data[chain] = sorted(ids)
    save_json(BASELINE_PATH, data)


def ratio_ok(chain: str, n: int, today: str) -> bool:
    """記録対象の件数が前回の半分未満に急減したら、3日続くまで終了判定をしない(取得の不具合の疑い)。
    季節の入れ替わりで本当に減ったなら、3日目に受け入れて基準を更新する。"""
    state = load_json(LIST_STATE_PATH, {})
    cur = state.get(chain) or {}
    prev = int(cur.get("count") or 0)
    if prev >= RATIO_MIN_PREV and n < prev * RATIO_MIN:
        low = dict(cur.get("low") or {})
        if low.get("date") != today:
            low = {"date": today, "days": int(low.get("days") or 0) + 1, "count": n}
        state[chain] = {**cur, "low": low}
        if low["days"] < RATIO_ACCEPT_DAYS:
            save_json(LIST_STATE_PATH, state, sort_keys=True)
            print(f"[WARNING] {chain}: 記録対象が{n}件(前回{prev}件)に急減。{low['days']}日目なので終了判定はしない",
                  file=sys.stderr)
            return False
    state[chain] = {"count": n, "checked_at": today}
    save_json(LIST_STATE_PATH, state, sort_keys=True)
    return True


# ---------------------------------------------------------------------------
# 歯止め: 1回で「新規候補」が極端に多いときは、公式のページ構造・ID体系の変更とみなして記録しない
# ---------------------------------------------------------------------------
# 2026-10-13 に松屋(ファストフードポチ)が商品URLの日付を一斉に更新し、定番メニューまで37件が
# 「新規」になった事故から入れた仕組み。新規候補(baselineにも既存レコードにも無い項目)が
# max(new_burst_min[チェーン別], 一覧の項目数×new_burst_ratio)を超えたら、その回はそのチェーンの
# 新規を記録せず、終了判定もしない。
# **初回(baselineがまだ無い)は対象外**(カフェポチは初回に季節商品を全部記録する)。
# 保留は翌日以降に候補が閾値以下に戻れば自然に再開する。3日連続で保留なら通知が「要判断」に変わる。
# 季節の入れ替えで一度に新作が10品以上出ることがある(スターバックスのホリデー)ため、
# 閾値はファストフードポチより高め(config の new_burst_min / new_burst_ratio / new_burst_min_by_chain)。

BURST_KEY = "_suspected_restructure"
BURST_ESCALATE_DAYS = 3


def burst_threshold(chain: str, list_size: int) -> int:
    lo = int((CONFIG.get("new_burst_min_by_chain") or {}).get(chain, CONFIG.get("new_burst_min", 15)))
    ratio = float(CONFIG.get("new_burst_ratio", 0.6))
    return max(lo, int(list_size * ratio))


def burst_filter(chain: str, items: dict, baseline: set[str] | None, pending: dict, stats: dict,
                 today: str) -> dict:
    """items({商品ID: 一覧の項目})を受け取り、歯止めが働かなければそのまま返す。
    働いたら、既存レコードのある項目だけを返し(最終確認日の更新だけは続ける)、
    stats[chain]["list_ok"]=False(baseline作成・終了判定を止める)と、保留の記録を残す。"""
    st = stats[chain]
    chain_pending = pending.setdefault(chain, {})
    if baseline is None:  # 初回は対象外
        chain_pending.pop(BURST_KEY, None)
        return items
    candidates = [pid for pid in items if pid not in baseline and load_product(chain, pid) is None]
    limit = burst_threshold(chain, len(items))
    if len(candidates) <= limit:
        chain_pending.pop(BURST_KEY, None)
        return items
    prev = chain_pending.get(BURST_KEY) or {}
    if prev.get("date") == today:
        days = int(prev.get("consecutive_days") or 1)
    else:
        days = int(prev.get("consecutive_days") or 0) + 1
    chain_pending[BURST_KEY] = {
        "reason": "suspected_restructure", "count": len(candidates), "threshold": limit,
        "list_size": len(items), "date": today, "consecutive_days": days,
        "first_date": prev.get("first_date") or today,
        "sample": sorted(candidates)[:5],
    }
    st["suspected_restructure"] = {"count": len(candidates), "threshold": limit, "consecutive_days": days}
    st["list_ok"] = False
    msg = (f"[WARNING] {chain}: 新規候補が一度に{len(candidates)}件(閾値{limit}件・一覧{len(items)}件)。"
           "公式のサイト構造変更の疑い。この回は新規を記録せず、終了判定も行わない")
    if days >= BURST_ESCALATE_DAYS:
        msg += f" [{days}日連続で保留中。運営者の判断が必要]"
    print(msg, file=sys.stderr)
    return {pid: e for pid, e in items.items() if load_product(chain, pid) is not None}


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------

class Robots:
    """ホストごとに robots.txt を1回だけ取る。404・HTML(robots.txtが無いサイトが返すページ)は全許可。
    取得に失敗したときも全許可として続行する(警告のみ)。403等のブロックは BlockedError。"""

    def __init__(self, getter, chain: str, stats_st: dict):
        self.getter = getter
        self.chain = chain
        self.st = stats_st
        self.parsers: dict[str, urllib.robotparser.RobotFileParser] = {}

    def allowed(self, base: str, path: str) -> bool:
        rp = self.parsers.get(base)
        if rp is None:
            text = None
            try:
                text = self.getter(self.chain, base + "/robots.txt")
                self.st["list_requests"] += 1
            except NotFoundError:
                self.st["list_requests"] += 1
            except BlockedError:
                raise
            except Exception as e:  # noqa: BLE001
                print(f"[warn] {self.chain} {base}/robots.txt 取得失敗(全許可として続行): {e}", file=sys.stderr)
            if text and re.search(r"<\s*html", text[:500], re.I):
                text = None  # robots.txtの代わりにHTMLを返すサイト
            rp = urllib.robotparser.RobotFileParser()
            rp.parse((text or "User-agent: *\nAllow: /").splitlines())
            self.parsers[base] = rp
        return rp.can_fetch(UA, base + path)


# ---------------------------------------------------------------------------
# 共通の巡回の流れ
# ---------------------------------------------------------------------------

class Run:
    """1チェーンの1回の巡回の状態と、共通の後処理(記録・追跡・終了判定)。"""

    def __init__(self, chain: str, pending: dict, stats: dict, today: str | None):
        self.chain = chain
        # 日付を指定した実行(テスト)では、初回観測の時刻もその日にそろえる
        self.now = now_iso() if today is None else f"{today}T07:00:00+09:00"
        self.today = today or today_jst()
        self.pending = pending
        self.pending.setdefault(chain, {})
        stats.setdefault(chain, new_stats())
        self.stats = stats
        self.st = stats[chain]

    def block(self, where: str, e: Exception) -> None:
        self.st["blocked"] = True
        print(f"[blocked] {self.chain} {where}: {e}", file=sys.stderr)

    def fail_detail(self, pid: str, url: str, reason: str) -> None:
        self.st["failed"] += 1
        self.pending[self.chain][pid] = {"url": url, "reason": reason, "checked_at": self.now}

    def finish(self, entries: dict[str, dict], list_ok: bool, build, refresh=None, enrich=None,
               present_extra: set[str] | None = None) -> None:
        """entries({商品ID: 一覧の項目})を記録する。

        build(entry) -> 新規レコード(ネットワークに触らない。一覧の情報だけで作る)
        refresh(existing, entry) -> 既存レコードに一覧の最新の項目を反映(純粋な更新。変更があれば True)
        enrich(product, entry, is_new) -> 詳細の取得で栄養などを埋める(ネットワークに触れてよい。変更があれば True)
        present_extra: 記録対象ではないが「公式サイトにまだ載っている」ことを示すID(終了判定に足す)
        """
        chain, st, today = self.chain, self.st, self.today
        st["list_ok"] = bool(list_ok and entries)
        st["limited_seen"] = len(entries)
        if st["list_ok"] and not ratio_ok(chain, len(entries), today):
            st["list_ok"] = False
        baseline = baseline_for(chain)
        entries = burst_filter(chain, entries, baseline, self.pending, self.stats, today)
        for pid, e in entries.items():
            existing = load_product(chain, pid)
            is_new = existing is None
            if is_new:
                product = build(e)
                st["new_products"] += 1
                changed = True
            else:
                product = existing
                relaunched = touch_seen(product, e.get("price"), e.get("price_max"), today)
                if relaunched:
                    st["relaunched"] = st.get("relaunched", 0) + 1
                if refresh:
                    refresh(product, e)
                changed = True
            if enrich is not None and not st["blocked"]:
                try:
                    enrich(product, e, is_new)
                except BlockedError as ex:
                    self.block(f"detail {pid}", ex)
                except NotFoundError as ex:
                    self.fail_detail(pid, e.get("official_url") or "", f"404: {ex}")
                except Exception as ex:  # noqa: BLE001
                    self.fail_detail(pid, e.get("official_url") or "", f"fetch_error: {ex}")
                else:
                    self.pending[chain].pop(pid, None)
            if changed:
                save_product(chain, product)
            if st["blocked"]:
                break
        if st["list_ok"] and not st["blocked"]:
            if baseline is None:
                save_baseline(chain, set(entries))
            apply_missing(chain, set(entries) | (present_extra or set()), today, st)


# ---------------------------------------------------------------------------
# 商品レコードの共通部分
# ---------------------------------------------------------------------------

def base_product(chain: str, pid: str, name: str, now: str, today: str) -> dict:
    """全チェーン共通のレコードの骨格。**説明文・画像は持たない**(規約対策)。"""
    return {
        "chain": chain,
        "product_id": pid,
        "name": name,
        "category": None,
        "price": None,
        "price_max": None,
        "price_note": None,
        "price_variants": None,
        "launch_date": None,
        "launch_date_source": None,
        "news_title": None,
        "news_url": None,
        "news_date": None,
        "listed_at": None,
        "planned_end_text": None,
        "nutrition": None,
        "nutrition_variants": None,
        "nutrition_note": None,
        "nutrition_source": None,
        "allergens": None,
        "allergen_source": None,
        "allergen_note": None,
        "limited_kind": None,
        "stores_note": None,
        "official_url": None,
        "first_seen_at": now,
        "last_seen_at": today,
        "status": "on_sale",
        "ended_observed_at": None,
        "missed_runs": 0,
        "price_history": [],
        "launch_search_runs": 0,
    }


def set_initial_price(product: dict, price: int | None, price_max: int | None, today: str) -> None:
    product["price_history"] = []
    product["price"] = None
    product["price_max"] = None
    record_price(product, price, price_max, today)


def nutrition_kcal(nut: dict | None) -> float | None:
    return (nut or {}).get("kcal")


def apply_nutrition(product: dict, variants: list[dict] | None, rep: dict | None, today: str) -> bool:
    """栄養を入れる。まだ空なら入れ、値が変わっていれば入れ替えて nutrition_changes に残す。変更があれば True。"""
    if not rep:
        return False
    new_nut = rep["nutrition"]
    new_vars = variants if variants and len(variants) > 1 else None
    old = product.get("nutrition")
    if old == new_nut and product.get("nutrition_variants") == new_vars and product.get("nutrition_label") == rep.get("label"):
        return False
    if old is not None and (old != new_nut):
        product.setdefault("nutrition_changes", []).append(
            {"date": today, "from_kcal": nutrition_kcal(old), "to_kcal": nutrition_kcal(new_nut)})
    product["nutrition"] = new_nut
    product["nutrition_variants"] = new_vars
    product["nutrition_label"] = rep.get("label")  # 代表の値がどのサイズ・条件のものか(Tall など)
    return True


def apply_allergens(product: dict, allergens: dict | None, today: str) -> bool:
    if allergens is None or product.get("allergens") == allergens:
        return False
    if product.get("allergens") is not None:
        product.setdefault("allergen_changes", []).append({"date": today})
    product["allergens"] = allergens
    return True


def due_for_recheck(product: dict, today: str) -> bool:
    """詳細を取り直す時期か(RECHECK_DAYS日以上たった、または一度も取れていない)。"""
    last = product.get("detail_checked_at")
    if not last:
        return True
    try:
        return (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(last)).days >= RECHECK_DAYS
    except ValueError:
        return True


# ---- 発売日の照合(見出し・お知らせ・プレスリリース) -----------------------------

LAUNCH_WINDOW_BEFORE_DAYS = 120
LAUNCH_WINDOW_AFTER_DAYS = 14
LAUNCH_SEARCH_RUNS_MAX = 3  # 本文の取得まで使う探索は、新しい商品について最大この回数


def launch_name_keys(name: str) -> list[str]:
    """発売日の照合に使う、商品名の正規化キー(全体と、末尾の（単品）（ホット）・先頭の【店舗限定】を除いたもの)。
    短すぎるキー(4文字以下)は誤爆するので使わない。"""
    keys = []
    variants = [name,
                re.sub(r"[（(][^）)]*[）)]\s*$", "", name),
                re.sub(r"^【[^】]*】", "", name),
                re.sub(r"ミニサイズ", "", re.sub(r"[（(][^）)]*[）)]\s*$", "", name))]
    for v in variants:
        k = parsers.normalize_name(v)
        if len(k) >= 5 and k not in keys:
            keys.append(k)
    return keys


def raw_name_variants(name: str) -> list[str]:
    """本文の中で商品名を探すための、NFKC化した商品名の候補(全体・末尾の括弧なし・先頭の【】なし)。"""
    n = unicodedata.normalize("NFKC", name)
    out = []
    for v in (n, re.sub(r"[(][^)]*[)]\s*$", "", n), re.sub(r"^\[[^\]]*\]", "", re.sub(r"^【[^】]*】", "", n))):
        v = v.strip()
        if len(v) >= 4 and v not in out:
            out.append(v)
    return out


def find_launch(name: str, sources: list[dict], ref: str) -> dict | None:
    """sources=[{title, text, date, url}] から、商品名が出てくる記事を探し、その発売日を返す。

    date が ref の前 LAUNCH_WINDOW_BEFORE_DAYS 日〜後 LAUNCH_WINDOW_AFTER_DAYS 日の記事だけを対象にする。
    発売日は記事の見出しから、無ければ本文の(商品名の近く→先頭の)「M月D日より」から取る。見つからなければ None。"""
    keys = launch_name_keys(name)
    if not keys or not ref:
        return None
    try:
        ref_d = datetime.date.fromisoformat(ref)
    except ValueError:
        return None
    lo = ref_d - datetime.timedelta(days=LAUNCH_WINDOW_BEFORE_DAYS)
    hi = ref_d + datetime.timedelta(days=LAUNCH_WINDOW_AFTER_DAYS)
    for s in sources:
        try:
            d = datetime.date.fromisoformat(s.get("date") or "")
        except ValueError:
            continue
        if not (lo <= d <= hi):
            continue
        title_n = parsers.normalize_name(s.get("title"))
        text = s.get("text") or ""
        text_n = parsers.normalize_name(text)
        in_title = any(k in title_n for k in keys)
        in_text = any(k in text_n for k in keys)
        if not (in_title or in_text):
            continue
        launch = parsers.launch_date_from_text(s.get("title"), d)
        if launch is None and text:
            # 商品名の近くの日付を優先し、無ければ本文の最初の発売日
            text_k = unicodedata.normalize("NFKC", text)
            for v in raw_name_variants(name):
                pos = text_k.find(v)
                if pos >= 0:
                    launch = parsers.launch_date_from_text(text_k[max(0, pos - 150): pos + len(v) + 150], d)
                    break
            if launch is None:
                launch = parsers.launch_date_from_text(text_k, d)
        if launch:
            return {"launch_date": launch, "title": s.get("title"), "url": s.get("url"), "date": s.get("date")}
    return None


def apply_launch(product: dict, hit: dict, source: str) -> None:
    product["launch_date"] = hit["launch_date"]
    product["launch_date_source"] = source
    product["news_title"] = hit["title"]
    product["news_url"] = hit["url"]
    product["news_date"] = hit["date"]


def launch_ref(product: dict) -> str:
    return product.get("listed_at") or (product.get("first_seen_at") or "")[:10]


def needs_launch(product: dict) -> bool:
    return product.get("launch_date") is None and product.get("status") != "ended"


# ---------------------------------------------------------------------------
# スターバックス
# ---------------------------------------------------------------------------

SBUX_LIST_BASE = "https://menu.starbucks.co.jp"
SBUX_PRODUCT_BASE = "https://product.starbucks.co.jp"
SBUX_STORIES_BASE = "https://stories.starbucks.co.jp"
SBUX_NUTRITION_URL = "https://product.starbucks.co.jp/allergy/nutrient/"
SBUX_ALLERGEN_URL = "https://product.starbucks.co.jp/allergy/allergen/"
SBUX_PAGE_SIZE = 10
SBUX_MAX_PAGES = 30
SBUX_MILK_NOTE = "ミルクは標準(ミルク変更なし。ミルクを使わない商品はそのまま)の値です。ミルクの変更やカスタマイズで変わります。詳しくは公式サイトでご確認ください"


def sbux_strip_paren(name: str) -> str:
    return re.sub(r"[（(](ホット|アイス|HOT|ICED?)[）)]\s*$", "", name).strip()


def sbux_build_targets(list_items: list[dict], nut: dict[str, dict]) -> dict[str, dict]:
    """記録対象 {商品コード: 項目} を決める。

    (a) 新作一覧(tag=new)にあって、栄養JSON(= 飲食物)にもある商品。グッズ・豆・ギフトは栄養JSONに無いので入らない。
    (b) 栄養JSONの STARBUCKS「季節のおすすめ」(c01)の商品のうち、(a)に入らなかったもの
        (新作一覧に載っていないホット/アイスの片方など)。価格は新作一覧に同じ名前(ホット/アイス表記を除く)が
        あればそこから、無ければ価格なし(null)。is_menu_product が false で価格も分からないものは入れない。"""
    by_norm: dict[str, dict] = {}
    for it in list_items:
        by_norm.setdefault(parsers.normalize_name(it["name"]), it)
    targets: dict[str, dict] = {}
    for it in list_items:
        n = nut.get(it["code"])
        if n:
            targets[it["code"]] = {"code": it["code"], "name": it["name"], "list": it, "nut": n}
    for code, n in nut.items():
        if code in targets or not (n["seasonal"] and n["top"] == "STARBUCKS" and n["store"] == "regular"):
            continue
        key = parsers.normalize_name(n["name"])
        li = by_norm.get(key) or by_norm.get(parsers.normalize_name(sbux_strip_paren(n["name"])))
        if li is None and not n["is_menu_product"]:
            continue
        targets[code] = {"code": code, "name": n["name"] if li is None else (li["name"] if key in by_norm else n["name"]),
                         "list": li, "nut": n}
    return targets


def sbux_entry(t: dict) -> dict:
    li, n = t["list"], t["nut"]
    price, pmax = (li["price_min"], li["price_max"]) if li else (None, None)
    if li and price is None:
        price, pmax = li["price"], li["price"]
    return {
        "pid": t["code"], "code": t["code"], "name": t["name"], "price": price, "price_max": pmax,
        "purchase_methods": li["purchase_methods"] if li else [], "certain_stores": bool(li and li["certain_stores"]),
        "nut": n, "official_url": f"{SBUX_LIST_BASE}/{t['code']}",
    }


def sbux_stores_note(e: dict) -> str | None:
    labels = [parsers.SBUX_PURCHASE_LABEL.get(p, p) for p in e["purchase_methods"]]
    if not labels:
        return None
    note = "・".join(labels)
    if e["certain_stores"]:
        note += "(一部店舗のみ)"
    return note


class PressSources:
    """発売日の照合に使う記事の一覧。必要になるまで取らない(遅延取得)。取得に失敗したら空のまま続行。"""

    def __init__(self, loader):
        self.loader = loader
        self.items: list[dict] | None = None

    def get(self) -> list[dict]:
        if self.items is None:
            try:
                self.items = self.loader()
            except BlockedError:
                raise
            except Exception as e:  # noqa: BLE001
                print(f"[warn] 発売日の照合元の取得に失敗(発売日なしで続行): {e}", file=sys.stderr)
                self.items = []
        return self.items


def crawl_starbucks(getter, pending: dict, stats: dict, today: str | None = None) -> None:
    chain = "starbucks"
    run = Run(chain, pending, stats, today)
    st, today, now = run.st, run.today, run.now
    robots = Robots(getter, chain, st)
    try:
        if not robots.allowed(SBUX_LIST_BASE, "/api/v1/list") or not robots.allowed(SBUX_PRODUCT_BASE, "/allergy/json/nutritions.json"):
            print("[skip] starbucks: robots.txtでDisallow", file=sys.stderr)
            return
        # 新作一覧: count 件になるまで10件ずつ
        items: list[dict] = []
        total = None
        complete = True
        start = 0
        for _ in range(SBUX_MAX_PAGES):
            try:
                page = parsers.parse_sbux_list(getter(chain, f"{SBUX_LIST_BASE}/api/v1/list?tag=new&start={start}"))
                st["list_requests"] += 1
            except BlockedError:
                raise
            except Exception as e:  # noqa: BLE001  取得失敗・構造変更
                print(f"[warn] starbucks 新作一覧 start={start}: {e}", file=sys.stderr)
                complete = False
                break
            total = page["count"]
            items.extend(page["items"])
            start += SBUX_PAGE_SIZE
            if not page["items"] or len(items) >= total:
                break
        else:
            complete = False
        if not items or (total is not None and len(items) < total):
            complete = False
        if not items:
            return  # 一覧が取れない回は何も更新しない(list_ok=False のまま)
        # 栄養JSONが記録対象の定義(飲食物かどうか)なので、読めなければこの回は記録しない
        try:
            nut = parsers.parse_sbux_nutrition(getter(chain, f"{SBUX_PRODUCT_BASE}/allergy/json/nutritions.json"))
            st["list_requests"] += 1
        except BlockedError:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"[warn] starbucks 栄養JSON: {e}", file=sys.stderr)
            return
        allergens: dict[str, dict] = {}
        try:
            allergens = parsers.parse_sbux_allergens(getter(chain, f"{SBUX_PRODUCT_BASE}/allergy/json/allergens.json"))
            st["list_requests"] += 1
        except BlockedError:
            raise
        except Exception as e:  # noqa: BLE001  アレルゲンだけ取れなくても記録は続ける(次回以降に埋める)
            print(f"[warn] starbucks アレルゲンJSON: {e}", file=sys.stderr)
            st["failed"] += 1
    except BlockedError as e:
        run.block("list", e)
        return

    targets = sbux_build_targets(items, nut)
    entries = {code: sbux_entry(t) for code, t in targets.items()}

    def feed_loader():
        feed = parsers.parse_wp_feed(getter(chain, f"{SBUX_STORIES_BASE}/press/press-releases/feed/"))
        st["list_requests"] += 1
        return [{"title": f["title"], "text": f["summary"], "date": f["date"], "url": f["link"]} for f in feed]

    press = PressSources(feed_loader)
    body_budget = {"left": int(CONFIG.get("starbucks_article_fetch_max", 4))}
    bodies: dict[str, str] = {}

    def fetch_bodies(sources: list[dict]) -> list[dict]:
        """見出しに発売日の語がある新しい記事の本文を、上限つきで取る(記事ごとに1回。この実行の中だけで持つ)。"""
        out = []
        for s in sources:
            if not parsers.launch_date_from_text(s["title"], datetime.date.fromisoformat(s["date"])) and not re.search(r"発売|登場|販売", s["title"]):
                continue
            if s["url"] in bodies:
                if bodies[s["url"]]:
                    out.append({**s, "text": bodies[s["url"]]})
                continue
            if body_budget["left"] <= 0:
                continue
            if not robots.allowed(SBUX_STORIES_BASE, "/press/"):
                break
            body_budget["left"] -= 1
            try:
                text = parsers.strip_html(getter(chain, s["url"]))
                st["detail_requests"] += 1
            except BlockedError:
                raise
            except Exception as e:  # noqa: BLE001
                print(f"[warn] starbucks プレス本文 {s['url']}: {e}", file=sys.stderr)
                bodies[s["url"]] = ""  # 失敗した記事も、この実行の中では取り直さない
                continue
            bodies[s["url"]] = text
            out.append({**s, "text": text})
        return out

    def build(e):
        n = e["nut"]
        p = base_product(chain, e["pid"], e["name"], now, today)
        p["category"] = "ビバレッジ" if n["kind"] == "beverage" else "フード"
        p["limited_kind"] = "季節のおすすめ" if n["seasonal"] else "新作"
        p["price_note"] = "公式サイトの新作一覧の表示(税込)。サイズや店内飲食・持ち帰りで異なります" if e["price"] is not None else None
        set_initial_price(p, e["price"], e["price_max"], today)
        p["stores_note"] = sbux_stores_note(e)
        p["official_url"] = e["official_url"]
        p["nutrition_source"] = {"label": "スターバックス 栄養成分情報", "url": SBUX_NUTRITION_URL}
        p["allergen_source"] = {"label": "スターバックス アレルゲン情報", "url": SBUX_ALLERGEN_URL}
        if n["kind"] == "beverage":
            p["nutrition_note"] = SBUX_MILK_NOTE
            p["allergen_note"] = "ミルクは標準(ミルク変更なし)の場合の情報です"
        p["source_list"] = ["menu.starbucks.co.jp/api/v1/list?tag=new", "product.starbucks.co.jp/allergy/json/nutritions.json"]
        return p

    def refresh(p, e):
        p["stores_note"] = sbux_stores_note(e) or p.get("stores_note")
        if e["nut"]["seasonal"] and p.get("limited_kind") == "新作":
            p["limited_kind"] = "季節のおすすめ"

    def enrich(p, e, is_new):
        variants = parsers.sbux_nutrition_variants(e["nut"])
        rep = parsers.sbux_representative(variants)
        apply_nutrition(p, variants, rep, today)
        al = allergens.get(e["code"])
        if al is not None:
            apply_allergens(p, {"contains": al["contains"], "partial": []}, today)
        if needs_launch(p) and (is_new or int(p.get("launch_search_runs") or 0) < LAUNCH_SEARCH_RUNS_MAX):
            ref = launch_ref(p)
            hit = find_launch(p["name"], press.get(), ref)
            if hit is None and int(p.get("launch_search_runs") or 0) < LAUNCH_SEARCH_RUNS_MAX:
                hit = find_launch(p["name"], fetch_bodies(press.get()), ref)
                p["launch_search_runs"] = int(p.get("launch_search_runs") or 0) + 1
            if hit:
                apply_launch(p, hit, "press_release")

    run.finish(entries, complete, build, refresh, enrich)


# ---------------------------------------------------------------------------
# タリーズコーヒー
# ---------------------------------------------------------------------------

TULLYS_BASE = parsers.TULLYS_BASE
TULLYS_NUTRITION_NOTE = "公式サイトの表示。HOT/COLD・サイズごとのエネルギー(kcal)のみ公表されています"
TULLYS_ALLERGEN_NOTE = "公式ページの「特定原材料 8品目」の表示です。「特定原材料に準ずるもの20品目」は公式サイトでご確認ください"


def tullys_pid(path: str) -> str:
    """/menu/drink/coffee/26pumpkin_pudding_latte.html -> drink-coffee-26pumpkin_pudding_latte"""
    p = re.sub(r"^/menu/", "", path)
    p = re.sub(r"\.html$", "", p)
    return re.sub(r"[^A-Za-z0-9_\-]+", "-", p).strip("-")


def tullys_pick_primary_energy(energy: list[dict]) -> dict | None:
    for e in energy:
        if "Tall" in e["label"] and "HOT" in e["label"]:
            return e
    for e in energy:
        if "Tall" in e["label"]:
            return e
    return energy[0] if energy else None


def crawl_tullys(getter, pending: dict, stats: dict, today: str | None = None) -> None:
    chain = "tullys"
    run = Run(chain, pending, stats, today)
    st, today, now = run.st, run.today, run.now
    robots = Robots(getter, chain, st)
    prefixes = tuple(CONFIG.get("tullys_record_prefixes") or ["/menu/drink/", "/menu/food/", "/menu/sweets/"])
    try:
        if not robots.allowed(TULLYS_BASE, parsers.TULLYS_LINEUP_PATH):
            print("[skip] tullys: robots.txtでDisallow", file=sys.stderr)
            return
        page = getter(chain, TULLYS_BASE + parsers.TULLYS_LINEUP_PATH)
        st["list_requests"] += 1
    except BlockedError as e:
        run.block("list", e)
        return
    except Exception as e:  # noqa: BLE001
        print(f"[warn] tullys 一覧取得失敗: {e}", file=sys.stderr)
        return
    lineup = parsers.parse_tullys_lineup(page)
    if not lineup:
        print("[warn] tullys 一覧: 商品が0件(構造変更の疑い)", file=sys.stderr)
        return
    entries: dict[str, dict] = {}
    skipped = []
    for it in lineup:
        if not it["path"].startswith(prefixes):
            skipped.append(it["name"])  # 豆・グッズ・ハニー/グロッサリー等(栄養が無い非飲食物は記録しない)
            continue
        pid = tullys_pid(it["path"])
        entries[pid] = {"pid": pid, "path": it["path"], "name": it["name"], "listed_at": it["listed_at"],
                        "official_url": TULLYS_BASE + it["path"], "price": None, "price_max": None}
    st["skipped_non_food"] = skipped

    def press_loader():
        out = []
        years = {today[:4]}
        years |= {str(int(today[:4]) - 1)} if today[5:7] in ("01", "02") else set()
        for y in sorted(years, reverse=True):
            if not robots.allowed(TULLYS_BASE, f"/company/pressrelease/{y}_index.html"):
                continue
            try:
                html = getter(chain, f"{TULLYS_BASE}/company/pressrelease/{y}_index.html")
            except NotFoundError:
                continue
            st["list_requests"] += 1
            out += [{"title": r["title"], "text": "", "date": r["date"], "url": TULLYS_BASE + r["path"]}
                    for r in parsers.parse_tullys_press(html)]
        return out

    press = PressSources(press_loader)
    recheck_left = {"n": RECHECK_MAX}

    def build(e):
        p = base_product(chain, e["pid"], e["name"], now, today)
        p["category"] = {"drink": "ドリンク", "food": "フード", "sweets": "スイーツ"}.get(e["pid"].split("-", 1)[0], None)
        p["listed_at"] = e["listed_at"]
        p["limited_kind"] = "季節の新商品"
        p["official_url"] = e["official_url"]
        p["stores_note"] = "店舗により取り扱いや価格が異なる場合があります"
        p["nutrition_source"] = {"label": "タリーズコーヒー 商品情報", "url": e["official_url"]}
        p["allergen_source"] = {"label": "タリーズコーヒー 商品情報", "url": e["official_url"]}
        p["source_list"] = ["www.tullys.co.jp/menu/seasonal_lineup/"]
        return p

    def refresh(p, e):
        if e.get("listed_at") and not p.get("listed_at"):
            p["listed_at"] = e["listed_at"]

    def enrich(p, e, is_new):
        if needs_launch(p) and (is_new or int(p.get("launch_search_runs") or 0) < LAUNCH_SEARCH_RUNS_MAX):
            hit = find_launch(p["name"], press.get(), launch_ref(p))
            if hit:
                apply_launch(p, hit, "press_release")
            else:
                p["launch_search_runs"] = int(p.get("launch_search_runs") or 0) + 1
        if not is_new:
            if not due_for_recheck(p, today) or recheck_left["n"] <= 0:
                return
            recheck_left["n"] -= 1
        if not robots.allowed(TULLYS_BASE, e["path"]):
            return
        html = getter(chain, TULLYS_BASE + e["path"])
        st["detail_requests"] += 1
        d = parsers.parse_tullys_detail(html)
        if d is None:
            raise ValueError("詳細ページの構造が想定と違う")
        p["detail_checked_at"] = today
        prices = d["prices"]
        if prices:
            lo = min(x["price"] for x in prices)
            hi = max(x["price"] for x in prices)
            p["price_variants"] = [{"label": x["label"], "price": x["price"]} for x in prices] if len(prices) > 1 else None
            p["price_note"] = "税込。店内飲食・テイクアウトは同一価格(公式の表記)。店舗により異なる場合があります"
            if is_new:
                set_initial_price(p, lo, hi, today)
            else:
                record_price(p, lo, hi, today)
        energy = d["energy"]
        if energy:
            variants = [{"label": x["label"] or None, "nutrition": {"kcal": x["kcal"]}} for x in energy]
            rep = tullys_pick_primary_energy(energy)
            rep_v = {"label": rep["label"], "nutrition": {"kcal": rep["kcal"]}}
            apply_nutrition(p, variants, rep_v, today)
            p["nutrition_note"] = TULLYS_NUTRITION_NOTE
        if d["allergens"] is not None:
            apply_allergens(p, {"contains": d["allergens"]["contains"], "partial": [],
                                "none_listed": d["allergens"]["none_listed"]}, today)
            p["allergen_note"] = TULLYS_ALLERGEN_NOTE

    run.finish(entries, True, build, refresh, enrich)


# ---------------------------------------------------------------------------
# コメダ珈琲店
# ---------------------------------------------------------------------------

KOMEDA_BASE = parsers.KOMEDA_BASE
KOMEDA_NOTICE_PATH = "/v1/hp/notice?limit=20&type=2&offset=0"
KOMEDA_PRICE_NOTE = "税込。店舗により価格が異なるため、公式の全国の価格帯(最小〜最大)を表示しています"


def crawl_komeda(getter, pending: dict, stats: dict, today: str | None = None) -> None:
    """コメダ: 一覧は内部API(おすすめ category=903)。構造が変わったら「構造変更の疑い」として list_failed 扱いにし、
    終了判定をしない(レスポンスは dict {"constant","menus"}。list だけ・menus 無しは ValueError)。"""
    chain = "komeda"
    run = Run(chain, pending, stats, today)
    st, today, now = run.st, run.today, run.now
    robots = Robots(getter, chain, st)
    try:
        if not robots.allowed(KOMEDA_BASE, "/v1/hp/menu"):
            print("[skip] komeda: robots.txtでDisallow", file=sys.stderr)
            return
        page = getter(chain, KOMEDA_BASE + parsers.KOMEDA_MENU_URL_PATH)
        st["list_requests"] += 1
    except BlockedError as e:
        run.block("list", e)
        return
    except Exception as e:  # noqa: BLE001
        print(f"[warn] komeda 一覧取得失敗: {e}", file=sys.stderr)
        return
    try:
        menu = parsers.parse_komeda_menu(page)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[warn] komeda 一覧の構造が想定と違う(構造変更の疑い): {e}", file=sys.stderr)
        return
    entries: dict[str, dict] = {}
    for it in menu["items"]:
        if it["is_hidden"]:
            continue
        entries[it["id"]] = {
            "pid": it["id"], "name": it["name"], "price": it["price_min"], "price_max": it["price_max"],
            "category": menu["categories"].get(it["large_type"]),
            "kind": "季節限定" if it["is_limited_time"] else "おすすめ",
            "official_url": parsers.KOMEDA_PUBLIC_URL, "is_setmenu": it["is_setmenu"],
        }

    def notice_loader():
        out = parsers.parse_komeda_notices(getter(chain, KOMEDA_BASE + KOMEDA_NOTICE_PATH))
        st["list_requests"] += 1
        return [{"title": n["title"], "text": n["text"], "date": n["date"], "url": parsers.KOMEDA_NEWS_URL} for n in out]

    press = PressSources(notice_loader)
    recheck_left = {"n": RECHECK_MAX}

    def build(e):
        p = base_product(chain, e["pid"], e["name"], now, today)
        p["category"] = e["category"]
        p["limited_kind"] = e["kind"]
        p["price_note"] = KOMEDA_PRICE_NOTE if e["price"] is not None else None
        set_initial_price(p, e["price"], e["price_max"], today)
        p["official_url"] = e["official_url"]
        p["stores_note"] = "店舗により取り扱いや販売時期が異なります"
        p["nutrition_source"] = {"label": "コメダ珈琲店 メニュー", "url": parsers.KOMEDA_PUBLIC_URL}
        p["allergen_source"] = {"label": "コメダ珈琲店 メニュー", "url": parsers.KOMEDA_PUBLIC_URL}
        p["source_list"] = ["eu.komeda.co.jp/v1/hp/menu?brand_type=1&category=903"]
        return p

    def refresh(p, e):
        if e.get("category") and p.get("category") != e["category"]:
            p["category"] = e["category"]

    def enrich(p, e, is_new):
        if needs_launch(p) and (is_new or int(p.get("launch_search_runs") or 0) < LAUNCH_SEARCH_RUNS_MAX):
            hit = find_launch(p["name"], press.get(), launch_ref(p))
            if hit:
                apply_launch(p, hit, "official_news")
            else:
                p["launch_search_runs"] = int(p.get("launch_search_runs") or 0) + 1
        if not is_new:
            if not due_for_recheck(p, today) or recheck_left["n"] <= 0:
                return
            recheck_left["n"] -= 1
        text = getter(chain, f"{KOMEDA_BASE}/v1/hp/menu-allergy/{e['pid']}")
        st["detail_requests"] += 1
        try:
            d = parsers.parse_komeda_allergy(text)
        except (ValueError, json.JSONDecodeError) as ex:
            raise ValueError(f"アレルギーAPIの構造が想定と違う: {ex}")
        p["detail_checked_at"] = today
        if d["kcal"] is not None:
            apply_nutrition(p, None, {"label": None, "nutrition": {"kcal": d["kcal"]}}, today)
            p["nutrition_note"] = "公式サイトで公表されているのはカロリー(kcal)のみです"
        apply_allergens(p, {"contains": d["contains"], "partial": []}, today)
        p["allergen_note"] = d["note"]

    run.finish(entries, True, build, refresh, enrich)


# ---------------------------------------------------------------------------
# ドトールコーヒーショップ
# ---------------------------------------------------------------------------

DOUTOR_BASE = parsers.DOUTOR_BASE
DOUTOR_ALLERGY_BASE = parsers.DOUTOR_ALLERGY_BASE
DOUTOR_ALLERGY_TOP = "https://allergy.doutor.co.jp/allergy_check/doutor_coffee_shop/"
DOUTOR_NUTRITION_NOTE = "1食当たりの値(公式の表記)。商品や店舗ごとで異なる場合があります"
DOUTOR_PRICE_FROM_NOTE = "「～」の付く価格は最低価格(組み合わせにより異なります)"


def doutor_official_url(card_id: str | None) -> str:
    if card_id:
        return f"{DOUTOR_BASE}/dcs/menu/detail/{card_id}.html"
    return DOUTOR_BASE + parsers.DOUTOR_SEASON_PATH


def doutor_price_pair(prices: list[dict]) -> tuple[int | None, int | None, bool]:
    """(代表価格, 上限, 「～」付きか)。代表は「販売価格」→先頭。上限は他の価格との最大(宅配価格など)。"""
    if not prices:
        return None, None, False
    primary = next((p for p in prices if p["label"] == "販売価格"), prices[0])
    return primary["price"], None, bool(primary.get("from"))


def crawl_doutor(getter, pending: dict, stats: dict, today: str | None = None) -> None:
    chain = "doutor"
    run = Run(chain, pending, stats, today)
    st, today, now = run.st, run.today, run.now
    robots = Robots(getter, chain, st)
    goods_words = CONFIG.get("doutor_goods_words") or ["コーヒー豆", "ドリップ", "パック入"]
    all_ok = True
    try:
        if not robots.allowed(DOUTOR_BASE, parsers.DOUTOR_SEASON_PATH):
            print("[skip] doutor: robots.txtでDisallow", file=sys.stderr)
            return
        season = None
        try:
            season = parsers.parse_doutor_season(getter(chain, DOUTOR_BASE + parsers.DOUTOR_SEASON_PATH), goods_words)
            st["list_requests"] += 1
        except BlockedError:
            raise
        except (ValueError, NotFoundError) as e:
            print(f"[warn] doutor 季節のおすすめ(構造変更の疑い): {e}", file=sys.stderr)
            all_ok = False
        except Exception as e:  # noqa: BLE001
            print(f"[warn] doutor 季節のおすすめ取得失敗: {e}", file=sys.stderr)
            all_ok = False
        cards: dict[str, dict] = {}
        for cat in CONFIG.get("doutor_categories") or parsers.DOUTOR_CATEGORIES:
            path = f"/dcs/menu/list/{cat}.html"
            if not robots.allowed(DOUTOR_BASE, path):
                all_ok = False
                continue
            try:
                got = parsers.parse_doutor_cards(getter(chain, DOUTOR_BASE + path))
                st["list_requests"] += 1
            except BlockedError:
                raise
            except Exception as e:  # noqa: BLE001
                print(f"[warn] doutor カテゴリ {cat} 取得失敗: {e}", file=sys.stderr)
                all_ok = False
                continue
            if not got:
                print(f"[warn] doutor カテゴリ {cat}: カードが0件(構造変更の疑い)", file=sys.stderr)
                all_ok = False
                continue
            for c in got:
                prev = cards.get(c["id"])
                if prev is None:
                    cards[c["id"]] = {**c, "cats": [cat]}
                else:
                    prev["is_new"] = prev["is_new"] or c["is_new"]
                    prev["cats"].append(cat)
    except BlockedError as e:
        run.block("list", e)
        return
    if season is None and not cards:
        return

    # 季節のおすすめ(season)の各商品を、カテゴリ一覧のカード(詳細URLの14桁ID)と名前で結びつける
    card_keys = {cid: parsers.doutor_name_keys(c["name"]) for cid, c in cards.items()}
    existing_all = load_chain_products(chain)
    name_ids = {parsers.normalize_name(p["name"]): p["product_id"] for p in existing_all
                if p["product_id"].startswith("sx-")}
    entries: dict[str, dict] = {}
    matched_cards: set[str] = set()
    skipped_goods = list((season or {}).get("skipped") or [])
    for it in (season or {}).get("items", []):
        keys = parsers.doutor_name_keys(it["name"])
        card_id = next((cid for cid, ks in card_keys.items() if ks & keys and cid not in matched_cards), None)
        norm = parsers.normalize_name(it["name"])
        if card_id:
            pid = name_ids.get(norm) or card_id  # 以前に名前で作ったIDの記録があれば、そのIDを使い続ける
            matched_cards.add(card_id)
        else:
            pid = name_ids.get(norm) or parsers.stable_id("sx", it["name"])
        price, pmax, from_flag = doutor_price_pair(it["prices"])
        entries[pid] = {
            "pid": pid, "name": it["name"], "price": price, "price_max": pmax, "from": from_flag,
            "prices": it["prices"], "reservation": it.get("reservation"), "card_id": card_id,
            "kind": "季節のおすすめ", "official_url": doutor_official_url(card_id),
        }
    # カテゴリ一覧でNEW印の付いた商品のうち、季節のおすすめに載っていないもの
    for cid, c in cards.items():
        if c["is_new"] and cid not in matched_cards and cid not in entries:
            entries[cid] = {"pid": cid, "name": c["name"], "price": None, "price_max": None, "from": False,
                            "prices": [], "reservation": None, "card_id": cid, "kind": "新商品（NEW）",
                            "official_url": doutor_official_url(cid), "need_price": True}
    present_extra = set(cards) | {pid for pid, e in entries.items()}
    st["skipped_goods"] = skipped_goods

    allergy_lists: dict[str, list[dict]] = {}
    recheck_left = {"n": RECHECK_MAX}

    def load_allergy_lists() -> bool:
        if allergy_lists:
            return True
        if not robots.allowed(DOUTOR_ALLERGY_BASE, "/allergy_check/"):
            return False
        for cat in parsers.DOUTOR_ALLERGY_CATEGORIES:
            url = DOUTOR_ALLERGY_BASE + parsers.DOUTOR_ALLERGY_LIST_PATH.format(cat=cat)
            lst = parsers.parse_doutor_allergy_list(getter(chain, url))
            st["detail_requests"] += 1
            allergy_lists[cat] = lst
        return True

    def allergy_candidates(name: str) -> list[dict]:
        """名前が一致するアレルギー側の商品(完全一致を優先、無ければ「アイス」「ホット」の接頭辞つきを最大2件)。"""
        keys = parsers.doutor_name_keys(name)
        exact, prefixed = [], []
        for lst in allergy_lists.values():
            for a in lst:
                ak = parsers.doutor_name_keys(a["name"])
                if ak & keys:
                    exact.append(a)
                    continue
                m = re.match(r"^(アイス|ホット)(.+)$", parsers.clean_text(a["name"]))
                if m and parsers.doutor_name_keys(m.group(2)) & keys:
                    prefixed.append({**a, "prefix": m.group(1)})
        return (exact or prefixed)[:2]

    def build(e):
        p = base_product(chain, e["pid"], e["name"], now, today)
        p["limited_kind"] = e["kind"]
        p["official_url"] = e["official_url"]
        p["id_kind"] = "detail" if re.fullmatch(r"\d{14}", e["pid"]) else "name"
        p["stores_note"] = "店舗により取り扱いや価格が異なる場合があります"
        apply_doutor_prices(p, e, True)
        p["nutrition_source"] = {"label": "ドトール アレルギー・栄養成分情報", "url": DOUTOR_ALLERGY_TOP}
        p["allergen_source"] = {"label": "ドトール アレルギー・栄養成分情報", "url": DOUTOR_ALLERGY_TOP}
        p["source_list"] = ["www.doutor.co.jp/dcs/menu/season.html"] + ([f"www.doutor.co.jp/dcs/menu/list/{c}.html" for c in (cards.get(e["card_id"]) or {}).get("cats", [])] if e.get("card_id") else [])
        return p

    def apply_doutor_prices(p, e, initial: bool) -> None:
        prices = e["prices"]
        if e["price"] is not None:
            labelled = [{"label": (x["label"] if x["label"] not in (None, "価格") else "店内飲食"), "price": x["price"]}
                        for x in prices]
            p["price_variants"] = labelled if len(labelled) > 1 else None
            p["price_note"] = DOUTOR_PRICE_FROM_NOTE if e["from"] else None
            p["price_from"] = bool(e["from"])
            if initial:
                set_initial_price(p, e["price"], e["price_max"], today)
        if e.get("reservation"):
            p["reservation"] = e["reservation"]
            rec = [pp["value"] for r in e["reservation"] for pp in r["periods"] if "お渡し" in pp["label"]]
            if rec:
                p["planned_end_text"] = "お渡し期間：" + rec[0]

    def refresh(p, e):
        if e.get("card_id") and p.get("id_kind") == "name" and not p.get("detail_id"):
            p["detail_id"] = e["card_id"]
            p["official_url"] = e["official_url"]
        if e["price"] is not None:
            apply_doutor_prices(p, e, False)

    def enrich(p, e, is_new):
        if e.get("need_price") and e["price"] is None and p.get("price") is None and (is_new or recheck_left["n"] > 0):
            d = parsers.parse_doutor_detail(getter(chain, DOUTOR_BASE + f"/dcs/menu/detail/{e['card_id']}.html"))
            st["detail_requests"] += 1
            if d and d["price"] is not None:
                e["price"] = d["price"]
                e["prices"] = [{"label": None, "price": d["price"], "from": False}]
                apply_doutor_prices(p, e, True)
        # 栄養・アレルゲン: アレルギー側の一覧に同名の商品があるとき(新規・まだ空・7日ごとの取り直し)
        need = p.get("nutrition") is None or p.get("allergens") is None
        if not (is_new or need and due_for_recheck(p, today)):
            return
        if not is_new and recheck_left["n"] <= 0:
            return
        if not is_new:
            recheck_left["n"] -= 1
        p["detail_checked_at"] = today
        if not load_allergy_lists():
            return
        cands = allergy_candidates(p["name"])
        variants, allerg = [], None
        for a in cands:
            d = parsers.parse_doutor_allergy_detail(
                getter(chain, f"{DOUTOR_ALLERGY_BASE}/allergy_check/doutor_coffee_shop/item/detail/{a['id']}/"))
            st["detail_requests"] += 1
            if not d:
                continue
            label = a.get("prefix")  # アイス/ホット。完全一致なら栄養表のサイズ名(M(R)サイズ等)
            for v in d["variants"]:
                lab = " ".join(x for x in [label, v["label"]] if x) or None
                variants.append({"label": lab, "nutrition": v["nutrition"]})
            if allerg is None:
                allerg = {"contains": list(d["allergens"]["contains"]), "partial": d["allergens"]["partial"],
                          "unknown": d["allergens"]["unknown"]}
            else:  # 複数の候補は安全側(どれかが含むなら含む)
                for c in d["allergens"]["contains"]:
                    if c not in allerg["contains"]:
                        allerg["contains"].append(c)
        if variants:
            apply_nutrition(p, variants, variants[0], today)
            p["nutrition_note"] = DOUTOR_NUTRITION_NOTE
        if allerg is not None and not allerg["unknown"]:
            allerg.pop("unknown", None)
            apply_allergens(p, allerg, today)
            p["allergen_note"] = "「●」は含む、「△」はのり・しらす・二枚貝の漁法に由来する条件つきの表示(公式の凡例)。工場製造時や店舗調理時に混入する可能性があります"

    run.finish(entries, all_ok and bool(entries), build, refresh, enrich, present_extra=present_extra)


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------

def collect_problems(stats: dict[str, dict]) -> dict[str, list[str]]:
    """この実行で問題があったチェーンを種別ごとに集める(通知用)。

    list_failed は「一覧の取得・解析が完全には成功しなかった」チェーン。取得失敗・商品0件(構造変更の疑い)・
    急減・robots.txtで弾いた一覧など、list_ok が False で終わった経路すべてを含む。
    ただし、ブロック(blocked)・例外(errors)・歯止め(suspected_restructure。既存の「保留」通知がある)は
    重複させないため除く。詳細ページ単発の失敗(stats["failed"])は一時的なことが多く翌日に自動で再試行されるので、
    問題には数えない。"""
    return {
        "blocked": [c for c, s in stats.items() if s.get("blocked")],
        "list_failed": [c for c, s in stats.items()
                        if not s.get("list_ok") and not s.get("blocked") and not s.get("error")
                        and not s.get("suspected_restructure")],
        "errors": [c for c, s in stats.items() if s.get("error")],
    }


def update_health(pending: dict, stats: dict[str, dict], today: str) -> dict[str, int]:
    """チェーンごとの「連続で問題が出ている日数」を pending["_health"] に持ち、今回問題があった
    チェーンの日数を返す。日付で数えるので同じ日の再実行では増えず、1日でも空けば数え直す。
    今回巡回しなかったチェーン(チェーン指定の実行)は触らない。歯止めの保留は別に consecutive_days を持つ。"""
    health = pending.setdefault("_health", {})
    bad = {c for names in collect_problems(stats).values() for c in names}
    yesterday = (datetime.date.fromisoformat(today) - datetime.timedelta(days=1)).isoformat()
    streaks: dict[str, int] = {}
    for chain in stats:
        if chain not in bad:
            health.pop(chain, None)
            continue
        prev = health.get(chain) or {}
        n = int(prev.get("days", 0))
        if prev.get("last_date") != today:
            n = n + 1 if prev.get("last_date") == yesterday else 1
        health[chain] = {"days": n, "last_date": today}
        streaks[chain] = n
    if not health:
        pending.pop("_health", None)
    return streaks


def save_run_summary(stats: dict[str, dict], streaks: dict[str, int] | None = None) -> None:
    """この実行で新規に記録した商品数・終了を観測した商品数・問題があったチェーンを書き出す。
    notify.py が読む。

    日付(UTC/JSTのずれ)で数えると朝の実行で通知が出ない・手動実行で二重に通知が出る
    不具合が姉妹サイト(コンビニポチ)で起きたため、実行ごとに上書きする一時ファイル
    (gitには入れない)にする。

    形式: {"new": {...}, "ended": {...}, "suspected": {...},
           "blocked": [チェーン], "list_failed": [チェーン], "errors": [チェーン],
           "streaks": {チェーン: 連続日数}, "detail_failed": {チェーン: 詳細取得失敗件数}}
    """
    out = {
        "new": {chain: s.get("new_products", 0) for chain, s in stats.items()},
        "ended": {chain: s.get("ended", 0) for chain, s in stats.items()},
        "suspected": {chain: s["suspected_restructure"] for chain, s in stats.items()
                      if s.get("suspected_restructure")},
    }
    problems = collect_problems(stats)
    out.update(problems)
    out["streaks"] = streaks or {}
    listed = set(problems["list_failed"]) | set(problems["blocked"]) | set(problems["errors"])
    out["detail_failed"] = {c: s["failed"] for c, s in stats.items() if s.get("failed") and c not in listed}
    RUN_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    RUN_SUMMARY.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")


CRAWLERS = {"starbucks": crawl_starbucks, "tullys": crawl_tullys, "komeda": crawl_komeda, "doutor": crawl_doutor}


def main(argv: list[str] | None = None) -> int:
    # 引数でチェーンを指定すると、そのチェーンだけ巡回する(例: crawl.py komeda)。
    # 引数なしは全チェーン(Actionsの定期実行)
    argv = sys.argv[1:] if argv is None else argv
    unknown = [a for a in argv if a not in CRAWLERS]
    if unknown:
        print(f"不明なチェーン: {unknown} (指定できるのは {list(CRAWLERS)})", file=sys.stderr)
        return 2
    targets = [CRAWLERS[a] for a in argv] if argv else list(CRAWLERS.values())
    pending = load_json(PENDING_PATH, {})
    stats: dict[str, dict] = {}
    getter = Getter()
    started = time.monotonic()
    names = {v: k for k, v in CRAWLERS.items()}

    for fn in targets:
        try:
            fn(getter, pending, stats)
        except Exception as e:  # noqa: BLE001
            print(f"[error] {fn.__name__} で例外: {e}", file=sys.stderr)
            err_chain = names.get(fn)
            if err_chain:
                stats.setdefault(err_chain, new_stats())["error"] = f"{type(e).__name__}: {e}"

    streaks = update_health(pending, stats, today_jst())
    save_json(PENDING_PATH, pending, sort_keys=True)
    save_run_summary(stats, streaks)

    print("=== クロール結果 ===")
    for chain, s in stats.items():
        print(
            f"{chain}: 一覧等{s.get('list_requests', 0)}件 / 詳細{s.get('detail_requests', 0)}件 "
            f"(リクエスト計{getter.counts.get(chain, 0)}) / 記録対象として一覧に居た{s.get('limited_seen', 0)}件 "
            f"/ 新規{s.get('new_products', 0)}件 / 終了観測{s.get('ended', 0)}件 / 失敗{s.get('failed', 0)}件 "
            f"/ 一覧取得={'全部成功' if s.get('list_ok') else '一部失敗'} "
            f"/ ブロック={'あり' if s.get('blocked') else 'なし'}"
            + (f" / 例外={s['error']}" if s.get("error") else "")
        )
    print(f"所要時間: {time.monotonic() - started:.0f}秒")
    return 0


if __name__ == "__main__":
    sys.exit(main())
