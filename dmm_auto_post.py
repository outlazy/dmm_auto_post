import sys
import subprocess

# --- 必要なライブラリ自動インストール ---
def install_and_import(package):
    try:
        __import__(package)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        globals()[package] = __import__(package)

for pkg in ['requests', 'bs4', 'python_wordpress_xmlrpc']:
    install_and_import(pkg)

import requests
from bs4 import BeautifulSoup
from python_wordpress_xmlrpc import Client, WordPressPost
from python_wordpress_xmlrpc.methods.posts import NewPost

# --- WordPress接続情報 ---
WP_URL      = "https://example.com/xmlrpc.php"
WP_USER     = "your_username"
WP_PASSWORD = "your_password"

# --- DMM素人一覧ページURL ---
LIST_URL = "https://video.dmm.co.jp/amateur/list/?sort=date"

def fetch_list_urls(list_url, limit=3):
    ''' 一覧ページから動画詳細ページURLを抽出 '''
    res = requests.get(list_url, headers={'User-Agent':'Mozilla/5.0'})
    soup = BeautifulSoup(res.content, 'html.parser')
    urls = []
    for a in soup.select('a[href*="/digital/videoc/-/detail/="]'):
        href = a.get('href')
        if href.startswith('/digital/videoc/-/detail/'):
            url = "https://www.dmm.co.jp" + href.split('?')[0]
            if url not in urls:
                urls.append(url)
        if len(urls) >= limit:
            break
    return urls

def fetch_detail_dmm(detail_url):
    ''' 詳細ページから情報を抽出 '''
    res = requests.get(detail_url, headers={'User-Agent':'Mozilla/5.0'})
    soup = BeautifulSoup(res.content, 'html.parser')

    # タイトル
    title = soup.select_one('h1#title')
    if not title:
        title = soup.select_one('title')
    title = title.text.strip() if title else "無題"

    # 説明文
    desc = soup.select_one('div.lh4, div.tx14, div#introduction')
    description = desc.get_text(separator='\n').strip() if desc else ''

    # ジャケット
    thumb = ""
    main_img = soup.select_one('.package-image img, .d-zoomimg img')
    if main_img and main_img.has_attr("src"):
        thumb = main_img["src"]

    # サンプル画像
    sample_imgs = []
    for img in soup.select('#sample-image-block img'):
        if img.has_attr("src"):
            sample_imgs.append(img["src"])

    # ジャンル
    genres = []
    for g in soup.select('tr:has(td.nw:contains("ジャンル")) a'):
        genres.append(g.text.strip())

    # 出演者（素人系は空のこと多い）
    cast = []
    for c in soup.select('tr:has(td.nw:contains("名前")) td:not(.nw)'):
        cast.append(c.text.strip())

    # 配信開始日
    release_date = ""
    for tr in soup.select('tr'):
        th = tr.find('td', align="right")
        if th and "配信開始日" in th.text:
            tds = tr.find_all('td')
            if len(tds) > 1:
                release_date = tds[1].text.strip()

    return {
        "title": title,
        "description": description,
        "thumb": thumb,
        "sample_imgs": sample_imgs,
        "genres": genres,
        "cast": cast,
        "release_date": release_date,
        "url": detail_url
    }

def post_to_wordpress(item):
    ''' WordPressに投稿 '''
    wp = Client(WP_URL, WP_USER, WP_PASSWORD)
    post = WordPressPost()
    post.title = item["title"]

    # 本文生成（画像＋説明＋リンク）
    content = []
    if item["thumb"]:
        content.append(f'<img src="{item["thumb"]}" alt="{item["title"]}">')
    content.append(f'<p>{item["description"]}</p>')
    if item["sample_imgs"]:
        for img in item["sample_imgs"]:
            content.append(f'<img src="{img}" alt="sample">')
    content.append(f'<p><a href="{item["url"]}" target="_blank">DMMで作品詳細を見る</a></p>')
    post.content = "\n".join(content)

    # タグ
    post.terms_names = {
        'post_tag': item["genres"] + item["cast"]
    }

    # カテゴリ例
    post.terms_names['category'] = ['素人']

    post.post_status = 'publish'
    wp.call(NewPost(post))
    print(f"Posted: {item['title']}")

def main():
    print("一覧ページ取得中...")
    detail_urls = fetch_list_urls(LIST_URL, limit=3)  # 投稿数制限
    for url in detail_urls:
        print(f"詳細取得: {url}")
        item = fetch_detail_dmm(url)
        post_to_wordpress(item)

if __name__ == "__main__":
    main()
