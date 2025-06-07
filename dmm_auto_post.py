#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import collections
import collections.abc
# Compatibility patch for wordpress_xmlrpc
collections.Iterable = collections.abc.Iterable
import time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# Load environment variables
load_dotenv()
WP_URL     = os.getenv("WP_URL")
WP_USER    = os.getenv("WP_USER")
WP_PASS    = os.getenv("WP_PASS")
AFF_ID     = os.getenv("DMM_AFFILIATE_ID")
DMM_API_ID = os.getenv("DMM_API_ID")

# Validate required variables
for name, val in [("WP_URL",WP_URL),("WP_USER",WP_USER),("WP_PASS",WP_PASS),("DMM_AFFILIATE_ID",AFF_ID),("DMM_API_ID",DMM_API_ID)]:
    if not val:
        raise RuntimeError(f"Missing environment variable: {name}")

# Affiliate API endpoint and parameters for latest amateur videos (genre 8503)
API_URL = "https://api.dmm.com/affiliate/v3/ItemList"
ITEM_PARAMS = {
    "api_id":       DMM_API_ID,
    "affiliate_id": AFF_ID,
    "site":         "FANZA",
    "service":      "digital",
    "genre_id":     "8503",  # amateur gyaru
    "hits":         10,       # fetch up to 10 items
    "sort":         "date",
    "output":       "json",
}

# Helper: build affiliate link
def make_affiliate_link(url: str) -> str:
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs["affiliate_id"] = AFF_ID
    new_query = urlencode(qs)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

# Fetch latest list via API
def fetch_latest_video():
    resp = requests.get(API_URL, params=ITEM_PARAMS, timeout=10)
    try:
        resp.raise_for_status()
    except Exception as e:
        print(f"DEBUG: API request failed: {e}")
        return None
    data = resp.json()
    items = data.get("result", {}).get("items", [])
    if not items:
        return None
    item = items[0]
    return {
        "title":      item.get("title", "No Title"),
        "detail_url": item.get("URL"),
        "description":item.get("description", ""),
    }

# Scrape detail page for images after release
def scrape_detail_images(detail_url: str) -> list[str]:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    # Bypass age-check cookie
    session.cookies.set("ckcy","1",domain=".dmm.co.jp")
    resp = session.get(detail_url, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")
    imgs = []
    # Sample images selectors
    selectors = ["div#sample-image-box img","img.sample-box__img","li.sample-box__item img","figure img"]
    for sel in selectors:
        for img in soup.select(sel):
            src = img.get("data-original") or img.get("src")
            if src and src not in imgs:
                imgs.append(src)
        if imgs:
            break
    # Fallback og:image
    if not imgs and soup.find("meta",property="og:image"):
        imgs.append(soup.find("meta",property="og:image")["content"].strip())
    print(f"DEBUG: scrape_detail_images found {len(imgs)} images")
    return imgs

# Upload image to WP
def upload_image(wp: Client, url: str) -> int:
    data = requests.get(url, timeout=10).content
    name = os.path.basename(urlparse(url).path)
    media_data = {"name": name, "type": "image/jpeg", "bits": xmlrpc_client.Binary(data)}
    res = wp.call(media.UploadFile(media_data))
    return res.get("id")

# Create WP post
def create_wp_post(video):
    wp = Client(WP_URL, WP_USER, WP_PASS)
    title = video["title"]
    # Check duplicate
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if any(p.title==title for p in existing):
        print(f"→ Skipping duplicate: {title}")
        return
    # Scrape images from detail page
    images = scrape_detail_images(video["detail_url"])
    # Upload first image as thumbnail
    thumb_id = upload_image(wp, images[0]) if images else None
    # Build content
    aff_link = make_affiliate_link(video["detail_url"])
    parts = []
    if thumb_id:
        parts.append(f'<p><a href="{aff_link}" target="_blank"><img src="{images[0]}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')
    if video.get("description"):
        parts.append(f'<div>{video["description"]}</div>')
    # Insert remaining images
    for img in images[1:]:
        parts.append(f'<p><img src="{img}" alt="{title}"></p>')
    # Final affiliate link
    parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')
    # Publish
    post = WordPressPost(); post.title=title; post.content="\n".join(parts)
    if thumb_id: post.thumbnail=thumb_id
    post.terms_names={"category":["DMM動画"],"post_tag":[]}; post.post_status="publish"
    wp.call(posts.NewPost(post)); print(f"✔ Posted: {title}")

# Main
if __name__=="__main__":
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job start")
    video = fetch_latest_video()
    if video:
        create_wp_post(video)
    else:
        print("No videos found to post.")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished")
