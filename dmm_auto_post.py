import sys
import subprocess

# 必要なモジュールリスト
pkgs = [
    ("requests", "requests"),
    ("bs4", "bs4"),
    ("python_wordpress_xmlrpc", "python-wordpress-xmlrpc"),
]

# pip未導入時は自動インストール
for import_name, install_name in pkgs:
    try:
        __import__(import_name)
    except ImportError:
        print(f"[AUTO INSTALL] pip install {install_name}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", install_name])

import requests
from bs4 import BeautifulSoup
from python_wordpress_xmlrpc import Client, WordPressPost
from python_wordpress_xmlrpc.methods.posts import NewPost
import time

# ---------- 設定 ----------
WORDPRESS_URL = "https://あなたのドメイン/xmlrpc.php"
WORDPRESS_ID = "あなたのID"
WORDPRESS_PW = "あなたのパスワード"

DMM_LIST_URL = "https://video.dmm.co.jp/amateur/list/?sort=date"
DMM_DETAIL_BASE = "https://www.dmm.co.jp"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
MAX_POST = 3

def get_latest_items():
    print("一覧ページ取得中...")
    headers = {"User-Agent": USER_AGENT}
    res = requests.get(DMM_LIST_URL, headers=headers)
    soup = BeautifulSoup(res.content, "html.parser")
    items = []
    for a in soup.select("a[href*='/digital/videoc/-/detail/']"):
        href = a.get("href")
        if "/detail/" in href and not href.endswith("/review/"):
            if not href.startswith("http"):
                href = DMM_DETAIL_BASE + href
            if href not in items:
                items.append(href)
    print(f"検出: {len(items)}件")
    return items[:MAX_POST]

def scrape_detail(url):
    print("詳細取得: " + url)
    headers = {"User-Agent": USER_AGENT}
    res = requests.get(url, headers=headers)
    soup = BeautifulSoup(res.content, "html.parser")
    title = soup.select_one("h1#title, h1[itemprop='name']")
    title = title.text.strip() if title else "タイトル不明"
    desc = soup.select_one(".mg-b20.lh4, .product-text__info")
    desc = desc.text.strip() if desc else ""
    date = ""
    for tr in soup.select("tr"):
        if tr.text.strip().startswith("配信開始日"):
            tds = tr.select("td")
            if len(tds) > 1:
                date = tds[1].text.strip()
            else:
                date = tds[0].text.strip()
            break
    genre = [a.text for a in soup.select("a[href*='keyword=']")]
    genre = ", ".join(genre)
    images = []
    for img in soup.select("#sample-image-block img"):
        img_url = img.get("src")
        if img_url.startswith("//"):
            img_url = "https:" + img_url
        images.append(img_url)
    thumb = ""
    if images:
        thumb = images[0]
    else:
        mainimg = soup.select_one("meta[property='og:image']")
        if mainimg:
            thumb = mainimg.get("content")
    return {
        "title": title,
        "desc": desc,
        "date": date,
        "genre": genre,
        "images": images,
        "thumb": thumb,
        "url": url,
    }

def post_to_wordpress(info):
    print(f"WordPress投稿: {info['title']}")
    wp = Client(WORDPRESS_URL, WORDPRESS_ID, WORDPRESS_PW)
    post = WordPressPost()
    post.title = info["title"]
    html = ""
    if info["thumb"]:
        html += f'<p><img src="{info["thumb"]}" alt="" /></p>\n'
    html += f'<p><a href="{info["url"]}" target="_blank">{info["title"]}（DMMで見る）</a></p>\n'
    html += f"<p>{info['desc']}</p>\n"
    if info["date"]:
        html += f"<p><b>配信開始日:</b> {info['date']}</p>\n"
    if info["genre"]:
        html += f"<p><b>ジャンル:</b> {info['genre']}</p>\n"
    if info["images"]:
        html += "<p>サンプル画像:<br>"
        for img in info["images"]:
            html += f'<img src="{img}" style="max-width:200px;margin:4px;">'
        html += "</p>\n"
    post.content = html
    post.terms_names = {
        "post_tag": info["genre"].split(", ")
    }
    post.post_status = "publish"
    wp.call(NewPost(post))
    print("投稿完了！")

def main():
    items = get_latest_items()
    if not items:
        print("動画が見つかりません")
        return
    for url in items:
        info = scrape_detail(url)
        post_to_wordpress(info)
        time.sleep(3)

if __name__ == "__main__":
    main()
