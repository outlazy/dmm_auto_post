#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import collections
import collections.abc
# WordPress XML-RPC compatibility patch
collections.Iterable = collections.abc.Iterable

import os
import time
import re
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# ───────────────────────────────────────────────────────────
# Load environment variables & constants
# ───────────────────────────────────────────────────────────
load_dotenv()
WP_URL     = os.getenv("WP_URL")
WP_USER    = os.getenv("WP_USER")
WP_PASS    = os.getenv("WP_PASS")
AFF_ID     = os.getenv("DMM_AFFILIATE_ID")
DMM_API_ID = os.getenv("DMM_API_ID")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
LIST_URL   = "https://video.dmm.co.jp/amateur/list/?sort=date"
MAX_ITEMS  = 10  # Number of videos to fetch

# Validate required variables
for name, v in [("WP_URL", WP_URL), ("WP_USER", WP_USER), ("WP_PASS", WP_PASS), ("DMM_AFFILIATE_ID", AFF_ID)]:
    if not v:
        raise RuntimeError(f"Missing environment variable: {name}")

# ───────────────────────────────────────────────────────────
# Helper functions
# ───────────────────────────────────────────────────────────
def make_affiliate_link(url: str) -> str:
    p = urlparse(url)
    qs = dict(parse_qsl(p.query))
    qs["affiliate_id"] = AFF_ID
    return urlunparse((p.scheme, p.netloc, p.path, p.params, urlencode(qs), p.fragment))


def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    s.cookies.set("ckcy", "1", domain=".dmm.co.jp")
    s.cookies.set("ckcy", "1", domain="video.dmm.co.jp")
    return s


def get_page_html(session: requests.Session, url: str) -> str:
    # Fetch page and handle age-check if presented
    resp = session.get(url, timeout=10)
    if "/age_check" in resp.url or "/security_check" in resp.url or "I Agree" in resp.text:
        soup = BeautifulSoup(resp.text, "html.parser")
        agree = soup.find("a", string=lambda t: t and "I Agree" in t)
        if agree and agree.get("href"):
            resp = session.get(agree["href"], timeout=10)
    resp.raise_for_status()
    return resp.text


def abs_url(href: str) -> str:
    if href.startswith("//"): return f"https:{href}"
    if href.startswith("/"):  return f"https://video.dmm.co.jp{href}"
    return href

# ───────────────────────────────────────────────────────────
# Fetch latest videos list via DMM Affiliate API or fallback
# ───────────────────────────────────────────────────────────
def fetch_listed_videos(limit: int):
    # Use API if available
    if DMM_API_ID:
        fl_params = {
            "api_id":        DMM_API_ID,
            "affiliate_id":  AFF_ID,
            "site":          "video",
            "service":       "amateur",
            "output":        "json",
        }
        try:
            fl = requests.get("https://api.dmm.com/affiliate/v3/floorList", params=fl_params, timeout=10)
            fl.raise_for_status()
            floor_list = fl.json().get("result", {}).get("floor", [])
            floor_id = floor_list[0].get("floorId") if floor_list else None
        except:
            floor_id = None

        if floor_id:
            il_params = {
                "api_id":        DMM_API_ID,
                "affiliate_id":  AFF_ID,
                "site":          "video",
                "service":       "amateur",
                "floorId":       floor_id,
                "hits":          limit,
                "sort":          "date",
                "output":        "json",
            }
            try:
                il = requests.get("https://api.dmm.com/affiliate/v3/ItemList", params=il_params, timeout=10)
                il.raise_for_status()
                items = il.json().get("result", {}).get("items", [])
                videos = [{"title": itm.get("title", "No Title"), "detail_url": itm.get("URL")} for itm in items]
                print(f"DEBUG: fetch_listed_videos found {len(videos)} items via DMM API")
                return videos
            except:
                pass

        # 3) Fallback: extract detail URLs via regex
    session = get_session()
    html = get_page_html(session, LIST_URL)
    seen = set()
    videos = []
    # regex to find /amateur/detail/... links
    for match in re.findall(r'href="(/amateur/detail/[^"]+?)"', html):
        url = abs_url(match)
        if url in seen:
            continue
        seen.add(url)
        videos.append({"title": None, "detail_url": url})
        if len(videos) >= limit:
            break
    print(f"DEBUG: fetch_listed_videos found {len(videos)} items via regex fallback")
    return videos

