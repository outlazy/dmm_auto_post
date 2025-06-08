import sys, subprocess, os

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
if missing:
    print("★ pipインストール直後はPythonのimportに反映されないことがあるため自動再実行します")
    os.execv(sys.executable, [sys.executable] + sys.argv)  # <--- ここで完全再起動

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from python_wordpress_xmlrpc import Client, WordPressPost
from python_wordpress_xmlrpc.methods.posts import NewPost, GetPosts
from python_wordpress_xmlrpc.methods.media import UploadFile
from python_wordpress_xmlrpc.compat import xmlrpc_client
import requests, time, re

# --- 環境変数 .env で管理（例：WP_URL, WP_USER, WP_PASS, AFF_ID） ---
load_dotenv()
WP_URL = os.getenv("WP_URL")
WP_USER = os.getenv("WP_USER")
WP_PASS = os.getenv("WP_PASS")
AFF_ID = os.getenv("AFF_ID", "dmm_affiliate-xxxx")
LIST_URL = "https://video.dmm.co.jp/amateur/list/?genre=79015"
MAX_POSTS = 3  # 投稿件数

def get_driver():
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=opts)

def get_video_links():
    driver = get_driver()
    driver.get(LIST_URL)
    time.sleep(3)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    driver.quit()
    urls = []
    for box in soup.select("li.list-box"):
        # 予約/未配信商品は除外
        if box.find("span", class_="icon-reserve") or box.find("span", class_="icon-pre"): continue
        a = box.find("a", href=True)
        if a:
            url = a["href"]
            if not url.startswith("http"):
                url = "https://video.dmm.co.jp" + url
            urls.append(url)
        if len(urls) >= MAX_POSTS: break
    return urls

def upload_image(wp, url):
    resp = requests.get(url)
    data = {
        'name': url.split('/')[-1],
        'type': 'image/jpeg',
        'bits': xmlrpc_client.Binary(resp.content)
    }
    return wp.call(UploadFile(data))

def fetch_and_post(detail_url, wp):
    driver = get_driver()
    driver.get(detail_url)
    time.sleep(2)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    driver.quit()
    title = soup.find("title").text.split(" - ")[0].strip()
    # サンプル画像
    sample_imgs = []
    img_block = soup.find("div", id="sample-image-block")
    if img_block:
        for a in img_block.find_all("a"):
            img = a.find("img")
            if img and img.get("src"):
                sample_imgs.append(img["src"] if img["src"].startswith("http") else "https:" + img["src"])
    desc_block = soup.find("div", class_="mg-b20 lh4")
    desc = desc_block.text.strip() if desc_block else ""
    label = ""
    for tr in soup.find_all("tr"):
        th = tr.find("td", class_="nw")
        if th and "レーベル" in th.text: label = tr.find_all("td")[-1].text.strip()
    genres = []
    for tr in soup.find_all("tr"):
        th = tr.find("td", class_="nw")
        if th and "ジャンル" in th.text:
            genres = [g.text.strip() for g in tr.find_all("a")]
    name = ""
    for tr in soup.find_all("tr"):
        th = tr.find("td", class_="nw")
        if th and "名前" in th.text: name = tr.find_all("td")[-1].text.strip()
    aff_url = f"https://al.dmm.co.jp/?lurl={detail_url}&af_id={AFF_ID}&ch=01&ch_id=link"
    content = f'<p><a href="{aff_url}"><img src="{sample_imgs[0]}" alt="{title}"></a></p>' if sample_imgs else ""
    content += f'<p>{desc}</p>'
    content += f'<p><a href="{aff_url}">公式ページで見る</a></p>'
    tags = list(set([label] + genres + ([name] if name else [])))
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if existing: print(f"→ Skipping duplicate: {title}"); return False
    post = WordPressPost()
    post.title = title
    post.content = content
    post.terms_names = {'post_tag': tags}
    if sample_imgs:
        image = upload_image(wp, sample_imgs[0])
        post.thumbnail = image['id']
    wp.call(NewPost(post))
    print(f"✔ Posted: {title}")
    return True

def main():
    wp = Client(WP_URL, WP_USER, WP_PASS)
    urls = get_video_links()
    for u in urls:
        try: fetch_and_post(u, wp)
        except Exception as e: print(f"[ERROR] {u}: {e}")

if __name__ == "__main__":
    main()
