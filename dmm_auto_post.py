#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client

# ───────────────────────────────────────────────────────────
# 環境変数読み込み
# ───────────────────────────────────────────────────────────
load_dotenv()
WP_URL     = os.getenv("WP_URL")
WP_USER    = os.getenv("WP_USER")
WP_PASS    = os.getenv("WP_PASS")
DMM_AFF_ID = os.getenv("DMM_AFFILIATE_ID", "")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
MAX_ITEMS  = 10

# 必須環境変数チェック
missing = []
for name, val in [("WP_URL", WP_URL), ("WP_USER", WP_USER), ("WP_PASS", WP_PASS), ("DMM_AFFILIATE_ID", DMM_AFF_ID)]:
    if not val:
        missing.append(name)
if missing:
    raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

TODAY = datetime.now().date()

# ───────────────────────────────────────────────────────────
# DMM セッション取得（年齢認証フォールバック対応）
# ───────────────────────────────────────────────────────────
def _get_dmm_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    # 旧エンドポイント
    try:
        s.post(
            "https://www.dmm.co.jp/my/-/service/=/security_age/",
            data={"adult": "ok"},
            timeout=10
        )
    except:
        pass
    # 新エンドポイント
    try:
        s.post(
            "https://www.dmm.co.jp/my/-/service/=/security_check/",
            data={"adult": "ok"},
            timeout=10
        )
    except:
        pass
    return s

