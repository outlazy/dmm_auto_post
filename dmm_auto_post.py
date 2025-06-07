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
    "site":         "video",     # Amateur content site
    "service":      "amateur",   # Amateur service
    "genre_id":     "8503",      # amateur gyaru
    "availability": "1",         # only released items
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

# Fetch latest videos via HTML scraping of genre page
def fetch_latest_videos(limit: int = 10):
    """
    Scrape the amateur gyaru genre page directly to get latest released videos.
    """
    GENRE_URL = "https://video.dmm.co.jp/amateur/list/?genre=8503"
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    session.cookies.set("ckcy","1",domain=".dmm.co.jp")
    resp = session.get(GENRE_URL, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    videos = []
    for li in soup.select("li.list-box")[:limit]:
        a = li.find("a", class_="tmb")
        if not a or not a.get("href"):
            continue
        url = a["href"] if a["href"].startswith("http") else f"https://video.dmm.co.jp{a['href']}"
        title = a.img.get("alt", "").strip() if a.img and a.img.get("alt") else a.get("title") or li.find("p", class_="title").get_text(strip=True)
        videos.append({"title": title, "detail_url": url})
    print(f"DEBUG: HTML scraping returned {len(videos)} items from genre page")
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
    if not imgs and soup.find("meta",property="og:image"):
        imgs.append(soup.find("meta",property="og:image")["content"].strip())
    print(f"DEBUG: scrape_detail_images found {len(imgs)} images for {detail_url}")
    return imgs

# Fetch sample images via Affiliate ItemDetail API
def fetch_sample_images_api(detail_url: str) -> list[str]:
    cid = dict(parse_qsl(urlparse(detail_url).query)).get("cid") or detail_url.rstrip("/").split("/")[-1]
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
    samples = item.get("sampleImageURL", {}).get("large")
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

# Create WordPress post
def create_wp_post(video):
    wp = Client(WP_URL, WP_USER, WP_PASS)
    title = video["title"]
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if any(p.title == title for p in existing):
        print(f"→ Skipping duplicate: {title}")
        return False
    images = scrape_detail_images(video["detail_url"])
    if not images:
        api_imgs = fetch_sample_images_api(video["detail_url"])
        if api_imgs:
            images = api_imgs
    if not images:
        print(f"→ Skipping unreleased or no-sample item: {title}")
        return False
    thumb_id = upload_image(wp, images[0])
    aff_link = make_affiliate_link(video["detail_url"])
    parts = []
    parts.append(f'<p><a href="{aff_link}" target="_blank"><img src="{images[0]}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')
    if video.get("description"):
        parts.append(f'<div>{video["description"]}</div>')
    for img in images[1:]:
        parts.append(f'<p><img src="{img}" alt="{title}"></p>')
    parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')
    tags = []
    tags.extend(video.get("actress", []))
    tags.extend(video.get("label", []))
    tags.extend(video.get("genres", []))
    seen = set()
    unique_tags = []
    for t in tags:
        if t and t not in seen:
            seen.add(t)
            unique_tags.append(t)
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