# ───────────────────────────────────────────────────────────
# Scrape detail page
# ───────────────────────────────────────────────────────────
def scrape_detail(url: str):
    session = get_session()
    html = get_page_html(session, url)
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else (soup.find("meta", property="og:title")["content"].strip() if soup.find("meta", property="og:title") else "No Title")
    d = soup.find("div", class_="mg-b20 lh4") or soup.find("p", id="sample-description")
    desc = d.get_text(" ", strip=True) if d else ""
    imgs = []
    for sel in ("div#sample-image-box img", "img.sample-box__img", "li.sample-box__item img"):  
        for img in soup.select(sel):
            src = img.get("data-original") or img.get("src")
            if src and src not in imgs: imgs.append(src)
        if imgs: break
    if not imgs and soup.find("meta", property="og:image"):
        imgs.append(soup.find("meta", property="og:image")["content"].strip())
    print(f"DEBUG: scrape_detail for {url} yielded {len(imgs)} images")
    return title, desc, imgs

# ───────────────────────────────────────────────────────────
# Upload image to WP
# ───────────────────────────────────────────────────────────
def upload_image(wp: Client, url: str) -> int:
    data = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10).content
    name = os.path.basename(url.split("?")[0])
    media_data = {"name": name, "type": "image/jpeg", "bits": xmlrpc_client.Binary(data)}
    res = wp.call(media.UploadFile(media_data))
    return res.get("id")

# ───────────────────────────────────────────────────────────
# Create WP post
# ───────────────────────────────────────────────────────────
def create_wp_post(title: str, desc: str, imgs: list[str], detail_url: str) -> bool:
    wp = Client(WP_URL, WP_USER, WP_PASS)
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if any(p.title == title for p in existing):
        print(f"→ Skipping duplicate: {title}")
        return False
    thumb_id = None
    if imgs:
        try: thumb_id = upload_image(wp, imgs[0])
        except Exception as e: print(f"Thumbnail upload failed: {e}")
    aff = make_affiliate_link(detail_url)
    parts = []
    if imgs: parts.append(f'<p><a href="{aff}" target="_blank"><img src="{imgs[0]}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{aff}" target="_blank">{title}</a></p>')
    parts.append(f'<div>{desc}</div>')
    for img in imgs[1:]: parts.append(f'<p><img src="{img}" alt="{title}"></p>')
    if imgs: parts.append(f'<p><a href=\"{aff}\" target=\"_blank\"><img src=\"{imgs[0]}\" alt=\"{title}\"></a></p>')
    parts.append(f'<p><a href=\"{aff}\" target=\"_blank\">{title}</a></p>')
    post = WordPressPost(); post.title=title; post.content="\n".join(parts)
    if thumb_id: post.thumbnail=thumb_id
    post.terms_names={"category":["DMM動画"],"post_tag":[]}; post.post_status="publish"
    wp.call(posts.NewPost(post)); print(f"✔ Posted: {title}"); return True

# ───────────────────────────────────────────────────────────
# Main job
# ───────────────────────────────────────────────────────────
def job():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job start")
    videos = fetch_listed_videos(MAX_ITEMS)
    if not videos:
        print("No videos found to post.")
    else:
        title, desc, imgs = scrape_detail(videos[0]["detail_url"])
        create_wp_post(title, desc, imgs, videos[0]["detail_url"])
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished")

if __name__ == "__main__": job()
