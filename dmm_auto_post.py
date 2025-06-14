#!/usr/bin/env python3
import os
import sys
import requests
import textwrap
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import posts, media
from xmlrpc import client as xmlrpc_client

def fetch_latest_video(genre_id: str) -> dict | None:
    """
    Fetch the latest video from DMM API for the given genre ID.
    Returns a dict with title, detail_url, thumb, description or None if no items.
    """
    api_id = os.getenv("DMM_API_ID")
    affiliate_id = os.getenv("DMM_AFFILIATE_ID")
    if not api_id or not affiliate_id:
        print("Error: DMM_API_ID / DMM_AFFILIATE_ID not set in environment.")
        sys.exit(1)
    # Prepare API parameters
    params = {
        "api_id": api_id,
        "affiliate_id": affiliate_id,
        "site": os.getenv("DMM_SITE", "FANZA"),
        "service": "digital",
        "floor": "videoa",
        "mono_genre_id": genre_id,
        "hits": 1,
        "sort": "date",
        "output": "json"
    }
    # Call DMM API
    response = requests.get("https://api.dmm.com/affiliate/v3/ItemList", params=params)
    response.raise_for_status()
    data = response.json()
    items = data.get("result", {}).get("items", [])
    if not items:
        return None
    item = items[0]
    # Extract detail URL
    url_val = item.get("URL")
    if isinstance(url_val, dict):
        detail_url = url_val.get("item") or url_val.get("affiliate") or ""
    else:
        detail_url = url_val or ""
    # Extract thumbnail
    img_val = item.get("imageURL")
    if isinstance(img_val, dict):
        thumb = img_val.get("large") or img_val.get("small") or ""
    else:
        thumb = img_val or ""
    return {
        "title": item.get("title", ""),
        "detail_url": detail_url,
        "thumb": thumb,
        "description": item.get("description", "")
    }

def post_to_wp(item: dict):
    """
    Post the item to WordPress if not duplicated.
    """
    wp_url = os.getenv("WP_URL")
    wp_user = os.getenv("WP_USER")
    wp_pass = os.getenv("WP_PASS")
    if not (wp_url and wp_user and wp_pass):
        print("Error: WP_URL / WP_USER / WP_PASS not set in environment.")
        sys.exit(1)
    wp = Client(wp_url, wp_user, wp_pass)
    # Check duplicates
    existing = wp.call(posts.GetPosts({"post_status": "publish", "s": item["title"]}))
    if any(p.title == item["title"] for p in existing):
        print(f"→ Skipping duplicate: {item['title']}")
        return
    # Upload thumbnail
    thumb_id = None
    if item.get("thumb"):
        try:
            img_data = requests.get(item["thumb"]).content
            media_data = {
                "name": os.path.basename(item["thumb"]),
                "type": "image/jpeg",
                "bits": xmlrpc_client.Binary(img_data)
            }
            resp_media = wp.call(media.UploadFile(media_data))
            thumb_id = resp_media.get("id")
        except Exception as e:
            print(f"Warning: thumbnail upload failed: {e}")
    # Generate content
    description = item.get("description", "") or "(説明文なし)"
    summary = textwrap.shorten(description, width=200, placeholder="…")
    content = f"<p>{summary}</p>\n"
    if thumb_id:
        content += f"<p><img src=\"{item['thumb']}\" alt=\"{item['title']}\"></p>\n"
    content += f"<p><a href=\"{item['detail_url']}\" target=\"_blank\">▶ 詳細・購入はこちら</a></p>\n"
    # Publish
    post = WordPressPost()
    post.title = item["title"]
    post.content = content
    if thumb_id:
        post.thumbnail = thumb_id
    post.terms_names = {"category": ["DMM動画"], "post_tag": []}
    post.post_status = "publish"
    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {item['title']}")

def main():
    load_dotenv()
    genre_id = os.getenv("GENRE_ID", "8503")
    video = fetch_latest_video(genre_id)
    if not video:
        print("No new video found.")
        return
    post_to_wp(video)

if __name__ == "__main__":
    main()
