#!/usr/bin/env python3
# fetch_and_post.py

import os
import time
import requests
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts

# ───────────────────────────────────────────────────────────
# 環境変数読み込み
# ───────────────────────────────────────────────────────────
load_dotenv()
API_ID       = os.getenv("DMM_API_ID")
AFFILIATE_ID = os.getenv("DMM_AFFILIATE_ID")
WP_URL       = os.getenv("WP_URL")
WP_USER      = os.getenv("WP_USER")
WP_PASS      = os.getenv("WP_PASS")
GENRE_IDS    = [1034, 8503]              # 取得したいジャンルをリストで指定
HITS         = int(os.getenv("HITS", 5)) # 1ジャンルあたりの取得件数

# ───────────────────────────────────────────────────────────
# DMM（FANZA）アフィリエイト API から取得
# ───────────────────────────────────────────────────────────
def fetch_videos_by_genre(genre_id, hits):
    url = "https://api.dmm.com/affiliate/v3/ItemList"
    params = {
        "api_id":        API_ID,
        "affiliate_id":  AFFILIATE_ID,
        "site":          "FANZA",    # AV動画は FANZA サイト
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
        print(f"[Error] API request failed for genre {genre_id}: {e}")
        return []
    data = resp.json().get("result", {})
    items = data.get("items", [])
    print(f"  -> Fetched {len(items)} items")
    videos = []
    for i in items:
        videos.append({
            "title":       i.get("title", "").strip(),
            "url":         i.get("affiliateURL", ""),
            "image_url":   i.get("largeImageURL", ""),
            "description": i.get("description", "").strip(),
            "genres":      [g.get("name") for g in i.get("genre", [])],
            "actors":      [a.get("name") for a in i.get("actor", [])]
        })
    return videos

# ───────────────────────────────────────────────────────────
# WordPress に投稿
# ───────────────────────────────────────────────────────────
def post_to_wp(item):
    print(f"--> Posting: {item['title']}")
    wp = Client(WP_URL, WP_USER, WP_PASS)

    # サムネイル画像アップロード
    img_data = requests.get(item["image_url"]).content
    data = {"name": os.path.basename(item["image_url"]), "type": "image/jpeg"}
    media_item = media.UploadFile(data, img_data)
    resp = wp.call(media_item)

    # 投稿作成
    post = WordPressPost()
    post.title = item["title"]
    post.content = (
        f'<p><a href="{item["url"]}" target="_blank">'
        f'<img src="{resp.url}" alt="{item["title"]}"/></a></p>'
        f'<p>{item["description"]}</p>'
        f'<p><a href="{item["url"]}" target="_blank">▶ 詳細・購入はこちら</a></p>'
    )
    post.thumbnail = resp.id
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
        time.sleep(1)  # API連続アクセス抑制

    print(f"=== Total videos to post: {len(all_videos)} ===")
    for vid in all_videos:
        try:
            post_to_wp(vid)
            time.sleep(1)
        except Exception as e:
            print(f"✖ Error posting '{vid['title']}': {e}")
    print("=== Job finished ===")

if __name__ == "__main__":
    main()
