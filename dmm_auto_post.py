#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import collections
import collections.abc
# WordPress XML-RPC の互換性パッチ
collections.Iterable = collections.abc.Iterable

import os
import time
import re
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# ───────────────────────────────────────────────────────────
# 一覧ページから動画URLとタイトル取得
# ───────────────────────────────────────────────────────────
def fetch_listed_videos(limit: int):
    session = get_session()
    resp = fetch_with_age_check(session, LIST_URL)
    soup = BeautifulSoup(resp.text, "html.parser")

    videos = []
    # detail ページへのリンクを全検索
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # 詳細ページへのリンク判定
        if "/amateur/" in href and "/detail/" in href:
            url = abs_url(href)
            title = a.get("title") or (a.img and a.img.get("alt")) or a.get_text(strip=True) or "No Title"
            # 重複防止
            if not any(v["detail_url"] == url for v in videos):
                videos.append({"title": title, "detail_url": url})
        if len(videos) >= limit:
            break

    # デバッグ: 取得件数
    print(f"DEBUG: fetch_listed_videos found {len(videos)} items from {LIST_URL}")
    return videos

# ───────────────────────────────────────────────────────────
# 詳細ページからタイトル・説明・画像取得
# ───────────────────────────────────────────────────────────
def scrape_detail(url: str):
    session = get_session()
    resp = fetch_with_age_check(session, url)
    soup = BeautifulSoup(resp.text, "html.parser")

    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else (soup.find("meta", property="og:title")["content"].strip() if soup.find("meta", property="og:title") else "No Title")

    d = soup.find(lambda tag: tag.name in ["div","p"] and (tag.get("class") == ["mg-b20","lh4"] or tag.get("id") == "sample-description"))
    desc = d.get_text(" ", strip=True) if d else ""

    imgs = []
    for sel in ("div#sample-image-box img", "img.sample-box__img", "li.sample-box__item img", "figure img"):  
        for img in soup.select(sel):
            src = img.get("data-original") or img.get("src")
            if src and src not in imgs:
                imgs.append(src)
        if imgs:
            break
    if not imgs and soup.find("meta", property="og:image"):
        imgs.append(soup.find("meta", property="og:image")["content"].strip())

    print(f"DEBUG: scrape_detail for {url} yielded {len(imgs)} images")
    return title, desc, imgs

# ───────────────────────────────────────────────────────────
# 画像アップロード
# ───────────────────────────────────────────────────────────
def upload_image(wp: Client, url: str) -> int:
    data = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10).content
    name = os.path.basename(url.split("?")[0])
    media_data = {"name": name, "type": "image/jpeg", "bits": xmlrpc_client.Binary(data)}
    res = wp.call(media.UploadFile(media_data))
    return res.get("id")

# ───────────────────────────────────────────────────────────
# Wordpress 投稿作成
# ───────────────────────────────────────────────────────────
def create_wp_post(title: str, desc: str, imgs: list[str], detail_url: str):
    wp = Client(WP_URL, WP_USER, WP_PASS)
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if any(p.title == title for p in existing):
        print(f"→ Skipping duplicate: {title}")
        return False

    thumb_id = None
    if imgs:
        try:
            thumb_id = upload_image(wp, imgs[0])
        except Exception as e:
            print(f"アイキャッチアップ失敗: {e}")

    aff = make_affiliate_link(detail_url)
    parts = []
    if imgs:
        parts.append(f'<p><a href="{aff}" target="_blank"><img src="{imgs[0]}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{aff}" target="_blank">{title}</a></p>')
    parts.append(f'<div>{desc}</div>')
    for img in imgs[1:]:
        parts.append(f'<p><img src="{img}" alt="{title}"></p>')
    if imgs:
        parts.append(f'<p><a href="{aff}" target="_blank"><img src="{imgs[0]}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{aff}" target="_blank">{title}</a></p>')

    post = WordPressPost()
    post.title       = title
    post.content     = "\n".join(parts)
    if thumb_id:
        post.thumbnail = thumb_id
    post.terms_names = {"category": ["DMM動画"], "post_tag": []}
    post.post_status = "publish"
    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {title}")
    return True

# ───────────────────────────────────────────────────────────
# メイン処理
# ───────────────────────────────────────────────────────────
def job():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job start")
    videos = fetch_listed_videos(MAX_ITEMS)
    if not videos:
        print("No videos found to post.")
    else:
        vid = videos[0]
        title, desc, imgs = scrape_detail(vid["detail_url"])
        create_wp_post(title, desc, imgs, vid["detail_url"])
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished")

if __name__ == "__main__":
    job()
