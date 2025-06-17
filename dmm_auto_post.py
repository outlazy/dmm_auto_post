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

# After bootstrap, import libraries
import os
import time
import requests
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
import collections.abc

# Compatibility patch for wordpress_xmlrpc
collections.Iterable = collections.abc.Iterable

# Load environment variables

def load_env() -> dict:
    load_dotenv()
    env = {
        "WP_URL": os.getenv("WP_URL"),
        "WP_USER": os.getenv("WP_USER"),
        "WP_PASS": os.getenv("WP_PASS"),
        "DMM_AFFILIATE_ID": os.getenv("DMM_AFFILIATE_ID"),
        "DMM_API_ID": os.getenv("DMM_API_ID"),
    }
    missing = [k for k, v in env.items() if not v]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")
    return env

env = load_env()
WP_URL = env["WP_URL"]
WP_USER = env["WP_USER"]
WP_PASS = env["WP_PASS"]
AFF_ID = env["DMM_AFFILIATE_ID"]
API_ID = env["DMM_API_ID"]

# API endpoints and settings
ITEM_DETAIL_URL = "https://api.dmm.com/affiliate/v3/ItemDetail"
GENRE_LIST_URL = "https://video.dmm.co.jp/amateur/list/"
GENRE_TARGET_ID = "8503"
MAX_POST = 10


def make_affiliate_link(url: str) -> str:
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs["affiliate_id"] = AFF_ID
    new_query = urlencode(qs)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def fetch_latest_videos() -> list[dict]:
    url = f"{GENRE_LIST_URL}?genre={GENRE_TARGET_ID}"
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    session.cookies.set("ckcy", "1", domain=".dmm.co.jp")

    try:
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"DEBUG: Failed to load genre page: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    # Age check bypass
    agree = soup.select_one("a[href*='adult']") or soup.find("a", string=lambda t: t and 'Agree' in t)
    if agree and agree.get('href'):
        agree_url = agree['href']
        if not agree_url.startswith('http'):
            agree_url = urlparse(url)._replace(path=agree_url).geturl()
        try:
            resp = session.get(agree_url, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            print(f"DEBUG: Age check bypass failed: {e}")
            return []

    videos = []
    for li in soup.select("li.list-box")[:MAX_POST]:
        a = li.find("a", class_="tmb")
        if not a or not a.get('href'):
            continue
        link = a['href']
        detail_url = link if link.startswith('http') else f"https://video.dmm.co.jp{link}"
        img = a.find('img')
        title = img['alt'].strip() if img and img.get('alt') else (li.find('p', class_='title').get_text(strip=True) if li.find('p', class_='title') else '')
        cid = detail_url.rstrip('/').split('/')[-1]
        videos.append({'title': title, 'detail_url': detail_url, 'cid': cid})

    print(f"DEBUG: Found {len(videos)} videos")
    return videos


def fetch_sample_images(cid: str) -> list[str]:
    params = {"api_id": API_ID, "affiliate_id": AFF_ID, "site": "video", "service": "amateur", "item": cid, "output": "json"}
    try:
        resp = requests.get(ITEM_DETAIL_URL, params=params, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"DEBUG: ItemDetail API error for {cid}: {e}")
        return []

    items = resp.json().get('result', {}).get('items', [])
    if not items:
        return []
    imgs = items[0].get('sampleImageURL', {}).get('large', [])
    return [imgs] if isinstance(imgs, str) else imgs


def upload_image(wp: Client, url: str) -> int:
    try:
        data = requests.get(url, timeout=10).content
    except Exception as e:
        print(f"DEBUG: Image download failed: {e}")
        return None
    name = os.path.basename(urlparse(url).path)
    media_data = {"name": name, "type": "image/jpeg", "bits": xmlrpc_client.Binary(data)}
    res = wp.call(media.UploadFile(media_data))
    return res.get('id')


def create_wp_post(video: dict) -> bool:
    wp = Client(WP_URL, WP_USER, WP_PASS)
    title = video['title']
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if any(p.title == title for p in existing):
        print(f"→ Duplicate skipped: {title}")
        return False
    imgs = fetch_sample_images(video['cid'])
    if not imgs:
        print(f"→ No images for: {title}")
        return False
    thumb = upload_image(wp, imgs[0])
    aff_url = make_affiliate_link(video['detail_url'])
    content = [f"<p><a href='{aff_url}' target='_blank'><img src='{imgs[0]}' alt='{title}'/></a></p>", f"<p><a href='{aff_url}' target='_blank'>{title}</a></p>"]
    content += [f"<p><img src='{i}' alt='{title}'/></p>" for i in imgs[1:]] + [f"<p><a href='{aff_url}' target='_blank'>{title}</a></p>"]
    post = WordPressPost()
    post.title = title
    post.content = "\n".join(content)
    post.thumbnail = thumb
    post.terms_names = {"category": ["DMM動画"], "post_tag": []}
    post.post_status = "publish"
    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {title}")
    return True


def main():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Start")
    videos = fetch_latest_videos()
    for v in videos:
        if create_wp_post(v):
            break
    else:
        print("No new videos.")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] End")


if __name__ == '__main__':
    main()
