import subprocess
import sys

# 必要パッケージを全自動インストール
def install_and_import(pkg):
    try:
        __import__(pkg)
    except ImportError:
        print(f"[AUTO INSTALL] pip install {pkg}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
    finally:
        globals()[pkg] = __import__(pkg)

for pkg in ["requests", "bs4", "python_wordpress_xmlrpc"]:
    install_and_import(pkg)

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    SELENIUM_OK = True
except ImportError:
    print("[AUTO INSTALL] pip install selenium")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "selenium"])
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    SELENIUM_OK = True

import time
from bs4 import BeautifulSoup
import requests
from python_wordpress_xmlrpc import Client, WordPressPost
from python_wordpress_xmlrpc.methods.posts import NewPost
from python_wordpress_xmlrpc.methods.media import UploadFile
from python_wordpress_xmlrpc.compat import xmlrpc_client

# WordPress設定
WP_URL = "https://example.com/xmlrpc.php"
WP_USER = "yourid"
WP_PASS = "yourpassword"

# スクレイピング対象
LIST_URL = "https://video.dmm.co.jp/amateur/list/?sort=date"

# 投稿数
POST_NUM = 3

def get_detail_urls_selenium(list_url, max_count):
    print("Selenium起動中…")
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    driver = webdriver.Chrome(options=options)
    driver.get(list_url)
    time.sleep(3)
    html = driver.page_source
    driver.quit()
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for a in soup.find_all("a", href=True):
        if "/digital/videoc/-/detail/=" in a["href"]:
            url = "https://www.dmm.co.jp" + a["href"].split("?")[0]
            if url not in urls:
                urls.append(url)
        if len(urls) >= max_count:
            break
    print("抽出詳細ページURL:", urls)
    return urls

def get_detail_urls_requests(list_url, max_count):
    print("requestsで一覧取得中…")
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(list_url, headers=headers)
    html = r.text
    if "JavaScriptを有効にしてください" in html:
        print("JavaScriptレンダリング必須→Seleniumに切替")
        return None
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for a in soup.find_all("a", href=True):
        if "/digital/videoc/-/detail/=" in a["href"]:
            url = "https://www.dmm.co.jp" + a["href"].split("?")[0]
            if url not in urls:
                urls.append(url)
        if len(urls) >= max_count:
            break
    print("抽出詳細ページURL:", urls)
    return urls

def get_detail_info(url):
    print("詳細ページ取得:", url)
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers)
    soup = BeautifulSoup(r.content, "html.parser")

    # タイトル
    title = soup.find("h1")
    title = title.text.strip() if title else "(タイトル不明)"

    # 画像（サンプル画像がある場合）
    img_list = []
    img_block = soup.find(id="sample-image-block")
    if img_block:
        for img in img_block.find_all("img"):
            if img.get("src"):
                img_list.append(img["src"] if img["src"].startswith("http") else "https:" + img["src"])

    # 概要文（説明文）
    desc_block = soup.find("div", class_="mg-b20")
    description = desc_block.text.strip() if desc_block else ""

    # 発売日
    release_date = ""
    for tr in soup.find_all("tr"):
        th = tr.find("td", align="right")
        if th and ("配信開始日" in th.text or "発売日" in th.text):
            tds = tr.find_all("td")
            if len(tds) >= 2:
                release_date = tds[1].text.strip()

    # レーベル、ジャンル
    label, genre = "", []
    for tr in soup.find_all("tr"):
        th = tr.find("td", align="right")
        if th and "レーベル" in th.text:
            tds = tr.find_all("td")
            if len(tds) >= 2:
                label = tds[1].text.strip()
        if th and "ジャンル" in th.text:
            tds = tr.find_all("td")
            if len(tds) >= 2:
                for a in tds[1].find_all("a"):
                    genre.append(a.text.strip())

    return {
        "title": title,
        "description": description,
        "release_date": release_date,
        "label": label,
        "genre": genre,
        "images": img_list,
        "url": url,
    }

def upload_image_to_wp(wp, img_url):
    img_data = requests.get(img_url).content
    data = {
        'name': img_url.split("/")[-1],
        'type': 'image/jpeg',
        'bits': xmlrpc_client.Binary(img_data),
    }
    response = wp.call(UploadFile(data))
    return response['id'], response['url']

def create_post(video, wp):
    print("WordPress投稿準備:", video["title"])
    post = WordPressPost()
    post.title = video["title"]
    post.content = f'<p><a href="{video["url"]}">{video["title"]}</a></p>'
    if video["images"]:
        img_html = ""
        for img_url in video["images"]:
            img_id, img_wp_url = upload_image_to_wp(wp, img_url)
            img_html += f'<a href="{video["url"]}"><img src="{img_wp_url}" alt="{video["title"]}"></a><br>\n'
        post.content = img_html + post.content
    post.content += f"<p>{video['description']}</p>"
    tags = [video["label"]] + video["genre"]
    post.terms_names = {"post_tag": list(set(tags))}
    post.post_status = "publish"
    post.custom_fields = [{"key": "release_date", "value": video["release_date"]}]
    wp.call(NewPost(post))
    print("投稿完了:", video["title"])

def main():
    # WordPressログイン
    wp = Client(WP_URL, WP_USER, WP_PASS)

    # 詳細URL抽出
    urls = get_detail_urls_requests(LIST_URL, POST_NUM)
    if not urls:
        urls = get_detail_urls_selenium(LIST_URL, POST_NUM)

    for url in urls:
        info = get_detail_info(url)
        # 投稿済み判定（タイトル or URL重複チェックが必要ならここで）
        create_post(info, wp)
        time.sleep(5)

if __name__ == "__main__":
    main()
