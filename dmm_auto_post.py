import subprocess
import sys

# 必要パッケージの自動インストール＆import
REQUIRED = [
    ("requests", "requests"),
    ("beautifulsoup4", "bs4"),
    ("python-wordpress-xmlrpc", "python_wordpress_xmlrpc"),
]
def install_and_import(install_name, import_name):
    try:
        __import__(import_name)
    except ImportError:
        print(f"[AUTO INSTALL] pip install {install_name}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", install_name])
    finally:
        globals()[import_name] = __import__(import_name)
for install_name, import_name in REQUIRED:
    install_and_import(install_name, import_name)

import time
from bs4 import BeautifulSoup
import requests
from python_wordpress_xmlrpc import Client, WordPressPost
from python_wordpress_xmlrpc.methods.posts import NewPost

# ------- 設定 -------
WP_XMLRPC_URL = "https://example.com/xmlrpc.php"    # ←WordPress XML-RPC エンドポイント
WP_USER = "your_id"                                # ←WordPressユーザー
WP_PASS = "your_password"                          # ←WordPressパスワード

DMM_LIST_URL = "https://video.dmm.co.jp/amateur/list/?sort=date"
DMM_DETAIL_BASE = "https://www.dmm.co.jp"

# 投稿済み判定用（メモリで直近10件だけ管理。必要ならDBやファイル管理に拡張して）
POSTED = []

def get_latest_items():
    print("一覧ページ取得中...")
    res = requests.get(DMM_LIST_URL)
    soup = BeautifulSoup(res.content, "html.parser")
    items = []
    for thumb in soup.select(".box-image a[href*='/detail/']"):
        detail_url = thumb.get("href")
        if not detail_url.startswith("http"):
            detail_url = DMM_DETAIL_BASE + detail_url
        items.append(detail_url)
    print(f"検出: {len(items)}件")
    return items

def get_item_detail(detail_url):
    res = requests.get(detail_url)
    soup = BeautifulSoup(res.content, "html.parser")
    title = soup.select_one("h1#title, .h-productTitle").get_text(strip=True) if soup.select_one("h1#title, .h-productTitle") else "無題"
    main_image = ""
    img_block = soup.select_one("#sample-image-block img") or soup.select_one(".d-zoomimg-sm img")
    if img_block:
        main_image = img_block.get("src")
        if main_image and main_image.startswith("//"):
            main_image = "https:" + main_image
    # サンプル画像（複数）
    sample_imgs = []
    for img in soup.select("#sample-image-block img"):
        src = img.get("src")
        if src and src.startswith("//"):
            src = "https:" + src
        sample_imgs.append(src)
    # 紹介文
    intro = soup.select_one(".mg-b20.lh4, .introduction, .mg-b20").get_text(separator="\n", strip=True) if soup.select_one(".mg-b20.lh4, .introduction, .mg-b20") else ""
    # 配信開始日
    release = ""
    for tr in soup.select("tr"):
        if "配信開始日" in tr.get_text():
            tds = tr.select("td")
            if tds and len(tds) > 1:
                release = tds[1].get_text(strip=True)
    return {
        "title": title,
        "main_image": main_image,
        "sample_imgs": sample_imgs,
        "intro": intro,
        "url": detail_url,
        "release": release
    }

def post_to_wordpress(item):
    wp = Client(WP_XMLRPC_URL, WP_USER, WP_PASS)
    post = WordPressPost()
    post.title = item["title"]
    parts = []
    # アイキャッチ＋サンプル
    if item["main_image"]:
        parts.append(f'<img src="{item["main_image"]}" alt="{item["title"]}" style="max-width:100%;" /><br>')
    # 本文
    if item["intro"]:
        parts.append(f"<p>{item['intro']}</p>")
    # サンプル画像
    if item["sample_imgs"]:
        for src in item["sample_imgs"]:
            parts.append(f'<img src="{src}" alt="{item["title"]}サンプル" style="max-width:100%;" /><br>')
    # 詳細ページリンク
    parts.append(f'<p><a href="{item["url"]}" target="_blank">DMMで詳細を見る</a></p>')
    # 配信開始日
    if item["release"]:
        parts.append(f"<div>配信開始日: {item['release']}</div>")
    post.content = "\n".join(parts)
    post.post_status = "publish"
    # 投稿
    wp.call(NewPost(post))
    print("投稿完了:", item["title"])

def main():
    items = get_latest_items()
    for url in items:
        if url in POSTED:
            continue
        item = get_item_detail(url)
        # 既にリリース済のみ投稿
        if item["release"] and item["main_image"]:
            post_to_wordpress(item)
            POSTED.append(url)
            if len(POSTED) > 10:  # 直近10件だけ管理
                POSTED.pop(0)
            time.sleep(2)  # 連投防止

if __name__ == "__main__":
    main()
