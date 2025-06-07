#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import collections
import collections.abc
# wordpress_xmlrpc の互換性パッチ
collections.Iterable = collections.abc.Iterable

import os
import requests
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
WP_URL     = os.getenv("WP_URL")
WP_USER    = os.getenv("WP_USER")
WP_PASS    = os.getenv("WP_PASS")
AFF_ID     = os.getenv("DMM_AFFILIATE_ID")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
DETAIL_URL = "https://www.dmm.co.jp/digital/videoc/-/detail/=/cid=docs087/?i3_ref=list&i3_ord=1&i3_pst=1"

# 必須チェック
for name, v in [("WP_URL",WP_URL),("WP_USER",WP_USER),("WP_PASS",WP_PASS),("DMM_AFFILIATE_ID",AFF_ID)]:
    if not v:
        raise RuntimeError(f"Missing environment variable: {name}")

def make_affiliate_link(url: str) -> str:
    p   = urlparse(url)
    qs  = dict(parse_qsl(p.query))
    qs["affiliate_id"] = AFF_ID
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(qs), p.fragment))

def get_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    # age-check bypass
    s.cookies.set("ckcy", "1", domain=".dmm.co.jp")
    s.cookies.set("ckcy", "1", domain="video.dmm.co.jp")
    return s

def scrape_detail(url: str):
    s = get_session()
    resp = s.get(url, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # — タイトル取得（<h1> → og:title → デフォルト）
    title = None
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    else:
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            title = og["content"].strip()
    if not title:
        title = "No Title"
        print(f"Warning: タイトルが見つかりませんでした → {url}")

    # — 説明文 —
    desc = ""
    d = (
        soup.find("div", class_="mg-b20 lh4")
        or soup.find("div", id="sample-description")
        or soup.find("p", id="sample-description")
    )
    if d:
        desc = d.get_text(" ", strip=True)
    else:
        print(f"Warning: 説明文が見つかりませんでした → {url}")

    # — サンプル画像 —
    imgs = []
    for sel in ("div#sample-image-box img", "img.sample-box__img", "li.sample-box__item img"):
        for img in soup.select(sel):
            src = img.get("data-original") or img.get("src")
            if src and src not in imgs:
                imgs.append(src)
        if imgs:
            break
    if not imgs:
        print(f"Warning: サンプル画像が見つかりませんでした → {url}")

    return title, desc, imgs

def upload_image(wp: Client, url: str) -> int:
    data = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10).content
    name = os.path.basename(url.split("?")[0])
    media_data = {
        "name": name,
        "type": "image/jpeg",
        "bits": xmlrpc_client.Binary(data)
    }
    resp = wp.call(media.UploadFile(media_data))
    return resp.get("id")

def create_wp_post(title: str, desc: str, imgs: list[str], detail_url: str):
    wp = Client(WP_URL, WP_USER, WP_PASS)

    # 重複チェック
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if any(p.title == title for p in existing):
        print("既存投稿をスキップ")
        return

    # アイキャッチ登録
    thumb_id = None
    if imgs:
        try:
            thumb_id = upload_image(wp, imgs[0])
        except Exception as e:
            print("アイキャッチアップ失敗:", e)

    aff = make_affiliate_link(detail_url)

    # 本文組み立て
    parts = []
    # ① 画像＋タイトル（aff link）
    if imgs:
        parts.append(f'<p><a href="{aff}" target="_blank"><img src="{imgs[0]}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{aff}" target="_blank">{title}</a></p>')
    # ② 説明文
    parts.append(f'<div>{desc}</div>')
    # ③ 残りサンプル画像
    for img in imgs[1:]:
        parts.append(f'<p><img src="{img}" alt="{title}"></p>')
    # ④ もう一度画像＋タイトル
    if imgs:
        parts.append(f'<p><a href="{aff}" target="_blank"><img src="{imgs[0]}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{aff}" target="_blank">{title}</a></p>')

    post = WordPressPost()
    post.title       = title
    post.content     = "\n".join(parts)
    post.terms_names = {"category": ["DMM動画"], "post_tag": []}
    post.post_status = "publish"
    if thumb_id:
        post.thumbnail = thumb_id

    wp.call(posts.NewPost(post))
    print("投稿完了:", title)

if __name__ == "__main__":
    title, desc, imgs = scrape_detail(DETAIL_URL)
    create_wp_post(title, desc, imgs, DETAIL_URL)
