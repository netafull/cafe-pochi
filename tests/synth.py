"""合成フィクスチャ: 4社の公式データの「構造だけ」を再現した、名前・数値を創作したデータ。

各社の規約が複製・転載・引用を禁じているため、公式のHTML/JSONをそのままリポジトリに入れない。
ここにあるのはパーサが読む箇所(タグ・クラス名・キー)だけを再現した最小のものと、それを組み立てる関数。
商品名・価格・栄養の数値はすべて創作(実在の商品とは無関係)。
"""

from __future__ import annotations

import json


# ---------------------------------------------------------------------------
# スターバックス
# ---------------------------------------------------------------------------

def sbux_item(code: str, name: str, price: int = 600, pmax: int | None = None, methods=("STARBUCKS_COFFEE",),
              certain: bool = False) -> dict:
    return {"item_code": code, "item_name": name, "price_in_vat": price, "price_in_vat_min": price,
            "price_in_vat_max": pmax if pmax is not None else price, "purchase_methods": list(methods),
            "certain_stores": certain, "brand_code": "x"}


def sbux_list_pages(items: list[dict], page_size: int = 10) -> dict[int, str]:
    """{start: JSON} 新作一覧(tag=new)を10件ずつのページにしたもの。"""
    pages = {}
    for start in range(0, max(len(items), 1), page_size):
        pages[start] = json.dumps({"count": len(items), "current_timestamp": "20261103000000",
                                    "item": items[start:start + page_size], "aggs": {}}, ensure_ascii=False)
    return pages


def _nut_row(kcal: float, milk=None, protein=3.0, lipid=5.0, carb=20.0, salt=0.2, sugar=15.0, fiber=0.5) -> dict:
    return {"_milk_type": milk, "calory": kcal, "protein": protein, "lipid": lipid, "carbohydrate": carb,
            "salt_eq": salt, "sodium": 80.0, "dietary_fiber": fiber, "saturated_fatty_acids": 2.0, "sugar": sugar,
            "potassium": 100.0, "trans_fatty_acids": 0.1, "caffeine": 50.0, "alcohol": "-"}


def sbux_product(code: str, name: str, sizes: dict[str, float], milks=("milk", "soy"), is_menu=True) -> dict:
    nbm = {}
    for size, kcal in sizes.items():
        if size == "STANDARD":
            nbm[size] = [_nut_row(kcal, None)]
        else:
            nbm[size] = [_nut_row(kcal + (30 if m == "soy" else 0), m) for m in milks]
    return {"url_jan_code": code, "link": f"/item/x/{code}/", "product_name_ja": name, "is_menu_product": is_menu,
            "nutrition_by_milk": nbm}


def sbux_nutrition_json(beverage_c01=(), food_c01=(), beverage_other=(), top_extra: dict | None = None) -> str:
    def store(c01, other, key="beverage"):
        cats = {}
        if c01:
            cats["c01"] = {"category_name_ja": "季節のおすすめ", "menu_groups": list(c01)}
        if other:
            cats["c04"] = {"category_name_ja": "エスプレッソ", "menu_groups": list(other)}
        return {"kbn_1": {"selling_store_name_ja": "regular", "last_updated": "20261102", "categories": cats}}
    d = {"STARBUCKS": {"beverage": store(beverage_c01, beverage_other), "food": store(food_c01, ())},
         "RESERVE": {"beverage": {}, "food": []}, "ROASTERY": {"beverage": {}, "food": []},
         "ONLINE_STORE": {"beverage": [], "food": []}}
    if top_extra:
        d.update(top_extra)
    return json.dumps(d, ensure_ascii=False)


def sbux_allergen_product(code: str, name: str, **flags) -> dict:
    row = {k: 0 for k in ("egg", "milk", "wheat", "buck_wheat", "peanut", "shrimp", "crab", "walnut", "almond",
                          "abalone", "cuttlefish", "salmon_roe", "orange", "cashewnut", "kiwi_fruit", "beef",
                          "sesame", "salmon", "mackerel", "soybean", "chicken", "banana", "pork", "matsutake",
                          "peach", "yamaimo", "apple", "gelatin", "seafood", "macadamia_nuts")}
    row.update(flags)
    rows = [dict(row, _milk_type="milk"), dict(row, _milk_type="soy", soybean=1)]
    return {"url_jan_code": code, "product_name_ja": name, "allergen_by_milk": rows}


