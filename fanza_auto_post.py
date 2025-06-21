#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DMMアフィリエイトAPIで「発売済みFANZA素人動画」を自動取得→WordPress自動投稿
- 設定は全て環境変数（Secrets）で管理
- config.yml等は不要
- 投稿済みチェック・発売済み日付判定付き
"""

import os
import requests
import time
from datetime import datetime
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client

DMM_API_URL = "https://api.dmm.com/affiliate/v3/ItemList"

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
        "site": "video",
        "service": "amateur",
        "sort": "date",   # 新着順（発売日の新しい順）
        "output": "json",
        "hits": 10,       # まとめて10件取得
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
    return items

def is_released(item):
    # APIレスポンスに「date」(発売日)フィールドが含まれている前提
    # 例:  "date": "2024-06-19"
    date_str = item.get("date")
    if not date_str:
        return False
    today = datetime.now().date()
    try:
        release_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        return release_date <= today
    except Exception:
        return False

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
    if "sampleImageURL" in item:
        sample = item["sampleImageURL"]
        # サンプル画像はリストor単独URL
        if isinstance(sample, dict):
            # v3 APIだと "large" "small"のURL（1枚のみの場合もあり）
            if "large" in sample:
                if isinstance(sample["large"], list):
                    images = sample["large"]
                else:
                    images = [sample["large"]]
        elif isinstance(sample, str):
            images = [sample]

    if not images:
        print(f"→ サンプル画像なし: {title}（スキップ）")
        return False

    thumb_id = upload_image(wp, images[0]) if images else None

    # 女優・レーベル・ジャンル
    tags = set()
    if "maker" in item and item["maker"]:
        tags.add(item["maker"])
    if "actress" in item and item["actress"]:
        for a in item["actress"]:
            tags.add(a["name"])
    if "genre" in item and item["genre"]:
        for g in item["genre"]:
            tags.add(g["name"])

    aff_link = make_affiliate_link(item["URL"], AFF_ID)
    # 本文構成
    parts = []
    # 1枚目画像アフィリンク
    parts.append(f'<p><a href="{aff_link}" target="_blank"><img src="{images[0]}" alt="{title}"></a></p>')
    # 商品名アフィリンク
    parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')
    # 商品説明
    desc = item.get("description", "")
    if desc:
        parts.append(f'<div>{desc}</div>')
    # 残りサンプル画像
    for img in images[1:]:
        parts.append(f'<p><img src="{img}" alt="{title}"></p>')
    # 最後にアフィリンク画像＋商品名
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
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 投稿開始")
    try:
        items = fetch_amateur_videos()
        posted = False
        for item in items:
            if not is_released(item):
                continue
            if create_wp_post(item):
                posted = True
                break  # 1件投稿で終了
        if not posted:
            print("新規投稿なし")
    except Exception as e:
        print(f"エラー: {e}")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 投稿終了")

if __name__ == "__main__":
    main()
