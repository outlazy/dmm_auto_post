#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import requests
import re
import json
from datetime import datetime
import pytz

from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client

DMM_API_URL = "https://api.dmm.com/affiliate/v3/ItemList"

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

def now_jst():
    return datetime.now(pytz.timezone('Asia/Tokyo'))

def get_env(key, required=True, default=None):
    val = os.environ.get(key, default)
    if required and not val:
        raise RuntimeError(f"環境変数 {key} が設定されていません")
    return val

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
    resp.raise_for_status()
    items = resp.json().get("result", {}).get("items", [])
    print(f"API取得件数: {len(items)}")
    return items

def is_released(item):
    date_str = item.get("date")
    if not date_str:
        return False
    jst = pytz.timezone('Asia/Tokyo')
    release_date = jst.localize(datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S"))
    return release_date <= now_jst()

def contains_jukujo(item):
    genres = [g.get("name", "") for g in item.get("iteminfo", {}).get("genre", [])]
    return "熟女" in genres

def make_affiliate_link(url, aff_id):
    from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs["affiliate_id"] = aff_id
    new_query = urlencode(qs)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

def upload_image(wp, url):
    try:
        data = requests.get(url, timeout=10).content
        name = os.path.basename(url.split("?")[0])
        media_data = {"name": name, "type": "image/jpeg", "bits": xmlrpc_client.Binary(data)}
        res = wp.call(media.UploadFile(media_data))
        return res.get("id"), res.get("url")
    except Exception as e:
        print(f"画像アップロード失敗: {url} ({e})")
        return None, url

def is_valid_description(desc):
    if not desc or len(desc) < 30:
        return False
    for ng in NG_DESCRIPTIONS:
        if ng in desc:
            return False
    return True

def fetch_description_from_detail_page(url, item):
    """meta description or JSON-LD内descriptionを取得、NGならAPIフォールバック"""
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        html = r.text
        # 1) meta
        m = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE
        )
        if m and is_valid_description(m.group(1).strip()):
            return m.group(1).strip()
        # 2) JSON-LD
        m2 = re.search(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                       html, re.DOTALL)
        if m2:
            jd = json.loads(m2.group(1))
            desc = jd.get("description", "") or jd.get("subjectOf", {}).get("description", "")
            if is_valid_description(desc):
                return desc.strip()
    except Exception as e:
        print(f"商品ページ説明抽出失敗: {e}")
    # APIフォールバック
    for key in ("description", "comment", "story"):
        val = item.get(key) or item.get("iteminfo", {}).get(key)
        if is_valid_description(val):
            return val
    # 最終フォールバック（ジャンル等）
    cast = "、".join(a["name"] for a in item.get("iteminfo", {}).get("actress", []) if "name" in a)
    label = "、".join(l["name"] for l in item.get("iteminfo", {}).get("label", []) if "name" in l)
    genres = "、".join(g["name"] for g in item.get("iteminfo", {}).get("genre", []) if "name" in g)
    volume = item.get("volume", "")
    return f"{item['title']}。ジャンル：{genres}。出演：{cast}。レーベル：{label}。収録時間：{volume}。"

def fetch_jsonld_script(url):
    """ページ内の最初のJSON-LD<script>を丸ごと返す"""
    try:
        r = requests.get(url, timeout=10)
        html = r.text
        m = re.search(r'(<script[^>]*type="application/ld\+json"[^>]*>.*?</script>)',
                      html, re.DOTALL)
        return m.group(1) if m else ""
    except:
        return ""

def create_wp_post(item):
    WP_URL = get_env('WP_URL')
    WP_USER = get_env('WP_USER')
    WP_PASS = get_env('WP_PASS')
    CATEGORY = get_env('CATEGORY')
    AFF_ID = get_env('DMM_AFFILIATE_ID')

    wp = Client(WP_URL, WP_USER, WP_PASS)
    title = item["title"]
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if any(p.title == title for p in existing):
        print(f"→ 既投稿: {title}（スキップ）")
        return False

    siu = item.get("sampleImageURL", {})
    images = siu.get("sample_l", {}).get("image") or siu.get("sample_s", {}).get("image") or []
    if not images:
        print(f"→ サンプル画像なし: {title}（スキップ）")
        return False
    thumb_id, _   = upload_image(wp, images[0])
    _, inline_url = upload_image(wp, images[0])

    desc = fetch_description_from_detail_page(item["URL"], item)
    if not desc:
        desc = "FANZA（DMM）素人動画の自動投稿です。"

    # JSON-LDブロック取得
    jsonld = fetch_jsonld_script(item["URL"])

    parts = []
    parts.append(f'<p><a href="{make_affiliate_link(item["URL"], AFF_ID)}" target="_blank">'
                 f'<img src="{inline_url}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{make_affiliate_link(item["URL"], AFF_ID)}" target="_blank">{title}</a></p>')
    parts.append(f'<div>{desc}</div>')
    if jsonld:
        parts.append(jsonld)
    for img in images[1:]:
        parts.append(f'<p><img src="{img}" alt="{title}"></p>')
    parts.append(f'<p><a href="{make_affiliate_link(item["URL"], AFF_ID)}" target="_blank">'
                 f'<img src="{inline_url}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{make_affiliate_link(item["URL"], AFF_ID)}" target="_blank">{title}</a></p>')

    post = WordPressPost()
    post.title      = title
    if thumb_id:
        post.thumbnail = thumb_id
    post.content    = "\n".join(parts)
    post.terms_names = {"category": [CATEGORY]}
    wp.call(posts.NewPost(post))
    print(f"✔ 投稿完了: {title}")
    return True

def main():
    published = {p.title for p in Client(
        get_env('WP_URL'),
        get_env('WP_USER'),
        get_env('WP_PASS')
    ).call(GetPosts({'number': 100, 'post_status': 'publish'}))}
    works = fetch_amateur_videos()
    for item in works:
        if item["title"] in published:
            continue
        if not is_released(item) or contains_jukujo(item):
            continue
        if create_wp_post(item):
            break

if __name__ == "__main__":
    main()
