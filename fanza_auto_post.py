#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FANZA（DMM）アフィリエイトAPIで素人動画（floor=videoc）を自動取得→WordPress投稿
・年齢確認＆リダイレクト後の個別ページで<script type="application/ld+json">のdescriptionだけ投稿
・共通説明文を絶対除外
・ジャンルに「熟女」含む場合はスキップ
・全て環境変数で管理（configファイル不要）
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
from html import unescape

DMM_API_URL = "https://api.dmm.com/affiliate/v3/ItemList"

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
        "floor": "videoc",    # 素人動画
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
        return res.get("id")
    except Exception as e:
        print(f"画像アップロード失敗: {url} ({e})")
        return None

def fetch_description_from_detail_page(url, item):
    """
    年齢確認＆リダイレクト後の個別商品ページで<script type="application/ld+json">内の
    商品descriptionだけを抽出し、共通説明文なら必ず除外する
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8"
        }
        cookies = {
            "age_check_done": "1"
        }
        # 最終リダイレクト先までたどる
        r = requests.get(url, timeout=10, headers=headers, cookies=cookies, allow_redirects=True)
        html = r.text

        # 共通説明NGワード集
        NG_COMMONS = [
            "総合アダルトサイト", "人気AV女優の独占作品", "創業20年の実績", "FANZA(ファンザ)", "会員様にご利用"
        ]

        # 1. 全てのJSON-LD <script type="application/ld+json"> を抜き出す
        ld_jsons = re.findall(
            r'<script[^>]+type=[\'"]application/ld\+json[\'"][^>]*>(.*?)</script>',
            html, re.DOTALL | re.IGNORECASE
        )
        from html import unescape
        for ld in ld_jsons:
            ld = ld.strip()
            ld = re.sub(r'<!--.*?-->', '', ld, flags=re.DOTALL)
            try:
                data = json.loads(ld)
                # 配列形式にも対応
                if isinstance(data, list):
                    for d in data:
                        if isinstance(d, dict) and "description" in d and d["description"]:
                            desc = unescape(d["description"].strip())
                            if desc and not any(ng in desc for ng in NG_COMMONS):
                                return desc
                        if isinstance(d, dict) and "subjectOf" in d and isinstance(d["subjectOf"], dict):
                            sdesc = unescape(d["subjectOf"].get("description", "").strip())
                            if sdesc and not any(ng in sdesc for ng in NG_COMMONS):
                                return sdesc
                # 辞書形式
                elif isinstance(data, dict):
                    if "description" in data and data["description"]:
                        desc = unescape(data["description"].strip())
                        if desc and not any(ng in desc for ng in NG_COMMONS):
                            return desc
                    if "subjectOf" in data and isinstance(data["subjectOf"], dict):
                        sdesc = unescape(data["subjectOf"].get("description", "").strip())
                        if sdesc and not any(ng in sdesc for ng in NG_COMMONS):
                            return sdesc
            except Exception as e:
                continue

        # 2. JSON-LDで出なければmeta descriptionもNGワードで除外しながら拾う
        meta_descs = re.findall(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE
        )
        for desc in meta_descs:
            decoded = unescape(desc.strip())
            if not any(ng in decoded for ng in NG_COMMONS):
                return decoded

        # なければ空文字
    except Exception as e:
        print(f"商品ページ説明抽出失敗: {e}")
    return ""

def create_wp_post(item):
    WP_URL = get_env('WP_URL')
    WP_USER = get_env('WP_USER')
    WP_PASS = get_env('WP_PASS')
    CATEGORY = get_env('CATEGORY')
    AFF_ID = get_env('DMM_AFFILIATE_ID')

    wp = Client(WP_URL, WP_USER, WP_PASS)
    title = item["title"]

    # 投稿済みチェック
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if any(p.title == title for p in existing):
        print(f"→ 既投稿: {title}（スキップ）")
        return False

    # サンプル画像
    images = []
    siu = item.get("sampleImageURL", {})
    if "sample_l" in siu and "image" in siu["sample_l"]:
        images = siu["sample_l"]["image"]
    elif "sample_s" in siu and "image" in siu["sample_s"]:
        images = siu["sample_s"]["image"]

    if not images:
        print(f"→ サンプル画像なし: {title}（スキップ）")
        return False

    thumb_id = upload_image(wp, images[0]) if images else None

    # タグ（レーベル・メーカー・女優・ジャンル）はiteminfo配下から抽出
    tags = set()
    ii = item.get("iteminfo", {})
    if "label" in ii and ii["label"]:
        for l in ii["label"]:
            if "name" in l:
                tags.add(l["name"])
    if "maker" in ii and ii["maker"]:
        for m in ii["maker"]:
            if "name" in m:
                tags.add(m["name"])
    if "actress" in ii and ii["actress"]:
        for a in ii["actress"]:
            if "name" in a:
                tags.add(a["name"])
    if "genre" in ii and ii["genre"]:
        for g in ii["genre"]:
            if "name" in g:
                tags.add(g["name"])

    aff_link = make_affiliate_link(item["URL"], AFF_ID)

    # 本文：個別descriptionだけ
    desc = fetch_description_from_detail_page(item["URL"], item)
    if not desc:
        desc = ""  # 本当に何もなければ空

    parts = []
    parts.append(f'<p><a href="{aff_link}" target="_blank"><img src="{images[0]}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')
    if desc:
        parts.append(f'<blockquote>{desc}</blockquote>')
    for img in images[1:]:
        parts.append(f'<p><img src="{img}" alt="{title}"></p>')
    parts.append(f'<p><a href="{aff_link}" target="_blank"><img src="{images[0]}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')

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
