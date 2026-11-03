#!/usr/bin/env python3
"""スターバックス・タリーズ・コメダ珈琲店・ドトールの公式データのパーサ。

ネットワークには触らない純粋関数だけを置く(合成フィクスチャでテストする)。
公式のHTML/JSONの構造が変わったら、まずここが壊れる。壊れ方は2通りに分ける:
  ・構造が想定と違う(キーが無い・型が違う) -> ValueError。呼び出し側は「構造変更の疑い」として
    list_failed 扱いにし、終了判定をしない。
  ・構造は正しいが該当の商品が0件 -> 空のリストを返す。

規約: 4社とも複製・転載・引用を禁じているため、ここで取り出すのは事実(商品名・価格・日付・
栄養成分・アレルゲン)だけ。説明文・画像のURLは、読み取りもしない。
"""

from __future__ import annotations

import datetime
import hashlib
import html as html_lib
import json
import re
import unicodedata

# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------


def clean_text(s: str | None) -> str:
    """HTMLタグ(<br>は空白扱い)を除き、実体参照を戻し、空白を整える。"""
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_lib.unescape(s)
    s = s.replace("\xa0", " ")
    s = re.sub(r"[ \t\r\n　]+", " ", s)
    return s.strip()


def strip_html(s: str | None) -> str:
    """発売日の照合用に、記事全体を平文にする(スクリプト・スタイルも除く)。保存はしない。"""
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.S)
    return clean_text(s)


_NORM_DROP = re.compile(r"[\s「」『』\"“”'’・＜＞<>()（）【】\[\]～〜~!！?？\-ー―—:：/／,、。.&＆®™©*＊★☆]+")


def normalize_name(s: str | None) -> str:
    """商品名の照合用: NFKC・実体参照・空白と記号を除き、小文字にする。"""
    s = html_lib.unescape(s or "")
    s = unicodedata.normalize("NFKC", s)
    return _NORM_DROP.sub("", s).lower()


