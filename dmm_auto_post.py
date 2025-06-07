#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import collections
import collections.abc
# Compatibility patch for wordpress_xmlrpc
collections.Iterable = collections.abc.Iterable
import time
import requests
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# Load environment variables
def load_env():
    load_dotenv()
    env = {
        "WP_URL":     os.getenv("WP_URL"),
        "WP_USER":    os.getenv("WP_USER"),
        "WP_PASS":    os.getenv("WP_PASS"),
        "AFF_ID":     os.getenv("DMM_AFFILIATE_ID"),
        "API_ID":     os.getenv("DMM_API_ID"),
    }
    for name, val in env.items():
        if not val:
            raise RuntimeError(f"Missing environment variable: {name}")
    return env

env = load_env()
WP_URL, WP_USER, WP_PASS, AFF_ID, API_ID = env["WP_URL"], env["WP_USER"], env["WP_PASS"], env["AFF_ID"], env["API_ID"]

# Affiliate API endpoints
ITEM_LIST_URL = "https://api.dmm.com/affiliate/v3/ItemList"
ITEM_DETAIL_URL = "https://api.dmm.com/affiliate/v3/ItemDetail"

# Parameters for listing amateur gyaru videos
LIST_PARAMS = {
    "api_id":       API_ID,
    "affiliate_id": AFF_ID,
    "site":         "video",
    "service":      "amateur",
    "genre_id":     "8503",
    "hits":         10,
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

# Fetch latest amateur videos via Affiliate API
def fetch_latest_videos() -> list[dict]:
    resp = requests.get(ITEM_LIST_URL, params=LIST_PARAMS, timeout=10)
    resp.raise_for_status()
    data = resp.json().get("result", {}).get("items", [])
    videos = []
    for item in data:
        videos.append({
            "title":      item.get("title", "No Title"),
            "detail_url": item.get("URL"),
            "description":item.get("description", ""),
            "cid":        item.get("content_id") or item.get("cid"),
        })
    return videos

# Fetch sample images via Affiliate ItemDetail API
def fetch_sample_images(cid: str) -> list[str]:
    params = {
        "api_id":       API_ID,
        "affiliate_id": AFF_ID,
        "site":         "video",
        "service":      "amateur",
        "item":         cid,
        "output":       "json",
    }
    resp = requests.get(ITEM_DETAIL_URL, params=params, timeout=10)
    resp.raise_for_status()
    items = resp.json().get("result", {}).get("items", [])
    if not items:
        return []
    sample_urls = items[0].get("sampleImageURL", {}).get("large")
    if isinstance(sample_urls, list):
        return sample_urls
    if isinstance(sample_urls, str):
        return [sample_urls]
    return []

# Upload image to WordPress
def upload_image(wp: Client, url: str) -> int:
    data = requests.get(url, timeout=10).content
    name = os.path.basename(urlparse(url).path)
    media_data = {"name": name, "type": "image/jpeg", "bits": xmlrpc_client.Binary(data)}
    res = wp.call(media.UploadFile(media_data))
    return res.get("id")

# Create WordPress post for a single video
def create_wp_post(video: dict) -> bool:
    wp = Client(WP_URL, WP_USER, WP_PASS)
    title = video["title"]
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if any(p.title == title for p in existing):
        print(f"→ Skipping duplicate: {title}")
        return False
    # Fetch samples
    images = fetch_sample_images(video["cid"])
    if not images:
        print(f"→ No samples for: {title}, skipping.")
        return False
    # Upload featured image
    thumb_id = upload_image(wp, images[0])
    # Build content
    aff = make_affiliate_link(video["detail_url"])
    parts = []
    # Featured
    parts.append(f'<p><a href="{aff}" target="_blank"><img src="{images[0]}" alt="{title}"></a></p>')
    # Title
    parts.append(f'<p><a href="{aff}" target="_blank">{title}</a></p>')
    # Description
    if video.get("description"):
        parts.append(f'<div>{video["description"]}</div>')
    # Remaining samples
    for img in images[1:]:
        parts.append(f'<p><img src="{img}" alt="{title}"></p>')
    # Final link
    parts.append(f'<p><a href="{aff}" target="_blank">{title}</a></p>')
    # Post tags: actress, label, genre from API detail
    # (Optional: fetch via ItemDetail API if needed)
    post = WordPressPost()
    post.title = title
    post.content = "\n".join(parts)
    post.thumbnail = thumb_id
    post.terms_names = {"category":["DMM動画"], "post_tag": []}
    post.post_status = "publish"
    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {title}")
    return True

# Main execution
def main():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job start")
    videos = fetch_latest_videos()
    for video in videos:
        if create_wp_post(video):
            break
    else:
        print("No new videos to post.")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished")

if __name__ == "__main__":
    main()
