#!/usr/bin/env python3
# fetch_and_post.py

import os
import collections
import collections.abc
# Python3.10+ では collections.Iterable が collections.abc.Iterable に移動したため、互換性を確保
collections.Iterable = collections.abc.Iterable
from bs4 import BeautifulSoup

# ───────────────────────────────────────────────────────────
# 環境変数読み込み
# ───────────────────────────────────────────────────────────
load_dotenv()
WP_URL    = os.getenv("WP_URL")
WP_USER   = os.getenv("WP_USER")
WP_PASS   = os.getenv("WP_PASS")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
MAX_ITEMS  = int(os.getenv("HITS", 5))  # 環境変数 HITS を使用して件数指定

if not WP_URL or not WP_USER or not WP_PASS:
    raise RuntimeError("環境変数 WP_URL / WP_USER / WP_PASS が設定されていません")

# ───────────────────────────────────────────────────────────
# DMM API で最新アマチュア動画を取得
# ───────────────────────────────────────────────────────────

def fetch_latest_videos(max_items: int):
    """HTMLスクレイピングで最新のアマチュア動画を取得"""
    # 年齢認証のためセッションを用意
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        # 年齢認証フォーム送信
        session.post(
            "https://www.dmm.co.jp/my/-/service/=/security_age/", data={"adult": "ok"}
        )
    except:
        pass

    LIST_URL = "https://video.dmm.co.jp/amateur/list/?genre=8503&limit=120"
    resp = session.get(LIST_URL)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    videos = []
    seen = set()
    # <li class="d-item__item"> 要素を取得
    for li in soup.select("li.d-item__item"):
        a = li.find("a", href=True)
        if not a:
            continue
        detail_path = a["href"]
        # detail URL のフルパスを生成
        detail_url = detail_path if detail_path.startswith("http") else f"https://www.dmm.co.jp{detail_path}"
        if detail_url in seen:
            continue
        img = li.find("img")
        if not img:
            continue
        # サムネイルは data-original または src
        thumb = img.get("data-original") or img.get("src", "")
        title = img.get("alt", "").strip() or img.get("title", "").strip()
        # 説明取得
        description = _fetch_description(detail_url, {"User-Agent": USER_AGENT})

        videos.append({"title": title, "detail_url": detail_url, "thumb": thumb, "description": description})
        seen.add(detail_url)
        if len(videos) >= max_items:
            break
    return videos

# 説明文取得は API から直接取得済みなので不要だが、HTML からも取れるよう残す
# （必要に応じて呼び出しはしない）
def _fetch_description(url: str, headers: dict) -> str:
    try:
        d_resp = requests.get(url, headers=headers)
        d_resp.raise_for_status()
        d_soup = BeautifulSoup(d_resp.text, "html.parser")
        desc_div = d_soup.find("div", class_="mg-b20 lh4")
        if desc_div:
            return desc_div.get_text(separator=" ", strip=True)
    except:
        pass
    return ""

# ───────────────────────────────────────────────────────────
# WordPress に投稿（重複チェック付き）
# ───────────────────────────────────────────────────────────

def post_to_wp(item: dict):
    wp = Client(WP_URL, WP_USER, WP_PASS)
    existing = wp.call(GetPosts({"post_status": "publish", "s": item["title"]}))
    if any(p.title == item["title"] for p in existing):
        print(f"→ Skipping duplicate: {item['title']}")
        return

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
            print(f"Warning: thumbnail upload failed for {item['title']}: {e}")

    description = item.get("description", "") or "(説明文なし)"
    summary = textwrap.shorten(description, width=200, placeholder="…")

    content = f"<p>{summary}</p>\n"
    if thumb_id:
        content += f"<p><img src=\"{item['thumb']}\" alt=\"{item['title']}\"></p>\n"
    content += f"<p><a href=\"{item['detail_url']}\" target=\"_blank\">▶ 詳細・購入はこちら</a></p>"

    post = WordPressPost()
    post.title = item["title"]
    post.content = content
    if thumb_id:
        post.thumbnail = thumb_id
    post.terms_names = {"category": ["DMM動画"], "post_tag": []}
    post.post_status = "publish"
    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {item['title']}")

# ───────────────────────────────────────────────────────────
# メイン処理
# ───────────────────────────────────────────────────────────

def main():
    print(f"=== Job start: fetching top {MAX_ITEMS} videos via API ===")
    videos = fetch_latest_videos(MAX_ITEMS)
    print(f"Fetched {len(videos)} videos.")
    if not videos:
        print("No videos to post.")
    else:
        vid = videos[0]
        try:
            print(f"--> Posting: {vid['title']}")
            post_to_wp(vid)
        except Exception as e:
            print(f"✖ Error posting '{vid['title']}': {e}")
    print("=== Job finished ===")

if __name__ == "__main__":
    main()
