# wp_rest_client.py (minimal REST: no term creation, no media upload)
import os, base64, re, sys, requests
from tenacity import retry, wait_exponential, stop_after_attempt

WP_URL = os.environ["WP_URL"].rstrip("/")
if WP_URL.endswith("/xmlrpc.php"):
    WP_URL = WP_URL.rsplit("/xmlrpc.php", 1)[0]
WP_USER = os.environ["WP_USER"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]

print(f"[REST] WP_URL(root)={WP_URL}", file=sys.stderr)

def _auth_header():
    token = base64.b64encode(f"{WP_USER}:{WP_APP_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}

def _rest_candidates(path: str):
    path = path.lstrip("/")
    return [f"{WP_URL}/wp-json/{path}", f"{WP_URL}/?rest_route=/{path}"]

@retry(wait=wait_exponential(multiplier=1, min=2, max=20), stop=stop_after_attempt(3))
def _post_any(paths, json=None, data=None, headers=None, params=None, **kw):
    hdr = {**_auth_header(), **(headers or {})}
    last_exc = None
    for u in paths:
        try:
            r = requests.post(u, params=params, json=json, data=data, headers=hdr, timeout=kw.get("timeout", 30))
            try:
                r.raise_for_status()
                return r
            except requests.HTTPError as e:
                body = (r.text or "")[:300].replace("\n", " ")
                print(f"[REST DEBUG] {r.status_code} at {u} :: {body}", file=sys.stderr)
                last_exc = e
                if r.status_code in (401,403,404,405):
                    continue
                raise
        except Exception as e:
            last_exc = e
            continue
    if last_exc:
        raise last_exc
    raise RuntimeError("_post_any: no candidates provided")

# ==== ここから最小権限モード ====
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
    # ターム作成＆メディアAPIは使わない
    fixed_cat_ids = [int(CATEGORY_ID_ENV)] if CATEGORY_ID_ENV.isdigit() else []
    fixed_tag_ids = _parse_ids(TAG_IDS_ENV)

    safe_slug = re.sub(r"[^a-z0-9\-]+", "-", (slug or title).lower()).strip("-")
    payload = {
        "title": title,
        "content": content_html,
        "status": status,
        "slug": safe_slug,
        "meta": meta or {}
    }
    if fixed_tag_ids:
        payload["tags"] = fixed_tag_ids
    if fixed_cat_ids:
        payload["categories"] = fixed_cat_ids

    r = _post_any(_rest_candidates("wp/v2/posts"),
                  json=payload, headers={"Content-Type": "application/json"})
    return r.json()
