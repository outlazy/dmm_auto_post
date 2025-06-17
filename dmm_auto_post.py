#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import subprocess

# --- Bootstrap dependencies before any other imports ---
required_packages = [
    ('dotenv', 'python-dotenv>=0.21.0'),
    ('requests', 'requests>=2.31.0'),
    ('wordpress_xmlrpc', 'python-wordpress-xmlrpc>=2.3'),
    ('bs4', 'beautifulsoup4>=4.12.2'),
]
for module, pkg in required_packages:
    try:
        __import__(module)
    except ImportError:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', pkg])

# Safe imports after bootstrap
import os
import time
import requests
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, urljoin
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client
import collections.abc

# Compatibility patch
collections.Iterable = collections.abc.Iterable

# Load env
def load_env():
    load_dotenv()
    env = {
        'WP_URL': os.getenv('WP_URL'),
        'WP_USER': os.getenv('WP_USER'),
        'WP_PASS': os.getenv('WP_PASS'),
        'DMM_AFFILIATE_ID': os.getenv('DMM_AFFILIATE_ID'),
        'DMM_API_ID': os.getenv('DMM_API_ID'),
    }
    missing = [k for k, v in env.items() if not v]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")
    return env

env = load_env()
WP_URL, WP_USER, WP_PASS = env['WP_URL'], env['WP_USER'], env['WP_PASS']
AFF_ID, API_ID = env['DMM_AFFILIATE_ID'], env['DMM_API_ID']

# Constants
ITEM_DETAIL_URL = 'https://api.dmm.com/affiliate/v3/ItemDetail'
GENRE_LIST_URL = 'https://video.dmm.co.jp/amateur/list/'
GENRE_TARGET_ID = '8503'
MAX_POST = 10

# Affiliate link builder
def make_affiliate_link(url: str) -> str:
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs['affiliate_id'] = AFF_ID
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(qs), parsed.fragment))

# Fetch videos with age-check bypass
def fetch_latest_videos() -> list:
    target = f"{GENRE_LIST_URL}?genre={GENRE_TARGET_ID}"
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
    session.cookies.set('ckcy', '1', domain='.dmm.co.jp')

    try:
        resp = session.get(target, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"DEBUG: Load page failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    # Age check bypass
    agree = soup.find('a', string=lambda t: t and 'Agree' in t)
    if agree and agree.get('href'):
        agree_url = urljoin(resp.url, agree['href'])
        try:
            resp = session.get(agree_url, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
        except Exception as e:
            print(f"DEBUG: Bypass failed: {e}")
            return []

    items = []
    # Updated selector: collect anchor tags with thumbnail class
    links = soup.select('a.tmb[href]')
    for a in links[:MAX_POST]:
        href = a['href']
        detail = href if href.startswith('http') else urljoin(resp.url, href)
        img = a.find('img')
        title = img.get('alt','').strip() if img and img.get('alt') else ''
        cid = detail.rstrip('/').split('/')[-1]
        items.append({'title': title, 'detail_url': detail, 'cid': cid})

    print(f"DEBUG: Found {len(items)} videos (via anchor.tmb)")
    return items

# Fetch sample images
def fetch_sample_images(cid: str) -> list:
    params = {'api_id': API_ID, 'affiliate_id': AFF_ID, 'site': 'video', 'service': 'amateur', 'item': cid, 'output': 'json'}
    try:
        r = requests.get(ITEM_DETAIL_URL, params=params, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"DEBUG: API error for {cid}: {e}")
        return []
    data = r.json().get('result', {}).get('items', [])
    if not data:
        return []
    imgs = data[0].get('sampleImageURL', {}).get('large', [])
    return [imgs] if isinstance(imgs, str) else imgs

# Upload image
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

# Create post
def create_wp_post(video: dict) -> bool:
    wp = Client(WP_URL, WP_USER, WP_PASS)
    title = video['title']
    existing = wp.call(GetPosts({'post_status': 'publish', 's': title}))
    if any(p.title == title for p in existing):
        print(f"→ Skip dup: {title}")
        return False
    imgs = fetch_sample_images(video['cid'])
    if not imgs:
        print(f"→ No imgs: {title}")
        return False
    thumb = upload_image(wp, imgs[0])
    link = make_affiliate_link(video['detail_url'])
    parts = [f"<p><a href='{link}'><img src='{imgs[0]}'/></a></p>", f"<p><a href='{link}'>{title}</a></p>"]
    parts += [f"<p><img src='{i}'/></p>" for i in imgs[1:]] + [f"<p><a href='{link}'>{title}</a></p>"]
    post = WordPressPost()
    post.title = title
    post.content = "\n".join(parts)
    post.thumbnail = thumb
    post.terms_names = {'category': ['DMM動画'], 'post_tag': []}
    post.post_status = 'publish'
    wp.call(posts.NewPost(post))
    print(f"✔ {title}")
    return True

# Main
def main():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Start")
    for video in fetch_latest_videos():
        if create_wp_post(video):
            break
    else:
        print("No new posts.")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] End")

if __name__ == '__main__':
    main()
