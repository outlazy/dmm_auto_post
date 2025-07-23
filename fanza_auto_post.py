import time
import os
import json
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods.posts import NewPost

def bypass_age_verification(driver):
    """FANZA年齢認証ページを自動突破"""
    try:
        btns = driver.find_elements(
            By.XPATH,
            "//a[contains(@href, 'age_check') or contains(text(),'同意') or contains(text(),'ENTER') or contains(text(),'入場')]"
        )
        if btns:
            print("[debug] 年齢認証ページ検知→自動ボタン押し")
            btns[0].click()
            time.sleep(2)
        else:
            print("[debug] 年齢認証ボタンが見つからない")
    except Exception as e:
        print("[debug] 年齢認証突破エラー:", e)

def get_fanza_product(url):
    """FANZA商品ページから情報取得＆認証突破"""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=chrome_options)
    driver.get(url)
    time.sleep(2)
    bypass_age_verification(driver)
    time.sleep(2)

    try:
        title = driver.title.strip()
    except:
        title = ""
    print(f"[debug] Page title: {title}")

    # description取得（<meta name="description"> or <script type="application/ld+json">）
    try:
        description = driver.find_element(By.CSS_SELECTOR, "meta[name='description']").get_attribute("content")
        print(f"[debug] 商品説明: {description[:100]}...")
    except:
        try:
            ldjsons = driver.find_elements(By.XPATH, "//script[@type='application/ld+json']")
            description = ""
            for tag in ldjsons:
                j = json.loads(tag.get_attribute("innerHTML"))
                if "description" in j:
                    description = j["description"]
                    break
            print(f"[debug] ld+jsonから商品説明取得: {description[:100]}...")
        except:
            description = ""
            print("[debug] 商品説明取得できず")

    # メイン画像例（サムネ）
    try:
        img = driver.find_element(By.CSS_SELECTOR, "meta[property='og:image']").get_attribute("content")
    except:
        img = ""
    print(f"[debug] メイン画像: {img}")

    driver.quit()
    return {
        "title": title,
        "description": description,
        "img": img,
        "url": url,
    }

def post_to_wordpress(data):
    """WordPress投稿処理"""
    wp_url = os.environ.get("WP_URL")
    wp_user = os.environ.get("WP_USER")
    wp_pass = os.environ.get("WP_PASS")
    category = os.environ.get("CATEGORY", "FANZA")

    client = Client(wp_url, wp_user, wp_pass)
    post = WordPressPost()
    post.title = data["title"]
    post.content = (
        f'<img src="{data["img"]}"><br>'
        f'<a href="{data["url"]}" target="_blank">商品ページはこちら</a><br><br>'
        f'{data["description"]}'
    )
    post.terms_names = {
        "category": [category]
    }
    post.post_status = "publish"
    post.id = client.call(NewPost(post))
    print(f"[debug] 投稿完了！記事ID: {post.id}")

if __name__ == "__main__":
    # ★商品URLは「GITHUB ACTIONSのenvまたはargsから取得」にすると便利！
    # 例: secretsからDMM商品URLリスト渡す場合
    product_url = os.environ.get("PRODUCT_URL")
    if not product_url:
        print("PRODUCT_URL 環境変数が未指定です。")
        exit(1)
    data = get_fanza_product(product_url)
    if data["description"]:
        post_to_wordpress(data)
    else:
        print("説明文が取得できなかったため、投稿をスキップします。")