def sbux_allergens_json(bev=(), food=()) -> str:
    def store(items):
        return {"kbn_1": {"selling_store_name_ja": "regular", "categories": {
            "c01": {"category_name_ja": "季節のおすすめ", "menu_groups": list(items)}}}}
    return json.dumps({"STARBUCKS": {"beverage": store(bev), "food": store(food)}}, ensure_ascii=False)


def wp_feed(items: list[tuple[str, str, str, str]]) -> str:
    """[(title, link, RFC822日付, summary)]"""
    body = ""
    for t, link, pub, desc in items:
        body += (f"<item><title>{t}</title><link>{link}</link><pubDate>{pub}</pubDate>"
                 f"<description><![CDATA[{desc}]]></description></item>")
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'


# ---------------------------------------------------------------------------
# タリーズコーヒー
# ---------------------------------------------------------------------------

def tullys_lineup_html(items: list[tuple[str, str, str]]) -> str:
    """[(path, name, data-new)]"""
    lis = "".join(
        f'<li class="sl-newitems-contents__item js_item_list_new" data-new="{stamp}">'
        f'<a class="sl-newitems-contents__link" href="{path}"><div class="sl-newitems-contents__img"><img src="/x.jpg" alt=""></div>'
        f'<p class="sl-newitems-contents__text">{name}</p></a></li>' for path, name, stamp in items)
    return f'<html><body><ul class="sl-newitems-contents__list">{lis}</ul></body></html>'


def tullys_detail_html(name: str, prices=(("Tall", 600),), allergens=("卵",), energy=(("HOT", "Tall", 250),),
                       bare_kcal: int | None = None, allergy_section: bool = True) -> str:
    plis = "".join(f'<li><strong>{lb}　</strong><span>￥{p:,}<span class="tax_text">（税込）</span></span></li>'
                   for lb, p in prices)
    al = ""
    if allergy_section:
        icons = "".join(f'<li><img src="/a.png" alt="{a}"><p>{a}</p></li>' for a in allergens)
        al = (f'<h2 class="common_title">食物アレルギー情報<a class="arrow_link" href="#">詳細</a></h2>'
              + (f'<p>商品に含まれる特定原材料 8品目</p><ul class="icon_list_horizontal">{icons}</ul>' if allergens else "")
              + '<p class="contami_text">※「特定原材料に準ずるもの20品目」については</p>')
    if bare_kcal is not None:
        en = f'<ul class="sprit_list">\n {bare_kcal}kcal\n</ul>'
    else:
        en = ""
        for temp in ("HOT", "COLD"):
            rows = [(s, k) for t, s, k in energy if t == temp]
            if rows:
                lis = "".join(f"<li><strong>{s}</strong><span>{k}kcal</span></li>" for s, k in rows)
                en += f'<p><span class="icon__{temp.lower()}">{temp}</span></p><ul class="sprit_list">{lis}</ul>'
    return (f'<html><body><div class="thumbnail__pic_image"><img src="/x.jpg" alt="{name}"></div>'
            f'<ul class="sprit_list price_list">{plis}</ul>'
            f'<section class="aside_column">{al}<h2 class="common_title">エネルギー情報<a href="#">詳細</a></h2>{en}</section>'
            f'<div id="__se_hint"><span class="__se_title">{name}</span></div></body></html>')


def tullys_press_html(rows: list[tuple[str, str, str]]) -> str:
    """[(YY/MM/DD, title, path)]"""
    trs = "".join(f'<tr class="press__item"><td class="press__item__date">{d}</td><td class="press__item__title">'
                  f'<a href="{path}">{t}</a></td></tr>' for d, t, path in rows)
    return f"<html><body><table>{trs}</table></body></html>"


# ---------------------------------------------------------------------------
# コメダ珈琲店
# ---------------------------------------------------------------------------

