#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FANZA（DMM）アフィリエイトAPIで素人動画（floor=videoc）を自動取得→WordPress投稿
・サンプル画像は全てWordPressにアップロードし、直リンクは使用しない
・タイトル: item["title"] → iteminfo.actress → ActressSearch API → 名前のみ
・ジャンルに「熟女」が含まれる場合は必ずスキップ
・全て環境変数（GitHub Secrets）で管理
"""

import os
import re
import requests
from datetime import datetime, date
import pytz
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client

DMM_API_URL      = "https://api.dmm.com/affiliate/v3/ItemList"
ACTRESS_SEARCH_URL = "https://api.dmm.com/affiliate/v3/ActressSearch"

# ──────────────────────────────────────────
# ユーティリティ
# ──────────────────────────────────────────

def now_jst():
    return datetime.now(pytz.timezone('Asia/Tokyo'))

def get_env(key, required=True, default=None):
    val = os.environ.get(key, default)
    if required and not val:
        raise RuntimeError(f"環境変数 {key} が設定されていません")
    return val

def _calc_age(birthday_str):
    try:
        bday = date.fromisoformat(birthday_str[:10])
        today = date.today()
        return today.year - bday.year - ((today.month, today.day) < (bday.month, bday.day))
    except Exception:
        return None

# ──────────────────────────────────────────
# DMM API
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
        raise
    items = resp.json().get("result", {}).get("items", [])
    print(f"API取得件数: {len(items)}")
    return items

def search_actress_profile(name, api_id, aff_id):
    """ActressSearch APIで名前を検索し {"size_str": ..., "age": ...} を返す。"""
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
# フィルタ
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
# タイトル生成
# ──────────────────────────────────────────

def build_title(item):
    """
    "名前 T158 B87(G) W58 H85" 形式のタイトルを返す。
    取得順:
      1. item["title"]         → 名前
      2. iteminfo.actress[0]   → サイズ
      3. ActressSearch API     → サイズ（名前で検索）
      ※ サイズが取れなければ名前のみ
    """
    API_ID = get_env("DMM_API_ID")
    AFF_ID = get_env("DMM_AFFILIATE_ID")
    ii = item.get("iteminfo", {})

    # Step 1: 名前
    name = item.get("title", "").strip()
    if not name:
        actresses = [a.get("name") for a in ii.get("actress", []) if a.get("name")]
        name = actresses[0] if actresses else "不明"
    print(f"  名前(APIタイトル): {name}")
    display_name = re.sub(r'\(\d+\)', '', name).strip()

    # Step 2: iteminfo.actress[0] のサイズ
    size_str = None
    if ii.get("actress"):
        a = ii["actress"][0]
        h = str(a.get("height", "") or "").strip()
        b = str(a.get("bust",   "") or "").strip()
        c = str(a.get("cup",    "") or "").strip()
        w = str(a.get("waist",  "") or "").strip()
        hip = str(a.get("hip",  "") or "").strip()
        if h and b and c and w and hip:
            size_str = f"T{h} B{b}({c}) W{w} H{hip}"
            print(f"  サイズ(iteminfo): {size_str}")

    # Step 3: ActressSearch API
    if not size_str:
        base_name = re.sub(r'\(\d+\)', '', name).strip()
        for candidate in list(dict.fromkeys([base_name, name])):
            profile = search_actress_profile(candidate, API_ID, AFF_ID)
            if profile and profile.get("size_str"):
                size_str = profile["size_str"]
                break

    result = f"{display_name} {size_str}" if size_str else display_name
    print(f"  生成タイトル: {result}")
    return result

# ──────────────────────────────────────────
# 画像・リンク
# ──────────────────────────────────────────

def make_affiliate_link(url, aff_id):
    from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs["affiliate_id"] = aff_id
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(qs), parsed.fragment))

def upload_image(wp, url):
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
            print(f"  警告: wp_urlが取得できませんでした。")
            return None, None
        if any(d in wp_url for d in ("dmm.co.jp", "dmm.com", "fanza")):
            print(f"  エラー: FANZAドメインのURLが返されました（スキップ）")
            return None, None
        print(f"  アップロード成功: {name} → {wp_url}")
        return media_id, wp_url
    except Exception as e:
        print(f"画像アップロード失敗: {url} ({e})")
        return None, None

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

    print(f"  サンプル画像 {len(fanza_images)} 枚をアップロード中...")
    wp_images = []
    for furl in fanza_images:
        mid, wp_url = upload_image(wp, furl)
        if wp_url:
            wp_images.append((mid, wp_url))
    if not wp_images:
        print(f"→ 画像アップロード全失敗: {title}（スキップ）")
        return False

    thumb_id = wp_images[0][0]

    # タグ
    tags = set()
    ii = item.get("iteminfo", {})
    for group in ("label", "maker", "actress", "genre"):
        for entry in ii.get(group, []):
            if "name" in entry:
                tags.add(entry["name"])

    aff_link = make_affiliate_link(item["URL"], AFF_ID)
    first_url = wp_images[0][1]

    parts = [
        f'<p><a href="{aff_link}" target="_blank"><img src="{first_url}" alt="{title}"></a></p>',
        f'<p><a href="{aff_link}" target="_blank">{title}</a></p>',
    ]
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
                break
        if not posted:
            print("新規投稿なし")
    except Exception as e:
        print(f"エラー: {e}")
    print(f"[{now_jst().strftime('%Y-%m-%d %H:%M:%S')}] 投稿終了")

if __name__ == "__main__":
    main()
