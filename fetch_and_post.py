#!/usr/bin/env python3
# fetch_and_post.py

import os
import time
import requests
import collections
from collections import abc as collections_abc
from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client
import textwrap
import re

# ───────────────────────────────────────────────────────────
# 環境変数読み込み
# ───────────────────────────────────────────────────────────
load_dotenv()
WP_URL    = os.getenv("WP_URL")
WP_USER   = os.getenv("WP_USER")
WP_PASS   = os.getenv("WP_PASS")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
LIST_URL   = "https://www.dmm.co.jp/amateur/list/?sort=date"
MAX_ITEMS  = int(os.getenv("HITS", 5))

if not WP_URL or not WP_USER or not WP_PASS:
    raise RuntimeError("環境変数 WP_URL / WP_USER / WP_PASS が設定されていません")

# ───────────────────────────────────────────────────────────
# HTTP GET + age_check bypass
# ───────────────────────────────────────────────────────────

def fetch_page(url: str, session: requests.Session) -> requests.Response:
    headers = {"User-Agent": USER_AGENT}
    res = session.get(url, headers=headers)
    # age check redirect contains "age_check" or English version
    if "age_check" in res.url or "en/age_check" in res.url:
        soup = BeautifulSoup(res.text, "lxml")
        # フォームによる同意
        form = soup.find("form")
        if form and form.get("action"):
            action = form["action"]
            data = { inp.get("name"): inp.get("value", "") for inp in form.find_all("input", {"name": True}) }
            session.post(action, data=data, headers=headers)
        else:
            # 英語「I Agree」リンク
            agree = soup.find("a", string=lambda t: t and ("I Agree" in t or "同意する" in t))
            if agree and agree.get("href"):
                session.get(agree["href"], headers=headers)
        res = session.get(url, headers=headers)
    res.raise_for_status()
    return res

# ───────────────────────────────────────────────────────────
# 詳細ページから説明文取得
# ───────────────────────────────────────────────────────────

def fetch_description(detail_url: str, session: requests.Session) -> str:
    res = fetch_page(detail_url, session)
    soup = BeautifulSoup(res.text, "lxml")
    desc_el = soup.select_one("div.mg-b20.lh4")
    return desc_el.get_text(strip=True) if desc_el else ""

# ───────────────────────────────────────────────────────────
# 一覧ページから動画情報を抽出
# ───────────────────────────────────────────────────────────

def fetch_videos_from_list(max_items: int):
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    resp = fetch_page(LIST_URL, session)
    soup = BeautifulSoup(resp.text, "lxml")

    videos = []
    seen = set()
    # 各動画は <li class="list-box"> 内の <a> でリンク
    for li in soup.select("li.list-box"):
        a = li.find("a", href=re.compile(r"/amateur/-/detail/"))
        if not a:
            continue
        href = a.get("href")
        detail_url = href if href.startswith("http") else f"https://www.dmm.co.jp{href}"
        if detail_url in seen:
            continue
        seen.add(detail_url)
        # サムネイル
        img = li.find("img")
        thumb = img.get("src", "") if img else ""
        # タイトル
        title = img.get("alt", "").strip() if img and img.get("alt") else a.get_text(strip=True)
        if not title:
            continue
        videos.append({
            "title": title,
            "detail_url": detail_url,
            "thumb": thumb
        })
        if len(videos) >= max_items:
            break
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

    # サムネイルをアップロード
    thumb_id = None
    if item["thumb"]:
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

    # 詳細ページから説明文を取得して要約
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    description = ""
    try:
        description = fetch_description(item["detail_url"], session)
    except Exception as e:
        print(f"Warning: description fetch failed for {item['title']}: {e}")
    if not description:
        description = "(説明文なし)"
    summary = textwrap.shorten(description, width=200, placeholder="…")

    # 本文組み立て
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
# メイン
# ───────────────────────────────────────────────────────────

def main():
    print(f"=== Job start: fetching top {MAX_ITEMS} videos from amateur list ===")
    videos = fetch_videos_from_list(MAX_ITEMS)
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
