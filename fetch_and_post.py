#!/usr/bin/env python3
# fetch_and_post.py

import os
import time
import requests
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts

# ───────────────────────────────────────────────────────────
# 環境変数読み込み
# ───────────────────────────────────────────────────────────
load_dotenv()
AFFILIATE_ID = os.getenv("DMM_AFFILIATE_ID")
WP_URL       = os.getenv("WP_URL")
WP_USER      = os.getenv("WP_USER")
WP_PASS      = os.getenv("WP_PASS")
LIST_URL     = "https://video.dmm.co.jp/av/list/?genre=1034"
HITS         = int(os.getenv("HITS", 5))
USER_AGENT   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# ───────────────────────────────────────────────────────────
# ページ取得＋年齢確認バイパス
# ───────────────────────────────────────────────────────────
def fetch_page(url: str, session: requests.Session) -> requests.Response:
    headers = {"User-Agent": USER_AGENT}
    res = session.get(url, headers=headers)
    # 年齢確認リダイレクト時
    if "age_check" in res.url:
        soup = BeautifulSoup(res.text, "lxml")
        # 1) <form> があれば submit
        form = soup.find("form")
        if form and form.get("action"):
            action = form["action"]
            data = {inp["name"]: inp.get("value", "") for inp in form.find_all("input") if inp.get("name")}
            session.post(action, data=data, headers=headers)
        else:
            # 2) 「I Agree/同意する」画像リンクを探してクリック
            img = soup.find("img", alt=lambda v: v and ("I Agree" in v or "同意する" in v))
            if img:
                a = img.find_parent("a", href=True)
                if a:
                    session.get(a["href"], headers=headers)
                else:
                    print(f"[Warning] Agree link parent not found on age_check page")
            else:
                print(f"[Warning] age_check bypass element not found on {url}")
        # 再取得
        res = session.get(url, headers=headers)
    res.raise_for_status()
    return res

# ───────────────────────────────────────────────────────────
# 動画一覧＆詳細スクレイピング
# ───────────────────────────────────────────────────────────
def fetch_videos_from_html() -> list[dict]:
    print("=== Start fetching videos ===")
    session = requests.Session()
    # 年齢認証バイパス含めて一覧取得
    listing = fetch_page(LIST_URL, session)
    soup = BeautifulSoup(listing.text, "lxml")
    cards = soup.select(".list-inner .item")[:HITS]
    if not cards:
        cards = soup.select("ul.search-list li")[:HITS]  # 別セレクタ例
    items = []

    for idx, card in enumerate(cards, start=1):
        a = card.find("a", href=True)
        if not a: continue
        link = a["href"]
        title = a.get("title") or a.get_text(strip=True)
        img = card.find("img")
        if not img: continue
        img_url = img.get("data-src") or img.get("src")

        # 詳細ページ取得
        detail = fetch_page(link, session)
        ds = BeautifulSoup(detail.text, "lxml")

        # ジャンル・出演者
        genres, actors = [], []
        for li in ds.select(".mg-b20 li"):
            label = li.select_one(".label").get_text(strip=True)
            text  = li.get_text(strip=True).replace(label, "").strip()
            if "ジャンル" in label:
                genres = [g.strip() for g in text.split(",") if g.strip()]
            elif any(k in label for k in ("出演", "女優")):
                actors = [a.strip() for a in text.split(",") if a.strip()]

        # 説明文
        desc_el = ds.select_one("#module-video-intro .text") or ds.select_one(".text")
        description = desc_el.get_text(strip=True) if desc_el else ""

        # アフィリエイトリンク
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
        time.sleep(1)

    print(f"=== Finished fetching {len(items)} videos ===")
    return items

# ───────────────────────────────────────────────────────────
# WordPress へ投稿
# ───────────────────────────────────────────────────────────
def post_to_wp(item: dict):
    print(f"--> Posting: {item['title']}")
    client = Client(WP_URL, WP_USER, WP_PASS)

    # サムネイルアップロード
    img_data = requests.get(item["image_url"], headers={"User-Agent": USER_AGENT}).content
    data = {"name": os.path.basename(item["image_url"]), "type": "image/jpeg"}
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

# ───────────────────────────────────────────────────────────
# エントリポイント
# ───────────────────────────────────────────────────────────
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
