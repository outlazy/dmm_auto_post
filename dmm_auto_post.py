import sys, subprocess, os, time, re
# pip自動
for pkg in ["selenium", "python-dotenv", "python_wordpress_xmlrpc", "beautifulsoup4", "requests"]:
    try: __import__(pkg.replace("-", "_"))
    except ImportError: subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from python_wordpress_xmlrpc import Client, WordPressPost
from python_wordpress_xmlrpc.methods.posts import NewPost, GetPosts
from python_wordpress_xmlrpc.methods.media import UploadFile
from python_wordpress_xmlrpc.compat import xmlrpc_client
import requests

load_dotenv()
WP_URL = os.getenv("WP_URL")
WP_USER = os.getenv("WP_USER")
WP_PASS = os.getenv("WP_PASS")
AFF_ID = os.getenv("AFF_ID", "dmm_affiliate-xxxx")
LIST_URL = "https://video.dmm.co.jp/amateur/list/?genre=79015"
MAX_POSTS = 3  # 何件投稿するか

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
    # タイトル
    title = soup.find("title").text.split(" - ")[0].strip()
    # サンプル画像
    sample_imgs = []
    img_block = soup.find("div", id="sample-image-block")
    if img_block:
        for a in img_block.find_all("a"):
            img = a.find("img")
            if img and img.get("src"):
                sample_imgs.append(img["src"] if img["src"].startswith("http") else "https:" + img["src"])
    # 本文
    desc_block = soup.find("div", class_="mg-b20 lh4")
    desc = desc_block.text.strip() if desc_block else ""
    # ラベル
    label = ""
    for tr in soup.find_all("tr"):
        th = tr.find("td", class_="nw")
        if th and "レーベル" in th.text: label = tr.find_all("td")[-1].text.strip()
    # ジャンル
    genres = []
    for tr in soup.find_all("tr"):
        th = tr.find("td", class_="nw")
        if th and "ジャンル" in th.text:
            genres = [g.text.strip() for g in tr.find_all("a")]
    # 名前
    name = ""
    for tr in soup.find_all("tr"):
        th = tr.find("td", class_="nw")
        if th and "名前" in th.text: name = tr.find_all("td")[-1].text.strip()
    # アフィリエイトリンク
    m = re.search(r'/cid=([^/]+)/', detail_url)
    cid = m.group(1) if m else ""
    aff_url = f"https://al.dmm.co.jp/?lurl={detail_url}&af_id={AFF_ID}&ch=01&ch_id=link"
    # WordPress本文
    content = f'<p><a href="{aff_url}"><img src="{sample_imgs[0]}" alt="{title}"></a></p>' if sample_imgs else ""
    content += f'<p>{desc}</p>'
    content += f'<p><a href="{aff_url}">公式ページで見る</a></p>'
    # タグ
    tags = list(set([label] + genres + ([name] if name else [])))
    # 投稿済み重複チェック
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if existing: print(f"→ Skipping duplicate: {title}"); return False
    # WP投稿
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
