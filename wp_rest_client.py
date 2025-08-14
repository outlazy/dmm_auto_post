import os, base64, re, sys, requests
from tenacity import retry, wait_exponential, stop_after_attempt
from wordpress_xmlrpc import Client
from wordpress_xmlrpc.methods import posts, taxonomies, media
from wordpress_xmlrpc.compat import xmlrpc_client

WP_URL = os.environ["WP_URL"].rstrip("/")
if WP_URL.endswith("/xmlrpc.php"):
    WP_URL = WP_URL.rsplit("/xmlrpc.php", 1)[0]
WP_USER = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")
WP_PASS = os.environ.get("WP_PASS", "")

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
            r.raise_for_status()
            return r
        except requests.HTTPError as e:
            last_exc = e
            if e.response is not None and e.response.status_code in (401, 403, 404, 405):
                continue
            raise
        except Exception as e:
            last_exc = e
            continue
    if last_exc: raise last_exc
    raise RuntimeError("_post_any: no candidates provided")

# REST
def rest_ensure_term(name, taxonomy):
    r = _post_any(_rest_candidates(f"wp/v2/{taxonomy}"),
                  json={"name": name}, headers={"Content-Type": "application/json"})
    return r.json().get("id")

def rest_upload_image(img_url):
    bin_ = requests.get(img_url, timeout=25, headers={"User-Agent": "Mozilla/5.0"}).content
    filename = img_url.split("?")[0].split("/")[-1] or "image.jpg"
    r = _post_any(_rest_candidates("wp/v2/media"),
                  data=bin_,
                  headers={"Content-Disposition": f'attachment; filename="{filename}"',
                           "Content-Type": "application/octet-stream"})
    return r.json()["id"]

def rest_create_post(title, content_html, tag_names, category_name, featured_img_url, slug, status, meta):
    tag_ids = [rest_ensure_term(t, "tags") for t in set(tag_names or []) if t]
    cat_id = rest_ensure_term(category_name, "categories") if category_name else None
    featured_id = rest_upload_image(featured_img_url) if featured_img_url else None
    payload = {"title": title, "content": content_html, "status": status,
               "tags": [i for i in tag_ids if i], "slug": slug, "meta": meta or {}}
    if cat_id: payload["categories"] = [cat_id]
    if featured_id: payload["featured_media"] = featured_id
    r = _post_any(_rest_candidates("wp/v2/posts"),
                  json=payload, headers={"Content-Type": "application/json"})
    return r.json()

# XML-RPC
def xmlrpc_client_init():
    return Client(WP_URL + "/xmlrpc.php", WP_USER, WP_PASS)

def xmlrpc_ensure_term(name, taxonomy):
    wp = xmlrpc_client_init()
    tax = "post_tag" if taxonomy == "tags" else ("category" if taxonomy == "categories" else taxonomy)
    existing = wp.call(taxonomies.GetTerms(tax, {"search": name}))
    for t in existing:
        if t.name.lower() == name.lower():
            return t.id
    new_term = taxonomies.WordPressTerm(); new_term.taxonomy = tax; new_term.name = name
    return wp.call(taxonomies.NewTerm(new_term))

def xmlrpc_upload_image(img_url):
    wp = xmlrpc_client_init()
    data = requests.get(img_url, timeout=25, headers={"User-Agent": "Mozilla/5.0"}).content
    filename = img_url.split("?")[0].split("/")[-1] or "image.jpg"
    bits = xmlrpc_client.Binary(data)
    resp = wp.call(media.UploadFile({"name": filename, "type": "image/jpeg", "bits": bits, "overwrite": False}))
    return resp.get("id")

def xmlrpc_create_post(title, content_html, tag_names, category_name, featured_img_url, slug, status, meta):
    wp = xmlrpc_client_init()
    cat_id = xmlrpc_ensure_term(category_name, "categories") if category_name else None
    featured_id = xmlrpc_upload_image(featured_img_url) if featured_img_url else None
    p = posts.WordPressPost()
    p.title = title; p.content = content_html; p.post_status = status
    p.terms_names = {"post_tag": list(set(tag_names or []))}
    if cat_id: p.terms = [{"taxonomy": "category", "term_id": cat_id}]
    p.slug = slug
    if featured_id: p.thumbnail = featured_id
    post_id = wp.call(posts.NewPost(p))
    return {"id": post_id}

# public
def create_post_dual(*, title, content_html, tag_names=None, category_name=None,
                     featured_img_url=None, slug=None, status="publish", meta=None):
    if WP_APP_PASSWORD:
        try:
            return rest_create_post(title, content_html, tag_names, category_name, featured_img_url, slug, status, meta)
        except Exception as e:
            print(f"[DUAL] REST failed: {e}", file=sys.stderr)
    if WP_PASS:
        return xmlrpc_create_post(title, content_html, tag_names, category_name, featured_img_url, slug, status, meta)
    raise RuntimeError("No available auth: set WP_APP_PASSWORD (REST) or WP_PASS (XML-RPC)")
