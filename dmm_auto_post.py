import sys, subprocess, os

# 必須パッケージ自動導入＆1回だけ再起動（無限ループ防止）
REQUIRED_PKGS = [
    ("selenium", "selenium"),
    ("bs4", "beautifulsoup4"),
    ("requests", "requests"),
    ("dotenv", "python-dotenv"),
    ("python_wordpress_xmlrpc", "python_wordpress_xmlrpc"),
]
missing = []
for mod, pipname in REQUIRED_PKGS:
    try:
        __import__(mod)
    except ImportError:
        print(f"[AUTO INSTALL] pip install {pipname}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pipname])
        missing.append(mod)
if missing and not os.environ.get("DMM_AUTO_POST_REEXEC"):
    os.environ["DMM_AUTO_POST_REEXEC"] = "1"
    print("★pip導入直後、import反映のため1回だけ自動再実行します")
    os.execve(sys.executable, [sys.executable] + sys.argv, os.environ)

# ---- ここから本体 ----
import time
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
from python_wordpress_xmlrpc import Client, WordPressPost
from python_wordpress_xmlrpc.methods.posts import NewPost
from python_wordpress_xmlrpc.methods.media import UploadFile

# .env で設定
from dotenv import load_dotenv
load_dotenv()
WP_URL      = os.environ.get("WP_URL")
WP_USER     = os.environ.get("WP_USER")
WP_PASS     = os.environ.get("WP_PASS")

DMM_GENRE_URL = "https://video.dmm.co.jp/amateur/list/?genre=79015"

def fetch_video_links():
    # ヘッドレスChromeで年齢認証等も突破
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    driver = webdriver.Chrome(options=options)
    driver.get(DMM_GENRE_URL)
    time.sleep(3)
    html = driver.page_source
    driver.quit()
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select('.d-item')  # 各動画要素
    links = []
    for item in items:
        a = item.find("a", href=True)
        if a:
            links.append("https://video.dmm.co.jp" + a["href"])
    return links

def fetch_video_detail(url):
    res = requests.get(url)
    soup = BeautifulSoup(res.text, "html.parser")
    title = soup.select_one("h1").text.strip() if soup.select_one("h1") else ""
    desc = soup.select_one(".lh4")
    desc = desc.text.strip() if desc else ""
    # サンプル画像
    images = []
    for img in soup.select("#sample-image-block img"):
        src = img.get("src")
        if src and src.startswith("http"):
            images.append(src)
        elif src:
            images.append("https:" + src)
    # 名前・レーベル・ジャンル
    info = soup.find_all("tr")
    name, label, genres = "", "", []
    for tr in info:
        if tr.find("td", string="名前："):
            name = tr.find_all("td")[1].text.strip()
        if tr.find("td", string="レーベル："):
            label = tr.find_all("td")[1].text.strip()
        if tr.find("td", string="ジャンル："):
            genres = [a.text.strip() for a in tr.find_all("a")]
    return {
        "title": title,
        "desc": desc,
        "images": images,
        "name": name,
        "label": label,
        "genres": genres,
        "url": url
    }

def upload_image(wp, img_url):
    img_data = requests.get(img_url).content
    filename = img_url.split("/")[-1]
    data = {
        'name': filename,
        'type': 'image/jpeg',
        'bits': img_data,
    }
    response = wp.call(UploadFile(data))
    return response['id']

def post_to_wordpress(video):
    wp = Client(WP_URL, WP_USER, WP_PASS)
    post = WordPressPost()
    post.title = video["title"]
    # アイキャッチ用画像
    thumb_id = None
    if video["images"]:
        thumb_id = upload_image(wp, video["images"][0])
        post.thumbnail = thumb_id
    # 本文HTML組立
    img_html = ""
    for img in video["images"]:
        img_html += f'<img src="{img}" alt="{video["title"]}" /><br>\n'
    post.content = f"""
{img_html}
{video['desc']}<br>
<a href="{video['url']}" target="_blank">公式ページはこちら</a>
"""
    # タグ：名前・レーベル・ジャンル
    tags = []
    if video["name"]: tags.append(video["name"])
    if video["label"]: tags.append(video["label"])
    tags.extend(video["genres"])
    post.terms_names = {
        "post_tag": tags,
        "category": ["素人動画"]
    }
    post.post_status = "publish"
    wp.call(NewPost(post))
    print("★投稿完了:", video["title"])

def main():
    print("=== DMM素人ジャンルページから最新動画取得 ===")
    links = fetch_video_links()
    print(f"★動画数: {len(links)}")
    for link in links[:3]:  # 例：最新3件だけ
        video = fetch_video_detail(link)
        post_to_wordpress(video)
        time.sleep(10)

if __name__ == "__main__":
    main()
