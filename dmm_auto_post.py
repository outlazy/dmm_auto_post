import sys
import subprocess

# 必要パッケージを自動インストール
def pip_install(pkg):
    try:
        __import__(pkg)
    except ImportError:
        subprocess.call([sys.executable, "-m", "pip", "install", pkg])

for pkg in ['requests', 'beautifulsoup4']:
    pip_install(pkg)

import requests
from bs4 import BeautifulSoup
import json

# WordPress REST APIに投稿
def post_to_wordpress_rest(title, content, media_url, wp_url, user, password):
    img_id = None
    if media_url:
        img_data = requests.get(media_url).content
        resp = requests.post(
            f'{wp_url}/wp-json/wp/v2/media',
            data=img_data,
            headers={'Content-Disposition': f'attachment; filename="{media_url.split("/")[-1]}"'},
            auth=(user, password)
        )
        if resp.status_code == 201:
            img_id = resp.json()['id']
    post_data = {
        "title": title,
        "content": content,
        "status": "publish",
    }
    if img_id:
        post_data["featured_media"] = img_id
    resp = requests.post(
        f'{wp_url}/wp-json/wp/v2/posts',
        headers={'Content-Type': 'application/json'},
        data=json.dumps(post_data),
        auth=(user, password)
    )
    return resp.status_code, resp.text

# DMM APIで素人動画
def get_dmm_items(api_id, aff_id, hits=3):
    url = "https://api.dmm.com/affiliate/v3/ItemList"
    params = {
        "api_id": api_id,
        "affiliate_id": aff_id,
        "site": "FANZA",
        "service": "digital",
        "floor_id": "videoc",   # 素人動画フロア
        "hits": hits,
        "sort": "date",
        "output": "json",
        "availability": 1,
    }
    r = requests.get(url, params=params)
    items = []
    try:
        j = r.json()
        items = j["result"]["items"]
    except Exception as e:
        print("API取得失敗:", e, r.text)
    return items

# DMM素人動画（https://video.dmm.co.jp/amateur/list/?genre=79015 など）をスクレイピング
def scrape_amateur_items(list_url, hits=3):
    r = requests.get(list_url)
    soup = BeautifulSoup(r.content, 'html.parser')
    blocks = soup.select('div.amateur-cassette')[:hits]
    items = []
    for block in blocks:
        # タイトル
        title = block.select_one('.amateur-cassette__title').get_text(strip=True)
        # 詳細ページURL
        detail_url = "https://video.dmm.co.jp" + block.select_one('a.amateur-cassette__thumb')['href']
        # サムネイル画像
        img_tag = block.select_one('img')
        img_url = img_tag['src'] if img_tag and 'src' in img_tag.attrs else ''
        # 概要（詳細ページも見に行けるが、まずは一覧から取得）
        desc = block.select_one('.amateur-cassette__description')
        desc_text = desc.get_text(strip=True) if desc else ''
        # 詳細ページでサンプル画像をさらに取得（必要なら↓コメント外して使う）
        # detail_html = requests.get(detail_url).content
        # detail_soup = BeautifulSoup(detail_html, 'html.parser')
        # sample_imgs = [img['src'] for img in detail_soup.select('#sample-image-block img')]
        items.append({
            'title': title,
            'URL': detail_url,
            'img_url': img_url,
            'desc': desc_text,
        })
    return items

if __name__ == "__main__":
    # 必要情報を編集！
    DMM_API_ID = "ここにAPI ID"
    AFF_ID = "ここにアフィリエイトID"
    WP_URL = "https://example.com"  # WordPressのURL
    WP_USER = "WordPressユーザー名"
    WP_PASS = "WordPressアプリケーションパスワード"
    HITS = 3

    # まずAPI
    items = get_dmm_items(DMM_API_ID, AFF_ID, hits=HITS)
    if len(items) == 0:
        print("APIで動画0件→スクレイピングへ切替")
        # 好きな素人ジャンルページ。ジャンルIDはDMM側で確認可
        SCRAPE_URL = "https://video.dmm.co.jp/amateur/list/?genre=79015"
        items = scrape_amateur_items(SCRAPE_URL, hits=HITS)
    else:
        print(f"API動画: {len(items)}件")
    for item in items:
        title = item["title"]
        detail_url = item.get("URL", "")
        desc = item.get("description", "") or item.get("desc", "")
        images = []
        img = item.get("sampleImageURL", {}).get("large") or item.get("img_url", "")
        if img:
            images.append(img)
        content = f'<a href="{detail_url}"><img src="{images[0]}" alt="{title}"></a><br>{desc}' if images else f'{desc}'
        status, result = post_to_wordpress_rest(
            title=title,
            content=content,
            media_url=images[0] if images else None,
            wp_url=WP_URL,
            user=WP_USER,
            password=WP_PASS,
        )
        print(f"[WP投稿]{title} -> {status}")
