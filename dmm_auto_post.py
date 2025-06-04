#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import requests
import schedule
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client

# ───────────────────────────────────────────────────────────
# 環境変数読み込み
# ───────────────────────────────────────────────────────────
load_dotenv()
WP_URL       = os.getenv("WP_URL")
WP_USER      = os.getenv("WP_USER")
WP_PASS      = os.getenv("WP_PASS")
AFFILIATE_ID = os.getenv("AFFILIATE_ID", "").strip()
USER_AGENT   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
# 取得する件数：最新10件までチェック
MAX_ITEMS    = 10

if not WP_URL or not WP_USER or not WP_PASS:
    raise RuntimeError("環境変数 WP_URL / WP_USER / WP_PASS が設定されていません")
if not AFFILIATE_ID:
    raise RuntimeError("環境変数 AFFILIATE_ID が設定されていません")

# ───────────────────────────────────────────────────────────
# DMM 年齢認証を突破してセッションを返す
# ───────────────────────────────────────────────────────────
def _get_dmm_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        session.post(
            "https://www.dmm.co.jp/my/-/service/=/security_age/",
            data={"adult": "ok"}
        )
    except Exception:
        pass
    return session

# ───────────────────────────────────────────────────────────
# DMM アマチュア動画一覧ページから最新 N 件の detail_url, title を取得
# ───────────────────────────────────────────────────────────
def fetch_latest_videos(max_items: int):
    session = _get_dmm_session()
    LIST_URL = "https://video.dmm.co.jp/amateur/list/?genre=8503&limit=120"
    resp = session.get(LIST_URL)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    videos = []
    seen = set()

    for li in soup.select("li.d-item__item"):
        a_tag = li.find("a", href=True)
        if not a_tag:
            continue

        detail_path = a_tag["href"]
        detail_url = detail_path if detail_path.startswith("http") else f"https://www.dmm.co.jp{detail_path}"
        if detail_url in seen:
            continue

        img = li.find("img")
        if not img:
            continue
        title = img.get("alt", "").strip() or img.get("title", "").strip()
        if not title:
            continue

        videos.append({
            "title": title,
            "detail_url": detail_url
        })
        seen.add(detail_url)

        if len(videos) >= max_items:
            break

    return videos

