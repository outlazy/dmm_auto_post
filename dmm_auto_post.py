#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import requests
import schedule
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client

# ───────────────────────────────────────────────────────────
# 環境変数読み込み（GitHub Actions の secrets から取得）
# ───────────────────────────────────────────────────────────
WP_URL       = os.getenv("WP_URL")
WP_USER      = os.getenv("WP_USER")
WP_PASS      = os.getenv("WP_PASS")
DMM_API_ID   = os.getenv("DMM_API_ID")
AFFILIATE_ID = os.getenv("AFFILIATE_ID")
USER_AGENT   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
# APIで取得する件数（同時に重複チェック用に複数件取得）
MAX_ITEMS    = 10

if not WP_URL or not WP_USER or not WP_PASS:
    raise RuntimeError("環境変数 WP_URL / WP_USER / WP_PASS が設定されていません")
if not DMM_API_ID or not AFFILIATE_ID:
    raise RuntimeError("環境変数 DMM_API_ID / AFFILIATE_ID が設定されていません")

# ───────────────────────────────────────────────────────────
# DMM Affiliate API を使って最新のアマチュア動画リストを取得
# ───────────────────────────────────────────────────────────
def fetch_latest_videos_from_api(max_items: int):
    endpoint = "https://api.dmm.com/affiliate/v3/ItemList"
    params = {
        "api_id": DMM_API_ID,
        "affiliate_id": AFFILIATE_ID,
        "site": "DMM.R18",
        "service": "videoa",
        "floor": "videoa_et",      # アマチュア系フロア
        "genre_id": "8503",        # ジャンル8503
        "sort": "-release_date",   # 新着順（降順）
        "hits": max_items,
        "output": "json"
    }
    resp = requests.get(endpoint, params=params, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    data = resp.json()
    items = data.get("result", {}).get("items", [])
    videos = []

    for it in items:
        title       = it.get("title", "").strip()
        detail_url  = it.get("affiliateURL", "").strip()
        description = it.get("content", "").strip() or "(説明文なし)"

        # サムネ画像URLリストを取得
        sample_images = []
        sample_dict = it.get("sampleImageURL", {})
        if isinstance(sample_dict, dict):
            for url in sample_dict.values():
                if url and url not in sample_images:
                    sample_images.append(url)

        # レーベル名取得
        label = ""
        if "label" in it and isinstance(it["label"], dict):
            label = it["label"].get("name", "").strip()

        # ジャンル名取得（iteminfo 内の genre の最初の要素）
        genre = ""
        iteminfo = it.get("iteminfo", {})
        genres = iteminfo.get("genre", []) if isinstance(iteminfo, dict) else []
        if isinstance(genres, list) and genres:
            first_genre = genres[0]
            if isinstance(first_genre, dict):
                genre = first_genre.get("name", "").strip()

        videos.append({
            "title": title,
            "detail_url": detail_url,
            "description": description,
            "sample_images": sample_images,
            "label": label,
            "genre": genre
        })

    return videos

# ───────────────────────────────────────────────────────────
# WordPress に投稿（重複チェック付き、タグにレーベル・ジャンルを1つずつ追加）
# ───────────────────────────────────────────────────────────
def post_to_wp(item: dict) -> bool:
    wp = Client(WP_URL, WP_USER, WP_PASS)

    # 重複チェック：同じタイトルの投稿があるか
    existing = wp.call(GetPosts({"post_status": "publish", "s": item["title"]}))
    if any(p.title == item["title"] for p in existing):
        print(f"→ Skipping duplicate: {item['title']}")
        return False

    first_img_url = item["sample_images"][0] if item.get("sample_images") else None

    # サムネイル画像をアップロード
    thumb_id = None
    if first_img_url:
        try:
            img_data = requests.get(first_img_url, headers={"User-Agent": USER_AGENT}).content
            media_data = {
                "name": os.path.basename(first_img_url.split("?")[0]),
                "type": "image/jpeg",
                "bits": xmlrpc_client.Binary(img_data)
            }
            resp_media = wp.call(media.UploadFile(media_data))
            thumb_id = resp_media.get("id")
        except Exception as e:
            print(f"Warning: アイキャッチアップロード失敗 ({first_img_url}): {e}")

    # 投稿本文を組み立て
    title         = item["title"]
    aff_link      = item["detail_url"]
    description   = item.get("description", "(説明文なし)")
    sample_images = item.get("sample_images", [])

    content_parts = []
    if sample_images:
        content_parts.append(
            f'<p><a href="{aff_link}" target="_blank">'
            f'<img src="{sample_images[0]}" alt="{title} サンプル1" />'
            f'</a></p>'
        )
    content_parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')
    content_parts.append(f'<p>{description}</p>')
    if len(sample_images) > 1:
        for idx, img_url in enumerate(sample_images[1:], start=2):
            content_parts.append(f'<p><img src="{img_url}" alt="{title} サンプル{idx}" /></p>')
    content_parts.append(f'<p><a href="{aff_link}" target="_blank">▶ 購入はこちら</a></p>')

    content = "\n".join(content_parts)

    # 投稿オブジェクト作成
    post = WordPressPost()
    post.title   = title
    post.content = content
    if thumb_id:
        post.thumbnail = thumb_id

    # カテゴリ／タグ：レーベルとジャンルを1つずつ追加
    tags = []
    if item.get("label"):
        tags.append(item["label"])
    if item.get("genre"):
        tags.append(item["genre"])
    post.terms_names = {
        "category": ["DMM動画"],
        "post_tag": tags
    }
    post.post_status = "publish"

    try:
        wp.call(posts.NewPost(post))
        print(f"✔ Posted: {title}")
        return True
    except Exception as e:
        print(f"✖ 投稿エラー ({title}): {e}")
        return False

# ───────────────────────────────────────────────────────────
# メイン処理：APIから最新10件を取得し、重複でない最初の作品を投稿
# ───────────────────────────────────────────────────────────
def job():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job start: fetching and posting")
    try:
        videos = fetch_latest_videos_from_api(MAX_ITEMS)
        if not videos:
            print("No videos found.")
            return

        for vid_info in videos:
            posted = post_to_wp(vid_info)
            if posted:
                break  # 投稿成功したらループを抜ける

    except Exception as e:
        print(f"Error in job(): {e}")
    finally:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished.")

# ───────────────────────────────────────────────────────────
# スケジューリング：4時間ごとに job() を実行
# ───────────────────────────────────────────────────────────
def main():
    job()  # 起動時に一度実行
    schedule.every(4).hours.do(job)
    print("Scheduler started. Running every 4 hours...")
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()
