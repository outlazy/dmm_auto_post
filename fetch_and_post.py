#!/usr/bin/env python3
# fetch_and_post.py

import os
import time
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.compat import xmlrpc_client

# ───────────────────────────────────────────────────────────
# 環境変数読み込み
# ───────────────────────────────────────────────────────────
load_dotenv()
AFFILIATE_ID = os.getenv("DMM_AFFILIATE_ID")
WP_URL       = os.getenv("WP_URL")
WP_USER      = os.getenv("WP_USER")
WP_PASS      = os.getenv("WP_PASS")
GENRE_ID     = 1034                  # genre=1034
HITS         = int(os.getenv("HITS", 5))
USER_AGENT   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
TODAY        = datetime.now().date()

if not AFFILIATE_ID:
    raise RuntimeError("環境変数 DMM_AFFILIATE_ID が設定されていません")

# ───────────────────────────────────────────────────────────
# 詳細ページから「紹介文」と「発売日」を取得
# ───────────────────────────────────────────────────────────
def fetch_detail(detail_url, session):
    headers = {"User-Agent": USER_AGENT}
    res = session.get(detail_url, headers=headers)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "lxml")

    # 説明文
    desc_el = soup.select_one("#module-video-intro .text") or soup.select_one(".text")
    description = desc_el.get_text(strip=True) if desc_el else "(説明文なし)"

    # 発売日（例: dl class="release" dd 要素）
    date_el = soup.find("dl", class_="release")
    release_date = None
    if date_el:
        dd = date_el.find("dd")
        try:
            release_date = datetime.strptime(dd.get_text(strip=True), "%Y-%m-%d").date()
        except Exception:
            pass

    return description, release_date

# ───────────────────────────────────────────────────────────
# 人気順リストをスクレイピング
# ───────────────────────────────────────────────────────────
def fetch_videos_by_html(genre_id, hits):
    url = f"https://video.dmm.co.jp/av/list/?genre={genre_id}&sort=ranking"
    print(f"=== Fetching genre {genre_id} by ranking ({hits}件) ===")
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    res = session.get(url)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "lxml")
    cards = soup.select(".list-inner .item")[:hits]
    videos = []

    for idx, card in enumerate(cards, start=1):
        a = card.find("a", href=True)
        if not a: continue
        link = a["href"]
        title = a.get("title") or a.get_text(strip=True)

        img = card.find("img")
        img_url = img.get("data-src") or img.get("src") or ""
        if not img_url:
            print(f"[Warning] No thumbnail for '{title}', skipping")
            continue

        # 詳細情報取得
        description, release_date = fetch_detail(link, session)
        if release_date and release_date > TODAY:
            print(f"[Skip] '{title}' は発売前 ({release_date})")
            continue

        # アフィリエイトリンク
        aff_url = f"{link}?i3_ref=list&i3_ord={idx}&affiliate_id={AFFILIATE_ID}"

        videos.append({
            "title":       title.strip(),
            "url":         aff_url,
            "image_url":   img_url,
            "description": description,
            "genres":      [str(genre_id)],
            "actors":      []
        })
        print(f"  ■ Fetched [{idx}]: {title}")
        time.sleep(1)

    print(f"=== Finished fetching {len(videos)} videos ===")
    return videos

# ───────────────────────────────────────────────────────────
# WordPressへ投稿
# ───────────────────────────────────────────────────────────
def post_to_wp(item):
    print(f"--> Posting: {item['title']}")
    wp = Client(WP_URL, WP_USER, WP_PASS)

    # サムネイルアップロード
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

    # 記事本文作成
    html = (
        f'<p><a href="{item["url"]}" target="_blank">'
        f'<img src="{attachment_url}" alt="{item["title"]}"/></a></p>'
        f'<p>{item["description"]}</p>'
        f'<p><a href="{item["url"]}" target="_blank">▶ 詳細・購入はこちら</a></p>'
    )

    post = WordPressPost()
    post.title       = item["title"]
    post.content     = html
    post.thumbnail   = attachment_id
    post.terms_names = {
        "category": ["DMM動画", "AV"],
        "post_tag": item["genres"] + item["actors"]
    }
    post.post_status = "publish"
    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {item['title']}")

# ───────────────────────────────────────────────────────────
# エントリポイント
# ───────────────────────────────────────────────────────────
def main():
    print("=== Job start ===")
    videos = fetch_videos_by_html(GENRE_ID, HITS)
    for vid in videos:
        try:
            post_to_wp(vid)
            time.sleep(1)
        except Exception as e:
            print(f"✖ Error posting '{vid['title']}': {e}")
    print("=== Job finished ===")

if __name__ == "__main__":
    main()
