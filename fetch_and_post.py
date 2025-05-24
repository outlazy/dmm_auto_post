#!/usr/bin/env python3
# fetch_and_post.py

import os
import time
from dmm import DMM
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts

# ───────────────────────────────────────────────────────────
# 環境変数読み込み
# ───────────────────────────────────────────────────────────
load_dotenv()
API_ID       = os.getenv("DMM_API_ID")
AFFILIATE_ID = os.getenv("DMM_AFFILIATE_ID")
WP_URL       = os.getenv("WP_URL")
WP_USER      = os.getenv("WP_USER")
WP_PASS      = os.getenv("WP_PASS")
HITS         = int(os.getenv("HITS", 5))     # 取得件数（デフォルト5件）

# API クライアント初期化
client_api = DMM(API_ID, AFFILIATE_ID)

def fetch_videos_by_genre(genre_id: int, hits: int = HITS) -> list[dict]:
    """
    DMM アフィリエイト API で genre_id=1034 の動画を取得
    API リファレンス: https://affiliate.dmm.com/api/v3/itemlist.html
    """
    print(f"=== Fetching genre {genre_id} videos ({hits} 件) ===")
    res = client_api.search(
        'ItemList',
        site='DMM.com',        # 一般向け
        service='digital',     # デジタルコンテンツ
        floor='videoa',        # アダルト動画（FANZA）
        mono_genre_id=genre_id,
        hits=hits,
        sort='date',           # 新着順
        output='json'
    )
    items = res.get('result', {}).get('items', [])
    print(f"Fetched {len(items)} items")
    return [{
        'title':       i.get('title', '').strip(),
        'url':         i.get('affiliateURL'),
        'image_url':   i.get('largeImageURL'),
        'description': i.get('description', '').strip(),
        'genres':      [g.get('name') for g in i.get('genre', [])],
        'actors':      [a.get('name') for a in i.get('actor', [])]
    } for i in items]

def post_to_wp(item: dict):
    """WordPress に投稿（画像アップロード＋記事作成）"""
    print(f"--> Posting: {item['title']}")
    wp = Client(WP_URL, WP_USER, WP_PASS)

    # 1) サムネイルアップロード
    img_data = requests.get(item['image_url']).content
    data = {
        'name': os.path.basename(item['image_url']),
        'type': 'image/jpeg'
    }
    media_item = media.UploadFile(data, img_data)
    resp = wp.call(media_item)

    # 2) 投稿作成
    post = WordPressPost()
    post.title = item['title']
    post.content = (
        f'<p><a href="{item["url"]}" target="_blank">'
        f'<img src="{resp.url}" alt="{item["title"]}"></a></p>'
        f'<p>{item["description"]}</p>'
        f'<p><a href="{item["url"]}" target="_blank">▶ 詳細・購入はこちら</a></p>'
    )
    post.thumbnail = resp.id
    post.terms_names = {
        'category': ['DMM動画', 'AV'],
        'post_tag': item['genres'] + item['actors']
    }
    post.post_status = 'publish'
    wp.call(posts.NewPost(post))
    print(f"✔ Posted: {item['title']}")

def main():
    print("=== Job start ===")
    videos = fetch_videos_by_genre(1034)
    for vid in videos:
        try:
            post_to_wp(vid)
            time.sleep(1)  # API & サイト負荷軽減
        except Exception as e:
            print(f"✖ Error posting {vid['title']}: {e}")
    print("=== Job finished ===")

if __name__ == "__main__":
    main()
