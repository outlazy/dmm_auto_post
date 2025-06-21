#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FANZA素人動画 人気順1位をWordPressへ自動投稿
- すべての設定は環境変数（GitHub Secretsや export など）で管理
- 設定ファイル（config.yml等）は不要
"""

import os
import requests
import time
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client

# --- 環境変数を取得 ---
def get_env(key, required=True, default=None):
    val = os.environ.get(key, default)
    if required and not val:
        raise RuntimeError(f"環境変数 {key} が設定されていません")
    return val

# --- アフィリエイトリンク生成 ---
def make_affiliate_link(url, aff_id):
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs["affiliate_id"] = aff_id
    new_query = urlencode(qs)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

# --- FANZA素人 人気順リストの1位商品ページ取得 ---
def fetch_top_video_url():
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
    detail_resp = session.get(detail_url, timeout=10, allow_redirects=True)
    detail_resp.raise_for_status()
    return detail_resp.url

# --- 商品ページスクレイピングで情報抽出 ---
def parse_product_page(url):
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    session.cookies.set("ckcy", "1", domain=".dmm.co.jp")
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.select_one("h1#title, h1.d-productTitle__title, h1")
    title = title.get_text(strip=True) if title else "無題"
    desc = soup.select_one("div#mu__product-description, .d-productDescription__text, .box-product-detail .item-comment")
    description = desc.get_text(strip=True) if desc else ""
    label = ""
    label_a = soup.find("a", href=lambda h: h and "/digital/videoc/-/label/=" in h)
    if label_a:
        label = label_a.get_text(strip=True)
    actresses = []
    for a in soup.find_all("a", href=lambda h: h and "/digital/videoc/-/list/=/article=actress/id=" in h):
        name = a.get_text(strip=True)
        if name and name not in actresses:
            actresses.append(name)
    genres = []
    for a in soup.find_all("a", href=lambda h: h and "/digital/videoc/-/list/=/article=keyword/id=" in h):
        name = a.get_text(strip=True)
        if name and name not in genres:
            genres.append(name)
    images = []
    for img in soup.select("div#sample-video > img, .d-productSample__item img, .sample-image img, #sample-image-block img"):
        src = img.get("src") or img.get("data-src")
        if src and src.startswith("http"):
            images.append(src)
    if not images:
        images = [img.get("src") for img in soup.find_all("img") if img.get("src") and "sample" in img.get("src")]
    return {
        "title": title,
        "description": description,
        "label": label,
        "actresses": actresses,
        "genres": genres,
        "images": images,
        "detail_url": url
    }

# --- 商品説明の要約（OpenAI利用、未設定時は原文返す） ---
def summarize(text, api_key=None):
    if not text or len(text) < 50 or not api_key:
        return text
    try:
        import openai
        openai.api_key = api_key
        prompt = f"次の商品の説明文を200文字以内で要約してください:\n{text}"
        resp = openai.ChatCompletion.create(
            model="gpt-3.5-turbo", messages=[{"role": "user", "content": prompt}],
            max_tokens=150, temperature=0.7)
        result = resp["choices"][0]["message"]["content"].strip()
        return result
    except Exception as e:
        print(f"要約失敗: {e}（原文を使用）")
        return text

# --- 画像をWordPressにアップロード（戻り値:画像ID） ---
def upload_image(wp, url):
    try:
        data = requests.get(url, timeout=10).content
        name = os.path.basename(urlparse(url).path)
        media_data = {"name": name, "type": "image/jpeg", "bits": xmlrpc_client.Binary(data)}
        res = wp.call(media.UploadFile(media_data))
        return res.get("id")
    except Exception as e:
        print(f"画像アップロード失敗: {url} ({e})")
        return None

# --- WordPress投稿（指定構成で） ---
def create_wp_post(product):
    WP_URL = get_env('WP_URL')
    WP_USER = get_env('WP_USER')
    WP_PASS = get_env('WP_PASS')
    CATEGORY = get_env('CATEGORY')
    AFF_ID = get_env('DMM_AFFILIATE_ID')
    OPENAI_KEY = os.environ.get('OPENAI_API_KEY', None)

    wp = Client(WP_URL, WP_USER, WP_PASS)
    title = product["title"]
    # 投稿済みチェック
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if any(p.title == title for p in existing):
        print(f"→ 既投稿: {title}（スキップ）")
        return False
    if not product["images"]:
        print(f"→ サンプル画像なし: {title}（スキップ）")
        return False
    thumb_id = upload_image(wp, product["images"][0])
    aff_link = make_affiliate_link(product["detail_url"], AFF_ID)
    parts = []
    parts.append(f'<p><a href="{aff_link}" target="_blank"><img src="{product["images"][0]}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')
    summary = summarize(product["description"], OPENAI_KEY)
    if summary:
        parts.append(f'<div>{summary}</div>')
    for img in product["images"][1:]:
        parts.append(f'<p><img src="{img}" alt="{title}"></p>')
    parts.append(f'<p><a href="{aff_link}" target="_blank"><img src="{product["images"][0]}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')
    tags = set()
    if product["label"]: tags.add(product["label"])
    tags.update(product["actresses"])
    tags.update(product["genres"])
    post = WordPressPost()
    post.title = title
    post.content = "\n".join(parts)
    post.thumbnail = thumb_id
    post.terms_names = {"category": [CATEGORY], "post_tag": list(tags)}
    post.post_status = "publish"
    wp.call(posts.NewPost(post))
    print(f"✔ 投稿完了: {title}")
    return True

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
