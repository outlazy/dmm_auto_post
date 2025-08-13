# --- 先頭のimportやWP_URL/USER/PASSの定義はそのまま ---

def _auth_header():
    token = base64.b64encode(f"{WP_USER}:{WP_APP_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}

# 追加：REST ルート候補を返す
def _rest_candidates(path: str):
    path = path.lstrip("/")
    return [
        f"{WP_URL}/wp-json/{path}",          # 通常
        f"{WP_URL}/?rest_route=/{path}",     # フォールバック
    ]

from tenacity import retry, wait_exponential, stop_after_attempt
import requests

@retry(wait=wait_exponential(multiplier=1, min=2, max=20), stop=stop_after_attempt(3))
def _post_any(paths, json=None, data=None, headers=None, params=None, **kw):
    last_exc = None
    hdr = {**_auth_header(), **(headers or {})}
    for u in paths:
        try:
            r = requests.post(u, params=params, json=json, data=data, headers=hdr, timeout=kw.get("timeout", 30))
            r.raise_for_status()
            return r
        except requests.HTTPError as e:
            last_exc = e
            # 404/405 は次の候補へフォールバック
            if e.response is not None and e.response.status_code in (404, 405):
                continue
            raise
        except Exception as e:
            last_exc = e
            continue
    if last_exc:
        raise last_exc
    raise RuntimeError("_post_any: no candidates provided")

# ▼ ここから下の API 呼び出しを _post_any + _rest_candidates に置き換える ▼

def ensure_term(name, taxonomy):  # taxonomy: 'tags' or 'categories'
    if not name:
        return None
    try:
        r = _post_any(_rest_candidates(f"wp/v2/{taxonomy}"),
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
    r = _post_any(_rest_candidates("wp/v2/media"),
                  data=bin_,
                  headers={
                      "Content-Disposition": f'attachment; filename="{filename}"',
                      "Content-Type": "application/octet-stream"
                  })
    return r.json()["id"]

def create_post(*, title, content_html, tag_names=None, category_name=None,
                featured_img_url=None, slug=None, status="publish", meta=None):
    tag_ids = [ensure_term(t, "tags") for t in set(tag_names or []) if t]
    cat_ids = [ensure_term(category_name, "categories")] if category_name else []

    featured_id = upload_image(featured_img_url) if featured_img_url else None
    safe_slug = re.sub(r"[^a-z0-9\\-]+", "-", (slug or title).lower()).strip("-")

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

    r = _post_any(_rest_candidates("wp/v2/posts"),
                  json=payload, headers={"Content-Type": "application/json"})
    return r.json()
