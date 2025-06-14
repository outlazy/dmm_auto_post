import sys
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from python_wordpress_xmlrpc import Client, WordPressPost
from python_wordpress_xmlrpc.methods.posts import NewPost

# ---------- 設定 ----------
WORDPRESS_URL = "https://あなたのドメイン/xmlrpc.php"
WORDPRESS_ID = "あなたのID"
WORDPRESS_PW = "あなたのパスワード"

DMM_LIST_URL = "https://video.dmm.co.jp/amateur/list/?sort=date"
DMM_DETAIL_BASE = "https://www.dmm.co.jp"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
MAX_POST = 3  # 1回で投稿する数（最新から）

# ---------- 一覧ページから商品URL収集 ----------
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

# ---------- 詳細ページから情報取得 ----------
def scrape_detail(url):
    print("詳細取得: " + url)
    headers = {"User-Agent": USER_AGENT}
    res = requests.get(url, headers=headers)
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
        if tr.text.strip().startswith("配信開始日"):
            tds = tr.select("td")
            if len(tds) > 1:
                date = tds[1].text.strip()
            else:
                date = tds[0].text.strip()
            break
    # ジャンル
    genre = [a.text for a in soup.select("a[href*='keyword=']")]
    genre = ", ".join(genre)
    # サンプル画像
    images = []
    for img in soup.select("#sample-image-block img"):
        img_url = img.get("src")
        if img_url.startswith("//"):
            img_url = "https:" + img_url
        images.append(img_url)
    # メイン画像（サムネ）
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

# ---------- WordPressへ投稿 ----------
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

# ---------- メイン処理 ----------
def main():
    items = get_latest_items()
    if not items:
        print("動画が見つかりません")
        return
    for url in items:
        info = scrape_detail(url)
        post_to_wordpress(info)
        time.sleep(3)  # 投稿間にインターバル（負荷対策）

if __name__ == "__main__":
    main()
