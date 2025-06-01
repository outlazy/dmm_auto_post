#!/usr/bin/env python3
# fetch_and_post.py

import os
import time
import requests
import textwrap
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client

# ───────────────────────────────────────────────────────────
# 環境変数読み込み
# ───────────────────────────────────────────────────────────
load_dotenv()
WP_URL    = os.getenv("WP_URL")
WP_USER   = os.getenv("WP_USER")
WP_PASS   = os.getenv("WP_PASS")
API_ID    = os.getenv("DMM_API_ID")
AFF_ID    = os.getenv("DMM_AFFILIATE_ID")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
MAX_ITEMS  = int(os.getenv("HITS", 5))  # 環境変数 HITS を使用して件数指定

if not WP_URL or not WP_USER or not WP_PASS:
    raise RuntimeError("環境変数 WP_URL / WP_USER / WP_PASS が設定されていません")
if not API_ID or not AFF_ID:
    raise RuntimeError("環境変数 DMM_API_ID / DMM_AFFILIATE_ID が設定されていません")

# ───────────────────────────────────────────────────────────
# API から最新動画を取得
# ───────────────────────────────────────────────────────────
def fetch_latest_videos(max_items: int):
    # ① genreSearch で「アマチュア」ジャンルIDを取得
    genre_url = "https://api.dmm.com/affiliate/v3/GenreSearch"
        genre_params = {
        "api_id": API_ID,
        "affiliate_id": AFF_ID,
        "site": "FANZA",
        "service": "digital",
        "floor": "videoa",
        "output": "json"
    }
    genre_resp = requests.get(genre_url, params=genre_params, headers={"User-Agent": USER_AGENT})
    genre_resp.raise_for_status()
    genre_data = genre_resp.json()
    items_genre = genre_data.get("result", {}).get("genres", [])
    # 名前に 'アマチュア' を含むジャンルを探す
    amateur_genre_id = None
    for g in items_genre:
        if "アマチュア" in g.get("name", ""):
            amateur_genre_id = g.get("id")
            break
    if not amateur_genre_id:
        raise RuntimeError("アマチュアジャンルが見つかりませんでした")

    # ② ItemList でアマチュア作品を取得
    url = "https://api.dmm.com/affiliate/v3/ItemList"
    params = {
        "api_id": API_ID,
        "affiliate_id": AFF_ID,
        "site": "FANZA",
        "service": "digital",
        "floor": "videoa",
        "mono_genre_id": amateur_genre_id,
        "sort": "date",
        "hits": max_items,
        "output": "json"
    }
    resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT})
    try:
        resp.raise_for_status()
    except Exception:
        print("API request failed:", resp.url)
        print("Response:", resp.text)
        raise
    data = resp.json()
    items = data.get("result", {}).get("items", [])
    videos = []
    for item in items:
        title = item.get("title", "").strip()
        detail_url = item.get("URL", "")
        img_urls = item.get("imageURL", {})
        thumb = img_urls.get("small") or img_urls.get("large") or ""
        description = item.get("description", "") or ""
        videos.append({
            "title": title,
            "detail_url": detail_url,
            "thumb": thumb,
            "description": description
        })
    return videos

# ───────────────────────────────────────────────────────────
# WordPressに投稿（重複チェック付き）
# ───────────────────────────────────────────────────────────
def post_to_wp(item: dict):
    wp = Client(WP_URL, WP_USER, WP_PASS)
    existing = wp.call(GetPosts({"post_status": "publish", "s": item["title"]}))
    if any(p.title == item["title"] for p in existing):
        print(f"→ Skipping duplicate: {item['title']}")
        return

    thumb_id = None
    if item.get("thumb"):
        try:
            img_data = requests.get(item["thumb"]).content
            media_data = {
                "name": os.path.basename(item["thumb"]),
                "type": "image/jpeg",
                "bits": xmlrpc_client.Binary(img_data)
            }
            resp_media = wp.call(media.UploadFile(media_data))
            thumb_id = resp_media.get("id")
        except Exception as e:
            print(f"Warning: thumbnail upload failed for {item['title']}: {e}")

    description = item.get("description", "") or ""
    if not description:
        description = "(説明文なし)"
    summary = textwrap.shorten(description, width=200, placeholder="…")

    content = f"<p>{summary}</p>\n"
    content += f"<p><a href=\"{item['detail_url']}\" target=\"_blank\">▶ 詳細・購入はこちら</a></p>"

    post = WordPressPost()
    post.title = item["title"]
    post.content = content
    if thumb_id:
        post.thumbnail = thumb_id
    post.terms_names = {"category": ["DMM動画"], "post_tag": []}
    post.post_status = "publish"
    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {item['title']}")

# ───────────────────────────────────────────────────────────
# メイン処理
# ───────────────────────────────────────────────────────────
def main():
    print(f"=== Job start: fetching top {MAX_ITEMS} videos from DMM API ===")
    videos = fetch_latest_videos(MAX_ITEMS)
    print(f"Fetched {len(videos)} videos.")
    for vid in videos:
        try:
            print(f"--> Posting: {vid['title']}")
            post_to_wp(vid)
            time.sleep(1)
        except Exception as e:
            print(f"✖ Error posting '{vid['title']}': {e}")
    print("=== Job finished ===")

if __name__ == "__main__":
    main()
