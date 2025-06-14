import sys
import subprocess
import time

# --- 必要パッケージ自動インストール ---
def ensure_import(install_name, import_name, alt_import=None):
    try:
        return __import__(import_name)
    except ImportError:
        print(f"[AUTO INSTALL] pip install {install_name}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", install_name])
        try:
            return __import__(import_name)
        except ImportError:
            if alt_import:
                return __import__(alt_import)
            raise

requests = ensure_import("requests", "requests")
bs4 = ensure_import("beautifulsoup4", "bs4")
selenium = ensure_import("selenium", "selenium")
wp_mod = ensure_import("python-wordpress-xmlrpc", "python_wordpress_xmlrpc", "wordpress_xmlrpc")

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

# --- WordPress設定（自分の環境に合わせてください） ---
WP_URL = "https://your-site.com/xmlrpc.php"
WP_USER = "your_id"
WP_PASS = "your_password"

# --- DMM素人一覧 ---
DMM_LIST_URL = "https://video.dmm.co.jp/amateur/list/?sort=date"
MAX_POST = 3  # 投稿数上限

def get_latest_items():
    print("一覧ページ取得中...(selenium使用)")
    # Chromeヘッドレス
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--window-size=1920x1080')
    chrome_options.add_argument('--user-agent=Mozilla/5.0')
    driver = webdriver.Chrome(options=chrome_options)
    driver.get(DMM_LIST_URL)
    time.sleep(4)  # SPAロード待機
    html = driver.page_source
    driver.quit()
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # SPA構造に合わせ、詳細ページURLだけを抽出
        if "/digital/videoc/-/detail/" in href and href not in items:
            if not href.startswith("http"):
                href = "https://www.dmm.co.jp" + href
            items.append(href)
    print(f"検出: {len(items)}件")
    return items[:MAX_POST]

def scrape_detail(url):
    print("詳細取得: " + url)
    res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    soup = BeautifulSoup(res.content, "html.parser")
    # タイトル
    title = soup.select_one("h1#title, h1[itemprop='name']")
    title = title.text.strip() if title else "タイトル不明"
    # 説明文
    desc = soup.select_one(".mg-b20.lh4, .product-text__info")
    desc = desc.text.strip() if desc else ""
    # 配信日
    date = ""
    for tr in soup.select("tr"):
        if "配信開始日" in tr.text:
            tds = tr.select("td")
            if len(tds) > 1:
                date = tds[1].text.strip()
            else:
                date = tds[0].text.strip()
            break
    # ジャンル
    genre = [a.text for a in soup.select("a[href*='keyword=']")]
    genre = ", ".join(genre)
    # 画像
    images = []
    for img in soup.select("#sample-image-block img"):
        img_url = img.get("src")
        if img_url.startswith("//"):
            img_url = "https:" + img_url
        images.append(img_url)
    thumb = images[0] if images else ""
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
    from python_wordpress_xmlrpc import Client, WordPressPost
    from python_wordpress_xmlrpc.methods.posts import NewPost
    print(f"WordPress投稿: {info['title']}")
    wp = Client(WP_URL, WP_USER, WP_PASS)
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
        "post_tag": info["genre"].split(", ") if info["genre"] else []
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
