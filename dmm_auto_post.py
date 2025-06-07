#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import collections
import collections.abc
# WordPress XML-RPC compatibility patch
collections.Iterable = collections.abc.Iterable

import os
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
# Load environment variables & constants
# ───────────────────────────────────────────────────────────
load_dotenv()
WP_URL     = os.getenv("WP_URL")
WP_USER    = os.getenv("WP_USER")
WP_PASS    = os.getenv("WP_PASS")
AFF_ID     = os.getenv("DMM_AFFILIATE_ID")
DMM_API_ID = os.getenv("DMM_API_ID")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
LIST_URL   = "https://video.dmm.co.jp/amateur/list/?sort=date"
MAX_ITEMS  = 10  # Number of videos to fetch

# Validate required variables
for name, v in [("WP_URL",WP_URL),("WP_USER",WP_USER),("WP_PASS",WP_PASS),("DMM_AFFILIATE_ID",AFF_ID)]:
    if not v:
        raise RuntimeError(f"Missing environment variable: {name}")

# ───────────────────────────────────────────────────────────
# Helper functions
# ───────────────────────────────────────────────────────────
def make_affiliate_link(url: str) -> str:
    p   = urlparse(url)
    qs  = dict(parse_qsl(p.query))
    qs["affiliate_id"] = AFF_ID
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(qs), p.fragment))

def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    # Bypass age-check cookies
    s.cookies.set("ckcy", "1", domain=".dmm.co.jp")
    s.cookies.set("ckcy", "1", domain="video.dmm.co.jp")
    return s

def abs_url(href: str) -> str:
    if href.startswith("//"):
        return f"https:{href}"
    if href.startswith("/"):
        return f"https://video.dmm.co.jp{href}"
    return href

# ───────────────────────────────────────────────────────────
# Fetch latest videos list via DMM Affiliate API
# ───────────────────────────────────────────────────────────
def fetch_listed_videos(limit: int):
    if not DMM_API_ID:
        raise RuntimeError("Missing environment variable: DMM_API_ID for Affiliate API")

    # 1) Get floorId via floorList
    fl_params = {
        "api_id":       DMM_API_ID,
        "affiliate_id": AFF_ID,
        "site":         "video",
        "service":      "amateur",
        "output":       "json",
    }
    fl_url = "https://api.dmm.com/affiliate/v3/floorList"
    try:
        fl_resp = requests.get(fl_url, params=fl_params, timeout=10)
        fl_resp.raise_for_status()
        fl_data = fl_resp.json()
        floor_items = fl_data.get("result", {}).get("floor", [])
        floor_id = floor_items[0].get("floorId") if floor_items else None
    except Exception as e:
        print(f"DEBUG: floorList API failed ({e}), falling back to HTML scraping")
        floor_id = None

    # 2) Fetch via itemList API if floor_id available
    if floor_id:
        il_params = {
            "api_id":       DMM_API_ID,
            "affiliate_id": AFF_ID,
            "site":         "video",
            "service":      "amateur",
            "floorId":      floor_id,
            "hits":         limit,
            "sort":         "date",
            "output":       "json",
        }
        il_url = "https://api.dmm.com/affiliate/v3/ItemList"
        try:
            il_resp = requests.get(il_url, params=il_params, timeout=10)
            il_resp.raise_for_status()
            il_data = il_resp.json()
            items = il_data.get("result", {}).get("items", [])
            videos = [{
                "title": itm.get("title", "No Title"),
                "detail_url": itm.get("URL")
            } for itm in items]
            print(f"DEBUG: fetch_listed_videos found {len(videos)} items via DMM API")
            return videos
        except Exception as e:
            print(f"DEBUG: ItemList API failed ({e}), falling back to HTML scraping")

    # 3) Fallback HTML scraping
    session = get_session()
    resp = session.get(LIST_URL, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")
    videos = []
    for li in soup.select("li.list-box")[:limit]:
        a = li.find("a", class_="tmb")
        if not a or not a.get("href"):
            continue
        url = abs_url(a["href"])
        title = a.img.get("alt", "").strip() if a.img and a.img.get("alt") else a.get("title") or (
            li.find("p", class_="title").get_text(strip=True) if li.find("p", class_="title") else "No Title"
        )
        videos.append({"title": title, "detail_url": url})
    print(f"DEBUG: fetch_listed_videos found {len(videos)} items via HTML scraping from {LIST_URL}")
    return videos

# ───────────────────────────────────────────────────────────
# Scrape detail page
# ───────────────────────────────────────────────────────────
def scrape_detail(url: str):
    session = get_session()
    resp = session.get(url, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else (
        soup.find("meta", property="og:title")["content"].strip() if soup.find("meta", property="og:title") else "No Title"
    )

    d = soup.find("div", class_="mg-b20 lh4") or soup.find("p", id="sample-description")
    desc = d.get_text(" ", strip=True) if d else ""

    imgs = []
    for sel in ("div#sample-image-box img", "img.sample-box__img", "li.sample-box__item img"):  
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
# Upload image
# ───────────────────────────────────────────────────────────
def upload_image(wp: Client, url: str) -> int:
    data = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10).content
    name = os.path.basename(url.split("?")[0])
    media_data = {"name": name, "type": "image/jpeg", "bits": xmlrpc_client.Binary(data)}
    res = wp.call(media.UploadFile(media_data))
    return res.get("id")

# ───────────────────────────────────────────────────────────
# Create WP post
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
            print(f"Thumbnail upload failed: {e}")
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
# Main job
# ───────────────────────────────────────────────────────────
def job():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job start")
    videos = fetch_listed_videos(MAX_ITEMS)
    if not videos:
        print("No videos found to post.")
    else:
        title, desc, imgs = scrape_detail(videos[0]["detail_url"])
        create_wp_post(title, desc, imgs, videos[0]["detail_url"])
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished")

if __name__ == "__main__":
    job()
