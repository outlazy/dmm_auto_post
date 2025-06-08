#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import collections
import collections.abc
collections.Iterable = collections.abc.Iterable

import os
import time
import requests
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client

# -- 各種ワード設定 --
SIRUTO_KEYWORDS = ["素人", "ナンパ", "投稿", "ハメ撮り", "初撮", "自撮り"]
NG_WORDS = ["デビュー", "専属", "女優", "企画", "S1", "MOODYZ", "アイポケ", "kawaii", "専属"]

# -- 設定・API --
load_dotenv()
WP_URL     = os.getenv("WP_URL")
WP_USER    = os.getenv("WP_USER")
WP_PASS    = os.getenv("WP_PASS")
AFF_ID     = os.getenv("DMM_AFFILIATE_ID")
API_ID     = os.getenv("DMM_API_ID")
MAX_CHECK  = 30    # 最大チェック件数
POST_LIMIT = 1     # 投稿件数

ITEMLIST_API = "https://api.dmm.com/affiliate/v3/ItemList"
DETAIL_API   = "https://api.dmm.com/affiliate/v3/ItemDetail"

GENRE_ID = "5026"     # 素人ジャンルID
FLOOR_ID = "videoa"   # アダルト動画フロア

def make_affiliate_link(url):
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs["affiliate_id"] = AFF_ID
    new_query = urlencode(qs)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

def is_true_amateur(title, desc):
    text = (title or "") + " " + (desc or "")
    # 1つでも素人ワードが含まれ、かつNGワードが含まれない場合のみ採用
    if not any(w in text for w in SIRUTO_KEYWORDS):
        return False
    if any(w in text for w in NG_WORDS):
        return False
    return True

def fetch_latest_videos():
    params = {
        "api_id":       API_ID,
        "affiliate_id": AFF_ID,
        "site":         "FANZA",
        "service":      "digital",
        "floor_id":     FLOOR_ID,
        "genre_id":     GENRE_ID,
        "hits":         MAX_CHECK,
        "sort":         "date",
        "output":       "json",
        "availability": "1",  # 発売済のみ
    }
    resp = requests.get(ITEMLIST_API, params=params, timeout=10)
    print(f"DEBUG: API status={resp.status_code}")
    print(f"DEBUG: API url={resp.url}")
    videos = []
    if resp.status_code == 200:
        items = resp.json().get("result", {}).get("items", [])
        for item in items:
            title = item.get("title") or ""
            desc = item.get("description") or ""
            if is_true_amateur(title, desc):
                cid = item.get("content_id") or item.get("cid")
                detail_url = item.get("URL")
                videos.append({
                    "title": title,
                    "cid": cid,
                    "detail_url": detail_url,
                    "description": desc,
                })
        print(f"DEBUG: API filtered {len(videos)} 真・素人 items")
    else:
        print(f"DEBUG: API failed. Body={resp.text[:200]}")
    return videos

def fetch_sample_images(cid):
    params = {
        "api_id": API_ID,
        "affiliate_id": AFF_ID,
        "site": "FANZA",
        "service": "digital",
        "floor_id": FLOOR_ID,
        "cid": cid,
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

def create_wp_post(wp, video, images):
    title = video["title"]
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if any(p.title == title for p in existing):
        print(f"→ Skipping duplicate: {title}")
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
    post.terms_names = {"category": ["DMM素人動画"], "post_tag": []}
    post.post_status = "publish"
    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {title}")
    return True

def main():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job start")
    videos = fetch_latest_videos()
    wp = Client(WP_URL, WP_USER, WP_PASS)
    post_count = 0
    for video in videos:
        images = fetch_sample_images(video["cid"])
        if not images:
            print(f"→ No samples for: {video['title']}, skipping.")
            continue
        if create_wp_post(wp, video, images):
            post_count += 1
        if post_count >= POST_LIMIT:
            break
    if post_count == 0:
        print("No new videos to post.")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished")

if __name__ == "__main__":
    main()
