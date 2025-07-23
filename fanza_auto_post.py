import time
import os
import json
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods.posts import NewPost

def bypass_age_verification(driver):
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
    # 商品リストは従来通りの方法で取得（例：API・ファイル・DB・直書きなど）
    # ここは本番の取得ロジックに差し替えてOK！
    product_urls = [
        # 例：Secret管理した一つだけ（複数もOK）
        os.environ.get("PRODUCT_URL"),
        # 追加で他の取得方法やリストをappend可能！
        # "https://www.dmm.co.jp/digital/videoa/-/detail/=/cid=xxx/",
    ]
    for url in product_urls:
        if not url:
            print("PRODUCT_URL 未指定（空データ）なのでスキップ")
            continue
        data = get_fanza_product(url)
        if data["description"]:
            post_to_wordpress(data)
        else:
            print(f"{url} 説明文が取得できなかったため、投稿をスキップします。")
