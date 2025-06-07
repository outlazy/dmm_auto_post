#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.compat import xmlrpc_client
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# ───────────────────────────────────────────────────────────
# 設定と環境変数読み込み
# ───────────────────────────────────────────────────────────
load_dotenv()
WP_URL      = os.getenv("WP_URL")
WP_USER     = os.getenv("WP_USER")
WP_PASS     = os.getenv("WP_PASS")
AFF_ID      = os.getenv("DMM_AFFILIATE_ID")
USER_AGENT  = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
DETAIL_URL  = "https://www.dmm.co.jp/digital/videoc/-/detail/=/cid=docs087/?i3_ref=list&i3_ord=1&i3_pst=1"
# ───────────────────────────────────────────────────────────

def make_affiliate_link(url: str) -> str:
    # 既存のクエリに affiliate_id を追加
    p = urlparse(url)
    qs = dict(parse_qsl(p.query))
    qs["affiliate_id"] = AFF_ID
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(qs), p.fragment))

def get_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    # DMM 年齢認証バイパス用クッキー
    s.cookies.set("ckcy", "1", domain=".dmm.co.jp")
    s.cookies.set("ckcy", "1", domain="video.dmm.co.jp")
    return s

def scrape_detail(url: str):
    s = get_session()
    resp = s.get(url, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # — タイトル —
    title = None
    if soup.find("h1"):
        title = soup.find("h1").get_text(strip=True)
    else:
        # fallback: og:title
        og = soup.find("meta", property="og:title")
        title = og["content"] if og else "No Title"

    # — 説明文 —
    desc = ""
    d = (soup.find("div", class_="mg-b20 lh4")
         or soup.find("div", id="sample-description")
         or soup.find("p", id="sample-description"))
    if d:
        desc = d.get_text(" ", strip=True)

    # — サンプル画像一覧 —
    imgs = []
    for sel in ("div#sample-image-box img", "img.sample-box__img", "li.sample-box__item img"):
        for img in soup.select(sel):
            src = img.get("data-original") or img.get("src")
            if src and src not in imgs:
                imgs.append(src)
        if imgs:
            break

    return title, desc, imgs

def upload_image(wp: Client, img_url: str) -> int:
    # 画像をダウンロードして WordPress にアップ
    data = requests.get(img_url, headers={"User-Agent": USER_AGENT}, timeout=10).content
    name = os.path.basename(img_url.split("?")[0])
    media_data = {
        "name": name,
        "type": "image/jpeg",
        "bits": xmlrpc_client.Binary(data)
    }
    r = wp.call(media.UploadFile(media_data))
    return r.get("id")

def create_wp_post(title: str, desc: str, imgs: list[str], detail_url: str):
    wp = Client(WP_URL, WP_USER, WP_PASS)

    # 重複チェック
    exist = wp.call(posts.GetPosts({"post_status": "publish", "s": title}))
    if any(p.title == title for p in exist):
        print("既存投稿があります。スキップします。")
        return

    # アイキャッチ用アップ
    thumb_id = None
    if imgs:
        try:
            thumb_id = upload_image(wp, imgs[0])
        except Exception as e:
            print("アイキャッチ画像アップロード失敗:", e)

    # アフィリエイトリンク作成
    aff_link = make_affiliate_link(detail_url)

    # 本文組み立て
    parts = []
    # 1) 最初に画像＋タイトル
    parts.append(
        f'<p><a href="{aff_link}" target="_blank">'
        f'<img src="{imgs[0]}" alt="{title}" /></a></p>'
    )
    parts.append(
        f'<p><a href="{aff_link}" target="_blank">{title}</a></p>'
    )
    # 2) 説明文（コピー or 要約済みテキスト）
    parts.append(f'<div>{desc}</div>')
    # 3) サンプル画像 2枚目以降
    for img in imgs[1:]:
        parts.append(f'<p><img src="{img}" alt="{title}" /></p>')
    # 4) 最後にもう一度画像＋タイトル
    parts.append(
        f'<p><a href="{aff_link}" target="_blank">'
        f'<img src="{imgs[0]}" alt="{title}" /></a></p>'
    )
    parts.append(
        f'<p><a href="{aff_link}" target="_blank">{title}</a></p>'
    )

    content = "\n".join(parts)

    # 投稿準備
    post = WordPressPost()
    post.title        = title
    post.content      = content
    if thumb_id:
        post.thumbnail = thumb_id
    post.terms_names  = {"category": ["DMM動画"], "post_tag": []}
    post.post_status  = "publish"

    wp.call(posts.NewPost(post))
    print("投稿完了：", title)

if __name__ == "__main__":
    title, desc, imgs = scrape_detail(DETAIL_URL)
    create_wp_post(title, desc, imgs, DETAIL_URL)
