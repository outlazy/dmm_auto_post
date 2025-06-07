#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import collections
import collections.abc
# wordpress_xmlrpc の互換性パッチ
collections.Iterable = collections.abc.Iterable

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
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# ───────────────────────────────────────────────────────────
# 環境変数読み込み
# ───────────────────────────────────────────────────────────
load_dotenv()
WP_URL      = os.getenv("WP_URL")
WP_USER     = os.getenv("WP_USER")
WP_PASS     = os.getenv("WP_PASS")
AFF_ID      = os.getenv("DMM_AFFILIATE_ID")
USER_AGENT  = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
LIST_URL    = "https://video.dmm.co.jp/amateur/list/?genre=8503"
KEYWORDS    = ["素人", "ギャル"]  # タイトルに含まれるキーワード
MAX_ITEMS   = 10

# 必須チェック
for name, v in [("WP_URL",WP_URL),("WP_USER",WP_USER),("WP_PASS",WP_PASS),("DMM_AFFILIATE_ID",AFF_ID)]:
    if not v:
        raise RuntimeError(f"Missing environment variable: {name}")

# ───────────────────────────────────────────────────────────
# affiliate_link 作成
# ───────────────────────────────────────────────────────────
def make_affiliate_link(url: str) -> str:
    p   = urlparse(url)
    qs  = dict(parse_qsl(p.query))
    qs["affiliate_id"] = AFF_ID
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(qs), p.fragment))

# ───────────────────────────────────────────────────────────
# セッション取得 & age-check bypass
# ───────────────────────────────────────────────────────────
def get_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    s.cookies.set("ckcy", "1", domain=".dmm.co.jp")
    s.cookies.set("ckcy", "1", domain="video.dmm.co.jp")
    return s

# ───────────────────────────────────────────────────────────
# 絶対 URL 変換
# ───────────────────────────────────────────────────────────
def abs_url(href: str) -> str:
    if href.startswith("//"):
        return f"https:{href}"
    if href.startswith("/"):
        return f"https://video.dmm.co.jp{href}"
    return href

# ───────────────────────────────────────────────────────────
# 一覧ページから動画情報取得
# ───────────────────────────────────────────────────────────
def fetch_listed_videos(limit: int):
    session = get_session()
    resp = session.get(LIST_URL, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    videos = []
    for a in soup.select("a.tmb"):
        href = a.get("href")
        if not href:
            continue
        url = abs_url(href)
        title = a.get("title") or (a.img and a.img.get("alt")) or a.get_text(strip=True)
        videos.append({"title": title, "detail_url": url})
        if len(videos) >= limit:
            break
    return videos

# ───────────────────────────────────────────────────────────
# キーワードでフィルタ
# ───────────────────────────────────────────────────────────
def filter_videos(videos, keywords):
    return [v for v in videos if all(k in v["title"] for k in keywords)]

# ───────────────────────────────────────────────────────────
# 詳細ページからデータ取得
# ───────────────────────────────────────────────────────────
def scrape_detail(url: str):
    s = get_session()
    resp = s.get(url, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # タイトル
    title = None
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    else:
        og = soup.find("meta", property="og:title")
        title = og["content"].strip() if og and og.get("content") else None
    title = title or "No Title"

    # 説明文
    desc = ""
    d = (soup.find("div", class_="mg-b20 lh4")
         or soup.find("div", id="sample-description")
         or soup.find("p", id="sample-description"))
    if d:
        desc = d.get_text(" ", strip=True)
    else:
        meta_desc = soup.find("meta", property="og:description")
        if meta_desc and meta_desc.get("content"):
            desc = meta_desc["content"].strip()

    # サンプル画像
    imgs = []
    for sel in ("div#sample-image-box img", "img.sample-box__img", "li.sample-box__item img"):
        for img in soup.select(sel):
            src = img.get("data-original") or img.get("src")
            if src and src not in imgs:
                imgs.append(src)
        if imgs:
            break
    # fallback: og:image
    if not imgs:
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            imgs.append(og_img["content"].strip())

    return title, desc, imgs

# ───────────────────────────────────────────────────────────
# 画像アップ & 投稿
# ───────────────────────────────────────────────────────────
def upload_image(wp: Client, url: str) -> int:
    data = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10).content
    name = os.path.basename(url.split("?")[0])
    media_data = {"name": name, "type": "image/jpeg", "bits": xmlrpc_client.Binary(data)}
    resp = wp.call(media.UploadFile(media_data))
    return resp.get("id")

def create_wp_post(item, detail_url: str):
    wp = Client(WP_URL, WP_USER, WP_PASS)

    # 重複チェック
    existing = wp.call(GetPosts({"post_status": "publish", "s": item["title"]}))
    if any(p.title == item["title"] for p in existing):
        print(f"→ Skipping duplicate: {item['title']}")
        return False

    # アイキャッチ
    thumb_id = None
    if item["imgs"]:
        try:
            thumb_id = upload_image(wp, item["imgs"][0])
        except Exception as e:
            print("アイキャッチアップ失敗:", e)

    aff = make_affiliate_link(detail_url)

    # 本文組み立て
    parts = []
    if item["imgs"]:
        parts.append(f'<p><a href="{aff}" target="_blank"><img src="{item["imgs"][0]}" alt="{item["title"]}"></a></p>')
    parts.append(f'<p><a href="{aff}" target="_blank">{item["title"]}</a></p>')
    parts.append(f'<div>{item["desc"]}</div>')
    for img in item["imgs"][1:]:
        parts.append(f'<p><img src="{img}" alt="{item["title"]}"></p>')
    if item["imgs"]:
        parts.append(f'<p><a href="{aff}" target="_blank"><img src="{item["imgs"][0]}" alt="{item["title"]}"></a></p>')
    parts.append(f'<p><a href="{aff}" target="_blank">{item["title"]}</a></p>')

    post = WordPressPost()
    post.title       = item["title"]
    post.content     = "\n".join(parts)
    if thumb_id:
        post.thumbnail = thumb_id
    post.terms_names = {"category": ["DMM動画"], "post_tag": []}
    post.post_status = "publish"

    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {item['title']}")
    return True

# ───────────────────────────────────────────────────────────
# ジョブ
# ───────────────────────────────────────────────────────────
def job():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job start")
    listed = fetch_listed_videos(MAX_ITEMS * 2)
    filtered = filter_videos(listed, KEYWORDS)
    if not filtered:
        print(f"No videos matching keywords: {KEYWORDS}")
    else:
        for vid in filtered:
            title, desc, imgs = scrape_detail(vid["detail_url"])
            if title == "No Title" or not desc or not imgs:
                continue
            if create_wp_post({"title": title, "desc": desc, "imgs": imgs}, vid["detail_url"]):
                break
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished")

if __name__ == "__main__":
    job()
