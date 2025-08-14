#!/usr/bin/env python3
# coding: utf-8

import os
import re
import requests
from bs4 import BeautifulSoup
import collections
import collections.abc
collections.Iterable = collections.abc.Iterable

from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import posts, media
from wordpress_xmlrpc.compat import xmlrpc_client

print("🧪 Running dlsite_auto_post_html_full_final_v35_fixed.py")

# 環境変数読み込み
AFFILIATE_ID = os.environ.get('AFFILIATE_ID')
WP_URL       = os.environ.get('WP_URL')
WP_USER      = os.environ.get('WP_USER')
WP_PASS      = os.environ.get('WP_PASS')

# DLsite商品一覧を取得
def fetch_dlsite_items(limit=100):
    url = (
        'https://www.dlsite.com/maniax/fsr/=/work_category[0]/doujin/'
        'order/release_d/work_type[0]/SOU/options[0]/JPN/options[1]/'
        'NM/per_page/100/from/left_pain.work_type'
    )
    print(f"🔍 Fetching URL: {url}")
    resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, 'html.parser')
    ul = soup.select_one('ul#search_result_img_box')
    works = ul.select('li.search_result_img_box_inner') if ul else []
    print(f"🔎 Retrieved {len(works)} items")
    return works[:limit]

# 個別ページ解析
def parse_item(el):
    a = el.select_one('dd.work_name a')
    title = a.get_text(strip=True)
    href = a['href']
    detail_url = href if href.startswith('http') else 'https://www.dlsite.com' + href
    m = re.search(r'/product_id/(RJ\d+)\.html', detail_url)
    product_id = m.group(1) if m else ''

    # 詳細ページ取得
    resp = requests.get(detail_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
    resp.raise_for_status()
    dsoup = BeautifulSoup(resp.text, 'html.parser')

    # 説明文
    intro = dsoup.find('div', id='intro-title')
    desc = dsoup.find('div', itemprop='description', class_='work_parts_container')
    description_html = (str(intro) if intro else '') + (str(desc) if desc else '')

    # タグ取得：サークル名、シリーズ名、ジャンル、声優、シナリオ
    tags = []
    for label in ['サークル名', 'シリーズ名', 'ジャンル', '声優','シナリオ']:
        th = dsoup.find('th', string=label)
        if th:
            td = th.find_next_sibling('td')
            if label == 'ジャンル':
                for a_genre in td.select('div.main_genre a'):
                    tags.append(a_genre.get_text(strip=True))
            else:
                # サークル名、シリーズ名、声優はリンクテキストをタグに追加
                for a_tag in td.select('a'):
                    tags.append(a_tag.get_text(strip=True))

    # 画像取得: Open Graphタグを優先
    og_img = dsoup.find('meta', property='og:image')
    if og_img and og_img.get('content'):
        main_img_url = og_img['content']
    else:
        main_img_tag = dsoup.select_one('div#work_image_main img') or dsoup.find('img', id='main')
        if main_img_tag:
            src = main_img_tag.get('data-original') or main_img_tag.get('src') or ''
            main_img_url = 'https:' + src if src.startswith('//') else src
        else:
            main_img_url = ''
    print(f"📷 Found main image: {main_img_url}")

    # featured用としても同じ画像を使用
    smp1_img_url = main_img_url

    return {
        'title': title,
        'product_id': product_id,
        'detail_url': detail_url,
        'description_html': description_html,
        'tags': tags,
        'main_image_url': main_img_url,
        'smp1_image_url': smp1_img_url
    }

# 画像をWPにアップロード
def upload_image(client, image_url, label):
    if not image_url:
        print(f"⚠️ No {label} URL to upload")
        return None
    print(f"⬆️ Uploading {label}: {image_url}")
    try:
        resp = requests.get(image_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        resp.raise_for_status()
        mime_type = resp.headers.get('Content-Type', 'image/jpeg')
        data = {
            'name': os.path.basename(image_url),
            'type': mime_type,
            'bits': xmlrpc_client.Binary(resp.content)
        }
        result = client.call(media.UploadFile(data))
        print(f"✅ Uploaded {label}: id={result.get('id')} url={result.get('url')}")
        return {'id': result.get('id'), 'url': result.get('url')}
    except Exception as e:
        print(f"❌ Failed to upload {label}: {e}")
        return None

# アフィリエイトリンク生成
def generate_affiliate_link(item):
    return (
        f"https://dlaf.jp/maniax/dlaf/=/t/n/link/work/aid/"
        f"{AFFILIATE_ID}/id/{item['product_id']}.html"
    )

# 投稿コンテンツ生成
def generate_post_content(item, inline_image_url):
    affiliate_link = generate_affiliate_link(item)
    return (
        f"<p><a href='{inline_image_url}' target='_blank'>"
        f"<img src='{inline_image_url}' alt='{item['title']}'/></a></p>\n"
        f"<p><a rel='noopener sponsored' href='{affiliate_link}' target='_blank'>{item['title']}</a></p>\n"
        f"{item['description_html']}\n"
        f"<p><a rel='noopener sponsored' href='{affiliate_link}' target='_blank'>{item['title']}</a></p>"
    )

# 既存投稿タイトル取得
def get_published_titles(client, number=100):
    existing = client.call(posts.GetPosts({'number': number, 'post_status': 'publish'}))
    titles = [p.title for p in existing]
    print(f"📑 Found {len(titles)} existing titles")
    return set(titles)

# WP投稿処理
def post_to_wordpress(item):
    client = Client(WP_URL, WP_USER, WP_PASS)
    featured = upload_image(client, item['smp1_image_url'], 'featured')
    inline   = upload_image(client, item['main_image_url'], 'inline')

    post = WordPressPost()
    post.title = item['title']
    if featured and featured.get('id'):
        post.thumbnail = featured['id']
    inline_url = inline['url'] if inline and inline.get('url') else item['main_image_url']
    post.content = generate_post_content(item, inline_url)
    post.post_status = 'publish'
    post.custom_fields = [{'key': 'product_id', 'value': item['product_id']}]
    if item['tags']:
        post.terms_names = {'post_tag': item['tags']}
    client.call(posts.NewPost(post))
    print(f"✅ Published: {item['title']}")

# メイン処理

def main():
    client = Client(WP_URL, WP_USER, WP_PASS)
    published = get_published_titles(client)
    works = fetch_dlsite_items()
    items = [parse_item(el) for el in works]
    new_items = [it for it in items if it['title'] not in published]
    if not new_items:
        print("⚠️ No new items to post")
        return
    post_to_wordpress(new_items[0])

if __name__ == '__main__':
    main()
