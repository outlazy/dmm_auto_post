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
import textwrap

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
            agree = soup.find("a", string=lambda t: t and ("同意する" in t))
            if agree and agree.get("href"):
                session.get(agree.get("href"), headers=headers)
        res = session.get(url, headers=headers)
    res.raise_for_status()
    return res

# ───────────────────────────────────────────────────────────
# Detail page scrape: description only
# ───────────────────────────────────────────────────────────
def fetch_description(detail_url: str, session: requests.Session) -> str:
    res = fetch_page(detail_url, session)
    soup = BeautifulSoup(res.text, "lxml")
    desc_el = soup.select_one("div.mg-b20.lh4")
    return desc_el.get_text(strip=True) if desc_el else ""

# ───────────────────────────────────────────────────────────
# Fetch items via API then enrich with HTML scrape
# ───────────────────────────────────────────────────────────
def fetch_videos_by_genres(genre_ids, hits):
    api_url = "https://api.dmm.com/affiliate/v3/ItemList"
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    items_out = []
    for genre_id in genre_ids:
        params = {
            "api_id":       API_ID,
            "affiliate_id": AFFILIATE_ID,
            "site":         "FANZA",
            "service":      "digital",
            "floor":        "videoa",
            "mono_genre_id":genre_id,
            "hits":         hits,
            "sort":         "rank",
            "output":       "json"
        }
        print(f"Fetching genre {genre_id}, {hits} items by ranking...")
        resp = requests.get(api_url, params=params)
        resp.raise_for_status()
        items = resp.json().get("result", {}).get("items", [])
        for i in items:
            title = i.get("title", "").strip()
            aff_url = i.get("affiliateURL", "")
            # Detail page URL: use API URL field
            url_info = i.get("URL") or {}
            if isinstance(url_info, dict):
                detail_url = url_info.get("pc") or url_info.get("list") or ""
            else:
                detail_url = ""
            # Main thumbnail
            img_info = i.get("imageURL", {}) or {}
            thumb = img_info.get("large") or img_info.get("small") or ""
            # Fetch and summarize description
            description = ""
            if detail_url:
                try:
                    description = fetch_description(detail_url, session)
                except Exception as e:
                    print(f"Warning: description fetch failed for {title}: {e}")
            if not description:
                description = i.get("description", "").strip()
            summary = textwrap.shorten(description, width=200, placeholder="…")
            items_out.append({
                "title": title,
                "url":   aff_url,
                "thumb": thumb,
                "summary": summary,
                "genres": [g.get("name") for g in i.get("genre", [])],
                "actors": [a.get("name") for a in i.get("actor", [])]
            })
            print(f"Fetched: {title}")
            time.sleep(1)
    return items_out

# ───────────────────────────────────────────────────────────
# Post to WordPress with duplicate check
# ───────────────────────────────────────────────────────────
def post_to_wp(item: dict):
    wp = Client(WP_URL, WP_USER, WP_PASS)
    existing = wp.call(GetPosts({'post_status':'publish','s':item['title']}))
    if any(p.title == item['title'] for p in existing):
        print(f"Skipping duplicate: {item['title']}")
        return
    thumb_id = None
    if item['thumb']:
        img_data = requests.get(item['thumb']).content
        data = {"name": os.path.basename(item['thumb']),"type":"image/jpeg","bits":xmlrpc_client.Binary(img_data)}
        resp = wp.call(media.UploadFile(data))
        thumb_id = resp.get("id")
    content = f"<p>{item['summary']}</p>"
    content += f"<p><a href='{item['url']}' target='_blank'>▶ 詳細・購入はこちら</a></p>"
    post = WordPressPost()
    post.title = item['title']
    post.content = content
    if thumb_id:
        post.thumbnail = thumb_id
    post.terms_names = {"category":["DMM動画"],"post_tag":item['genres']+item['actors']}
    post.post_status = 'publish'
    wp.call(posts.NewPost(post))
    print(f"Posted: {item['title']}")

# ───────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────
def main():
    videos = fetch_videos_by_genres(GENRE_IDS, HITS)
    for vid in videos:
        try:
            post_to_wp(vid)
            time.sleep(1)
        except Exception as e:
            print(f"Error posting {vid['title']}: {e}")

if __name__ == '__main__':
    main()
