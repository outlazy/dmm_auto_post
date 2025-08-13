# wp_rest_client.py (POST-only compatible)
import os, base64, re, sys, requests
from tenacity import retry, wait_exponential, stop_after_attempt

WP_URL = os.environ["WP_URL"].rstrip("/")
if WP_URL.endswith("/xmlrpc.php"):
    WP_URL = WP_URL.rsplit("/xmlrpc.php", 1)[0]
WP_USER = os.environ["WP_USER"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]

print(f"[REST] WP_URL={WP_URL}/wp-json/", file=sys.stderr)

def _auth_header():
    token = base64.b64encode(f"{WP_USER}:{WP_APP_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}

@retry(wait=wait_exponential(multiplier=1, min=2, max=20), stop=stop_after_attempt(3))
def _post(url, json=None, data=None, headers=None, params=None, **kw):
    hdr = {**_auth_header(), **(headers or {})}
    r = requests.post(url, params=params, json=json, data=data, headers=hdr, timeout=kw.pop("timeout", 30), **kw)
    r.raise_for_status()
    return r

# --- ターム（タグ/カテゴリ）を GET せずに確保 ---
# 既存の場合は WordPress が 400 term_exists を返すので、その ID を使う

def ensure_term(name, taxonomy):  # taxonomy: 'tags' or 'categories'
    if not name:
        return None
    try:
        r = _post(f"{WP_URL}/wp-json/wp/v2/{taxonomy}",
                  json={"name": name}, headers={"Content-Type": "application/json"})
        return r.json().get("id")
    except requests.HTTPError as e:
        if e.response is not None:
            try:
                data = e.response.json()
                if isinstance(data, dict) and data.get("code") == "term_exists":
                    return int(data.get("data", {}).get("term_id"))
            except Exception:
                pass
        raise


def upload_image(img_url):
    bin_ = requests.get(img_url, timeout=25, headers={"User-Agent": "Mozilla/5.0"}).content
    filename = img_url.split("?")[0].split("/")[-1] or "image.jpg"
    r = _post(f"{WP_URL}/wp-json/wp/v2/media",
              data=bin_,
              headers={
                  "Content-Disposition": f'attachment; filename="{filename}"',
                  "Content-Type": "application/octet-stream"
              })
    return r.json()["id"]

# --- 投稿（常に作成） ---
# 重複回避は呼び出し側で .posted.log を使って行う

def create_post(*, title, content_html, tag_names=None, category_name=None,
                featured_img_url=None, slug=None, status="publish", meta=None):
    tag_ids = [ensure_term(t, "tags") for t in set(tag_names or []) if t]
    cat_ids = [ensure_term(category_name, "categories")] if category_name else []

    featured_id = upload_image(featured_img_url) if featured_img_url else None
    safe_slug = re.sub(r"[^a-z0-9\-]+", "-", (slug or title).lower()).strip("-")

    payload = {
        "title": title,
        "content": content_html,
        "status": status,
        "tags": [i for i in tag_ids if i],
        "categories": [i for i in cat_ids if i],
        "slug": safe_slug,
        "meta": meta or {}
    }
    if featured_id:
        payload["featured_media"] = featured_id

    r = _post(f"{WP_URL}/wp-json/wp/v2/posts",
              json=payload, headers={"Content-Type": "application/json"})
    return r.json()
