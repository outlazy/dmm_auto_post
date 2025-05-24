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
GENRE_ID     = 1034
HITS         = int(os.getenv("HITS", 5))

# ───────────────────────────────────────────────────────────
# DMM アフィリエイト API から取得
# ───────────────────────────────────────────────────────────
def fetch_videos_by_genre(genre_id: int, hits: int) -> list[dict]:
    url = "https://api.dmm.com/affiliate/v3/ItemList"
    params = {
        "api_id":        API_ID,
        "affiliate_id":  AFFILIATE_ID,
        "site":          "FANZA",      # アダルトは FANZA
        "service":       "digital",
        "floor":         "videoa",     # AV 動画
        "mono_genre_id": genre_id,
        "hits":          hits,
        "sort":          "date",       # 新着順
        "output":        "json"
    }
    print(f"=== Fetching genre {genre_id} videos ({hits}件) ===")
    res = requests.get(url, params=params)
    res.raise_for_status()
    data = res.json().get("result", {})
    items = data.get("items", [])
    print(f"Fetched {len(items)} videos")
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
# WordPress 投稿
# ───────────────────────────────────────────────────────────
def post_to_wp(item: dict):
    print(f"--> Posting: {item['title']}")
    client = Client(WP_URL, WP_USER, WP_PASS)

    # サムネイル画像アップロード
    img_data = requests.get(item["image_url"]).content
    data = {
        "name": os.path.basename(item["image_url"]),
        "type": "image/jpeg"
    }
    media_item = media.UploadFile(data, img_data)
    resp = client.call(media_item)

    # 投稿作成
    post = WordPressPost()
    post.title = item["title"]
    post.content = (
        f'<p><a href="{item["url"]}" target="_blank">'
        f'<img src="{resp.url}" alt="{item["title"]}"></a></p>'
        f'<p>{item["description"]}</p>'
        f'<p><a href="{item["url"]}" target="_blank">▶ 詳細・購入はこちら</a></p>'
    )
    post.thumbnail = resp.id
    post.terms_names = {
        "category": ["DMM動画", "AV"],
        "post_tag": item["genres"] + item["actors"]
    }
    post.post_status = "publish"
    client.call(posts.NewPost(post))
    print(f"✔ Posted: {item['title']}")

# ───────────────────────────────────────────────────────────
# エントリポイント
# ───────────────────────────────────────────────────────────
def main():
    print("=== Job start ===")
    videos = fetch_videos_by_genre(GENRE_ID, HITS)
    for vid in videos:
        try:
            post_to_wp(vid)
            time.sleep(1)  # API & サーバー負荷軽減
        except Exception as e:
            print(f"✖ Error posting '{vid['title']}': {e}")
    print("=== Job finished ===")

if __name__ == "__main__":
    main()
