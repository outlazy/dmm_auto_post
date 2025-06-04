#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import requests
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client

# ───────────────────────────────────────────────────────────
# 環境変数読み込み (.env があれば読み込む)
# ───────────────────────────────────────────────────────────
load_dotenv()

WP_URL             = os.getenv("WP_URL")
WP_USER            = os.getenv("WP_USER")
WP_PASS            = os.getenv("WP_PASS")
DMM_API_ID         = os.getenv("DMM_API_ID")
DMM_AFFILIATE_ID   = os.getenv("DMM_AFFILIATE_ID")
USER_AGENT         = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
MAX_ITEMS          = 10  # 重複チェック用に最大10件を取得

# 必須環境変数チェック
missing = []
for var in ("WP_URL", "WP_USER", "WP_PASS", "DMM_API_ID", "DMM_AFFILIATE_ID"):
    if not os.getenv(var):
        missing.append(var)
if missing:
    raise RuntimeError(f"環境変数が設定されていません: {', '.join(missing)}")

# ───────────────────────────────────────────────────────────
# DMM Affiliate API から最新アマチュア動画リストを取得
#   site=DMM.R18, service=digital, floor=videoa, genre_id=8503
# ───────────────────────────────────────────────────────────
def fetch_latest_videos_from_api(max_items: int):
    endpoint = "https://api.dmm.com/affiliate/v3/ItemList"
    params = {
        "api_id":         DMM_API_ID,
        "affiliate_id":   DMM_AFFILIATE_ID,
        "site":           "DMM.R18",
        "service":        "digital",
        "floor":          "videoa",
        "genre_id":       "8503",
        "sort":           "-release_date",
        "hits":           max_items,
        "output":         "json"
    }

    try:
        resp = requests.get(endpoint, params=params, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print(f"HTTPError: {e} → URL: {resp.url}")
        try:
            print("Response JSON:", resp.json())
        except Exception:
            print("Response text:", resp.text[:200])
        raise

    data = resp.json()
    items = data.get("result", {}).get("items", [])
    videos = []

    for it in items:
        title       = it.get("title", "").strip()
        detail_url  = it.get("affiliateURL", "").strip()
        description = it.get("content", "").strip() or "(説明文なし)"

        # サムネ画像URLを収集（値がリストの場合、その中身を展開）
        sample_images = []
        sample_dict = it.get("sampleImageURL", {})
        if isinstance(sample_dict, dict):
            for v in sample_dict.values():
                if isinstance(v, list):
                    for url in v:
                        if url and url not in sample_images:
                            sample_images.append(url)
                elif isinstance(v, str):
                    if v not in sample_images:
                        sample_images.append(v)

        # レーベル名取得
        label = ""
        if "label" in it and isinstance(it["label"], dict):
            label = it["label"].get("name", "").strip()

        # ジャンル名リスト取得
        genres = []
        iteminfo = it.get("iteminfo", {})
        raw_genres = iteminfo.get("genre", []) if isinstance(iteminfo, dict) else []
        if isinstance(raw_genres, list):
            for g in raw_genres:
                if isinstance(g, dict):
                    name = g.get("name", "").strip()
                    if name:
                        genres.append(name)

        videos.append({
            "title":         title,
            "detail_url":    detail_url,
            "description":   description,
            "sample_images": sample_images,
            "label":         label,
            "genres":        genres
        })

    return videos

# ───────────────────────────────────────────────────────────
# WordPress への投稿（重複チェック、タグにレーベルとジャンルの語をすべて追加）
# ───────────────────────────────────────────────────────────
def post_to_wp(item: dict) -> bool:
    wp = Client(WP_URL, WP_USER, WP_PASS)

    # 重複チェック：同じタイトルの投稿がないか
    existing = wp.call(GetPosts({"post_status": "publish", "s": item["title"]}))
    if any(p.title == item["title"] for p in existing):
        print(f"→ Skipping duplicate: {item['title']}")
        return False

    # アイキャッチ用の1枚目画像をアップロード
    thumb_id = None
    if item.get("sample_images"):
        first_img = item["sample_images"][0]
        try:
            img_data = requests.get(first_img, headers={"User-Agent": USER_AGENT}).content
            media_data = {
                "name": os.path.basename(first_img.split("?")[0]),
                "type": "image/jpeg",
                "bits": xmlrpc_client.Binary(img_data)
            }
            resp_media = wp.call(media.UploadFile(media_data))
            thumb_id = resp_media.get("id")
        except Exception as e:
            print(f"Warning: アイキャッチアップロード失敗 ({first_img}): {e}")

    # 本文を組み立て
    title         = item["title"]
    aff_link      = item["detail_url"]
    description   = item["description"]
    sample_images = item["sample_images"]

    content_parts = []
    # 1) サムネ1枚目をアフィリンク付きで
    if sample_images:
        content_parts.append(
            f'<p><a href="{aff_link}" target="_blank">'
            f'<img src="{sample_images[0]}" alt="{title} サンプル1" /></a></p>'
        )
    # 2) タイトルをアフィリンク付きテキストで
    content_parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')
    # 3) 説明文
    content_parts.append(f'<p>{description}</p>')
    # 4) 2枚目以降のサムネ画像をすべて貼る
    if len(sample_images) > 1:
        for idx, img_url in enumerate(sample_images[1:], start=2):
            content_parts.append(f'<p><img src="{img_url}" alt="{title} サンプル{idx}" /></p>')
    # 5) 購入リンク
    content_parts.append(f'<p><a href="{aff_link}" target="_blank">▶ 購入はこちら</a></p>')

    content = "\n".join(content_parts)

    # 投稿オブジェクト作成
    post = WordPressPost()
    post.title   = title
    post.content = content
    if thumb_id:
        post.thumbnail = thumb_id

    # タグにレーベルと、ジャンル名をスペースで分割したすべての語を追加
    tags = []
    if item.get("label"):
        tags.append(item["label"])
    for genre in item.get("genres", []):
        for word in genre.split():
            if word and word not in tags:
                tags.append(word)

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
# メイン処理：API から最新10件を取得し、重複でない最初の作品を投稿
# ───────────────────────────────────────────────────────────
def job():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job start")
    try:
        videos = fetch_latest_videos_from_api(MAX_ITEMS)
        if not videos:
            print("No videos found.")
            return

        for vid in videos:
            if post_to_wp(vid):
                break  # 投稿成功したらループを抜ける

    except Exception as e:
        print(f"Error in job(): {e}")
    finally:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished")

# ───────────────────────────────────────────────────────────
# エントリポイント：一度だけ job() を呼び出して終了
# ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    job()
