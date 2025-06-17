#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import subprocess

# --- Bootstrap dependencies: install missing packages before any imports ---
required_packages = [
    ('dotenv', 'python-dotenv>=0.21.0'),
    ('requests', 'requests>=2.31.0'),
    ('wordpress_xmlrpc', 'python-wordpress-xmlrpc>=2.3'),
    ('bs4', 'beautifulsoup4>=4.12.2')
]
for module_name, pkg_spec in required_packages:
    try:
        __import__(module_name)
    except ImportError:
        subprocess.check_call([
            sys.executable, '-m', 'pip', 'install', pkg_spec
        ])

# --- Now safe to import third-party libraries ---
import os
import time
import requests
from dotenv import load_dotenv
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client
import collections.abc
from bs4 import BeautifulSoup

# Compatibility patch for older wordpress_xmlrpc
collections.Iterable = collections.abc.Iterable

# Load environment configuration
def load_env():
    load_dotenv()
    env = {
        'WP_URL': os.getenv('WP_URL'),
        'WP_USER': os.getenv('WP_USER'),
        'WP_PASS': os.getenv('WP_PASS'),
        'DMM_AFFILIATE_ID': os.getenv('DMM_AFFILIATE_ID'),
        'DMM_API_ID': os.getenv('DMM_API_ID')
    }
    missing = [k for k, v in env.items() if not v]
    if missing:
        raise RuntimeError(
            f"Missing environment variables: {', '.join(missing)}"
        )
    return env

env = load_env()
WP_URL = env['WP_URL']
WP_USER = env['WP_USER']
WP_PASS = env['WP_PASS']
AFF_ID = env['DMM_AFFILIATE_ID']
API_ID = env['DMM_API_ID']

# DMM Affiliate API endpoints
GENRE_SEARCH_URL = 'https://api.dmm.com/affiliate/v3/GenreSearch'
ITEM_LIST_URL = 'https://api.dmm.com/affiliate/v3/ItemList'
ITEM_DETAIL_URL = 'https://api.dmm.com/affiliate/v3/ItemDetail'

# Settings
genre_keyword = '素人'
max_posts = 10

# Helper: build affiliate URL
def make_affiliate_link(url: str) -> str:
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs['affiliate_id'] = AFF_ID
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path,
         parsed.params, urlencode(qs), parsed.fragment)
    )

# Get genre ID by keyword search
def get_genre_id(keyword: str) -> str:
    params = {
        'api_id': API_ID,
        'affiliate_id': AFF_ID,
        'site': 'video',
        'service': 'amateur',
        'keyword': keyword,
        'hits': '100',
        'offset': '1',
        'output': 'json'
    }
    try:
        r = requests.get(
            GENRE_SEARCH_URL, params=params, timeout=10
        )
        r.raise_for_status()
    except Exception as e:
        print(f"DEBUG: GenreSearch failed: {e}")
        return ''
    genres = r.json().get('result', {}).get('genres', [])
    for g in genres:
        if keyword in g.get('name', ''):
            return g.get('id', '')
    return genres[0].get('id', '') if genres else ''

# Fetch latest videos list
def fetch_latest_videos() -> list:
    gid = get_genre_id(genre_keyword)
    if not gid:
        print("DEBUG: No genre ID found")
        return []
    params = {
        'api_id': API_ID,
        'affiliate_id': AFF_ID,
        'site': 'video',
        'service': 'amateur',
        'genre_id': gid,
        'sort': '-release_date',
        'hits': max_posts,
        'output': 'json'
    }
    try:
        r = requests.get(
            ITEM_LIST_URL, params=params, timeout=10
        )
        r.raise_for_status()
    except Exception as e:
        print(f"DEBUG: ItemList failed: {e}")
        return []
    items = r.json().get('result', {}).get('items', [])
    vids = []
    for it in items:
        cid = it.get('content_id', '')
        title = it.get('title', '').strip()
        detail = (
            f"https://www.dmm.co.jp/digital/videoc/-/detail/=/cid={cid}/"
        )
        vids.append(
            {'title': title, 'detail_url': detail, 'cid': cid}
        )
    print(f"DEBUG: Found {len(vids)} videos via API")
    return vids

# Fetch sample images via ItemDetail API
def fetch_sample_images(cid: str) -> list:
    params = {
        'api_id': API_ID,
        'affiliate_id': AFF_ID,
        'site': 'video',
        'service': 'amateur',
        'item': cid,
        'output': 'json'
    }
    try:
        r = requests.get(
            ITEM_DETAIL_URL, params=params, timeout=10
        )
        r.raise_for_status()
    except Exception as e:
        print(f"DEBUG: ItemDetail failed for {cid}: {e}")
        return []
    items = r.json().get('result', {}).get('items', [])
    if not items:
        return []
    imgs = items[0].get('sampleImageURL', {}).get('large', [])
    return [imgs] if isinstance(imgs, str) else imgs

# Upload image to WordPress
def upload_image(wp: Client, url: str) -> int:
    try:
        data = requests.get(url, timeout=10).content
    except Exception as e:
        print(f"DEBUG: Download failed: {e}")
        return None
    name = os.path.basename(urlparse(url).path)
    media_data = {
        'name': name,
        'type': 'image/jpeg',
        'bits': xmlrpc_client.Binary(data)
    }
    res = wp.call(media.UploadFile(media_data))
    return res.get('id')

# Create WordPress post
def create_wp_post(video: dict) -> bool:
    wp = Client(WP_URL, WP_USER, WP_PASS)
    existing = wp.call(
        GetPosts({'post_status':'publish','s': video['title']})
    )
    if any(p.title == video['title'] for p in existing):
        print(f"→ Duplicate skipped: {video['title']}")
        return False
    imgs = fetch_sample_images(video['cid'])
    if not imgs:
        print(f"→ No images for: {video['title']}")
        return False
    thumb = upload_image(wp, imgs[0])
    aff = make_affiliate_link(video['detail_url'])
    content = [
        f"<p><a href='{aff}'><img src='{imgs[0]}' alt='{video['title']}'/></a></p>",
        f"<p><a href='{aff}'>{video['title']}</a></p>"
    ]
    for img in imgs[1:]:
        content.append(
            f"<p><img src='{img}' alt='{video['title']}'/></p>"
        )
    content.append(
        f"<p><a href='{aff}'>{video['title']}</a></p>"
    )
    post = WordPressPost()
    post.title = video['title']
    post.content = "\n".join(content)
    post.thumbnail = thumb
    post.terms_names = {'category':['DMM動画'],'post_tag':[]}
    post.post_status = 'publish'
    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {video['title']}")
    return True

# Main execution
def main():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job start")
    for v in fetch_latest_videos():
        if create_wp_post(v):
            break
    else:
        print("No new videos to post.")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished")

if __name__ == '__main__':
    main()
