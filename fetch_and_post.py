#!/usr/bin/env python3
# fetch_and_post.py

import os
import time
import requests
from datetime import datetime
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.compat import xmlrpc_client

# ───────────────────────────────────────────────────────────
# 環境変数読み込み
# ───────────────────────────────────────────────────────────
load_dotenv()
API_ID       = os.getenv("DMM_API_ID")
AFFILIATE_ID = os.getenv("DMM_AFFILIATE_ID")
WP_URL       = os.getenv("WP_URL")
WP_USER      = os.getenv("WP_USER")
WP_PASS      = os.getenv("WP_PASS")
GENRE_IDS    = [1034, 8503]               # 取得したいジャンルIDリスト
HITS         = int(os.getenv("HITS", 5))  # 各ジャンルあたりの取得件数

if not API_ID or not AFFILIATE_ID:
    raise RuntimeError("環境変数 DMM_API_ID / DMM_AFFILIATE_ID が設定されていません")

# ───────────────────────────────────────────────────────────
# DMM(FANZA)アフィリエイトAPIから動画情報を取得
# ───────────────────────────────────────────────────────────
def fetch_videos_by_genre(genre_id: int, hits: int) -> list[dict]:
    url = "https://api.dmm.com/affiliate/v3/ItemList"
    params = {
        "api_id":        API_ID,
        "affiliate_id":  AFFILIATE_ID,
        "site":          "FANZA",
        "service":       "digital",
        "floor":         "videoa",
        "mono_genre_id": genre_id,
        "hits":          hits,
        "sort":          "date",     # 新着順
        "output":        "json"
    }
    print(f"=== Fetching genre {genre_id} ({hits}件) ===")
    resp = requests.get(url, params=params)
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print(f"[Error] genre {genre_id} API request failed: {e}")
        return []

    result = resp.json().get("result", {})
    items  = result.get("items", [])
    print(f"  -> API returned {len(items)} items")

    videos = []
    for i in items:
        # メイン画像
        img_info = i.get("imageURL", {}) or {}
        main_img = img_info.get("large") or img_info.get("small") or ""
        if not main_img:
            print(f"[Warning] No main image for '{i.get('title')}', skipping")
            continue

        # サンプル画像取得（リスト形式）
        sample_info = i.get("sampleImageURL", {}) or {}
        samples = []
        for val in sample_info.values():
            if isinstance(val, list):
                samples.extend(val)
            elif isinstance(val, str):
                samples.append(val)

        # 説明文
        desc = i.get("description", "").strip() or "(説明文なし)"

        videos.append({
            "title":       i.get("title", "").strip(),
            "url":         i.get("affiliateURL", ""),
            "image_url":   main_img,
            "description": desc,
            "genres":      [g.get("name") for g in i.get("genre", [])],
            "actors":      [a.get("name") for a in i.get("actor", [])],
            "samples":     samples
        })
    return videos

# ───────────────────────────────────────────────────────────
# WordPressに投稿
# ───────────────────────────────────────────────────────────
def post_to_wp(item: dict):
    print(f"--> Posting: {item['title']}")
    wp = Client(WP_URL, WP_USER, WP_PASS)

    # 1) サムネイル画像アップロード
    img_data = requests.get(item["image_url"]).content
    data = {
        "name": os.path.basename(item["image_url"]),
        "type": "image/jpeg",
        "bits": xmlrpc_client.Binary(img_data)
    }
    media_item = media.UploadFile(data)
    resp = wp.call(media_item)
    attachment_url = resp["url"]
    attachment_id  = resp["id"]

    # 2) 記事本文HTML組み立て
    html = []
    # メインサムネイル
    html.append(f'<p><a href="{item["url"]}" target="_blank"><img src="{attachment_url}" alt="{item["title"]}"/></a></p>')
    # 説明文
    html.append(f'<p>{item["description"]}</p>')
    # サンプル画像
    for sample_url in item.get("samples", []):
        html.append(f'<p><img src="{sample_url}" alt="サンプル画像"/></p>')
    # アフィリエイトリンク
    html.append(f'<p><a href="{item["url"]}" target="_blank">▶ 詳細・購入はこちら</a></p>')

    post = WordPressPost()
    post.title       = item["title"]
    post.content     = "\n".join(html)
    post.thumbnail   = attachment_id
    post.terms_names = {
        "category": ["DMM動画", "AV"],
        "post_tag": item["genres"] + item["actors"]
    }
    post.post_status = "publish"

    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {item['title']}")

# ───────────────────────────────────────────────────────────
# メイン処理
# ───────────────────────────────────────────────────────────
def main():
    print("=== Job start ===")
    all_videos = []
    for gid in GENRE_IDS:
        vids = fetch_videos_by_genre(gid, HITS)
        all_videos.extend(vids)
        time.sleep(1)

    print(f"=== Total to post: {len(all_videos)} videos ===")
    for vid in all_videos:
        try:
            post_to_wp(vid)
            time.sleep(1)
        except Exception as e:
            print(f"✖ Error posting '{vid['title']}': {e}")
    print("=== Job finished ===")

if __name__ == "__main__":
    main()