# ───────────────────────────────────────────────────────────
# 個別ページから詳細情報を取得
# ───────────────────────────────────────────────────────────
def fetch_detail_info(url: str):
    session = _get_dmm_session()
    resp = session.get(url, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # 説明文: 複数セレクタを試す
    desc_div = (
        soup.find("div", class_="mg-b20 lh4")
        or soup.find("div", id="sample-description")
        or soup.find("p", id="sample-description")
    )
    description = desc_div.get_text(separator=" ", strip=True) if desc_div else ""
    if not description:
        print(f"Warning: 説明文が見つかりませんでした → {url}")

    # 発売日
    release_date = None
    for dt in soup.select("dt"):
        if "発売日" in dt.get_text(strip=True):
            dd = dt.find_next_sibling("dd")
            if dd:
                text = dd.get_text(strip=True)
                try:
                    release_date = datetime.strptime(text, "%Y-%m-%d").date()
                except:
                    pass
            break

    # サンプル画像: 複数セレクタを試す
    sample_images = []
    # 1) #sample-image-box 内の img
    for img in soup.select("div#sample-image-box img"):
        src = img.get("data-original") or img.get("src")
        if src and src not in sample_images:
            sample_images.append(src)
    # 2) class="sample-box__img"
    if not sample_images:
        for img in soup.select("img.sample-box__img"):
            src = img.get("data-original") or img.get("src")
            if src and src not in sample_images:
                sample_images.append(src)
    # 3) li.sample-box__item 内の img
    if not sample_images:
        for img in soup.select("li.sample-box__item img"):
            src = img.get("data-original") or img.get("src")
            if src and src not in sample_images:
                sample_images.append(src)
    if not sample_images:
        print(f"Warning: サンプル画像が見つかりませんでした → {url}")

    # レーベル
    label = ""
    for dt in soup.select("dt"):
        if "レーベル" in dt.get_text(strip=True):
            dd = dt.find_next_sibling("dd")
            if dd:
                a = dd.find("a")
                label = a.get_text(strip=True) if a else dd.get_text(strip=True)
            break

    # ジャンル一覧
    genres = []
    for dt in soup.select("dt"):
        if "ジャンル" in dt.get_text(strip=True):
            dd = dt.find_next_sibling("dd")
            if dd:
                for a in dd.find_all("a"):
                    nm = a.get_text(strip=True)
                    if nm:
                        genres.append(nm)
            break

    return description, sample_images, label, genres, release_date

# ───────────────────────────────────────────────────────────
# 一覧ページから動画のタイトルとURLを取得
# ───────────────────────────────────────────────────────────
def fetch_listed_videos(max_items: int):
    session = _get_dmm_session()
    LIST_URL = "https://video.dmm.co.jp/amateur/list/?genre=8503"
    resp = session.get(LIST_URL, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    videos = []
    for li in soup.select("li.list-box"):
        a = li.find("a", class_="tmb")
        if not a or not a.get("href"):
            continue
        title_tag = li.find("p", class_="title")
        title = title_tag.get_text(strip=True) if title_tag else "No Title"
        videos.append({
            "title": title,
            "detail_url": a["href"]
        })
        if len(videos) >= max_items:
            break
    return videos

# ───────────────────────────────────────────────────────────
# 最新動画リストをフィルタリング
# ───────────────────────────────────────────────────────────
def fetch_latest_videos(max_items: int):
    listed = fetch_listed_videos(max_items * 2)
    valid = []
    for vid in listed:
        desc, imgs, label, genres, rel = fetch_detail_info(vid["detail_url"])
        # 未来登録は除外
        if rel and rel > TODAY:
            continue
        # 説明文・画像が揃っていないものは除外
        if not desc or not imgs:
            continue
        valid.append({
            "title":         vid["title"],
            "detail_url":    vid["detail_url"],
            "description":   desc,
            "sample_images": imgs,
            "label":         label,
            "genres":        genres
        })
        if len(valid) >= max_items:
            break
    return valid

# ───────────────────────────────────────────────────────────
# WordPressへ投稿
# ───────────────────────────────────────────────────────────
def post_to_wp(item: dict) -> bool:
    wp = Client(WP_URL, WP_USER, WP_PASS)
    # 重複チェック
    existing = wp.call(GetPosts({"post_status": "publish", "s": item["title"]}))
    if any(p.title == item["title"] for p in existing):
        print(f"→ Skipping duplicate: {item['title']}")
        return False

    # アイキャッチ登録
    thumb_id = None
    first_img = item["sample_images"][0]
    try:
        img_data = requests.get(first_img, headers={"User-Agent": USER_AGENT}, timeout=10).content
        media_data = {
            "name": os.path.basename(first_img.split("?")[0]),
            "type": "image/jpeg",
            "bits": xmlrpc_client.Binary(img_data)
        }
        resp_media = wp.call(media.UploadFile(media_data))
        thumb_id = resp_media.get("id")
    except Exception as e:
        print(f"Warning: アイキャッチアップロード失敗 ({first_img}): {e}")

    # 本文組み立て
    aff_link = item["detail_url"] + f"?affiliate_id={DMM_AFF_ID}"
    parts = [
        f'<p><a href="{aff_link}" target="_blank"><img src="{item["sample_images"][0]}" alt="{item["title"]} サンプル1" /></a></p>',
        f'<p><a href="{aff_link}" target="_blank">{item["title"]}</a></p>',
        f'<p>{item["description"]}</p>'
    ]
    for img_url in item["sample_images"][1:]:
        parts.append(f'<p><img src="{img_url}" alt="{item["title"]} サンプル" /></p>')
    parts.append(f'<p><a href="{aff_link}" target="_blank">▶ 購入はこちら</a></p>')
    content = "\n".join(parts)

    # 投稿オブジェクト作成
    post = WordPressPost()
    post.title   = item["title"]
    post.content = content
    if thumb_id:
        post.thumbnail = thumb_id

    # タグ設定（レーベル＋ジャンル語を分割して追加）
    tags = []
    if item["label"]:
        tags.append(item["label"])
    for g in item["genres"]:
        for w in g.split():
            if w and w not in tags:
                tags.append(w)
    post.terms_names = {"category": ["DMM動画"], "post_tag": tags}
    post.post_status = "publish"

    # 投稿実行
    try:
        wp.call(posts.NewPost(post))
        print(f"✔ Posted: {item['title']}")
        return True
    except Exception as e:
        print(f"✖ 投稿エラー ({item['title']}): {e}")
        return False

# ───────────────────────────────────────────────────────────
# メインジョブ
# ───────────────────────────────────────────────────────────
def job():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job start")
    videos = fetch_latest_videos(MAX_ITEMS)
    if not videos:
        print("No videos found.")
    else:
        for vid in videos:
            if post_to_wp(vid):
                break
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished")

if __name__ == "__main__":
    job()
