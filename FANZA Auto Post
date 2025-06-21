#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FANZA（DMM）素人動画 人気順1位を4時間ごとにWordPress自動投稿
- アフィリエイトリンク生成
- レーベル/女優名/ジャンルをタグ化
- サンプル画像1枚目はアイキャッチ
- 説明は要約優先、不足時はコピペ
"""

import os
import requests
import time
import collections
import collections.abc
collections.Iterable = collections.abc.Iterable
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client
from dotenv import load_dotenv

# 要約用 OpenAI API（任意：環境変数 OPENAI_API_KEY をセット）
try:
    import openai
    USE_OPENAI = True
except ImportError:
    USE_OPENAI = False

# -- 環境変数ロード --
def load_env():
    load_dotenv()
    env = {
        "WP_URL":     os.getenv("WP_URL"),
        "WP_USER":    os.getenv("WP_USER"),
        "WP_PASS":    os.getenv("WP_PASS"),
        "AFF_ID":     os.getenv("DMM_AFFILIATE_ID"),
        "API_ID":     os.getenv("DMM_API_ID"),
        "OPENAI_KEY": os.getenv("OPENAI_API_KEY")
    }
    for name, val in env.items():
        if not val and name != "OPENAI_KEY":
            raise RuntimeError(f"Missing environment variable: {name}")
    return env

env = load_env()
WP_URL, WP_USER, WP_PASS, AFF_ID, API_ID, OPENAI_KEY = env.values()
if USE_OPENAI and OPENAI_KEY:
    openai.api_key = OPENAI_KEY

# -- アフィリエイトリンク生成 --
def make_affiliate_link(url: str) -> str:
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs["affiliate_id"] = AFF_ID
    new_query = urlencode(qs)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

# -- FANZA素人 人気順リストの1位商品ページ取得 --
def fetch_top_video_url() -> str:
    RANKING_URL = "https://video.dmm.co.jp/amateur/list/?sort=ranking"
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    session.cookies.set("ckcy", "1", domain=".dmm.co.jp")
    resp = session.get(RANKING_URL, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    li = soup.select_one("li.list-box")
    if not li:
        raise RuntimeError("ランキングリストから商品が取得できません")
    a = li.find("a", class_="tmb")
    if not a or not a.get("href"):
        raise RuntimeError("ランキング商品のリンクが取得できません")
    href = a["href"]
    detail_url = href if href.startswith("http") else f"https://video.dmm.co.jp{href}"
    # 商品ページ（DMM本体）へリダイレクトするURL取得
    detail_resp = session.get(detail_url, timeout=10, allow_redirects=True)
    detail_resp.raise_for_status()
    # DMM本体商品ページへリダイレクトされる場合が多い
    return detail_resp.url

# -- 商品ページスクレイピングで情報抽出 --
def parse_product_page(url: str) -> dict:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    session.cookies.set("ckcy", "1", domain=".dmm.co.jp")
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    # 題名
    title = soup.select_one("h1#title, h1.d-productTitle__title, h1")
    title = title.get_text(strip=True) if title else "無題"
    # 商品説明
    desc = soup.select_one("div#mu__product-description, .d-productDescription__text, .box-product-detail .item-comment")
    description = desc.get_text(strip=True) if desc else ""
    # レーベル
    label = ""
    label_a = soup.find("a", href=lambda h: h and "/digital/videoc/-/label/=" in h)
    if label_a:
        label = label_a.get_text(strip=True)
    # 女優名
    actresses = []
    for a in soup.find_all("a", href=lambda h: h and "/digital/videoc/-/list/=/article=actress/id=" in h):
        name = a.get_text(strip=True)
        if name and name not in actresses:
            actresses.append(name)
    # ジャンル
    genres = []
    for a in soup.find_all("a", href=lambda h: h and "/digital/videoc/-/list/=/article=keyword/id=" in h):
        name = a.get_text(strip=True)
        if name and name not in genres:
            genres.append(name)
    # サンプル画像
    images = []
    for img in soup.select("div#sample-video > img, .d-productSample__item img, .sample-image img, #sample-image-block img"):
        src = img.get("src") or img.get("data-src")
        if src and src.startswith("http"):
            images.append(src)
    if not images:
        # 画像ブロックが違う場合
        images = [img.get("src") for img in soup.find_all("img") if img.get("src") and "sample" in img.get("src")]
    # 商品ID抽出（例：cid=xxxxxx）
    cid = ""
    parsed = urlparse(url)
    q = dict(parse_qsl(parsed.query))
    if "cid" in q:
        cid = q["cid"]
    else:
        try:
            for p in parsed.path.split("/"):
                if p.startswith("cid="):
                    cid = p.replace("cid=", "")
        except Exception:
            pass
    return {
        "title": title,
        "description": description,
        "label": label,
        "actresses": actresses,
        "genres": genres,
        "images": images,
        "detail_url": url,
        "cid": cid
    }

# -- 商品説明の要約（OpenAI利用、失敗時は原文返す） --
def summarize(text: str) -> str:
    if not text or len(text) < 50:
        return text
    if USE_OPENAI and OPENAI_KEY:
        try:
            prompt = f"次の商品の説明文を200文字以内で要約してください:\n{text}"
            resp = openai.ChatCompletion.create(
                model="gpt-3.5-turbo", messages=[{"role": "user", "content": prompt}],
                max_tokens=150, temperature=0.7)
            result = resp["choices"][0]["message"]["content"].strip()
            return result
        except Exception as e:
            print(f"要約失敗: {e}（原文を使用）")
            return text
    else:
        # 簡易要約（100-200字カット）
        if len(text) > 200:
            return text[:200] + "..."
        return text

# -- 画像をWordPressにアップロード（戻り値:画像ID） --
def upload_image(wp: Client, url: str) -> int:
    try:
        data = requests.get(url, timeout=10).content
        name = os.path.basename(urlparse(url).path)
        media_data = {"name": name, "type": "image/jpeg", "bits": xmlrpc_client.Binary(data)}
        res = wp.call(media.UploadFile(media_data))
        return res.get("id")
    except Exception as e:
        print(f"画像アップロード失敗: {url} ({e})")
        return None

# -- WordPress投稿（指定構成で） --
def create_wp_post(product: dict) -> bool:
    wp = Client(WP_URL, WP_USER, WP_PASS)
    title = product["title"]
    # 投稿済みか確認
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if any(p.title == title for p in existing):
        print(f"→ 既投稿: {title}（スキップ）")
        return False
    # 画像チェック
    if not product["images"]:
        print(f"→ サンプル画像なし: {title}（スキップ）")
        return False
    # アイキャッチ
    thumb_id = upload_image(wp, product["images"][0])
    # 本文構築
    aff_link = make_affiliate_link(product["detail_url"])
    parts = []
    # 1. アフィリンク画像
    parts.append(f'<p><a href="{aff_link}" target="_blank"><img src="{product["images"][0]}" alt="{title}"></a></p>')
    # 2. アフィリンク商品名
    parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')
    # 3. 商品説明（要約優先）
    summary = summarize(product["description"])
    if summary:
        parts.append(f'<div>{summary}</div>')
    # 4. サンプル画像（2枚目以降、リンクなし）
    for img in product["images"][1:]:
        parts.append(f'<p><img src="{img}" alt="{title}"></p>')
    # 5. 最後にアフィリンク画像＆商品名
    parts.append(f'<p><a href="{aff_link}" target="_blank"><img src="{product["images"][0]}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')
    # タグ
    tags = set()
    if product["label"]: tags.add(product["label"])
    tags.update(product["actresses"])
    tags.update(product["genres"])
    # 投稿
    post = WordPressPost()
    post.title = title
    post.content = "\n".join(parts)
    post.thumbnail = thumb_id
    post.terms_names = {"category": ["DMM人気動画"], "post_tag": list(tags)}
    post.post_status = "publish"
    wp.call(posts.NewPost(post))
    print(f"✔ 投稿完了: {title}")
    return True

# -- メイン実行（4時間ごとにcron推奨） --
def main():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 投稿開始")
    try:
        product_url = fetch_top_video_url()
        product = parse_product_page(product_url)
        result = create_wp_post(product)
        if not result:
            print("新規投稿なし")
    except Exception as e:
        print(f"エラー: {e}")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 投稿終了")

if __name__ == "__main__":
    main()