def komeda_menu_json(items: list[dict], wrap: str = "dict") -> str:
    menus = []
    for it in items:
        m = {"id": it["id"], "name": it["name"], "large_type": it.get("large_type", "1-3"),
             "national_price_eatin": it.get("price", "530～590円"), "national_price_takeout": "",
             "is_limited_time": it.get("limited", True), "is_recommendation": True, "is_hidden": it.get("hidden", False),
             "is_sold_out": False, "is_setmenu": False}
        menus.append(m)
    constant = {"fields": [{"name": "large_type", "display_name": "大カテゴリ", "values": [
        {"id": "1-2", "display_name": "ドリンク"}, {"id": "1-3", "display_name": "デザート"},
        {"id": "1-4", "display_name": "スナック"}]}]}
    if wrap == "list":
        return json.dumps(menus, ensure_ascii=False)
    return json.dumps({"constant": constant, "menus": menus}, ensure_ascii=False)


def komeda_allergy_json(kcal="300", contains=("卵", "乳成分"), comment="") -> str:
    names = ["卵", "乳成分", "小麦", "えび", "そば", "大豆"]
    return json.dumps({"calori": kcal, "allergy_comment": comment,
                       "menu_allergies": [{"name": n, "is_allergy": n in contains} for n in names]}, ensure_ascii=False)


def komeda_notices_json(notices: list[tuple[str, str, str]]) -> str:
    """[(title, YYYY.MM.DD, contents HTML)]"""
    return json.dumps({"total": len(notices), "items": [
        {"id": 100 + i, "title": t, "contents": c, "date": d, "type": "2"} for i, (t, d, c) in enumerate(notices)]},
        ensure_ascii=False)


# ---------------------------------------------------------------------------
# ドトールコーヒーショップ
# ---------------------------------------------------------------------------

def _price_eatin(price: str, size: str | None = None) -> str:
    sz = f'<div class="m size_class" data-mh="sizeName"><span class="size_str">{size}</span></div>' if size else ""
    return (f'<div class="pinfo"><ul class="psize"><li class="info">{sz}'
            f'<div class="pprice eatin-price" data-mh="eatin">{price}</div></li></ul></div>')


def _price_area(rows: list[tuple[str, str]]) -> str:
    return "".join(f'<div class="dcs-season-menuList-priceArea"><p class="__head"><span>{h}</span></p>'
                   f'<div class="__body"><p>{p}</p></div></div>' for h, p in rows)


def doutor_season_html(blocks: list[list[str]]) -> str:
    """blocks = ブロックごとの <li>…</li> 文字列のリスト(doutor_item_* で作る)。"""
    out = ""
    for i, items in enumerate(blocks, start=1):
        out += (f'<div id="menu{i}" class="dcs-season-product"><img src="/x.jpg" alt="x">'
                f'<div class="dcs-season-product-txt"><ul class="dcs-season-menuList opacity">{"".join(items)}</ul></div></div>')
    return f"<html><body>{out}</body></html>"


def doutor_item(name: str, price: str = "¥500", size: str | None = None, area: list[tuple[str, str]] | None = None) -> str:
    body = _price_area(area) if area else _price_eatin(price, size)
    return (f'<li class="dcs-season-menuList-item"><div class="dcs-season-menuList-wrap">'
            f'<p class="dcs-season-menuList-name">{name}</p></div>{body}<p>説明文のダミー</p></li>')


def doutor_item_noprice(name: str) -> str:
    return (f'<li class="dcs-season-menuList-item"><p class="dcs-season-menuList-name">{name}</p>'
            '<p class="mt10">素材：紙</p></li>')


def doutor_item_reservation(channel: str, labels: tuple[str, str], values: tuple[str, str]) -> str:
    return (f'<li class="dcs-season-menuList-item"><div class="dcs-season-menuList-wrap">'
            f'<p class="dcs-season-menuList-name">■{channel}</p></div>'
            f'<ul class="psize"><li><div class="priceList-eatin"><span class="priceList-txt">{labels[0]}</span>'
            f'<span class="priceList-txt">{labels[1]}</span></div></li>'
            f'<li><div class="priceList-eatin"><span class="priceList-txt">{values[0]}</span>'
            f'<span class="priceList-txt">{values[1]}</span></div></li></ul></li>')


