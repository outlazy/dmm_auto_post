#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FANZA（DMM）アフィリエイトAPIで素人動画（floor=videoc）を自動取得→WordPress投稿
・日本時間（JST）で動作
・サンプル画像は全てWordPressにアップロードし、直リンクは使用しない
・タイトル取得順: item["title"] → iteminfo.actress → ActressSearch API → 名前のみ
・ジャンルに「熟女」が含まれる場合は必ずスキップ
・config.yml等の設定ファイル不要、全て環境変数（GitHub Secrets等）で管理
"""

import os
import re
import json
import requests
from datetime import datetime, date
import pytz
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client

DMM_API_URL = "https://api.dmm.com/affiliate/v3/ItemList"
ACTRESS_SEARCH_URL = "https://api.dmm.com/affiliate/v3/ActressSearch"

NG_DESCRIPTIONS = [
    "From here on, it will be an adult site",
    "18歳未満",
    "アダルト商品を取り扱う",
    "未成年",
    "成人向け",
    "アダルトサイト",
    "ご利用は18歳以上",
    "18才未満",
]

# ──────────────────────────────────────────
# 基本ユーティリティ
# ──────────────────────────────────────────

def now_jst():
    return datetime.now(pytz.timezone('Asia/Tokyo'))

def get_env(key, required=True, default=None):
    val = os.environ.get(key, default)
    if required and not val:
        raise RuntimeError(f"環境変数 {key} が設定されていません")
    return val

def _calc_age(birthday_str):
    """'YYYY-MM-DD' 形式の誕生日から現在年齢を返す。取得不可なら None。"""
    try:
        bday = date.fromisoformat(birthday_str[:10])
        today = date.today()
        return today.year - bday.year - ((today.month, today.day) < (bday.month, bday.day))
    except Exception:
        return None

# ──────────────────────────────────────────
# DMM API 取得
# ──────────────────────────────────────────

def fetch_amateur_videos():
    API_ID = get_env("DMM_API_ID")
    AFF_ID = get_env("DMM_AFFILIATE_ID")
    params = {
        "api_id": API_ID,
        "affiliate_id": AFF_ID,
        "site": "FANZA",
        "service": "digital",
        "floor": "videoc",
        "sort": "date",
        "output": "json",
        "hits": 20,
    }
    resp = requests.get(DMM_API_URL, params=params, timeout=10)
    try:
        resp.raise_for_status()
    except Exception:
        print("---- DMM API Error ----")
        print(resp.text)
        print("----------------------")
        raise

    items = resp.json().get("result", {}).get("items", [])
    print(f"API取得件数: {len(items)}")
    for item in items:
        print("==== APIアイテム全体 ====")
        print(item)
        siu = item.get("sampleImageURL", {})
        if "sample_l" in siu and "image" in siu["sample_l"]:
            print("sample_l images:", siu["sample_l"]["image"])
        if "sample_s" in siu and "image" in siu["sample_s"]:
            print("sample_s images:", siu["sample_s"]["image"])
    return items

def search_actress_profile(name, api_id, aff_id):
    """
    ActressSearch API で名前を検索し
    {"size_str": "T158 B87(G) W58 H85", "age": 21} を返す。
    見つからない・データなしなら None。
    """
    try:
        params = {
            "api_id": api_id,
            "affiliate_id": aff_id,
            "keyword": name,
            "output": "json",
        }
        resp = requests.get(ACTRESS_SEARCH_URL, params=params, timeout=10)
        resp.raise_for_status()
        actresses = resp.json().get("result", {}).get("actress", [])
        print(f"  ActressSearch「{name}」→ {len(actresses)}件")
        if not actresses:
            return None
        a = actresses[0]
        height   = str(a.get("height",   "") or "").strip()
        bust     = str(a.get("bust",     "") or "").strip()
        cup      = str(a.get("cup",      "") or "").strip()
        waist    = str(a.get("waist",    "") or "").strip()
        hip      = str(a.get("hip",      "") or "").strip()
        birthday = str(a.get("birthday", "") or "").strip()
        size_str = f"T{height} B{bust}({cup}) W{waist} H{hip}" \
                   if height and bust and cup and waist and hip else None
        age = _calc_age(birthday) if birthday else None
        print(f"  → size_str={size_str}, age={age}")
        return {"size_str": size_str, "age": age}
    except Exception as e:
        print(f"  ActressSearch失敗: {e}")
    return None

# ──────────────────────────────────────────
# フィルタリング
# ──────────────────────────────────────────

def is_released(item):
    date_str = item.get("date")
    if not date_str:
        return False
    try:
        jst = pytz.timezone('Asia/Tokyo')
        release_date = jst.localize(datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S"))
        return release_date <= now_jst()
    except Exception:
        return True

def contains_jukujo(item):
    ii = item.get("iteminfo", {})
    genres = [g.get("name", "") for g in ii.get("genre", []) if "name" in g]
    return "熟女" in genres

# ──────────────────────────────────────────
# タイトル生成（名前 + サイズ）
# ──────────────────────────────────────────

def build_title(item):
    """
    タイトルを "名前(年齢) T158 B87(G) W58 H85" 形式で返す。

    取得順:
      1. item["title"]                → 名前
      2. iteminfo.actress[0]          → サイズ・年齢（APIに入っていれば）
      3. ActressSearch API（名前で検索）→ サイズ・年齢
      4. 年齢除去して再検索            → "キミカ(27)" → "キミカ"
      ※ どこにも登録がなければ名前のみ
    """
    API_ID = get_env("DMM_API_ID")
    AFF_ID = get_env("DMM_AFFILIATE_ID")
    ii = item.get("iteminfo", {})

    # ── Step 1: 名前 ──
    name = item.get("title", "").strip()
    if not name:
        actresses = [a.get("name") for a in ii.get("actress", []) if a.get("name")]
        name = actresses[0] if actresses else "不明"
    print(f"  名前(APIタイトル): {name}")

    # ── Step 2: iteminfo.actress[0] のサイズフィールド ──
    size_str = None
    age = None
    if ii.get("actress"):
        a = ii["actress"][0]
        height   = str(a.get("height",   "") or "").strip()
        bust     = str(a.get("bust",     "") or "").strip()
        cup      = str(a.get("cup",      "") or "").strip()
        waist    = str(a.get("waist",    "") or "").strip()
        hip      = str(a.get("hip",      "") or "").strip()
        birthday = str(a.get("birthday", "") or "").strip()
        print(f"  iteminfo.actress[0]: h={height} b={bust} c={cup} w={waist} hip={hip} bday={birthday}")
        if height and bust and cup and waist and hip:
            size_str = f"T{height} B{bust}({cup}) W{waist} H{hip}"
            print(f"  サイズ(iteminfo): {size_str}")
        if birthday:
            age = _calc_age(birthday)

    # ── Step 3 & 4: ActressSearch API ──
    if not size_str or age is None:
        base_name = re.sub(r'\(\d+\)', '', name).strip()
        candidates = list(dict.fromkeys([base_name, name]))
        for candidate in candidates:
            profile = search_actress_profile(candidate, API_ID, AFF_ID)
            if not profile:
                continue
            if not size_str and profile.get("size_str"):
                size_str = profile["size_str"]
            if age is None and profile.get("age") is not None:
                age = profile["age"]
            if size_str and age is not None:
                break

    # ── Step 5: item["comment"] フィールドからサイズ・年齢を抽出 ──
    # 素人動画はActressSearchに未登録のことが多いため、
    # commentフィールドに "キミカ(27) T158 B87(G) W58 H85" 形式で含まれることがある
    if not size_str or age is None:
        SIZE_PAT = re.compile(r'T(\d{2,3})\s*B(\d{2,3})\(([A-Z]{1,2})\)\s*W(\d{2,3})\s*H(\d{2,3})')
        AGE_PAT  = re.compile(r'\((\d{1,2})\)')
        comment = item.get("comment", "") or ""
        print(f"  comment冒頭: {comment[:150]}")
        if comment:
            if not size_str:
                m = SIZE_PAT.search(comment)
                if m:
                    size_str = f"T{m.group(1)} B{m.group(2)}({m.group(3)}) W{m.group(4)} H{m.group(5)}"
                    print(f"  サイズ(comment): {size_str}")
            if age is None:
                # コメント冒頭 "名前(年齢) ..." の (年齢) を取得
                m = AGE_PAT.search(comment[:80])
                if m:
                    raw = int(m.group(1))
                    if 18 <= raw <= 60:
                        age = raw
                        print(f"  年齢(comment): {age}")

    # ── 名前に年齢を付加 ──
    has_age = bool(re.search(r'\(\d+\)', name))
    base_name = re.sub(r'\(\d+\)', '', name).strip()
    if not has_age and age is not None:
        display_name = f"{base_name}({age})"
    else:
        display_name = name  # 元々 "キミカ(27)" 形式ならそのまま

    result = f"{display_name} {size_str}" if size_str else display_name
    print(f"  生成タイトル: {result}")
    return result

# ──────────────────────────────────────────
# アフィリエイトリンク・画像
# ──────────────────────────────────────────

def make_affiliate_link(url, aff_id):
    from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs["affiliate_id"] = aff_id
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(qs), parsed.fragment))

def upload_image(wp, url):
    """
    画像をダウンロードしてWordPressにアップロード。
    戻り値: (media_id, wp_url)。失敗時は (None, None)。
    """
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.content
        name = os.path.basename(url.split("?")[0]) or "image.jpg"
        if "." not in name:
            name = "image.jpg"
        media_data = {"name": name, "type": "image/jpeg", "bits": xmlrpc_client.Binary(data)}
        res = wp.call(media.UploadFile(media_data))
        print(f"  UploadFile レスポンス keys: {list(res.keys()) if isinstance(res, dict) else res}")
        media_id = res.get("id") if isinstance(res, dict) else None
        wp_url = None
        if isinstance(res, dict):
            wp_url = res.get("url") or res.get("link") or res.get("guid")
        if not wp_url:
            print(f"  警告: wp_urlが取得できませんでした。レスポンス: {res}")
            return None, None
        if any(d in wp_url for d in ("dmm.co.jp", "dmm.com", "fanza")):
            print(f"  エラー: FANZAドメインのURLが返されました（スキップ）: {wp_url}")
            return None, None
        print(f"  アップロード成功: {name} → {wp_url}")
        return media_id, wp_url
    except Exception as e:
        print(f"画像アップロード失敗: {url} ({e})")
        return None, None

# ──────────────────────────────────────────
# 本文の説明文取得
# ──────────────────────────────────────────

def is_valid_description(desc):
    if not desc or len(desc) < 30:
        return False
    return not any(ng in desc for ng in NG_DESCRIPTIONS)

def fetch_description(item):
    """
    本文説明文を取得する。
    APIの comment / description / story フィールドを確認し、
    なければ自動生成する。
    """
    ii = item.get("iteminfo", {})
    for key in ("comment", "description", "story"):
        val = item.get(key) or ii.get(key)
        if is_valid_description(val):
            return val

    # 自動生成
    cast   = "、".join([a["name"] for a in ii.get("actress", []) if "name" in a])
    label  = "、".join([l["name"] for l in ii.get("label",   []) if "name" in l])
    genres = "、".join([g["name"] for g in ii.get("genre",   []) if "name" in g])
    volume = item.get("volume", "")
    base   = f"{item.get('title','')}。ジャンル：{genres}。出演：{cast}。レーベル：{label}。収録時間：{volume}。"
    return base if len(base) > 10 else "FANZA（DMM）素人動画の自動投稿です。"

# ──────────────────────────────────────────
# WordPress 投稿
# ──────────────────────────────────────────

def create_wp_post(item):
    WP_URL   = get_env('WP_URL')
    WP_USER  = get_env('WP_USER')
    WP_PASS  = get_env('WP_PASS')
    CATEGORY = get_env('CATEGORY')
    AFF_ID   = get_env('DMM_AFFILIATE_ID')

    wp = Client(WP_URL, WP_USER, WP_PASS)

    # タイトル生成
    title = build_title(item)

    # 投稿済みチェック
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if any(p.title == title for p in existing):
        print(f"→ 既投稿: {title}（スキップ）")
        return False

    # サンプル画像
    siu = item.get("sampleImageURL", {})
    fanza_images = []
    if "sample_l" in siu and "image" in siu["sample_l"]:
        fanza_images = siu["sample_l"]["image"]
    elif "sample_s" in siu and "image" in siu["sample_s"]:
        fanza_images = siu["sample_s"]["image"]

    if not fanza_images:
        print(f"→ サンプル画像なし: {title}（スキップ）")
        return False

    # WordPressにアップロード
    print(f"  サンプル画像 {len(fanza_images)} 枚をアップロード中...")
    wp_images = []
    for fanza_url in fanza_images:
        mid, wp_url = upload_image(wp, fanza_url)
        if wp_url:
            wp_images.append((mid, wp_url))
        else:
            print(f"  ↳ スキップ: {fanza_url}")

    if not wp_images:
        print(f"→ 画像アップロード全失敗: {title}（スキップ）")
        return False

    thumb_id = wp_images[0][0]

    # タグ（レーベル・メーカー・女優・ジャンル）
    tags = set()
    ii = item.get("iteminfo", {})
    for group in ("label", "maker", "actress", "genre"):
        for entry in ii.get(group, []):
            if "name" in entry:
                tags.add(entry["name"])

    aff_link = make_affiliate_link(item["URL"], AFF_ID)
    desc = fetch_description(item)

    first_url = wp_images[0][1]
    parts = [
        f'<p><a href="{aff_link}" target="_blank"><img src="{first_url}" alt="{title}"></a></p>',
        f'<p><a href="{aff_link}" target="_blank">{title}</a></p>',
    ]
    if desc:
        parts.append(f'<div>{desc}</div>')
    for _, wp_url in wp_images[1:]:
        parts.append(f'<p><img src="{wp_url}" alt="{title}"></p>')
    parts += [
        f'<p><a href="{aff_link}" target="_blank"><img src="{first_url}" alt="{title}"></a></p>',
        f'<p><a href="{aff_link}" target="_blank">{title}</a></p>',
    ]

    post = WordPressPost()
    post.title = title
    post.content = "\n".join(parts)
    if thumb_id:
        post.thumbnail = thumb_id
    post.terms_names = {"category": [CATEGORY], "post_tag": list(tags)}
    post.post_status = "publish"
    wp.call(posts.NewPost(post))
    print(f"✔ 投稿完了: {title}")
    return True

# ──────────────────────────────────────────
# メイン
# ──────────────────────────────────────────

def main():
    print(f"[{now_jst().strftime('%Y-%m-%d %H:%M:%S')}] 投稿開始")
    try:
        items = fetch_amateur_videos()
        posted = False
        for item in items:
            if not is_released(item):
                print(f"→ 未発売: {item.get('title')}")
                continue
            if contains_jukujo(item):
                print(f"→ 熟女ジャンル: {item.get('title')}（スキップ）")
                continue
            if create_wp_post(item):
                posted = True
                break  # 1件投稿で終了
        if not posted:
            print("新規投稿なし")
    except Exception as e:
        print(f"エラー: {e}")
    print(f"[{now_jst().strftime('%Y-%m-%d %H:%M:%S')}] 投稿終了")

if __name__ == "__main__":
    main()
