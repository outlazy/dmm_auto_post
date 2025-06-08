#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import requests
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client

load_dotenv()
WP_URL     = os.getenv("WP_URL")
WP_USER    = os.getenv("WP_USER")
WP_PASS    = os.getenv("WP_PASS")
AFF_ID     = os.getenv("DMM_AFFILIATE_ID")
API_ID     = os.getenv("DMM_API_ID")
MAX_POST   = 5

LIST_API = "https://video.dmm.co.jp/api/v1/amateur/list"
DETAIL_API = "https://api.dmm.com/affiliate/v3/ItemDetail"
GENRE_HTML = "https://video.dmm.co.jp/amateur/list/?genre=8503"

def make_affiliate_link(url):
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs["affiliate_id"] = AFF_ID
    new_query = urlencode(qs)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

def fetch_latest_videos():
    """API優先・HTML fallback方式で動画リスト取得"""
    # 1. API方式（推奨、だめならHTML fallback）
    params = {
        "genre": "8503",
        "sort": "date",
        "offset": 1,
        "limit": MAX_POST,
    }
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    try:
        resp = session.get(LIST_API, params=params, timeout=10)
        print(f"DEBUG: API status={resp.status_code}")
        if resp.status_code == 200:
            try:
                data = resp.json()
                if data.get("contents"):
                    videos = []
                    for item in data.get("contents", []):
                        cid = item.get("cid")
                        detail_url = f"https://www.dmm.co.jp/digital/videoc/-/detail/=/cid={cid}/"
                        videos.append({
                            "title": item.get("title"),
                            "cid": cid,
                            "detail_url": detail_url,
                            "description": item.get("description") or "",
                        })
                    print(f"DEBUG: DMM API scraping found {len(videos)} items")
                    return videos
            except Exception as e:
                print(f"DEBUG: API JSON decode failed: {e}")
        else:
            print(f"DEBUG: API failed status={resp.status_code}")
    except Exception as e:
        print(f"DEBUG: API request error: {e}")

    # 2. HTML fallback（SPA構造対応。初回ロード時のスニペットJSON抽出）
    print("DEBUG: Falling back to HTML parsing.")
    videos = []
    try:
        resp = session.get(GENRE_HTML, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        # Next.js埋め込みスクリプトからJSON部分を抽出
        for script in soup.find_all("script"):
            if "__NEXT_DATA__" in (script.get("id") or ""):
                import json
                data = json.loads(script.string)
                # データ構造は変動あり、各自で適宜調整！
                items = (data.get("props", {}).get("pageProps", {}).get("contents", []) or
                         data.get("props", {}).get("pageProps", {}).get("videos", []))
                for item in items[:MAX_POST]:
                    cid = item.get("cid")
                    detail_url = f"https://www.dmm.co.jp/digital/videoc/-/detail/=/cid={cid}/"
                    videos.append({
                        "title": item.get("title"),
                        "cid": cid,
                        "detail_url": detail_url,
                        "description": item.get("description") or "",
                    })
                print(f"DEBUG: HTML fallback found {len(videos)} items")
                break
    except Exception as e:
        print(f"DEBUG: HTML fallback failed: {e}")
    return videos

def fetch_sample_images(cid):
    params = {
        "api_id": API_ID,
        "affiliate_id": AFF_ID,
        "site": "video",
        "service": "amateur",
        "item": cid,
        "output": "json",
    }
    resp = requests.get(DETAIL_API, params=params, timeout=10)
    try:
        resp.raise_for_status()
        items = resp.json().get("result", {}).get("items", [])
    except Exception as e:
        print(f"DEBUG: ItemDetail API failed for {cid}: {e}")
        return []
    if not items:
        return []
    samples = items[0].get("sampleImageURL", {}).get("large")
    if isinstance(samples, list):
        return samples
    if isinstance(samples, str):
        return [samples]
    return []

def upload_image(wp, url):
    data = requests.get(url, timeout=10).content
    name = os.path.basename(urlparse(url).path)
    media_data = {"name": name, "type": "image/jpeg", "bits": xmlrpc_client.Binary(data)}
    res = wp.call(media.UploadFile(media_data))
    return res.get("id")

def create_wp_post(video):
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
    post.terms_names = {"category": ["DMM動画"], "post_tag": []}
    post.post_status = "publish"
    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {title}")
    return True

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
