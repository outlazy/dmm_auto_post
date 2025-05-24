```python
#!/usr/bin/env python3
# fetch_and_post.py

import os
import time
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.compat import xmlrpc_client

# ───────────────────────────────────────────────────────────
# 環境変数読み込み
# ───────────────────────────────────────────────────────────
load_dotenv()
AFFILIATE_ID = os.getenv("DMM_AFFILIATE_ID")
WP_URL       = os.getenv("WP_URL")
WP_USER      = os.getenv("WP_USER")
WP_PASS      = os.getenv("WP_PASS")
HITS         = int(os.getenv("HITS", 5))
USER_AGENT   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
TODAY        = datetime.now().date()

# 検索対象ページ(URL) リスト（人気順）
LIST_URLS = [
    "https://video.dmm.co.jp/amateur/list/?genre=8503&sort=ranking",
    "https://video.dmm.co.jp/av/list/?genre=1034&i3_ref=list&dmmref=video_list_genre_1034&sort=ranking"
]

if not AFFILIATE_ID:
    raise RuntimeError("環境変数 DMM_AFFILIATE_ID が設定されていません")

# ───────────────────────────────────────────────────────────
# 詳細ページから「紹介文」と「発売日」を取得
# ───────────────────────────────────────────────────────────
def fetch_detail(detail_url, session):
    headers = {"User-Agent": USER_AGENT}
    res = session.get(detail_url, headers=headers)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "lxml")
    # 説明文
    desc_el = soup.select_one("#module-video-intro .text") or soup.select_one(".text")
    description = desc_el.get_text(strip=True) if desc_el else "(説明文なし)"
    # 発売日
    release_date = None
    dl = soup.find("dl", class_="release")
    if dl:
        dd = dl.find("dd")
        try:
            release_date = datetime.strptime(dd.get_text(strip=True), "%Y-%m-%d").date()
        except:
            pass
    return description, release_date

# ───────────────────────────────────────────────────────────
# ページスクレイピングで動画リスト取得
# ───────────────────────────────────────────────────────────
def fetch_videos_from_pages(urls):
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    videos = []

    for url in urls:
        print(f"=== Fetching URL: {url}")
        res = session.get(url)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "lxml")
        cards = (soup.select(".list-inner .item") or
                 soup.select(".list-box li") or
                 soup.select("ul.search-list > li"))[:HITS]

        for idx, card in enumerate(cards, start=1):
            a = card.find("a", href=True)
            if not a:
                continue
            link = a["href"]
            title = a.get("title") or a.get_text(strip=True)
            # サムネイル
            img = card.find("img")
            img_url = img.get("data-src") or img.get("src") or ""
            if not img_url:
                print(f"[Warning] No thumbnail for '{title}', skipping")
                continue
            # 詳細
            description, release_date = fetch_detail(link, session)
            if release_date and release_date > TODAY:
                print(f"[Skip] '{title}' は発売前 ({release_date})")
                continue
            # サンプル画像
            samples = []
            sample_tags = card.select(".sample-wrap img")
            for st in sample_tags:
                s_url = st.get("data-src") or st.get("src")
                if s_url:
                    samples.append(s_url)

            # アフィリエイトリンク
            aff_url = f"{link}?i3_ref=list&i3_ord={idx}&affiliate_id={AFFILIATE_ID}"

            videos.append({
                "title":       title.strip(),
                "url":         aff_url,
                "image_url":   img_url,
                "description": description,
                "samples":     samples,
                "genres":      [],  # 必要に応じて追加
                "actors":      []
            })
            print(f"  ■ Fetched [{idx}]: {title}")
            time.sleep(1)
    print(f"=== Total fetched {len(videos)} videos ===")
    return videos

# ───────────────────────────────────────────────────────────
# WordPressに投稿
# ───────────────────────────────────────────────────────────
def post_to_wp(item: dict):
    print(f"--> Posting: {item['title']}")
    wp = Client(WP_URL, WP_USER, WP_PASS)
    # サムネイル
    img_data = requests.get(item["image_url"]).content
    data = {
        "name": os.path.basename(item["image_url"]),
        "type": "image/jpeg",
        "bits": xmlrpc_client.Binary(img_data)
    }
    media_item = media.UploadFile(data)
    resp = wp.call(media_item)
    attach_url = resp["url"]
    attach_id  = resp["id"]

    # HTML組み立て
    html = [f'<p><a href="{item['url']}" target="_blank">'
            f'<img src="{attach_url}" alt="{item['title']}"/></a></p>',
            f'<p>{item['description']}</p>']
    # サンプル画像を本文下側、リンクの上に
    for s in item.get("samples", []):
        html.append(f'<p><img src="{s}" alt="サンプル画像"/></p>')
    html.append(f'<p><a href="{item['url']}" target="_blank">▶ 詳細・購入はこちら</a></p>')

    post = WordPressPost()
    post.title       = item['title']
    post.content     = "\n".join(html)
    post.thumbnail   = attach_id
    post.terms_names = {"category": ["DMM動画","AV"], "post_tag": []}
    post.post_status = "publish"
    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {item['title']}")

# ───────────────────────────────────────────────────────────
# メイン
# ───────────────────────────────────────────────────────────
def main():
    print("=== Job start ===")
    videos = fetch_videos_from_pages(LIST_URLS)
    for vid in videos:
        try:
            post_to_wp(vid)
            time.sleep(1)
        except Exception as e:
            print(f"✖ Error posting '{vid['title']}': {e}")
    print("=== Job finished ===")

if __name__ == "__main__":
    main()
```
