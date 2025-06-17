#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import subprocess
import os
import time

# --- Bootstrap dependencies: install missing packages before any imports ---
required_packages = [
    ('dotenv', 'python-dotenv>=0.21.0'),
    ('requests', 'requests>=2.31.0'),
    ('bs4', 'beautifulsoup4>=4.12.2'),
    ('wordpress_xmlrpc', 'python-wordpress-xmlrpc>=2.3')
]
for module_name, pkg_spec in required_packages:
    try:
        __import__(module_name)
    except ImportError:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', pkg_spec])

# Now safe to import third-party libraries
import requests
from dotenv import load_dotenv
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, urljoin
from bs4 import BeautifulSoup
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client
import collections.abc

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
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")
    return env

env = load_env()
WP_URL = env['WP_URL']
WP_USER = env['WP_USER']
WP_PASS = env['WP_PASS']
AFF_ID = env['DMM_AFFILIATE_ID']
API_ID = env['DMM_API_ID']

# Settings
LIST_URL = 'https://video.dmm.co.jp/amateur/list/?sort=date'
max_posts = 10

# Helper: build affiliate URL
def make_affiliate_link(url: str) -> str:
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs['affiliate_id'] = AFF_ID
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(qs), parsed.fragment))

# Fetch latest videos by scraping list page with age-check bypass
def fetch_latest_videos() -> list:
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
    # Bypass age check
    session.cookies.set('ckcy', '1', domain='.dmm.co.jp')

    try:
        resp = session.get(LIST_URL, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"DEBUG: List page load failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    # If still on age-check page, click 'I Agree'
    agree = soup.find('a', string=lambda t: t and 'Agree' in t)
    if agree and agree.get('href'):
        try:
            resp = session.get(urljoin(LIST_URL, agree['href']), timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
        except Exception as e:
            print(f"DEBUG: Age bypass failed: {e}")
            return []

    vids = []
    for li in soup.select('li.list-box')[:max_posts]:
        a = li.find('a', class_='tmb')
        if not a or not a.get('href'):
            continue
        href = a['href']
        detail = href if href.startswith('http') else urljoin(LIST_URL, href)
        title = a.find('img').get('alt', '').strip() if a.find('img') else ''
        cid = detail.rstrip('/').split('cid=')[-1]
        vids.append({'title': title, 'detail_url': detail, 'cid': cid})
    print(f"DEBUG: Scraped {len(vids)} videos from list page")
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
        r = requests.get('https://api.dmm.com/affiliate/v3/ItemDetail', params=params, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"DEBUG: ItemDetail API failed for {cid}: {e}")
        return []
    data = r.json().get('result', {}).get('items', [])
    if not data:
        return []
    imgs = data[0].get('sampleImageURL', {}).get('large', [])
    return [imgs] if isinstance(imgs, str) else imgs

# Upload image to WordPress
def upload_image(wp: Client, url: str) -> int:
    try:
        content = requests.get(url, timeout=10).content
    except Exception as e:
        print(f"DEBUG: Download failed: {e}")
        return None
    name = os.path.basename(urlparse(url).path)
    media_data = {'name': name, 'type': 'image/jpeg', 'bits': xmlrpc_client.Binary(content)}
    res = wp.call(media.UploadFile(media_data))
    return res.get('id')

# Create WordPress post
def create_wp_post(video: dict) -> bool:
    wp = Client(WP_URL, WP_USER, WP_PASS)
    existing = wp.call(GetPosts({'post_status': 'publish', 's': video['title']}))
    if any(p.title == video['title'] for p in existing):
        print(f"→ Skip duplicate: {video['title']}")
        return False
    imgs = fetch_sample_images(video['cid'])
    if not imgs:
        print(f"→ No images for: {video['title']}")
        return False
    thumb_id = upload_image(wp, imgs[0])
    aff = make_affiliate_link(video['detail_url'])
    parts = [
        f"<p><a href='{aff}' target='_blank'><img src='{imgs[0]}' alt='{video['title']}'/></a></p>",
        f"<p><a href='{aff}' target='_blank'>{video['title']}</a></p>"
    ]
    for img in imgs[1:]:
        parts.append(f"<p><img src='{img}' alt='{video['title']}'/></p>")
    parts.append(f"<p><a href='{aff}' target='_blank'>{video['title']}</a></p>")
    post = WordPressPost()
    post.title = video['title']
    post.content = "\n".join(parts)
    post.thumbnail = thumb_id
    post.terms_names = {'category': ['DMM動画'], 'post_tag': []}
    post.post_status = 'publish'
    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {video['title']}")
    return True

# Main execution
def main():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job start")
    for video in fetch_latest_videos():
        if create_wp_post(video):
            break
    else:
        print("No new videos to post.")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished")

if __name__ == '__main__':
    main()
