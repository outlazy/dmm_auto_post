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
from bs4 import BeautifulSoup

# ───────────────────────────────────────────────────────────
# 環境変数読み込み
# ───────────────────────────────────────────────────────────
load_dotenv()
WP_URL    = os.getenv("WP_URL")
WP_USER   = os.getenv("WP_USER")
WP_PASS   = os.getenv("WP_PASS")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
MAX_ITEMS  = int(os.getenv("HITS", 5))  # 環境変数 HITS を使用して件数指定

if not WP_URL or not WP_USER or not WP_PASS:
    raise RuntimeError("環境変数 WP_URL / WP_USER / WP_PASS が設定されていません")

# ───────────────────────────────────────────────────────────
# HTML スクレイピングで最新アマチュア動画を取得
# ───────────────────────────────────────────────────────────

def fetch_latest_videos(max_items: int):
    # セッションを使って年齢認証をバイパス
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        session.post(
            "https://www.dmm.co.jp/my/-/service/=/security_age/", data={"adult": "ok"}
        )
    except:
        pass

    LIST_URL = "https://video.dmm.co.jp/amateur/list/?genre=8503&limit=120"
    resp = session.get(LIST_URL)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    videos = []
    seen = set()
    # <a> タグから '/detail/' を含むリンクを抽出
    for a in soup.find_all("a", href=True):
        detail_url = a["href"]
        if "/detail/" not in detail_url:
            continue
        if detail_url in seen:
            continue
        img = a.find("img")
        if not img:
            continue
        thumb = img.get("data-original") or img.get("src", "")
        # サムネイルがアマチュア動画用か判定
        if "/amateur/" not in thumb:
            continue
        title = img.get("alt", "").strip() or img.get("title", "").strip()
        description = _fetch_description(detail_url, session)

        videos.append({"title": title, "detail_url": detail_url, "thumb": thumb, "description": description})
        seen.add(detail_url)
        if len(videos) >= max_items:
            break
    return videos

    # ② li.item 内の <img> を探す
    for li in soup.select("li.item"):
        a = li.find("a", href=True)
        img = li.find("img")
        if not a or not img:
            continue
        detail_url = a["href"]
        if detail_url in seen:
            continue
        title = img.get("alt", "").strip() or img.get("title", "").strip()
        thumb = img.get("src", "")
        description = _fetch_description(detail_url, headers)
        videos.append({"title": title, "detail_url": detail_url, "thumb": thumb, "description": description})
        seen.add(detail_url)
        if len(videos) >= max_items:
            return videos

    # ③ p.tmb > a 内の <img> を探す
    for a in soup.select("p.tmb > a[href*='/detail/']"):  
        img = a.find("img")
        detail_url = a.get("href")
        if not img or not detail_url or detail_url in seen:
            continue
        title = img.get("alt", "").strip() or img.get("title", "").strip()
        thumb = img.get("src", "")
        description = _fetch_description(detail_url, headers)
        videos.append({"title": title, "detail_url": detail_url, "thumb": thumb, "description": description})
        seen.add(detail_url)
        if len(videos) >= max_items:
            return videos

    return videos

# 説明文取得共通ルーチン

def _fetch_description(url: str, headers: dict) -> str:
    try:
        d_resp = requests.get(url, headers=headers)
        d_resp.raise_for_status()
        d_soup = BeautifulSoup(d_resp.text, "html.parser")
        desc_div = d_soup.find("div", class_="mg-b20 lh4")
        if desc_div:
            return desc_div.get_text(separator=" ", strip=True)
    except:
        pass
    return ""

# ───────────────────────────────────────────────────────────
# WordPress に投稿（重複チェック付き）
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

    description = item.get("description", "") or "(説明文なし)"
    summary = textwrap.shorten(description, width=200, placeholder="…")

    content = f"<p>{summary}</p>\n"
    if thumb_id:
        content += f"<p><img src=\"{item['thumb']}\" alt=\"{item['title']}\"></p>\n"
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
    print(f"=== Job start: fetching top {MAX_ITEMS} videos via HTML scrape ===")
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
