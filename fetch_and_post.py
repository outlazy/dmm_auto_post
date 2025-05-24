#!/usr/bin/env python3
# fetch_and_post.py

import os
import time
import requests
import collections
from collections import abc as collections_abc
# Monkey-patch for python-wordpress-xmlrpc compatibility
collections.Iterable = collections_abc.Iterable

from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client

# ───────────────────────────────────────────────────────────
# 環境変数読み込み
# ───────────────────────────────────────────────────────────
load_dotenv()
API_ID       = os.getenv("DMM_API_ID")
AFFILIATE_ID = os.getenv("DMM_AFFILIATE_ID")
WP_URL       = os.getenv("WP_URL")
WP_USER      = os.getenv("WP_USER")
WP_PASS      = os.getenv("WP_PASS")
GENRE_IDS    = [1034, 8503]
HITS         = int(os.getenv("HITS", 5))
USER_AGENT   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
TODAY        = datetime.now().date()

if not API_ID or not AFFILIATE_ID:
    raise RuntimeError("環境変数 DMM_API_ID / DMM_AFFILIATE_ID が設定されていません")

# ───────────────────────────────────────────────────────────
# HTTP GET with age_check bypass
# ───────────────────────────────────────────────────────────
def fetch_page(url: str, session: requests.Session) -> requests.Response:
    headers = {"User-Agent": USER_AGENT}
    res = session.get(url, headers=headers)
    if "age_check" in res.url:
        soup = BeautifulSoup(res.text, "lxml")
        form = soup.find("form")
        if form and form.get("action"):
            action = form["action"]
            data = {inp.get("name"): inp.get("value", "") for inp in form.find_all("input") if inp.get("name")}
            session.post(action, data=data, headers=headers)
        else:
            agree = soup.find("a", string=lambda t: t and ("I Agree" in t or "同意する" in t))
            if agree and agree.get("href"):
                session.get(agree.get("href"), headers=headers)
        res = session.get(url, headers=headers)
    res.raise_for_status()
    return res

# ───────────────────────────────────────────────────────────
# Detail page scrape: description and sample images
# ───────────────────────────────────────────────────────────
def fetch_detail(detail_url: str, session: requests.Session):
    res = fetch_page(detail_url, session)
    soup = BeautifulSoup(res.text, "lxml")
    desc_el = soup.select_one("div.mg-b20.lh4")
    description = desc_el.get_text(strip=True) if desc_el else ""
    samples = []
    for a in soup.select("#sample-image-block a[id^=sample-image]"):
        img = a.find("img")
        if img and img.get("src"):
            samples.append(img.get("src"))
    return description, samples

# ───────────────────────────────────────────────────────────
# Fetch items via API then enrich with HTML scrape
# ───────────────────────────────────────────────────────────
def fetch_videos_by_genres(genre_ids, hits):
    api_url = "https://api.dmm.com/affiliate/v3/ItemList"
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    all_items = []
    for genre_id in genre_ids:
        params = {
            "api_id":        API_ID,
            "affiliate_id":  AFFILIATE_ID,
            "site":          "FANZA",
            "service":       "digital",
            "floor":         "videoa",
            "mono_genre_id": genre_id,
            "hits":          hits,
            "sort":          "rank",
            "output":        "json"
        }
        print(f"=== Fetching genre {genre_id} by ranking ({hits}件) ===")
        resp = requests.get(api_url, params=params)
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            print(f"[Error] genre {genre_id} API request failed: {e}")
            continue
        items = resp.json().get("result", {}).get("items", [])
        print(f"  -> API returned {len(items)} items")
        for i in items:
            title = i.get("title", "").strip()
            aff_url = i.get("affiliateURL", "")
            detail_url = aff_url.split('?')[0]
            img_info = i.get("imageURL", {}) or {}
            main_img = img_info.get("large") or img_info.get("small") or ""
            description_html, samples_html = fetch_detail(detail_url, session)
            api_desc = i.get("description", "").strip()
            description = description_html or api_desc or "(説明文なし)"
            samples = samples_html
            if not samples:
                sample_api = i.get("sampleImageURL", {}) or {}
                for val in sample_api.values():
                    if isinstance(val, list): samples.extend(val)
                    elif isinstance(val, str): samples.append(val)
            all_items.append({
                "title":       title,
                "url":         aff_url,
                "image_url":   main_img,
                "description": description,
                "samples":     samples,
                "genres":      [g.get("name") for g in i.get("genre", [])],
                "actors":      [a.get("name") for a in i.get("actor", [])]
            })
            print(f"  ■ Fetched: {title}, samples:{len(samples)}")
            time.sleep(1)
    print(f"=== Total fetched {len(all_items)} videos ===")
    return all_items

# ───────────────────────────────────────────────────────────
# Post to WordPress with duplicate check
# ───────────────────────────────────────────────────────────
def post_to_wp(item: dict):
    print(f"--> Posting: {item['title']}")
    wp = Client(WP_URL, WP_USER, WP_PASS)
    existing = wp.call(GetPosts({'post_status': 'publish', 's': item['title']}))
    for p in existing:
        if p.title == item['title']:
            print(f"→ Skipping duplicate: {item['title']}")
            return
    img_data = requests.get(item["image_url"]).content
    media_data = {"name": os.path.basename(item["image_url"]), "type": "image/jpeg", "bits": xmlrpc_client.Binary(img_data)}
    resp_media = wp.call(media.UploadFile(media_data))
    attach_url = resp_media["url"]
    attach_id  = resp_media["id"]
    html = [
        f'<p><a href="{item['url']}" target="_blank"><img src="{attach_url}" alt="{item['title']}"/></a></p>',
        f'<p>{item['description']}</p>'
    ]
    for s in item.get("samples", []):
        html.append(f'<p><img src="{s}" alt="サンプル画像"/></p>')
    html.append(f'<p><a href="{item['url']}" target="_blank">▶ 詳細・購入はこちら</a></p>')
    post = WordPressPost()
    post.title       = item['title']
    post.content     = "\n".join(html)
    post.thumbnail   = attach_id
    post.terms_names = {"category": ["DMM動画","AV"], "post_tag": item['genres'] + item['actors']}
    post.post_status = "publish"
    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {item['title']}\n")

# ───────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────
def main():
    print("=== Job start ===")
    videos = fetch_videos_by_genres(GENRE_IDS, HITS)
    for vid in videos:
        try:
            post_to_wp(vid)
            time.sleep(1)
        except Exception as e:
            print(f"✖ Error posting '{vid['title']}': {e}")
    print("=== Job finished ===")

if __name__ == "__main__":
    main()
