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
    from dotenv import load_dotenv as ld
    ld()
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
WP_URL, WP_USER, WP_PASS, AFF_ID, API_ID = env.values()

# Affiliate API endpoints
ITEM_LIST_URL = "https://api.dmm.com/affiliate/v3/ItemList"
ITEM_DETAIL_URL = "https://api.dmm.com/affiliate/v3/ItemDetail"

# Parameters for listing amateur videos (no genre filter)
LIST_PARAMS = {
    "api_id":       API_ID,
    "affiliate_id": AFF_ID,
    "site":         "video",
    "service":      "amateur",
    "hits":         20,
    "sort":         "date",
    "output":       "json",
}
GENRE_TARGET_ID = "8503"  # amateur gyaru
MAX_POST = 10

# Build affiliate link
def make_affiliate_link(url: str) -> str:
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs["affiliate_id"] = AFF_ID
    new_query = urlencode(qs)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

# Fetch latest amateur gyaru videos via Affiliate API using floorList and ItemList
from requests.exceptions import HTTPError

def fetch_latest_videos() -> list[dict]:
    """
    Scrape latest amateur gyaru videos from HTML genre page: https://video.dmm.co.jp/amateur/list/?genre=8503
    """
    GENRE_URL = "https://video.dmm.co.jp/amateur/list/?genre=8503"
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    session.cookies.set("ckcy","1",domain=".dmm.co.jp")
    try:
        resp = session.get(GENRE_URL, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"DEBUG: HTML fetch failed: {e}")
        return []
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "html.parser")
    videos = []
    for li in soup.select("li.list-box")[:MAX_POST]:
        a = li.find("a", class_="tmb")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        detail_url = href if href.startswith("http") else f"https://video.dmm.co.jp{href}"
        title = a.img.get("alt","").strip() if a.img and a.img.get("alt") else (a.get("title") or li.find("p", class_="title").get_text(strip=True))
        # extract cid
        cid = detail_url.rstrip('/').split('/')[-1]
        videos.append({"title": title, "detail_url": detail_url, "cid": cid, "description": ""})
    print(f"DEBUG: HTML scraping returned {len(videos)} items from genre page")
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
    samples = items[0].get("sampleImageURL", {}).get("large")
    if isinstance(samples, list):
        return samples
    if isinstance(samples, str):
        return [samples]
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
    parts.append(f'<p><a href="{aff}" target="_blank"><img src="{images[0]}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{aff}" target="_blank">{title}</a></p>')
    if video.get("description"):
        parts.append(f'<div>{video["description"]}</div>')
    for img in images[1:]:
        parts.append(f'<p><img src="{img}" alt="{title}"></p>')
    parts.append(f'<p><a href="{aff}" target="_blank">{title}</a></p>')
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
