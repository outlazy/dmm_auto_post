#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client

# ───────────────────────────────────────────────────────────
# 環境変数読み込み (.env があれば読み込む)
# ───────────────────────────────────────────────────────────
load_dotenv()

WP_URL    = os.getenv("WP_URL")
WP_USER   = os.getenv("WP_USER")
WP_PASS   = os.getenv("WP_PASS")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
MAX_ITEMS = 10

# 必須環境変数チェック
missing = []
for var in ("WP_URL", "WP_USER", "WP_PASS"):
    if not os.getenv(var):
        missing.append(var)
if missing:
    raise RuntimeError(f"環境変数が設定されていません: {', '.join(missing)}")

TODAY = datetime.now().date()


def _get_dmm_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    try:
        s.post("https://www.dmm.co.jp/my/-/service/=/security_age/", data={"adult": "ok"})
    except:
        pass
    return s


# ───────────────────────────────────────────────────────────
# DMM アマチュア動画一覧ページから最新 N 件の作品情報をスクレイピング
# （発売前を除外し、説明・画像ありをフィルタ）
# ───────────────────────────────────────────────────────────
def fetch_latest_videos_scrape(max_items: int):
    session = _get_dmm_session()
    LIST_URL = "https://video.dmm.co.jp/amateur/list/?genre=8503&limit=120"
    resp = session.get(LIST_URL)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    videos = []
    seen = set()

    for li in soup.select("li.d-item__item"):
        if len(videos) >= max_items:
            break

        a_tag = li.find("a", href=True)
        if not a_tag:
            continue
        detail_url = a_tag["href"]
        if not detail_url.startswith("http"):
            detail_url = "https://www.dmm.co.jp" + detail_url
        if detail_url in seen:
            continue

        # タイトル
        img = li.find("img")
        if not img:
            continue
        title = img.get("alt", "").strip()
        if not title:
            continue

        # 作品ページを開いて詳細をチェック
        desc, sample_images, label, genres, release_date = fetch_detail_info(detail_url)
        # 発売前 or 説明文なし or 画像なし → スキップ
        if release_date and release_date > TODAY:
            continue
        if not desc:
            continue
        if not sample_images:
            continue

        videos.append({
            "title": title,
            "detail_url": detail_url,
            "description": desc,
            "sample_images": sample_images,
            "label": label,
            "genres": genres
        })
        seen.add(detail_url)

    return videos


# ───────────────────────────────────────────────────────────
# 詳細ページから 説明文・サンプル画像・レーベル・ジャンル・発売日を取得
# ───────────────────────────────────────────────────────────
def fetch_detail_info(url: str):
    session = _get_dmm_session()
    resp = session.get(url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # 説明文取得
    desc_div = soup.find("div", class_="mg-b20 lh4")
    if desc_div:
        description = desc_div.get_text(separator=" ", strip=True)
    else:
        description = ""
    # 説明文なしなら空文字

    # 発売日取得
    release_date = None
    for dt in soup.select("dt"):
        if "発売日" in dt.get_text(strip=True):
            dd = dt.find_next_sibling("dd")
            if dd:
                date_str = dd.get_text(strip=True)
                try:
                    release_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    pass
            break

    # サンプル画像取得
    sample_images = []
    # パターン：div#sample-image-box img
    for img in soup.select("div#sample-image-box img"):
        src = img.get("data-original") or img.get("src")
        if src and src not in sample_images:
            sample_images.append(src)
    # fallback: class="sample-box__img"
    if not sample_images:
        for img in soup.select("img.sample-box__img"):
            src = img.get("data-original") or img.get("src")
            if src and src not in sample_images:
                sample_images.append(src)

    # レーベル取得
    label = ""
    for dt in soup.find_all("dt"):
        if "レーベル" in dt.get_text(strip=True):
            dd = dt.find_next_sibling("dd")
            if dd:
                a = dd.find("a")
                label = a.get_text(strip=True) if a else dd.get_text(strip=True)
            break

    # ジャンル取得（複数）
    genres = []
    for dt in soup.find_all("dt"):
        if "ジャンル" in dt.get_text(strip=True):
            dd = dt.find_next_sibling("dd")
            if dd:
                for a in dd.find_all("a"):
                    text = a.get_text(strip=True)
                    if text:
                        genres.append(text)
            break

    return description, sample_images, label, genres, release_date


# ───────────────────────────────────────────────────────────
# WordPress に投稿（重複チェック、タグにレーベル＋ジャンル語をすべて追加）
# ───────────────────────────────────────────────────────────
def post_to_wp(item: dict) -> bool:
    wp = Client(WP_URL, WP_USER, WP_PASS)

    # 重複チェック：同じタイトルの投稿がないか
    existing = wp.call(GetPosts({"post_status": "publish", "s": item["title"]}))
    if any(p.title == item["title"] for p in existing):
        print(f"→ Skipping duplicate: {item['title']}")
        return False

    # アイキャッチ＝サンプル画像１枚目をアップロード
    thumb_id = None
    first_img = item["sample_images"][0]
    try:
        img_data = requests.get(first_img, headers={"User-Agent": USER_AGENT}).content
        media_data = {
            "name": os.path.basename(first_img.split("?")[0]),
            "type": "image/jpeg",
            "bits": xmlrpc_client.Binary(img_data)
        }
        resp_media = wp.call(media.UploadFile(media_data))
        thumb_id = resp_media.get("id")
    except Exception as e:
        print(f"Warning: アイキャッチアップロード失敗 ({first_img}): {e}")

    # 本文組み立て
    title         = item["title"]
    aff_link      = item["detail_url"] + f"?affiliate_id={os.getenv('DMM_AFFILIATE_ID','')}"
    description   = item["description"]
    sample_images = item["sample_images"]

    content_parts = []
    # 1) サムネ１枚目
    content_parts.append(
        f'<p><a href="{aff_link}" target="_blank">'
        f'<img src="{sample_images[0]}" alt="{title} サンプル1" /></a></p>'
    )
    # 2) タイトルリンク
    content_parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')
    # 3) 説明文
    content_parts.append(f'<p>{description}</p>')
    # 4) ２枚目以降のサムネ画像
    for idx, img_url in enumerate(sample_images[1:], start=2):
        content_parts.append(f'<p><img src="{img_url}" alt="{title} サンプル{idx}" /></p>')
    # 5) 購入リンク
    content_parts.append(f'<p><a href="{aff_link}" target="_blank">▶ 購入はこちら</a></p>')

    content = "\n".join(content_parts)

    # 投稿オブジェクト作成
    post = WordPressPost()
    post.title   = title
    post.content = content
    if thumb_id:
        post.thumbnail = thumb_id

    # タグにレーベル＋ジャンルの各語を追加
    tags = []
    if item["label"]:
        tags.append(item["label"])
    for genre in item["genres"]:
        for word in genre.split():
            if word and word not in tags:
                tags.append(word)

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
# メイン処理：スクレイピングで最新10件を取得し、重複でない最初の作品を投稿
# ───────────────────────────────────────────────────────────
def job():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job start")
    try:
        videos = fetch_latest_videos_scrape(MAX_ITEMS)
        if not videos:
            print("No videos found.")
            return
        for vid in videos:
            if post_to_wp(vid):
                break  # 投稿成功したら抜ける
    except Exception as e:
        print(f"Error in job(): {e}")
    finally:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Job finished")


# ───────────────────────────────────────────────────────────
# エントリポイント
# ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    job()
