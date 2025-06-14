import requests

# WP情報（例）
WP_URL = "https://example.com/wp-json/wp/v2/posts"
WP_USER = "your_username"
WP_PASS = "your_application_password"  # アプリケーションパスワード推奨

# 投稿内容
data = {
    "title": "テスト投稿",
    "status": "publish",
    "content": "Python3.13から投稿できたらOKです。",
    "tags": [],  # タグIDを入れる
}

# ベーシック認証
from requests.auth import HTTPBasicAuth
auth = HTTPBasicAuth(WP_USER, WP_PASS)

# 投稿
response = requests.post(WP_URL, json=data, auth=auth)
print(response.status_code)
print(response.json())
