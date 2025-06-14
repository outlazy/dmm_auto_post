import sys
import subprocess

def ensure(pkg, import_name=None):
    import_name = import_name or pkg.replace('-', '_')  # ←ここで自動置換
    try:
        __import__(import_name)
    except ImportError:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', pkg])
        __import__(import_name)

ensure('requests')
ensure('lxml')
ensure('python-wordpress-xmlrpc', 'python_wordpress_xmlrpc')

import requests
from lxml import html
from python_wordpress_xmlrpc import Client, WordPressPost
from python_wordpress_xmlrpc.methods.posts import NewPost

LIST_URL = "https://video.dmm.co.jp/amateur/list/?sort=date"

WP_URL = "https://あなたのサイト.com/xmlrpc.php"
WP_USER = "WPログイン名"
WP_PASS = "WPアプリケーションパスワード"

print("一覧ページ取得中...")
r = requests.get(LIST_URL, timeout=20)
tree = html.fromstring(r.content)

detail_links = tree.xpath('//div[contains(@class,"d-item")]/a/@href')
if not detail_links:
    print("動画が見つかりません")
    sys.exit(0)

detail_url = detail_links[0]
if not detail_url.startswith("http"):
    detail_url = "https://video.dmm.co.jp" + detail_url
print("詳細ページURL:", detail_url)

r2 = requests.get(detail_url, timeout=20)
tree2 = html.fromstring(r2.content)

title = tree2.xpath('//h1/text()')
title = title[0].strip() if title else "タイトル未取得"

desc = tree2.xpath('//div[contains(@class, "mg-b20") and contains(@class, "lh4")]/text()')
desc = "\n".join([d.strip() for d in desc if d.strip()]) if desc else "紹介文未取得"

img_url = tree2.xpath('//div[@id="sample-image-block"]//img/@src')
img_url = img_url[0] if img_url else ""

print(f"タイトル: {title}\n画像: {img_url}\n本文: {desc}")

wp = Client(WP_URL, WP_USER, WP_PASS)
post = WordPressPost()
post.title = title
post.content = f'<img src="{img_url}" alt="{title}"><br>\n{desc}<br>\n<a href="{detail_url}">動画詳細はこちら</a>'
post.post_status = "publish"
post.terms_names = {
    'category': ['DMM素人動画'],
}
post_id = wp.call(NewPost(post))
print(f"投稿完了！投稿ID: {post_id}")
