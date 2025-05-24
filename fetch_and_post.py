#!/usr/bin/env python3
# dmm_video_auto_post.py

import os
import time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts

# 環境変数読み込み (.env または GitHub Secrets 経由)
load_dotenv()

# 設定
AFFILIATE_ID = os.getenv("DMM_AFFILIATE_ID")
LIST_URL     = "https://video.dmm.co.jp/av/list/?genre=1034"
HITS         = int(os.getenv("HITS", 5))

def fetch_description(detail_url: str) -> str:
    """動画詳細ページから紹介文をスクレイピング"""
    res = requests.get(detail_url)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "lxml")
    el = soup.select_one("#module-video-intro .text") or soup.select_one(".text")
    return el.get_text(strip=True) if el else ""

def fetch_videos_from_html(hits: int = HITS) -> list[dict]:
    """genre=1034 の一覧ページから先頭 hits 件を取得"""
    res = requests.get(LIST_URL)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "lxml")
    cards = soup.select(".list-inner .item")[:hits]
    items = []

    for idx, card in enumerate(cards, start=1):
        a = card.select_one("a")
        link = a["href"]
        title = a.get("title") or card.select_one(".ttl").get_text(strip=True)
        img = card.select_one("img")
        img_url = img.get("data-src") or img.get("src")
        desc = fetch_description(link)
        aff_url = f"{link}?i3_ref=list&i3_ord={idx}&affiliate_id={AFFILIATE_ID}"

        items.append({
            "title":       title,
            "url":         aff_url,
            "image_url":   img_url,
            "description": desc,
            "genres":      ["ジャンル1034"],
            "actors":      []
        })
        time.sleep(1)  # リクエスト間隔
    return items

def post_to_wp(item: dict):
    """WordPress に投稿"""
    wp_url  = os.getenv("WP_URL")
    wp_user = os.getenv("WP_USER")
    wp_pass = os.getenv("WP_PASS")

    client = Client(wp_url, wp_user, wp_pass)

    # 1) サムネイル画像をアップロード
    img_data = requests.get(item["image_url"]).content
    data = {
        "name": os.path.basename(item["image_url"]),
        "type": "image/jpeg",
    }
    media_item = media.UploadFile(data, img_data)
    res = client.call(media_item)

    # 2) 投稿内容を作成
    post = WordPressPost()
    post.title = item["title"]
    post.content = (
        f'<p><a href="{item["url"]}" target="_blank">'
        f'<img src="{res.url}" alt="{item["title"]}"></a></p>'
        f'<p>{item["description"]}</p>'
        f'<p><a href="{item["url"]}" target="_blank">▶ 詳細・購入はこちら</a></p>'
    )
    post.thumbnail = res.id
    post.terms_names = {
        "category": ["DMM動画", "AV"],
        "post_tag": item["genres"] + item["actors"]
    }
    post.post_status = "publish"

    client.call(posts.NewPost(post))
    print(f"Published: {item['title']}")

def main():
    videos = fetch_videos_from_html()
    for vid in videos:
        try:
            post_to_wp(vid)
        except Exception as e:
            print(f"Error posting {vid['title']}: {e}")

if __name__ == "__main__":
    main()
