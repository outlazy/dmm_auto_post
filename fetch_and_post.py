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
# アマチュアビデオ一覧（最新1件）のみ取得
LIST_URL   = "https://video.dmm.co.jp/amateur/list/?sort=date"
MAX_ITEMS  = 1  # 最新1件を取得

if not WP_URL or not WP_USER or not WP_PASS:
    raise RuntimeError("環境変数 WP_URL / WP_USER / WP_PASS が設定されていません")

# ───────────────────────────────────────────────────────────
# HTTP GET + age_check bypass
# ───────────────────────────────────────────────────────────

def fetch_page(url: str, session: requests.Session) -> requests.Response:
    headers = {"User-Agent": USER_AGENT}
    res = session.get(url, headers=headers)
    if "age_check" in res.url or "en/age_check" in res.url:
        soup = BeautifulSoup(res.text, "lxml")
        form = soup.find("form")
        if form and form.get("action"):
            action = form["action"]
            data = { inp.get("name"): inp.get("value", "") for inp in form.find_all("input", {"name": True}) }
            session.post(action, data=data, headers=headers)
        else:
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
# 一覧ページから動画情報を抽出（最新1件）
# ───────────────────────────────────────────────────────────

def fetch_latest_video():
    # スクレイピングで最新1件を取得
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    resp = fetch_page(LIST_URL, session)
    soup = BeautifulSoup(resp.text, "lxml")
    # 詳細リンクを含む最初の <a> タグを取得
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/amateur/-/detail/" not in href:
            continue
        detail_url = href if href.startswith("http") else f"https://video.dmm.co.jp{href}"
        img = a.find("img")
        # imgがない場合はリンク先にサムネがないと判断し次へ
        if not img:
            continue
        thumb = img.get("src", "")
        title = img.get("alt", "").strip() if img.get("alt") else a.get_text(strip=True)
        # 説明文をスクレイピング
        description = ""
        try:
            description = fetch_description(detail_url, session)
        except:
            description = ""
        return {"title": title, "detail_url": detail_url, "thumb": thumb, "description": description}
    return None"title": title, "detail_url": detail_url, "thumb": thumb, "description": description}

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
    description = item.get("description", "") or ""
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
    print("=== Job start: fetching latest video from amateur list ===")
    video = fetch_latest_video()
    if not video:
        print("No videos found.")
        return
    print(f"--> Found: {video['title']}")
    post_to_wp(video)
    print("=== Job finished ===")

if __name__ == "__main__":
    main()