def doutor_cards_html(cards: list[tuple[str, str, bool]]) -> str:
    """[(14桁ID, 名前, NEW印)]"""
    out = ""
    for cid, name, is_new in cards:
        new = '<span class="dcs-menu-cat-img-new new_icon"><img src="/n.png"></span>' if is_new else ""
        out += (f'<li><a href="/dcs/menu/detail/{cid}.html" class="dcs-menu-cat-a opacity">'
                f'<div class="dcs-menu-cat-img">{new}<img src="/x.jpg" alt=""></div>'
                f'<h2 class="mod-head-dcs01"><span class="rt_cf_product_name">{name}</span></h2></a></li>')
    return f"<html><body><ul>{out}</ul></body></html>"


def doutor_detail_html(name: str, price: str = "¥ 480") -> str:
    return (f'<html><body><h1 class="dcs-menu-detail-head"><span class="pnam-jp rt_cf_product_name">{name}</span></h1>'
            f'<ul class="psize"><li class="info"><div class="pprice eatin-price"><span class="rt_cf_price_eat_in">{price}</span></div>'
            '<!--  <div class="pprice takeout-price"><span class="rt_cf_price_to_go">110</span></div> --></li></ul></body></html>')


def doutor_allergy_list_html(rows: list[tuple[str, str]]) -> str:
    out = "".join(f'<li><a href="/allergy_check/doutor_coffee_shop/item/detail/{i}" class="cards_item">'
                  f'<p class="cards_title">{n}</p></a></li>' for i, n in rows)
    return f"<html><body><ul>{out}</ul></body></html>"


def doutor_allergy_detail_html(name: str, circle=("卵",), triangle=(), line=("えび", "かに"),
                               tabs: list[tuple[str | None, float]] | None = None, extra_kind: str | None = None) -> str:
    cards = ""
    for kind, names in (("circle", circle), ("triangle", triangle), ("line", line)):
        for n in names:
            cards += (f'<li class="infoCards_item infoCards_item-{kind}"><div class="infoCards_head">'
                      f'<p class="infoCards_title">{n}</p></div></li>')
    if extra_kind:
        cards += (f'<li class="infoCards_item infoCards_item-{extra_kind}"><div class="infoCards_head">'
                  '<p class="infoCards_title">未知の品目</p></div></li>')
    tabs = tabs or [(None, 250.0)]
    btns = "".join(f'<li class="infoTab_btnsItem"><button type="button" class="infoTab_btnsTrigger">{lb}</button></li>'
                   for lb, _ in tabs if lb)
    blocks = ""
    for _lb, kcal in tabs:
        blocks += ('<div class="infoTab_block" data-tab-content><ul class="infoTab_list">'
                   f'<li class="infoTab_item"><p class="infoTab_title">エネルギー</p><p class="infoTab_txt"><span>{kcal:g}</span>kcal</p></li>'
                   '<li class="infoTab_item"><p class="infoTab_title">たんぱく質</p><p class="infoTab_txt"><span>4.5</span>g</p></li>'
                   '<li class="infoTab_item"><p class="infoTab_title">脂質</p><p class="infoTab_txt"><span>10.0</span>g</p></li>'
                   '<li class="infoTab_item"><p class="infoTab_title">炭水化物</p><p class="infoTab_txt"><span>30.5</span>g</p></li>'
                   '<li class="infoTab_item"><p class="infoTab_title">食塩相当量</p><p class="infoTab_txt"><span>0.3</span>g</p></li>'
                   '<li class="infoTab_item"><p class="infoTab_title">アルコール</p><p class="infoTab_txt"><span>-</span></p></li>'
                   '</ul></div>')
    return (f'<html><body><h1 class="detailHeader_title">{name}</h1>'
            f'<section id="anc01"><ul class="infoCards_list">{cards}</ul></section>'
            f'<section id="anc02"><div class="infoTab"><ul class="infoTab_btnsList">{btns}</ul>'
            f'<div class="infoTab_blocks">{blocks}</div></div></section></body></html>')