def to_float(v) -> float | None:
    """「401」「21.0」「-」→数値/None。数値はそのまま。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = unicodedata.normalize("NFKC", str(v)).strip().replace(",", "")
    if t in ("", "-", "ー", "−"):
        return None
    m = re.match(r"^-?\d+(?:\.\d+)?", t)
    return float(m.group(0)) if m else None


def parse_yen(text: str | None) -> int | None:
    """「￥1,380」「¥ 560」「680円」→ 整数。見つからなければ None。"""
    t = unicodedata.normalize("NFKC", text or "")
    m = re.search(r"(?:¥|￥)\s*([\d,]+)|([\d,]+)\s*円", t)
    if not m:
        return None
    return int((m.group(1) or m.group(2)).replace(",", ""))


def _round(v: float | None, nd: int) -> float | int | None:
    if v is None:
        return None
    r = round(v, nd)
    return int(r) if nd == 0 else r


def stable_id(prefix: str, name: str) -> str:
    """URLやIDを持たない商品の、名前から作る安定したID(例 sx-1a2b3c4d5e)。"""
    return f"{prefix}-{hashlib.sha1(normalize_name(name).encode('utf-8')).hexdigest()[:10]}"


# ---- 発売日(見出し・本文の「11月2日（月）より」) ------------------------------

_DATE_RE = re.compile(r"(\d{1,2})月(\d{1,2})日")
_LAUNCH_WORD_RE = re.compile(r"より|から|発売|販売|登場|スタート|開始|解禁")


def launch_date_from_text(text: str | None, ref: datetime.date) -> str | None:
    """見出し・本文の中の最初の「M月D日」を発売日として返す(年は ref の日付から補う)。

    「M月D日」の後ろ(20文字以内)に「より/から/発売/販売/登場/スタート」が続くものだけ採用する
    (「12月25日まで」「予約は11月1日まで」のような終わりの日付を取り違えない)。
    ref(記事の日付など)から半年以上ずれる年になる場合は前後の年に寄せる。"""
    t = unicodedata.normalize("NFKC", text or "")
    for m in _DATE_RE.finditer(t):
        tail = t[m.end(): m.end() + 20]
        # 「10月28日（水）より」「11月2日(月)発売」。「まで」が先に来るものは終わりの日付
        end_pos = re.search(r"まで", tail)
        word = _LAUNCH_WORD_RE.search(tail)
        if not word or (end_pos and end_pos.start() < word.start()):
            continue
        mo, d = int(m.group(1)), int(m.group(2))
        try:
            cand = datetime.date(ref.year, mo, d)
        except ValueError:
            continue
        delta = (cand - ref).days
        if delta > 180:
            cand = datetime.date(ref.year - 1, mo, d)
        elif delta < -180:
            cand = datetime.date(ref.year + 1, mo, d)
        return cand.isoformat()
    return None


# ---------------------------------------------------------------------------
# スターバックス
# ---------------------------------------------------------------------------

SBUX_ALLERGEN_NAMES = {
    # 公式のアレルゲンページ(product.starbucks.co.jp/allergy/allergen/)の表示名と並び
    "egg": "卵", "milk": "乳", "wheat": "小麦", "buck_wheat": "そば", "peanut": "落花生",
    "shrimp": "えび", "crab": "かに", "walnut": "くるみ", "almond": "アーモンド", "abalone": "あわび",
    "cuttlefish": "いか", "salmon_roe": "いくら", "orange": "オレンジ", "cashewnut": "カシューナッツ",
    "kiwi_fruit": "キウイフルーツ", "beef": "牛肉", "sesame": "ごま", "salmon": "さけ", "mackerel": "さば",
    "soybean": "大豆", "chicken": "鶏肉", "banana": "バナナ", "pork": "豚肉", "matsutake": "まつたけ",
    "macadamia_nuts": "マカダミアナッツ", "peach": "もも", "yamaimo": "やまいも", "apple": "りんご",
    "gelatin": "ゼラチン", "seafood": "魚介類",
}
SBUX_SIZE_ORDER = ["SHORT", "TALL", "GRANDE", "VENTI", "STANDARD"]
SBUX_SIZE_LABEL = {"SHORT": "Short", "TALL": "Tall", "GRANDE": "Grande", "VENTI": "Venti", "STANDARD": None}
# 「ミルク変更なし」を標準とする。無ければ(ミルクを使わない商品) None、ミルクの商品で no_change が無ければ milk
SBUX_STANDARD_MILK = ["no_change", None, "milk", "default"]
SBUX_STORE_PRIORITY = ["STARBUCKS", "RESERVE", "ROASTERY", "ONLINE_STORE"]
SBUX_PURCHASE_LABEL = {
    "STARBUCKS_COFFEE": "スターバックス コーヒー", "TEA_CAFE": "スターバックス ティー & カフェ",
    "RESERVE_CAFE": "リザーブ カフェ", "RESERVE_STORE": "リザーブ ストア", "RESERVE_BAR": "リザーブ バー",
    "RESERVE_ROASTERY_TOKYO": "リザーブ ロースタリー 東京", "ONLINE_STORE": "オンラインストア",
}


def parse_sbux_list(text: str) -> dict:
    """menu.starbucks.co.jp/api/v1/list?tag=new の1ページ。-> {"count": 総数, "items": [項目]}。

    各項目は item_code / item_name / 価格(min・max) / 販売チャネル などの事実だけ取り出す
    (image_url などは読まない)。構造が違えば ValueError。"""
    d = json.loads(text)
    if not isinstance(d, dict) or not isinstance(d.get("item"), list) or not isinstance(d.get("count"), int):
        raise ValueError("スターバックスの新作一覧: 想定外の構造")
    items = []
    for x in d["item"]:
        if not isinstance(x, dict) or not x.get("item_code") or not x.get("item_name"):
            raise ValueError("スターバックスの新作一覧: item_code / item_name が無い項目")
        price = x.get("price_in_vat")
        pmin = x.get("price_in_vat_min", price)
        pmax = x.get("price_in_vat_max", price)
        items.append({
            "code": str(x["item_code"]),
            "name": html_lib.unescape(str(x["item_name"])).strip(),
            "price": int(price) if isinstance(price, (int, float)) else None,
            "price_min": int(pmin) if isinstance(pmin, (int, float)) else None,
            "price_max": int(pmax) if isinstance(pmax, (int, float)) else None,
            "purchase_methods": [str(p) for p in (x.get("purchase_methods") or [])],
            "certain_stores": bool(x.get("certain_stores")),
        })
    return {"count": d["count"], "items": items}


def parse_sbux_nutrition(text: str) -> dict[str, dict]:
    """product.starbucks.co.jp/allergy/json/nutritions.json -> {url_jan_code: 商品}。

    商品 = {name, kind(beverage/food), top(STARBUCKS/RESERVE/ROASTERY/…), store(販売区分名),
            category_id, category(カテゴリ名), is_menu_product, sizes: {サイズ: [栄養の行]}}。
    同じ商品が複数の販売区分(通常店舗・ティー&カフェ・リザーブ等)にあるときは、
    STARBUCKS の通常店舗(selling_store_name_ja == "regular")を最優先で採る。"""
    d = json.loads(text)
    if not isinstance(d, dict) or "STARBUCKS" not in d:
        raise ValueError("スターバックスの栄養JSON: 想定外の構造")
    out: dict[str, dict] = {}
    seen_any = False
    for top in SBUX_STORE_PRIORITY:
        tv = d.get(top)
        if not isinstance(tv, dict):
            continue
        for kind in ("beverage", "food"):
            kv = tv.get(kind)
            if not isinstance(kv, dict):
                continue
            for kbn, store in kv.items():
                cats = store.get("categories") if isinstance(store, dict) else None
                if not isinstance(cats, dict):
                    raise ValueError("スターバックスの栄養JSON: categories が無い")
                for cid, cat in cats.items():
                    for g in cat.get("menu_groups") or []:
                        code = str(g.get("url_jan_code") or "")
                        if not code:
                            continue
                        seen_any = True
                        entry = {
                            "name": html_lib.unescape(str(g.get("product_name_ja") or "")).strip(),
                            "kind": kind, "top": top,
                            "store": store.get("selling_store_name_ja"),
                            "category_id": cid,
                            "category": html_lib.unescape(str(cat.get("category_name_ja") or "")),
                            "is_menu_product": bool(g.get("is_menu_product", True)),
                            "sizes": g.get("nutrition_by_milk") or {},
                            "seasonal": cid == "c01",
                        }
                        cur = out.get(code)
                        if cur is None:
                            out[code] = entry
                            continue
                        # 季節のおすすめ(c01)に載ったことが一度でもあれば覚えておく
                        cur["seasonal"] = cur["seasonal"] or entry["seasonal"]
                        # 通常店舗(regular)の記載を優先
                        if entry["store"] == "regular" and cur["store"] != "regular" and entry["top"] == cur["top"]:
                            entry["seasonal"] = cur["seasonal"]
                            out[code] = entry
    if not seen_any:
        raise ValueError("スターバックスの栄養JSON: 商品が1件も読めない")
    return out


def sbux_nutrition_variants(prod: dict) -> list[dict]:
    """栄養JSONの商品 -> [{label, nutrition}](サイズ順)。ミルクは標準(ミルク変更なし)だけ。"""
    variants = []
    sizes = prod.get("sizes") or {}
    for size in [s for s in SBUX_SIZE_ORDER if s in sizes] + [s for s in sizes if s not in SBUX_SIZE_ORDER]:
        rows = sizes[size]
        if not isinstance(rows, list) or not rows:
            continue
        row = None
        for want in SBUX_STANDARD_MILK:
            row = next((r for r in rows if r.get("_milk_type") == want), None)
            if row is not None:
                break
        row = row or rows[0]
        nut = {
            "kcal": _round(to_float(row.get("calory")), 0),
            "protein_g": _round(to_float(row.get("protein")), 1),
            "fat_g": _round(to_float(row.get("lipid")), 1),
            "carbs_g": _round(to_float(row.get("carbohydrate")), 1),
            "sugar_g": _round(to_float(row.get("sugar")), 1),
            "fiber_g": _round(to_float(row.get("dietary_fiber")), 1),
            "salt_g": _round(to_float(row.get("salt_eq")), 2),
        }
        other = {}
        for key, label, unit, nd in (("saturated_fatty_acids", "飽和脂肪酸", "g", 1),
                                     ("trans_fatty_acids", "トランス脂肪酸", "g", 2),
                                     ("sodium", "ナトリウム", "mg", 0), ("potassium", "カリウム", "mg", 0),
                                     ("caffeine", "カフェイン", "mg", 0)):
            v = to_float(row.get(key))
            if v is not None:
                other[label] = f"{_round(v, nd):g}{unit}"
        if other:
            nut["other"] = other
        if nut["kcal"] is None:
            continue
        variants.append({"label": SBUX_SIZE_LABEL.get(size, size.title()), "nutrition": nut})
    return variants


def sbux_representative(variants: list[dict]) -> dict | None:
    """代表の栄養: Tall があれば Tall、無ければ先頭(フードの STANDARD など)。"""
    if not variants:
        return None
    for v in variants:
        if v["label"] == "Tall":
            return v
    return variants[0]


def parse_sbux_allergens(text: str) -> dict[str, dict]:
    """allergens.json -> {url_jan_code: {"contains": [品目名]}}(標準のミルクの行を採用)。
    nutritions.json と同じ階層。商品が複数の販売区分にあるときは、含む品目の和集合(安全側)。"""
    d = json.loads(text)
    if not isinstance(d, dict) or "STARBUCKS" not in d:
        raise ValueError("スターバックスのアレルゲンJSON: 想定外の構造")
    out: dict[str, dict] = {}
    for top in SBUX_STORE_PRIORITY:
        tv = d.get(top)
        if not isinstance(tv, dict):
            continue
        for kind in ("beverage", "food"):
            kv = tv.get(kind)
            if not isinstance(kv, dict):
                continue
            for _kbn, store in kv.items():
                for _cid, cat in (store.get("categories") or {}).items():
                    for g in cat.get("menu_groups") or []:
                        code = str(g.get("url_jan_code") or "")
                        rows = g.get("allergen_by_milk")
                        if not code or not isinstance(rows, list) or not rows:
                            continue
                        row = None
                        for want in SBUX_STANDARD_MILK:
                            row = next((r for r in rows if r.get("_milk_type") == want), None)
                            if row is not None:
                                break
                        row = row or rows[0]
                        contains = [jp for key, jp in SBUX_ALLERGEN_NAMES.items() if row.get(key) in (1, True, "1")]
                        cur = out.setdefault(code, {"contains": []})
                        for c in contains:
                            if c not in cur["contains"]:
                                cur["contains"].append(c)
    for v in out.values():
        order = list(SBUX_ALLERGEN_NAMES.values())
        v["contains"].sort(key=order.index)
    return out


def parse_wp_feed(text: str) -> list[dict]:
    """WordPress の RSS(スターバックスのプレスリリース)-> [{title, link, date, summary}]。
    summary は発売日・商品名の照合にだけ使い、保存しない。"""
    if "<rss" not in text and "<channel" not in text:
        raise ValueError("RSSではない応答")
    out = []
    for m in re.finditer(r"<item>(.*?)</item>", text, re.S):
        b = m.group(1)
        title = re.search(r"<title>(.*?)</title>", b, re.S)
        link = re.search(r"<link>(.*?)</link>", b, re.S)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", b, re.S)
        desc = re.search(r"<description>(.*?)</description>", b, re.S)
        if not title or not link:
            continue
        date = None
        if pub:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(pub.group(1).strip())
                date = dt.astimezone(datetime.timezone(datetime.timedelta(hours=9))).date().isoformat()
            except (TypeError, ValueError):
                date = None
        summary = desc.group(1) if desc else ""
        summary = re.sub(r"^\s*<!\[CDATA\[|\]\]>\s*$", "", summary)
        out.append({
            "title": clean_text(re.sub(r"^\s*<!\[CDATA\[|\]\]>\s*$", "", title.group(1))),
            "link": link.group(1).strip(),
            "date": date,
            "summary": clean_text(summary),
        })
    return out


# ---------------------------------------------------------------------------
# タリーズコーヒー
# ---------------------------------------------------------------------------

TULLYS_BASE = "https://www.tullys.co.jp"
TULLYS_LINEUP_PATH = "/menu/seasonal_lineup/"


def parse_tullys_lineup(html: str) -> list[dict]:
    """/menu/seasonal_lineup/ の項目 -> [{path, name, listed_at(公式の掲載日時の日付)}]。

    `<li class="… js_item_list_new" data-new="2026-10-07 11:09:12">` ごとに商品リンクと名前。
    1件も読めないページ(構造変更・別ページへのリダイレクト)は ValueError にしない(呼び出し側が0件を失敗扱いにする)。"""
    out = []
    for m in re.finditer(r'<li\b[^>]*\bdata-new="([^"]*)"[^>]*>(.*?)</li>', html, re.S):
        body = m.group(2)
        href = re.search(r'<a\b[^>]*\bhref="([^"]+)"', body)
        name = re.search(r'<p class="[^"]*__text">(.*?)</p>', body, re.S)
        if not href or not name:
            continue
        stamp = m.group(1).strip()
        out.append({
            "path": html_lib.unescape(href.group(1)).split("#")[0],
            "name": clean_text(name.group(1)),
            "listed_at": stamp[:10] if re.match(r"\d{4}-\d{2}-\d{2}", stamp) else None,
            "listed_stamp": stamp,
        })
    return out


def parse_tullys_detail(html: str) -> dict | None:
    """タリーズの商品詳細 -> {name, prices:[{label, price}], allergens, energy:[{label, kcal}]}。

    ・価格は `<ul class="… price_list">` の `<li><strong>Short</strong><span>￥630<span class="tax_text">（税込）`
    ・アレルゲンは「食物アレルギー情報」欄の「商品に含まれる特定原材料 8品目」のアイコン(alt)。
      欄はあるが品目が無い商品(エアロカーノ)は contains=[] で none_listed=True。欄が無ければ None。
    ・エネルギーは「エネルギー情報」欄の HOT/COLD × サイズ、または単独の「279kcal」。
    説明文は読まない。名前が取れなければ None。"""
    name = None
    m = re.search(r'<span class="__se_title">(.*?)</span>', html, re.S)
    if m:
        name = clean_text(m.group(1))
    if not name:
        m = re.search(r'<div class="thumbnail__pic_image"><img[^>]*\balt="([^"]*)"', html)
        name = clean_text(m.group(1)) if m else None
    if not name:
        return None

    prices = []
    pm = re.search(r'<ul class="[^"]*price_list[^"]*">(.*?)</ul>', html, re.S)
    if pm:
        for li in re.finditer(r"<li>\s*<strong>([^<]*)</strong>\s*<span>\s*[¥￥]\s*([\d,]+)", pm.group(1), re.S):
            prices.append({"label": clean_text(li.group(1)), "price": int(li.group(2).replace(",", ""))})

    allergens = None
    a0 = html.find("食物アレルギー情報")
    e0 = html.find("エネルギー情報")
    if a0 >= 0:
        seg = html[a0: e0 if e0 > a0 else a0 + 4000]
        if "商品に含まれる特定原材料" in seg:
            alts = re.findall(r'<img[^>]*\balt="([^"]+)"[^>]*>\s*<p>', seg)
            allergens = {"contains": [clean_text(a) for a in alts], "none_listed": not alts}
        else:
            allergens = {"contains": [], "none_listed": True}

    energy = []
    if e0 >= 0:
        seg = html[e0:]
        end = seg.find("</section>")
        seg = seg[: end if end > 0 else 6000]
        temp = ""
        for mm in re.finditer(r'class="icon__(hot|cold)"|<strong>(.*?)</strong>\s*<span>\s*([\d.]+)\s*kcal', seg):
            if mm.group(1):
                temp = mm.group(1).upper()
            else:
                label = clean_text(mm.group(2))
                energy.append({"label": f"{temp} {label}".strip(), "kcal": float(mm.group(3))})
        if not energy:  # フードは「279kcal」だけ
            bare = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*kcal", seg)
            if bare:
                energy.append({"label": "", "kcal": float(bare.group(1))})
    return {"name": name, "prices": prices, "allergens": allergens, "energy": energy}


def parse_tullys_press(html: str, year_hint: int | None = None) -> list[dict]:
    """プレスリリース一覧 /company/pressrelease/{年}_index.html -> [{date, title, path}]。
    日付は「26/10/08」。"""
    out = []
    for m in re.finditer(r'<td class="press__item__date">\s*(\d{2})/(\d{2})/(\d{2})\s*</td>\s*'
                         r'<td class="press__item__title">\s*<a href="([^"]+)">(.*?)</a>', html, re.S):
        yy, mm_, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            date = datetime.date(2000 + yy, mm_, dd).isoformat()
        except ValueError:
            continue
        out.append({"date": date, "title": clean_text(m.group(5)), "path": html_lib.unescape(m.group(4))})
    return out


# ---------------------------------------------------------------------------
# コメダ珈琲店
# ---------------------------------------------------------------------------

KOMEDA_BASE = "https://eu.komeda.co.jp"
KOMEDA_MENU_URL_PATH = "/v1/hp/menu?brand_type=1&category=903"
KOMEDA_PUBLIC_URL = "https://www.komeda.co.jp/menu/?brand=1"
KOMEDA_NEWS_URL = "https://www.komeda.co.jp/news/"


def komeda_price_range(s: str | None) -> tuple[int, int] | None:
    """「680～740円」「530円～590円（税込）」「950円」-> (最小, 最大)。"""
    t = unicodedata.normalize("NFKC", s or "")
    nums = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", t)]
    if not nums:
        return None
    return min(nums), max(nums)


def parse_komeda_menu(text: str) -> dict:
    """/v1/hp/menu?brand_type=1&category=903 -> {"categories": {大カテゴリID: 名前}, "items": [...]}。

    レスポンスは dict {"constant": {...}, "menus": [...]}(2026-11-03に確認)。list だけ・menus が無い・
    id/name が無い項目があれば ValueError(構造変更の疑い)。説明文・画像URLは読まない。"""
    d = json.loads(text)
    if not isinstance(d, dict) or not isinstance(d.get("menus"), list):
        raise ValueError("コメダのメニューAPI: 想定外の構造(menus が無い)")
    cats: dict[str, str] = {}
    for f in ((d.get("constant") or {}).get("fields") or []):
        if isinstance(f, dict) and f.get("name") == "large_type":
            for v in f.get("values") or []:
                if isinstance(v, dict) and v.get("id"):
                    cats[str(v["id"])] = str(v.get("display_name") or "")
    items = []
    for m in d["menus"]:
        if not isinstance(m, dict) or not m.get("id") or not m.get("name"):
            raise ValueError("コメダのメニューAPI: id / name が無い項目")
        rng = (komeda_price_range(m.get("national_price_eatin"))
               or komeda_price_range(m.get("national_price_takeout")))
        items.append({
            "id": str(m["id"]),
            "name": clean_text(str(m["name"])),
            "price_min": rng[0] if rng else None,
            "price_max": rng[1] if rng else None,
            "price_text": m.get("national_price_eatin") or m.get("national_price_takeout") or None,
            "large_type": str(m.get("large_type") or ""),
            "is_limited_time": bool(m.get("is_limited_time")),
            "is_recommendation": bool(m.get("is_recommendation")),
            "is_hidden": bool(m.get("is_hidden")),
            "is_sold_out": bool(m.get("is_sold_out")),
            "is_setmenu": bool(m.get("is_setmenu")),
        })
    return {"categories": cats, "items": items}


def parse_komeda_allergy(text: str) -> dict:
    """/v1/hp/menu-allergy/{id} -> {"kcal": float|None, "contains": [品目], "note": 注記|None}。
    {"calori": "237", "allergy_comment": "", "menu_allergies": [{"name": "卵", "is_allergy": true}, …]}。
    キーが無ければ ValueError。"""
    d = json.loads(text)
    if not isinstance(d, dict) or not isinstance(d.get("menu_allergies"), list) or "calori" not in d:
        raise ValueError("コメダのアレルギーAPI: 想定外の構造")
    contains = [str(a.get("name")) for a in d["menu_allergies"] if isinstance(a, dict) and a.get("is_allergy")]
    note = clean_text(str(d.get("allergy_comment") or "")) or None
    return {"kcal": to_float(d.get("calori")), "contains": contains, "note": note}


def parse_komeda_notices(text: str) -> list[dict]:
    """/v1/hp/notice?limit=20&type=2 -> [{id, title, date(YYYY-MM-DD), text(平文。照合専用で保存しない)}]。"""
    d = json.loads(text)
    items = d.get("items") if isinstance(d, dict) else None
    if not isinstance(items, list):
        raise ValueError("コメダのお知らせAPI: 想定外の構造")
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", str(it.get("date") or ""))
        if not m:
            continue
        out.append({"id": str(it.get("id")), "title": clean_text(str(it.get("title") or "")),
                    "date": f"{m.group(1)}-{m.group(2)}-{m.group(3)}",
                    "text": strip_html(str(it.get("contents") or ""))})
    return out


# ---------------------------------------------------------------------------
# ドトールコーヒーショップ
# ---------------------------------------------------------------------------

DOUTOR_BASE = "https://www.doutor.co.jp"
DOUTOR_ALLERGY_BASE = "https://allergy.doutor.co.jp"
DOUTOR_SEASON_PATH = "/dcs/menu/season.html"
DOUTOR_CATEGORIES = ["hotdrink", "icedrink", "milano", "hotdog", "hotsandwich", "toast", "morning", "sweets"]
DOUTOR_ALLERGY_CATEGORIES = ["2-1-", "2-2-", "2-3-"]  # ドリンク / フード / デザート
DOUTOR_ALLERGY_LIST_PATH = "/allergy_check/doutor_coffee_shop/search/?menu_category={cat}"

_SUBTITLE_RE = re.compile(r"[~〜～].*?[~〜～]")


def doutor_name_keys(name: str | None) -> set[str]:
    """ドトールの商品名の照合キー: 正規化した全体と、「～…～」の副題を除いた本体。

    表記揺れ(全角半角・空白・波ダッシュの種類)は normalize_name が吸収する。"""
    s = unicodedata.normalize("NFKC", html_lib.unescape(name or ""))
    full = normalize_name(s)
    keys = {full} if full else set()
    base = normalize_name(_SUBTITLE_RE.sub("", s))
    if base and base != full and len(base) >= 4:
        keys.add(base)
    return keys


def parse_doutor_season(html: str, goods_words: list[str] | None = None) -> dict:
    """季節のおすすめ /dcs/menu/season.html -> {"items": [...], "skipped": [除外した名前]}。

    `<div id="menuN" class="dcs-season-product">` のブロックごとに、`<li class="dcs-season-menuList-item">`
    を1商品として読む。名前は `<p class="dcs-season-menuList-name">`、価格は
    (a)`<div class="pprice eatin-price">¥570～</div>`(サイズ列があれば直前の `size_str` がラベル)、
    (b)`<p class="__head"><span>販売価格</span></p> … <div class="__body"><p>¥4,300</p>`。
    商品でないもの(価格が無い: 「■店頭受取」「■宅配」「■ドトール バリューカードキャンペーン」「今年は…ピック付！」)は除外する。
    「■店頭受取」「■宅配」の予約・お渡し期間は、同じブロックの商品に reservation として付ける。
    価格のラベルに goods_words(コーヒー豆・ドリップ等)を含む商品は、豆・パッケージ商品として除外する。
    ブロックが1つも無いページは ValueError(構造変更の疑い)。"""
    goods_words = goods_words or []
    blocks = list(re.finditer(r'<div id="menu(\d+)" class="dcs-season-product">', html))
    if not blocks:
        raise ValueError("ドトールの季節のおすすめ: dcs-season-product が1つも無い")
    items, skipped = [], []
    for bi, bm in enumerate(blocks):
        end = blocks[bi + 1].start() if bi + 1 < len(blocks) else len(html)
        block = html[bm.end(): end]
        chunks = re.split(r'<li class="dcs-season-menuList-item">', block)[1:]
        reservation = []
        block_items = []
        for ch in chunks:
            nm = re.search(r'<p class="dcs-season-menuList-name">(.*?)</p>', ch, re.S)
            if not nm:
                continue
            name = clean_text(nm.group(1))
            if name.startswith("■"):
                spans = [clean_text(s) for s in re.findall(r'<span class="priceList-txt"[^>]*>(.*?)</span>', ch, re.S)]
                if spans and len(spans) % 2 == 0:
                    half = len(spans) // 2
                    reservation.append({"channel": name.lstrip("■").strip(),
                                        "periods": [{"label": a, "value": b}
                                                    for a, b in zip(spans[:half], spans[half:])]})
                continue
            prices = []
            label = None
            for pm in re.finditer(
                    r'<span class="size_str">(.*?)</span>'
                    r'|<p class="__head"><span>(.*?)</span></p>'
                    r'|<div class="pprice eatin-price"[^>]*>\s*([^<]*?)\s*</div>'
                    r'|<div class="__body">\s*<p>\s*([^<]*?)\s*</p>', ch, re.S):
                if pm.group(1) is not None:
                    label = clean_text(pm.group(1))
                elif pm.group(2) is not None:
                    label = clean_text(pm.group(2))
                else:
                    raw = pm.group(3) if pm.group(3) is not None else pm.group(4)
                    yen = parse_yen(raw)
                    if yen is not None:
                        prices.append({"label": label, "price": yen, "from": "～" in (raw or "") or "〜" in (raw or "")})
                    label = None
            if not prices:
                continue  # 価格の無い項目(ピックの案内など)は商品ではない
            if any(w in (p["label"] or "") for p in prices for w in goods_words):
                skipped.append(name)
                continue
            block_items.append({"name": name, "prices": prices, "block": f"menu{bm.group(1)}"})
        for it in block_items:
            if reservation and any(p["label"] in ("販売価格", "宅配価格") for p in it["prices"]):
                it["reservation"] = reservation
            items.append(it)
    return {"items": items, "skipped": skipped}


def parse_doutor_cards(html: str) -> list[dict]:
    """カテゴリ一覧 /dcs/menu/list/{cat}.html のカード -> [{id(14桁), name, is_new}]。
    NEW印は `<span class="dcs-menu-cat-img-new new_icon">`。カードが0件でも空リスト(呼び出し側が判断)。"""
    out = []
    for m in re.finditer(r'<a href="(?:https://www\.doutor\.co\.jp)?/dcs/menu/detail/(\d+)\.html" class="dcs-menu-cat-a[^"]*">(.*?)</a>',
                         html, re.S):
        nm = re.search(r'rt_cf_product_name">(.*?)</span>', m.group(2), re.S)
        if not nm:
            continue
        out.append({"id": m.group(1), "name": clean_text(nm.group(1)), "is_new": "new_icon" in m.group(2)})
    return out


def parse_doutor_detail(html: str) -> dict | None:
    """商品詳細 /dcs/menu/detail/{id}.html -> {name, price(店内の税込)}。HTMLコメントの中は読まない。"""
    h = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    nm = re.search(r'<span class="pnam-jp rt_cf_product_name">(.*?)</span>', h, re.S)
    if not nm:
        return None
    pr = re.search(r'class="rt_cf_price_eat_in">(.*?)</span>', h, re.S)
    return {"name": clean_text(nm.group(1)), "price": parse_yen(pr.group(1)) if pr else None,
            "is_new": "dcs-menu-detail-img-new" in h}


def parse_doutor_allergy_list(html: str) -> list[dict]:
    """allergy.doutor.co.jp の一覧 -> [{id, name}]。カードは
    `<a href="/allergy_check/doutor_coffee_shop/item/detail/11003073" class="cards_item">` と `cards_title`。"""
    out = []
    for m in re.finditer(r'<a href="/allergy_check/doutor_coffee_shop/item/detail/(\d+)/?" class="cards_item">(.*?)</a>',
                         html, re.S):
        t = re.search(r'<p class="cards_title">(.*?)</p>', m.group(2), re.S)
        if t:
            out.append({"id": m.group(1), "name": clean_text(t.group(1))})
    return out


def parse_doutor_allergy_detail(html: str) -> dict | None:
    """アレルギー・栄養の詳細 -> {name, allergens:{contains, partial}, variants:[{label, nutrition}]}。

    アレルゲンの凡例(公式のCSV `infoCards_item-circle` の content:"●"、`-triangle` は△、`-line` は「－」と、
    詳細ページの注意書き「●はアレルギー物質を含むことを示す」「△はのり・しらすについてえび・かにが混ざる漁法…」で確認済み):
    circle=含む / triangle=条件つき(△) / line=含まない。想定外のクラスがあれば、そのカードは含む判定にしない
    (unknown に残す)。栄養は「1食当たり」。サイズ違いは infoTab のボタン名がラベル。
    名前が取れなければ None。"""
    h = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    nm = re.search(r'<h1 class="detailHeader_title">(.*?)</h1>', h, re.S)
    if not nm:
        return None
    contains, partial, unknown = [], [], []
    cards = re.findall(r'<li class="infoCards_item infoCards_item-([a-z]+)">.*?<p class="infoCards_title">(.*?)</p>', h, re.S)
    for kind, title in cards:
        t = clean_text(title)
        if kind == "circle":
            contains.append(t)
        elif kind == "triangle":
            partial.append({"name": t, "mark": "△"})
        elif kind != "line":
            unknown.append(t)
    labels = [clean_text(x) for x in re.findall(r'<button type="button" class="infoTab_btnsTrigger">(.*?)</button>', h, re.S)]
    blocks = re.findall(r'<div class="infoTab_block"[^>]*>(.*?)</ul>', h, re.S)
    variants = []
    keymap = {"エネルギー": "kcal", "たんぱく質": "protein_g", "脂質": "fat_g", "炭水化物": "carbs_g", "食塩相当量": "salt_g"}
    for i, b in enumerate(blocks):
        nut: dict = {}
        other: dict = {}
        for it in re.finditer(r'<p class="infoTab_title">(.*?)</p>\s*<p class="infoTab_txt">(.*?)</p>', b, re.S):
            title = clean_text(it.group(1))
            val_html = it.group(2)
            vm = re.search(r"<span>\s*([^<]*?)\s*</span>\s*([^<\s]*)", val_html)
            num = to_float(vm.group(1)) if vm else None
            unit = (vm.group(2) if vm else "") or ""
            if title in keymap:
                if num is not None:
                    nut[keymap[title]] = num
            elif num is not None:
                other[title] = f"{num:g}{unit}"
        if nut.get("kcal") is None:
            continue
        for k in ("protein_g", "fat_g", "carbs_g", "salt_g"):
            nut.setdefault(k, None)
        if other:
            nut["other"] = other
        label = labels[i] if len(labels) == len(blocks) and len(blocks) > 1 else (labels[i] if i < len(labels) and len(blocks) > 1 else None)
        variants.append({"label": label, "nutrition": nut})
    return {"name": clean_text(nm.group(1)),
            "allergens": {"contains": contains, "partial": partial, "unknown": unknown},
            "variants": variants}
