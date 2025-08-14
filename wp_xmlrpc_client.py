# wp_xmlrpc_client.py — XML-RPC posting (no REST)
import os, re, requests
from wordpress_xmlrpc import Client
from wordpress_xmlrpc.methods import posts
from wordpress_xmlrpc.compat import xmlrpc_client

WP_URL = os.environ["WP_URL"].rstrip("/")
if not WP_URL.endswith("/xmlrpc.php"):
    WP_XMLRPC = WP_URL + "/xmlrpc.php"
else:
    WP_XMLRPC = WP_URL
WP_USER = os.environ["WP_USER"]
WP_PASS = os.environ["WP_PASS"]

# 任意：既存カテゴリ/タグID（数値）を使いたい場合に読む
CATEGORY_ID_ENV = os.getenv("CATEGORY_ID", "").strip()
TAG_IDS_ENV = os.getenv("TAG_IDS", "").strip()

def _parse_ids(csv):
    if not csv: return []
    out = []
    for x in csv.split(","):
        x = x.strip()
        if x.isdigit(): out.append(int(x))
    return out

def create_post(*, title, content_html, tag_names=None, category_name=None,
                featured_img_url=None, slug=None, status="publish", meta=None):
    wp = Client(WP_XMLRPC, WP_USER, WP_PASS)

    p = posts.WordPressPost()
    p.title = title
    p.content = content_html
    p.post_status = status
    if slug:
        p.slug = slug

    # 既存IDを直接付与（任意）
    cat_ids = [int(CATEGORY_ID_ENV)] if CATEGORY_ID_ENV.isdigit() else []
    tag_names = list(set(tag_names or []))

    if cat_ids:
        # terms はID指定が少し面倒なので、taxonomy=category の term_id を構成
        p.terms = [{"taxonomy": "category", "term_id": cid} for cid in cat_ids]
    if tag_names:
        p.terms_names = {"post_tag": tag_names}

    post_id = wp.call(posts.NewPost(p))
    return {"id": post_id}