# ───────────────────────────────────────────────────────────
# detail ページから説明文、サンプル画像、レーベル、ジャンルを取得
# ───────────────────────────────────────────────────────────
def fetch_detail_info(detail_url: str):
    session = _get_dmm_session()
    resp = session.get(detail_url, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # 説明文取得
    desc_div = soup.find("div", class_="mg-b20 lh4")
    description = ""
    if desc_div:
        description = desc_div.get_text(separator=" ", strip=True)
    if not description:
        alt_desc = soup.find("p", class_="compTxt")
        if alt_desc:
            description = alt_desc.get_text(separator=" ", strip=True)
    if not description:
        description = "(説明文なし)"

    # サンプル画像取得
    sample_images = []
    for img in soup.select("div#sample-image-box img"):
        src = img.get("data-original") or img.get("src")
        if src and src not in sample_images:
            sample_images.append(src)
    if not sample_images:
        for img in soup.select("img.sample-box__img"):
            src = img.get("data-original") or img.get("src")
            if src and src not in sample_images:
                sample_images.append(src)
    if not sample_images:
        for img in soup.find_all("img"):
            src = img.get("data-original") or img.get("src") or ""
            if "sample" in src and src not in sample_images:
                sample_images.append(src)

    # メタ情報取得：レーベルとジャンル
    label = None
    genre = None
    for dt in soup.find_all("dt"):
        key = dt.get_text(strip=True)
        if "レーベル" in key:
            dd = dt.find_next_sibling("dd")
            if dd:
                a = dd.find("a")
                label = a.get_text(strip=True) if a else dd.get_text(strip=True)
        if "ジャンル" in key:
            dd = dt.find_next_sibling("dd")
            if dd:
                first_genre = dd.find("a")
                genre = first_genre.get_text(strip=True) if first_genre else dd.get_text(strip=True)
        if label and genre:
            break

    return description, sample_images, label, genre

# ───────────────────────────────────────────────────────────
# WordPress に投稿（重複チェック付き、タグにレーベル・ジャンル追加）
# ───────────────────────────────────────────────────────────
def post_to_wp(item: dict) -> bool:
    """
    item の中身：
      - title: 投稿タイトル
      - detail_url: DMM の詳細ページ URL
      - description: 説明文テキスト
      - sample_images: 画像 URL リスト（1枚目含む）
      - label: レーベル名（ある場合）
      - genre: ジャンル名（ある場合）
    戻り値：
      - True: 投稿成功
      - False: 重複などで投稿しなかった
    """
    wp = Client(WP_URL, WP_USER, WP_PASS)

    # 重複チェック：同じタイトルの投稿が存在しないか
    existing = wp.call(GetPosts({"post_status": "publish", "s": item["title"]}))
    if any(p.title == item["title"] for p in existing):
        print(f"→ Skipping duplicate: {item['title']}")
        return False

    first_img_url = item["sample_images"][0] if item.get("sample_images") else None

    # サムネイル画像をアップロード
    thumb_id = None
    if first_img_url:
        try:
            img_data = requests.get(first_img_url, headers={"User-Agent": USER_AGENT}).content
            media_data = {
                "name": os.path.basename(first_img_url.split("?")[0]),
                "type": "image/jpeg",
                "bits": xmlrpc_client.Binary(img_data)
            }
            resp_media = wp.call(media.UploadFile(media_data))
            thumb_id = resp_media.get("id")
        except Exception as e:
            print(f"Warning: アイキャッチアップロード失敗 ({first_img_url}): {e}")

    # アフィリエイトリンクを組み立て
    aff_link = f"{item['detail_url']}?affiliate_id={AFFILIATE_ID}"

    # 投稿本文を組み立て
    title = item["title"]
    description = item.get("description", "(説明文なし)")
    sample_images = item.get("sample_images", [])

    content_parts = []
    if sample_images:
        content_parts.append(
            f'<p><a href="{aff_link}" target="_blank">'
            f'<img src="{sample_images[0]}" alt="{title} サンプル1" />'
            f'</a></p>'
        )
    content_parts.append(
        f'<p><a href="{aff_link}" target="_blank">{title}</a></p>'
    )
    content_parts.append(f'<p>{description}</p>')
    if len(sample_images) > 1:
        for idx, img_url in enumerate(sample_images[1:], start=2):
            content_parts.append(f'<p><img src="{img_url}" alt="{title} サンプル{idx}" /></p>')
    content_parts.append(
        f'<p><a href="{aff_link}" target="_blank">▶ 購入はこちら</a></p>'
    )
    content = "\n".join(content_parts)

    # 投稿オブジェクト作成
    post = WordPressPost()
    post.title = title
    post.content = content
    if thumb_id:
        post.thumbnail = thumb_id

    # カテゴリ／タグ設定：レーベルとジャンルを1つずつタグに追加
    tags = []
    if item.get("label"):
        tags.append(item["label"])
    if item.get("genre"):
        tags.append(item["genre"])
    post.terms_names = {
        "category": ["DMM動画"],
        "post_tag": tags
    }
    post.post_status = "publish"

    try:
        wp.call(posts.NewPost(post))
        print(f"✔ Posted: {title}")
        return True
    except Exception as e:
        print(f"✖ 投稿エラー ({title}): {e}")
        return False

# ───────────────────────────────────────────────────────────
# メイン処理：最新10件から重複でない最初の作品を投稿
# ───────────────────────────────────────────────────────────
def job():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job start: fetching and posting")
    try:
        videos = fetch_latest_videos(MAX_ITEMS)
        if not videos:
            print("No videos found.")
            return

        for vid_info in videos:
            detail_url = vid_info["detail_url"]
            title = vid_info["title"]
            description, sample_images, label, genre = fetch_detail_info(detail_url)
            item = {
                "title": title,
                "detail_url": detail_url,
                "description": description,
                "sample_images": sample_images,
                "label": label,
                "genre": genre
            }
            posted = post_to_wp(item)
            if posted:
                break  # 投稿成功したらループを抜ける
            else:
                continue  # 重複なら次の作品へ

    except Exception as e:
        print(f"Error in job(): {e}")
    finally:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished.")

# ───────────────────────────────────────────────────────────
# スケジューリング：4時間ごとに job() を実行
# ───────────────────────────────────────────────────────────
def main():
    job()
    schedule.every(4).hours.do(job)
    print("Scheduler started. Running every 4 hours...")
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()
