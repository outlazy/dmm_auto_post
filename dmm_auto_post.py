#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import requests
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# Load environment variables
load_dotenv()
WP_URL = os.getenv("WP_URL")
WP_USER = os.getenv("WP_USER")
WP_PASS = os.getenv("WP_PASS")
AFF_ID = os.getenv("DMM_AFFILIATE_ID")
DMM_API_ID = os.getenv("DMM_API_ID")

# Validate required variables
for name, val in [("WP_URL", WP_URL), ("WP_USER", WP_USER), ("WP_PASS", WP_PASS), ("DMM_AFFILIATE_ID", AFF_ID), ("DMM_API_ID", DMM_API_ID)]:
    if not val:
        raise RuntimeError(f"Missing environment variable: {name}")

# Affiliate API endpoint and parameters for latest amateur videos (genre 8503)
API_URL = "https://api.dmm.com/affiliate/v3/ItemList"
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

# Build affiliate link
def make_affiliate_link(url: str) -> str:
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs["affiliate_id"] = AFF_ID
    new_query = urlencode(qs)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

# Fetch latest video via ItemList API
def fetch_latest_video():
    resp = requests.get(API_URL, params=ITEM_PARAMS, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("result", {}).get("items", [])
    if not items:
        return None
    item = items[0]
    return {
        "title":       item.get("title", "No Title"),
        "detail_url":  item.get("URL"),
        "description": item.get("description", ""),
        "images":      [item.get("imageURL", {}).get("large")] if item.get("imageURL") else [],
    }

# Upload image to WP
def upload_image(wp: Client, url: str) -> int:
    data = requests.get(url).content
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
    if any(p.title == title for p in existing):
        print(f"→ Skipping duplicate: {title}")
        return

    # Upload thumbnail
    thumb_id = None
    if video["images"] and video["images"][0]:
        thumb_id = upload_image(wp, video["images"][0])

    # Build content
    aff_link = make_affiliate_link(video["detail_url"])
    parts = []
    if thumb_id:
        parts.append(f'<p><a href="{aff_link}" target="_blank"><img src="{video["images"][0]}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')
    if video.get("description"):
        parts.append(f'<div>{video["description"]}</div>')
    # Additional images not available via API
    parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')

    post = WordPressPost()
    post.title = title
    post.content = "\n".join(parts)
    if thumb_id:
        post.thumbnail = thumb_id
    post.terms_names = {"category": ["DMM動画"], "post_tag": []}
    post.post_status = "publish"
    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {title}")

# Main
if __name__ == "__main__":
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job start")
    video = fetch_latest_video()
    if video:
        create_wp_post(video)
    else:
        print("No videos found to post.")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished")
