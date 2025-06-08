#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

# ==== 設定 ====
WP_CATEGORY = "DMM素人動画"
WP_TAGS = []
BASE_URL = "https://video.dmm.co.jp"
LIST_URL = BASE_URL + "/amateur/list/?sort=date"
MAX_POST = 5
SELENIUM_WAIT = 7  # 秒

# .envからWordPress情報とDMMアフィリエイトIDを取得
load_dotenv()
WP_URL     = os.getenv("WP_URL")
WP_USER    = os.getenv("WP_USER")
WP_PASS    = os.getenv("WP_PASS")
AFF_ID     = os.getenv("DMM_AFFILIATE_ID")

def make_affiliate_link(detail_url):
    if not AFF_ID:
        return detail_url
    if "affiliate_id" in detail_url:
        return detail_url
    delim = "&" if "?" in detail_url else "?"
    return f"{detail_url}{delim}affiliate_id={AFF_ID}"

def is_released(release_date_str):
    try:
        today = datetime.today().date()
        rel = datetime.strptime(release_date_str, "%Y-%m-%d").date()
        return rel <= today
    except:
        return False

def fetch_video_list_selenium():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    # chromedriverのパスが通っていればこれでOK
    driver = webdriver.Chrome(options=chrome_options)
    driver.get(LIST_URL)
    time.sleep(SELENIUM_WAIT)  # ページの動的生成が完了するまで待つ
    html = driver.page_source
    driver.quit()
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for box in soup.select("li.list-box"):
        a_tag = box.select_one("a")
        if not a_tag:
            continue
        href = a_tag["href"]
        detail_url = BASE_URL + href if href.startswith("/") else href
        title = (box.select_one(".list-box__title") or {}).get_text(strip=True)
        date_tag = box.select_one(".list-box__release-date")
        rel_date = ""
        if date_tag:
            rel_date = date_tag.get_text(strip=True).replace("配信開始日：", "").replace("発売日：", "").split()[0]
        if not (title and detail_url and rel_date):
            continue
        items.append({"title": title, "detail_url": detail_url, "release_date": rel_date})
    print(f"DEBUG: video items found: {len(items)}")
    return items

def fetch_video_detail(detail_url):
    resp = requests.get(detail_url, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")
    # サンプル画像
    imgs = []
    for img in soup.select("div.p-slider__item img, div#sample-video-img img"):
        src = img.get("src")
        if src and src.startswith("https://"):
            imgs.append(src)
    # 商品説明
    desc = ""
    desc_tag = soup.select_one("section#introduction, div.introduction__text, div.p-work-information__txt, .p-introduction__text, #work-introduction")
    if desc_tag:
        desc = desc_tag.get_text(strip=True)
    return imgs, desc

def upload_image(wp, url):
    try:
        data = requests.get(url, timeout=15).content
        name = os.path.basename(url)
        media_data = {
            "name": name,
            "type": "image/jpeg",
            "bits": xmlrpc_client.Binary(data)
        }
        res = wp.call(media.UploadFile(media_data))
        return res.get("id")
    except Exception as e:
        print(f"画像アップロード失敗: {e}")
        return None

def create_wp_post(wp, video, images, desc):
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
    if desc:
        parts.append(f'<div>{desc}</div>')
    for img in images[1:]:
        parts.append(f'<p><img src="{img}" alt="{title}"></p>')
    parts.append(f'<p><a href="{aff}" target="_blank">{title}</a></p>')
    post = WordPressPost()
    post.title = title
    post.content = "\n".join(parts)
    if thumb_id:
        post.thumbnail = thumb_id
    post.terms_names = {"category": [WP_CATEGORY], "post_tag": WP_TAGS}
    post.post_status = "publish"
    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {title}")
    return True

def main():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job start")
    videos = fetch_video_list_selenium()
    if not videos:
        print("No new videos to post.")
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished")
        return
    wp = Client(WP_URL, WP_USER, WP_PASS)
    count = 0
    for video in videos:
        if not is_released(video["release_date"]):
            continue
        imgs, desc = fetch_video_detail(video["detail_url"])
        if not imgs:
            print(f"→ No sample images for: {video['title']}, skipping.")
            continue
        if create_wp_post(wp, video, imgs, desc):
            count += 1
        if count >= MAX_POST:
            break
    if count == 0:
        print("No new videos to post.")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished")

if __name__ == "__main__":
    main()
