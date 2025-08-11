# wp_rest_client.py
import os, base64, re, requests
from tenacity import retry, wait_exponential, stop_after_attempt

WP_URL = os.environ["WP_URL"].rstrip("/")
WP_USER = os.environ["WP_USER"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]

def _auth_header():
    token = base64.b64encode(f"{WP_USER}:{WP_APP_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}

@retry(wait=wait_exponential(multiplier=1, min=2, max=20), stop=stop_after_attempt(3))
def _get(url, **kw):
    r = requests.get(url, headers=_auth_header(), timeout=kw.pop("timeout", 15), **kw)
    r.raise_for_status()
    return r

@retry(wait=wait_exponential(multiplier=1, min=2, max=20), stop=stop_after_attempt(3))
def _post(url, json=None, data=None, headers=None, **kw):
    hdr = {**_auth_header(), **(headers or {})}
    r = requests.post(url, json=json, data=data, headers=hdr, timeout=kw.pop("timeout", 30), **kw)
    r.raise_for_status()
    return r

def ensure_term(name, taxonomy):  # taxonomy: 'tags' or 'categories'
    r = _get(f"{WP_URL}/wp-json/wp/v2/{taxonomy}", params={"search": name, "per_page": 1})
    arr = r.json()
    if arr and arr[0]["name"].lower() == name.lower():
        return arr[0]["id"]
    r = _post(f"{WP_URL}/wp-json/wp/v2/{taxonomy}",
              json={"name": name}, headers={"Content-Type": "application/json"})
    return r.json()["id"]

def upload_image(img_url):
    bin_ = requests.get(img_url, timeout=25).content
    filename = img_url.split("?")[0].split("/")[-1] or "image.jpg"
    r = _post(f"{WP_URL}/wp-json/wp/v2/media",
              data=bin_,
              headers={
                  "Content-Disposition": f'attachment; filename="{filename}"',
                  "Content-Type": "application/octet-stream"
              })
    return r.json()["id"]

def create_or_update_post(*, title, content_html, tag_names=None, category_name=None,
                          featured_img_url=None, slug=None, status="publish", meta=None):
    tag_ids = [ensure_term(t, "tags") for t in set(tag_names or [])]
    cat_ids = [ensure_term(category_name, "categories")] if category_name else []

    featured_id = upload_image(featured_img_url) if featured_img_url else None

    payload = {
        "title": title,
        "content": content_html,
        "status": status,
        "tags": tag_ids,
        "categories": cat_ids,
        "meta": meta or {}
    }
    if slug:
        payload["slug"] = re.sub(r"[^a-z0-9\-]+", "-", slug.lower()).strip("-")
    if featured_id:
        payload["featured_media"] = featured_id

    # 同一slugがあれば更新
    if slug:
        rr = _get(f"{WP_URL}/wp-json/wp/v2/posts", params={"slug": payload["slug"]})
        arr = rr.json()
        if arr:
            post_id = arr[0]["id"]
            r = _post(f"{WP_URL}/wp-json/wp/v2/posts/{post_id}",
                      json=payload, headers={"Content-Type": "application/json"})
            return r.json()

    r = _post(f"{WP_URL}/wp-json/wp/v2/posts",
              json=payload, headers={"Content-Type": "application/json"})
    return r.json()
