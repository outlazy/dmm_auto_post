#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import collections
import collections.abc
# WordPress XML-RPC の互換性パッチ
collections.Iterable = collections.abc.Iterable

import os
import re
import time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# ───────────────────────────────────────────────────────────
# 環境変数読み込み & 定数定義
# ───────────────────────────────────────────────────────────
load_dotenv()
WP_URL     = os.getenv("WP_URL")
WP_USER    = os.getenv("WP_USER")
WP_PASS    = os.getenv("WP_PASS")
AFF_ID     = os.getenv("DMM_AFFILIATE_ID")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
LIST_URL   = "https://video.dmm.co.jp/amateur/list/?sort=date"
MAX_ITEMS  = 10  # 一覧取得数

# 必須環境変数チェック
for name, v in [("WP_URL",WP_URL),("WP_USER",WP_USER),("WP_PASS",WP_PASS),("DMM_AFFILIATE_ID",AFF_ID)]:
    if not v:
        raise RuntimeError(f"Missing environment variable: {name}")

# ───────────────────────────────────────────────────────────
# affiliate_id 付与
# ───────────────────────────────────────────────────────────
def make_affiliate_link(url: str) -> str:
    p  = urlparse(url)
    qs = dict(parse_qsl(p.query))
    qs["affiliate_id"] = AFF_ID
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(qs), p.fragment))

# ───────────────────────────────────────────────────────────
# セッション取得 & Age-check bypass
# ───────────────────────────────────────────────────────────
def get_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    # 成人確認バイパス用 Cookie
    s.cookies.set("ckcy", "1", domain=".dmm.co.jp")
    s.cookies.set("ckcy", "1", domain="video.dmm.co.jp")
    return s

# ───────────────────────────────────────────────────────────
# Age check フォーム自動送信
# ───────────────────────────────────────────────────────────
def fetch_with_age_check(session: requests.Session, url: str) -> requests.Response:
    resp = session.get(url, timeout=10)
    if "age_check" in resp.url or "security_check" in resp.url or "I Agree" in resp.text:
        soup = BeautifulSoup(resp.text, "html.parser")
        agree = soup.find("a", string=re.compile(r"I Agree", re.I))
        if agree and agree.get("href"):
            session.get(agree["href"], timeout=10)
        else:
            form = soup.find("form")
            if form and form.get("action"):
                action = form["action"]
                data = {inp.get("name"): inp.get("value", "ok") or "ok" for inp in form.find_all("input", attrs={"name": True})}
                session.post(action, data=data, timeout=10)
        resp = session.get(url, timeout=10)
    resp.raise_for_status()
    return resp

# ───────────────────────────────────────────────────────────
# URL 正規化
# ───────────────────────────────────────────────────────────
def abs_url(href: str) -> str:
    if href.startswith("//"):
        return f"https:{href}"
    if href.startswith("/"):
        return f"https://video.dmm.co.jp{href}"
    return href

# ───────────────────────────────────────────────────────────
# 一覧ページから動画URL取得
# ───────────────────────────────────────────────────────────
# ───────────────────────────────────────────────────────────
# DMM アフィリエイト API で新着動画を取得
# ───────────────────────────────────────────────────────────
def fetch_listed_videos(limit: int):
    """
    DMMアフィリエイトAPIで新着順に Amateur 動画を取得し、API失敗時はHTMLスクレイピングにフォールバックします。
    """
    # API経由で取得
    api_id = os.getenv("DMM_API_ID")
    if api_id:
                params = {
            "api_id": api_id,
            "affiliate_id": AFF_ID,
            "site": "FANZA",
            "service": "digital",
            "floor": "videoa",
            "hits": limit,
            "sort": "date",
            "output": "json"
        }
        url = "https://api.dmm.com/affiliate/v3/ItemList"
        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("result", {}).get("items", [])
            videos = [{"title": itm.get("title"), "detail_url": itm.get("URL")} for itm in items]
            print(f"DEBUG: fetch_listed_videos found {len(videos)} items via DMM API")
            return videos
        except Exception as e:
            print(f"DEBUG: DMM API fetch failed: {e}, falling back to HTML scraping")

    # HTMLスクレイピングにフォールバック
    session = get_session()
    resp = fetch_with_age_check(session, LIST_URL)
    soup = BeautifulSoup(resp.text, "html.parser")
    videos = []
    for li in soup.select("li.list-box")[:limit]:
        a = li.find("a", class_="tmb")
        if not a or not a.get("href"):
            continue
        url = abs_url(a["href"])
        title = a.img.get("alt", "").strip() if a.img and a.img.get("alt") else a.get("title") or li.find("p", class_="title").get_text(strip=True)
        videos.append({"title": title, "detail_url": url})
    print(f"DEBUG: fetch_listed_videos found {len(videos)} items via <li.list-box> scraping from {LIST_URL}")
    return videos

# ───────────────────────────────────────────────────────────
# 詳細ページからタイトル・説明・画像取得
# ───────────────────────────────────────────────────────────
def scrape_detail(url: str):
    session = get_session()
    resp = fetch_with_age_check(session, url)
    soup = BeautifulSoup(resp.text, "html.parser")

    # タイトル取得
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else (
        soup.find("meta", property="og:title")["content"].strip() 
        if soup.find("meta", property="og:title") else "No Title"
    )

    # 説明文取得
    d = soup.find(lambda tag: tag.name in ["div","p"] and (
        (tag.get("class") == ["mg-b20","lh4"]) or (tag.get("id") == "sample-description")
    ))
    desc = d.get_text(" ", strip=True) if d else ""

    # サンプル画像取得
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
def create_wp_post(title: str, desc: str, imgs: list[str], detail_url: str) -> bool:
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
