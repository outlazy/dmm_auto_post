#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FANZA（DMM）アフィリエイトAPIで素人動画（floor=videoc）を自動取得→WordPress投稿
・日本時間（JST）で動作
・APIサンプル画像/タグもiteminfo配下から自動抽出
・ジャンルに「熟女」が含まれる場合は必ずスキップ
・本文には「商品個別の説明文だけ」記載、DMMの注意書きや短文は自動除外
・config.yml等の設定ファイル不要、全て環境変数（GitHub Secrets等）で管理
"""

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
    """
    商品ページから<meta name="description">またはJSON-LD内の"description"を取得し、
    NG文の場合はAPIデータにフォールバック。
    """
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        html = r.text
        # 1. meta description
        m = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE
        )
        if m and is_valid_description(m.group(1).strip()):
            return m.group(1).strip()
        # 2. JSON-LD
        m2 = re.search(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                       html, re.DOTALL)
        if m2:
            jd = json.loads(m2.group(1))
            desc = jd.get("description", "")
            if not desc and isinstance(jd.get("subjectOf"), dict):
                desc = jd["subjectOf"].get("description", "")
            if is_valid_description(desc):
                return desc.strip()
    except Exception as e:
        print(f"商品ページ説明抽出失敗: {e}")
    # APIフォールバック
    for key in ("description", "comment", "story"):
        val = item.get(key) or item.get("iteminfo", {}).get(key)
        if is_valid_description(val):
            return val
    # 最終フォールバック
    cast = "、".join(a["name"] for a in item.get("iteminfo", {}).get("actress", []) if "name" in a)
    label = "、".join(l["name"] for l in item.get("iteminfo", {}).get("label", []) if "name" in l)
    genres = "、".join(g["name"] for g in item.get("iteminfo", {}).get("genre", []) if "name" in g)
    volume = item.get("volume", "")
    base = f"{item['title']}。ジャンル：{genres}。出演：{cast}。レーベル：{label}。収録時間：{volume}。"
    return base

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

    thumb_id, thumb_url = upload_image(wp, images[0])
    inline_id, inline_url = upload_image(wp, images[0])

    desc = fetch_description_from_detail_page(item["URL"], item)
    if not desc:
        desc = "FANZA（DMM）素人動画の自動投稿です。"

    parts = []
    # 画像リンク＋タイトル
    parts.append(f'<p><a href="{make_affiliate_link(item["URL"], AFF_ID)}" target="_blank">'
                 f'<img src="{inline_url}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{make_affiliate_link(item["URL"], AFF_ID)}" target="_blank">{title}</a></p>')

    # 説明文＋JSON-LDスクリプト差し込み
    parts.append(f'<div>{desc}</div>')
    parts.append("""<script type="application/ld+json">
{"@context":"http://schema.org","@type":"Product","name":"ひまり","image":"https://pics.dmm.co.jp/digital/amateur/orecz196/orecz196jp.jpg","description":"ひまりちゃん（20）大学の後輩ちゃんとハメハメしてきました。世間では卒業したら高学歴というスタンプを良い意味で押されるくらいには頭の良い大学に、勉強して入っきたインテリの女の子なので、色恋系には疎いので、女子としてみてないよ感を出したらイケましたw私服姿しか見たことなかったですが、なかなかいい身体。なんかそのギャップにめちゃくちゃ興奮したので、思いっきり中出ししちゃいました。","sku":"orecz196","brand":{"@type":"Brand","name":"俺の素人-Z-"},"subjectOf":{"@type":"VideoObject","name":"ひまり","description":"ひまりちゃん（20）大学の後輩ちゃんとハメハメしてきました。世間では卒業したら高学歴というスタンプを良い意味で押されるくらいには頭の良い大学に、勉強して入っきたインテリの女の子なので、色恋系には疎いので、女子としてみてないよ感を出したらイケましたw私服姿しか見たことなかったですが、なかなかいい身体。なんかそのギャップにめちゃくちゃ興奮したので、思いっきり中出ししちゃいました。","contentUrl":"https://cc3001.dmm.co.jp/litevideo/freepv/o/ore/orecz196/orecz196sm.mp4","thumbnailUrl":"https://pics.dmm.co.jp/digital/amateur/orecz196/orecz196jp.jpg","uploadDate":"2025-07-23","actor":{"@type":"Person","name":"ひまり","alternateName":"ひまり"},"genre":["ハイビジョン","巨乳","企画","中出し","女子大生","ナンパ"]},"offers":{"@type":"Offer","availability":"https://schema.org/InStock","priceCurrency":"JPY","price":"400"}}
</script>""")

    # 続きの画像やリンク
    for img in images[1:]:
        parts.append(f'<p><img src="{img}" alt="{title}"></p>')
    parts.append(f'<p><a href="{make_affiliate_link(item["URL"], AFF_ID)}" target="_blank">'
                 f'<img src="{inline_url}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{make_affiliate_link(item["URL"], AFF_ID)}" target="_blank">{title}</a></p>')

    post = WordPressPost()
    post.title = title
    if thumb_id:
        post.thumbnail = thumb_id
    post.content = "\n".join(parts)
    post.terms_names = {"category": [CATEGORY]}
    wp.call(posts.NewPost(post))
    print(f"✔ 投稿完了: {title}")
    return True

def main():
    client = Client(get_env('WP_URL'), get_env('WP_USER'), get_env('WP_PASS'))
    published = {p.title for p in client.call(GetPosts({'number': 100, 'post_status': 'publish'}))}
    items = fetch_amateur_videos()
    for item in items:
        if item["title"] in published:
            continue
        if not is_released(item) or contains_jukujo(item):
            continue
        if create_wp_post(item):
            break

if __name__ == "__main__":
    main()
