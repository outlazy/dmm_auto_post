#!/usr/bin/env python3
# fetch_and_post.py

import os
import time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts

# 環境変数読み込み (.env または GitHub Secrets)
load_dotenv()

# 設定
AFFILIATE_ID = os.getenv("DMM_AFFILIATE_ID")
LIST_URL     = "https://video.dmm.co.jp/av/list/?genre=1034"
HITS         = int(os.getenv("HITS", 5))
USER_AGENT   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

def fetch_page(url: str, session: requests.Session) -> requests.Response:
    """ページ取得（年齢確認フォーム対応付き）"""
    headers = {"User-Agent": USER_AGENT}
    res = session.get(url, headers=headers)
    if "age_check" in res.url:
        # 年齢確認フォームをサブミット
        soup = BeautifulSoup(res.text, "lxml")
        form = soup.find("form")
        action = form["action"]
        data = {inp["name"]: inp.get("value", "") for inp in form.find_all("input")}
        session.post(action, data=data, headers=headers)
        res = session.get(url, headers=headers)
    res.raise_for_status()
    return res

def fetch_videos_from_html() -> list[dict]:
    """一覧ページをスクレイピングし、詳細ページも回って metadata 取得"""
    print("=== Start fetching videos ===")
    session = requests.Session()
    listing = fetch_page(LIST_URL, session)
    soup = BeautifulSoup(listing.text, "lxml")
    cards = soup.select(".list-inner .item")[:HITS]
    items = []

    for idx, card in enumerate(cards, start=1):
        a = card.select_one("a")
        link = a["href"]
        title = a.get("title") or card.select_one(".ttl").get_text(strip=True)
        img = card.select_one("img")
        img_url = img.get("data-src") or img.get("src")

        # 詳細ページ取得
        detail = fetch_page(link, session)
        ds = BeautifulSoup(detail.text, "lxml")

        # ジャンル・出演者抽出
        genres, actors = [], []
        for li in ds.select(".mg-b20 li"):
            label = li.select_one(".label").get_text(strip=True)
            text  = li.get_text(strip=True).replace(label, "").strip()
            if "ジャンル" in label:
                genres = [g.strip() for g in text.split(",")]
            elif "出演" in label or "女優" in label:
                actors = [a.strip() for a in text.split(",")]

        # 説明文抽出
        desc_el = ds.select_one("#module-video-intro .text") or ds.select_one(".text")
        description = desc_el.get_text(strip=True) if desc_el else ""

        # アフィリエイトリンク生成
        aff_url = f"{link}?i3_ref=list&i3_ord={idx}&affiliate_id={AFFILIATE_ID}"

        items.append({
            "title":       title,
            "url":         aff_url,
            "image_url":   img_url,
            "description": description,
            "genres":      genres or ["ジャンル1034"],
            "actors":      actors
        })

        print(f"  ■ Fetched [{idx}]: {title}")
        time.sleep(1)  # サイト負荷軽減

    print(f"=== Finished fetching {len(items)} videos ===")
    return items

def post_to_wp(item: dict):
    """WordPress に投稿（画像アップロード→投稿作成）"""
    print(f"--> Posting: {item['title']}")
    client = Client(
        os.getenv("WP_URL"),
        os.getenv("WP_USER"),
        os.getenv("WP_PASS")
    )

    # 画像アップロード
    img_data = requests.get(item["image_url"], headers={"User-Agent": USER_AGENT}).content
    data = {
        "name": os.path.basename(item["image_url"]),
        "type": "image/jpeg"
    }
    media_item = media.UploadFile(data, img_data)
    res = client.call(media_item)

    # 投稿作成
    post = WordPressPost()
    post.title = item["title"]
    post.content = (
        f'<p><a href="{item["url"]}" target="_blank">'
        f'<img src="{res.url}" alt="{item["title"]}"></a></p>'
        f'<p>{item["description"]}</p>'
        f'<p><a href="{item["url"]}" target="_blank">▶ 詳細・購入はこちら</a></p>'
    )
    post.thumbnail = res.id
    post.terms_names = {
        "category": ["DMM動画", "AV"],
        "post_tag": item["genres"] + item["actors"]
    }
    post.post_status = "publish"

    client.call(posts.NewPost(post))
    print(f"✔ Posted: {item['title']}")

def main():
    print("=== Job start ===")
    videos = fetch_videos_from_html()
    for vid in videos:
        try:
            post_to_wp(vid)
        except Exception as e:
            print(f"✖ Error posting {vid['title']}: {e}")
    print("=== Job finished ===")

if __name__ == "__main__":
    main()
