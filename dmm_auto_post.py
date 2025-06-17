#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import subprocess

# --- Dependency bootstrap: install missing packages at runtime ---
required_packages = [
    ('python-dotenv', 'dotenv', 'python-dotenv>=0.21.0'),
    ('requests', 'requests', 'requests>=2.31.0'),
    ('wordpress_xmlrpc', 'wordpress_xmlrpc', 'python-wordpress-xmlrpc>=2.3'),
    ('bs4', 'bs4', 'beautifulsoup4>=4.12.2'),
]
for module_name, import_name, pkg in required_packages:
    try:
        __import__(import_name)
    except ImportError:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', pkg])

import os
import collections
import collections.abc  # Compatibility patch for wordpress_xmlrpc
collections.Iterable = collections.abc.Iterable

import time
import requests
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from bs4 import BeautifulSoup


def load_env() -> dict:
    """
    Load environment variables from .env and validate presence.
    """
    load_dotenv()
    env = {
        "WP_URL": os.getenv("WP_URL"),
        "WP_USER": os.getenv("WP_USER"),
        "WP_PASS": os.getenv("WP_PASS"),
        "DMM_AFFILIATE_ID": os.getenv("DMM_AFFILIATE_ID"),
        "DMM_API_ID": os.getenv("DMM_API_ID"),
    }
    for name, val in env.items():
        if not val:
            raise RuntimeError(f"Missing environment variable: {name}")
    return env

# Load environment
env = load_env()
WP_URL = env["WP_URL"]
WP_USER = env["WP_USER"]
WP_PASS = env["WP_PASS"]
AFF_ID = env["DMM_AFFILIATE_ID"]
API_ID = env["DMM_API_ID"]

ITEM_DETAIL_URL = "https://api.dmm.com/affiliate/v3/ItemDetail"
GENRE_TARGET_ID = "8503"  # amateur genre
MAX_POST = 10


def make_affiliate_link(url: str) -> str:
    """
    Append affiliate ID to DMM product URL.
    """
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs["affiliate_id"] = AFF_ID
    new_query = urlencode(qs)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def fetch_latest_videos() -> list[dict]:
    """
    Scrape latest amateur videos from DMM HTML page.
    """
    genre_url = f"https://video.dmm.co.jp/amateur/list/?genre={GENRE_TARGET_ID}"
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    session.cookies.set("ckcy", "1", domain=".dmm.co.jp")
    try:
        resp = session.get(genre_url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"DEBUG: HTML fetch failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    videos = []
    for li in soup.select("li.list-box")[:MAX_POST]:
        a = li.find("a", class_="tmb")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        detail_url = href if href.startswith("http") else f"https://video.dmm.co.jp{href}"
        img_tag = a.find("img")
        if img_tag and img_tag.get("alt"):
            title = img_tag.get("alt").strip()
        else:
            title_tag = li.find("p", class_="title")
            title = title_tag.get_text(strip=True) if title_tag else ""
        cid = detail_url.rstrip("/").split("/")[-1]
        videos.append({"title": title, "detail_url": detail_url, "cid": cid})

    print(f"DEBUG: HTML scraping returned {len(videos)} items")
    return videos


def fetch_sample_images(cid: str) -> list[str]:
    """
    Fetch sample image URLs from DMM Affiliate API.
    """
    params = {
        "api_id": API_ID,
        "affiliate_id": AFF_ID,
        "site": "video",
        "service": "amateur",
        "item": cid,
        "output": "json",
    }
    try:
        resp = requests.get(ITEM_DETAIL_URL, params=params, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"DEBUG: ItemDetail API failed for cid {cid}: {e}")
        return []

    data = resp.json()
    items = data.get("result", {}).get("items", [])
    if not items:
        return []
    samples = items[0].get("sampleImageURL", {}).get("large")
    if isinstance(samples, str):
        return [samples]
    return samples or []


def upload_image(wp: Client, url: str) -> int:
    """
    Download and upload an image to WordPress, returning its media ID.
    """
    try:
        img_data = requests.get(url, timeout=10).content
    except Exception as e:
        print(f"DEBUG: Failed to download image {url}: {e}")
        return None
    name = os.path.basename(urlparse(url).path)
    media_data = {"name": name, "type": "image/jpeg", "bits": xmlrpc_client.Binary(img_data)}
    res = wp.call(media.UploadFile(media_data))
    return res.get("id")


def create_wp_post(video: dict) -> bool:
    """
    Create a WordPress post for a single video. Returns True if posted.
    """
    wp = Client(WP_URL, WP_USER, WP_PASS)
    title = video["title"]
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if any(p.title == title for p in existing):
        print(f"→ Skipping duplicate: {title}")
        return False
    images = fetch_sample_images(video["cid"])
    if not images:
        print(f"→ No samples for: {title}, skipping.")
        return False
    thumb_id = upload_image(wp, images[0])
    aff_url = make_affiliate_link(video["detail_url"])
    parts = [
        f'<p><a href="{aff_url}" target="_blank"><img src="{images[0]}" alt="{title}"></a></p>',
        f'<p><a href="{aff_url}" target="_blank">{title}</a></p>'
    ]
    for img in images[1:]:
        parts.append(f'<p><img src="{img}" alt="{title}"></p>')
    parts.append(f'<p><a href="{aff_url}" target="_blank">{title}</a></p>')
    post = WordPressPost()
    post.title = title
    post.content = "\n".join(parts)
    post.thumbnail = thumb_id
    post.terms_names = {"category": ["DMM動画"], "post_tag": []}
    post.post_status = "publish"
    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {title}")
    return True


def main():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job start")
    for video in fetch_latest_videos():
        if create_wp_post(video):
            break
    else:
        print("No new videos to post.")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished")


if __name__ == "__main__":
    main()
