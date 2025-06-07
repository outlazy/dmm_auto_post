#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import collections
import collections.abc
# Compatibility patch for wordpress_xmlrpc
collections.Iterable = collections.abc.Iterable
import time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# Load environment variables
load_dotenv()
WP_URL     = os.getenv("WP_URL")
WP_USER    = os.getenv("WP_USER")
WP_PASS    = os.getenv("WP_PASS")
AFF_ID     = os.getenv("DMM_AFFILIATE_ID")
DMM_API_ID = os.getenv("DMM_API_ID")

# Validate required variables
for name, val in [("WP_URL",WP_URL),("WP_USER",WP_USER),("WP_PASS",WP_PASS),("DMM_AFFILIATE_ID",AFF_ID),("DMM_API_ID",DMM_API_ID)]:
    if not val:
        raise RuntimeError(f"Missing environment variable: {name}")

# Affiliate API endpoint and parameters
API_URL = "https://api.dmm.com/affiliate/v3/ItemList"
ITEM_PARAMS = {
    "api_id":       DMM_API_ID,
    "affiliate_id": AFF_ID,
    "site":         "FANZA",
    "service":      "digital",
    "genre_id":     "8503",  # amateur gyaru
    "availability": "1",      # only released items
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

# Fetch latest videos via API
def fetch_latest_videos():
    resp = requests.get(API_URL, params=ITEM_PARAMS, timeout=10)
    try:
        resp.raise_for_status()
    except Exception as e:
        print(f"DEBUG: API request failed: {e}")
        return []
    data = resp.json()
    items = data.get("result", {}).get("items", [])
    videos = []
    for item in items:
        # collect API image as fallback
        api_img = None
        img_info = item.get("imageURL", {})
        if img_info:
            large = img_info.get("large")
            if isinstance(large, list) and large:
                api_img = large[0]
            elif isinstance(large, str):
                api_img = large
        videos.append({
            "title":       item.get("title", "No Title"),
            "detail_url":  item.get("URL"),
            "description": item.get("description", ""),
            "actress":     [a.get("name") for a in item.get("actress", [])],
            "label":       [l.get("name") for l in item.get("label", [])],
            "genres":      [g.get("name") for g in item.get("genre", [])],
            "api_image":   api_img,
        })
    print(f"DEBUG: API returned {len(videos)} items")
    return videos

# Scrape detail page for sample images
def scrape_detail_images(detail_url: str) -> list[str]:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    session.cookies.set("ckcy","1",domain=".dmm.co.jp")
    html = session.get(detail_url, timeout=10).text
    soup = BeautifulSoup(html, "html.parser")
    imgs = []
    selectors = [
        "div#sample-image-box img",
        "img.sample-box__img",
        "li.sample-box__item img",
        "figure img",
        "img.sample-box__thumb",
        "div.sample-box img",
        "div#sampleArea img",
    ]
    for sel in selectors:
        for img in soup.select(sel):
            src = img.get("data-original") or img.get("src")
            if src and src not in imgs:
                imgs.append(src)
        if imgs:
            break
    # Fallback og:image
    if not imgs and soup.find("meta",property="og:image"):
        imgs.append(soup.find("meta",property="og:image")["content"].strip())
    print(f"DEBUG: scrape_detail_images found {len(imgs)} images for {detail_url}")
    return imgs

# Fetch sample images via Affiliate ItemDetail API
def fetch_sample_images_api(detail_url: str) -> list[str]:
    # extract cid parameter
    m = parse_qsl(urlparse(detail_url).query)
    cid = dict(m).get("cid") or detail_url.rstrip("/").split("/")[-1]
    params = {
        "api_id":       DMM_API_ID,
        "affiliate_id": AFF_ID,
        "site":         "FANZA",
        "service":      "digital",
        "item":         cid,
        "output":       "json",
    }
    url = "https://api.dmm.com/affiliate/v3/ItemDetail"
    resp = requests.get(url, params=params, timeout=10)
    try:
        resp.raise_for_status()
    except:
        print(f"DEBUG: ItemDetail API failed for {cid}")
        return []
    data = resp.json().get("result",{}).get("items",[])
    if not data:
        return []
    item = data[0]
    # sampleImageURL may be list or single
    samples = item.get("sampleImageURL", {}).get("large")
    if isinstance(samples, list):
        return samples
    if isinstance(samples, str):
        return [samples]
    return []

# Update create_wp_post to use API samples fallback


# Upload image to WordPress
def upload_image(wp: Client, url: str) -> int:
    data = requests.get(url, timeout=10).content
    name = os.path.basename(urlparse(url).path)
    media_data = {"name": name, "type": "image/jpeg", "bits": xmlrpc_client.Binary(data)}
    res = wp.call(media.UploadFile(media_data))
    return res.get("id")

# Create WordPress post
def create_wp_post(video):
    wp = Client(WP_URL, WP_USER, WP_PASS)
    title = video["title"]
    # Skip if duplicate
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if any(p.title == title for p in existing):
        print(f"→ Skipping duplicate: {title}")
        return False
    # Scrape sample images
        images = scrape_detail_images(video["detail_url"])
    if not images:
        # fallback to API sample images
        api_imgs = fetch_sample_images_api(video["detail_url"])
        if api_imgs:
            images = api_imgs
    if not images:
        print(f"→ Skipping unreleased or no-sample item: {title}")
        return False
    # Upload first image as featured
    thumb_id = upload_image(wp, images[0])
    # Build content
    aff_link = make_affiliate_link(video["detail_url"])
    parts = []
    # Featured image
    parts.append(f'<p><a href="{aff_link}" target="_blank"><img src="{images[0]}" alt="{title}"></a></p>')
    # Title link
    parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')
    # Description
    if video.get("description"):
        parts.append(f'<div>{video["description"]}</div>')
    # Additional sample images
    for img in images[1:]:
        parts.append(f'<p><img src="{img}" alt="{title}"></p>')
    # Final affiliate link
    parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')
    # Set tags: actress, label, genre
    tags = []
    tags.extend(video.get("actress", []))
    tags.extend(video.get("label", []))
    tags.extend(video.get("genres", []))
    # Deduplicate tags preserving order
    seen = set()
    unique_tags = []
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            unique_tags.append(t)
    # Create post object
    post = WordPressPost()
    post.title = title
    post.content = "\n".join(parts)
    post.thumbnail = thumb_id
    post.terms_names = {"category": ["DMM動画"], "post_tag": unique_tags}
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
