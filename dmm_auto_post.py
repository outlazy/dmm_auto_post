#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import subprocess

# --- Bootstrap dependencies ---
required_packages = [
    ('dotenv', 'python-dotenv>=0.21.0'),
    ('requests', 'requests>=2.31.0'),
    ('wordpress_xmlrpc', 'python-wordpress-xmlrpc>=2.3'),
    ('bs4', 'beautifulsoup4>=4.12.2')
]
for module, pkg in required_packages:
    try:
        __import__(module)
    except ImportError:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', pkg])

# Now safe to import non-builtins
import os
import time
import requests
from dotenv import load_dotenv
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client
from bs4 import BeautifulSoup
import collections.abc

# Compatibility patch for wordpress_xmlrpc
collections.Iterable = collections.abc.Iterable

# Load environment variables
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

# API endpoints
genre_search_url = 'https://api.dmm.com/affiliate/v3/GenreSearch'
item_list_url = 'https://api.dmm.com/affiliate/v3/ItemList'
item_detail_url = 'https://api.dmm.com/affiliate/v3/ItemDetail'

# Settings
genre_keyword = '素人'
max_posts = 10

# Build affiliate link
def make_affiliate_link(url: str) -> str:
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs['affiliate_id'] = AFF_ID
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(qs), parsed.fragment))

# Retrieve genre ID via GenreSearch API
def get_genre_id(keyword: str) -> str:
    """
    Use GenreSearch API to find genre ID matching keyword.
    """
    params = {
        'api_id': API_ID,
        'affiliate_id': AFF_ID,
        'site': 'DMM.R18',        # use adult video site
        'service': 'videoa',      # amateur video category service
        'output': 'json'
    }
    try:
        resp = requests.get(genre_search_url, params=params, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"DEBUG: GenreSearch API failed: {e}")
        return ''
    genres = resp.json().get('result', {}).get('genres', [])
    for g in genres:
        # match keyword in name or fallback to exact id list
        if keyword in g.get('name', ''):
            return g.get('id', '')
    # fallback to first genre id if not found
    return genres[0].get('id', '') if genres else ''

# Fetch latest videos via ItemList API
def fetch_latest_videos() -> list:
    genre_id = get_genre_id(genre_keyword)
    if not genre_id:
        print("DEBUG: No genre ID found")
        return []
    params = {
        'api_id': API_ID,
        'affiliate_id': AFF_ID,
        'site': 'video',
        'service': 'amateur',
        'genre_id': genre_id,
        'sort': '-release_date',
        'hits': max_posts,
        'output': 'json'
    }
    try:
        resp = requests.get(item_list_url, params=params, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"DEBUG: ItemList API failed: {e}")
        return []
    items = resp.json().get('result', {}).get('items', [])
    videos = []
    for it in items:
        videos.append({'title': it.get('title','').strip(), 'detail_url': it.get('URL',''), 'cid': it.get('content_id','')})
    print(f"DEBUG: Found {len(videos)} videos via API")
    return videos

# Fetch sample images via ItemDetail API
def fetch_sample_images(cid: str) -> list:
    params = {'api_id': API_ID, 'affiliate_id': AFF_ID, 'site': 'video', 'service': 'amateur', 'item': cid, 'output': 'json'}
    try:
        resp = requests.get(item_detail_url, params=params, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"DEBUG: ItemDetail API failed for {cid}: {e}")
        return []
    result = resp.json().get('result', {}).get('items', [])
    samples = result[0].get('sampleImageURL', {}).get('large', []) if result else []
    return [samples] if isinstance(samples, str) else samples

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
    title = video['title']
    existing = wp.call(GetPosts({'post_status':'publish','s':title}))
    if any(p.title == title for p in existing):
        print(f"→ Skip duplicate: {title}")
        return False
    imgs = fetch_sample_images(video['cid'])
    if not imgs:
        print(f"→ No images for: {title}")
        return False
    thumb = upload_image(wp, imgs[0])
    aff = make_affiliate_link(video['detail_url'])
    parts = [f"<p><a href='{aff}' target='_blank'><img src='{imgs[0]}' alt='{title}'/></a></p>", f"<p><a href='{aff}' target='_blank'>{title}</a></p>"]
    parts += [f"<p><img src='{i}' alt='{title}'/></p>" for i in imgs[1:]]
    parts.append(f"<p><a href='{aff}' target='_blank'>{title}</a></p>")
    post = WordPressPost()
    post.title = title
    post.content = "\n".join(parts)
    post.thumbnail = thumb
    post.terms_names = {'category':['DMM動画'],'post_tag':[]}
    post.post_status = 'publish'
    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {title}")
    return True

# Main execution
def main():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job start")
    for video in fetch_latest_videos():
        if create_wp_post(video): break
    else:
        print("No new videos to post.")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished")

if __name__ == '__main__':
    main()
