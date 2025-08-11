import os, re, time, base64
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from tenacity import retry, wait_exponential, stop_after_attempt

WP_URL = os.environ["WP_URL"].rstrip("/")
WP_USER = os.environ["WP_USER"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]

def wp_auth_header():
    token = base64.b64encode(f"{WP_USER}:{WP_APP_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}

@retry(wait=wait_exponential(multiplier=1, min=2, max=20), stop=stop_after_attempt(3))
def get_html(url, timeout=15):
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.text

def extract_description(html, base_url):
    soup = BeautifulSoup(html, "lxml")

    # 1) 明示的セレクタ候補
    candidates = []
    for tag in soup.find_all(True, attrs={"id": True}):
        if re.search(r"(desc|detail|intro|comment|text|lead|summary)", tag["id"], re.I):
            candidates.append(tag)
    for tag in soup.find_all(True, attrs={"class": True}):
        cls = " ".join(tag.get("class", []))
        if re.search(r"(desc|detail|intro|comment|text|lead|summary|article|content)", cls, re.I):
            candidates.append(tag)

    # 2) 文字数でスコアリング（短すぎ/長すぎ除外）
    def clean_text(t):
        t = re.sub(r"\s+", " ", t)
        # よくあるノイズを刈り取る
        t = re.sub(r"(シェア|Tweet|注意|禁止|著作権|無断転載|※).*?$", "", t)
        return t.strip()

    texts = []
    for el in candidates[:50]:
        txt = clean_text(el.get_text(" ", strip=True))
        if 80 <= len(txt) <= 1500:
            texts.append(txt)

    # 3) fallback: meta description / og:description
    if not texts:
        for sel in [
            ('meta', {'property': 'og:description'}),
            ('meta', {'name': 'description'}),
        ]:
            m = soup.find(*sel)
            if m and m.get("content"):
                txt = clean_text(m["content"])
                if len(txt) >= 40:
                    texts.append(txt)
                    break

    # 4) さらにfallback: 最長段落
    if not texts:
        paras = [clean_text(p.get_text(" ", strip=True)) for p in soup.find_all("p")]
        paras = [p for p in paras if 60 <= len(p) <= 1000]
        if paras:
            texts.append(sorted(paras, key=len, reverse=True)[0])

    return texts[0] if texts else ""

def summarize(text, max_chars=320):
    # 抽出型の簡易サマライザ（安全・軽量）
    sents = re.split(r"[。.!?]\s*", text)
    sents = [s for s in sents if 6 <= len(s) <= 200]
    uniq, seen = [], set()
    for s in sents:
        key = s[:30]
        if key not in seen:
            uniq.append(s)
            seen.add(key)
        if sum(len(x) for x in uniq) > max_chars:
            break
    out = "。".join(uniq).strip("。")
    return (out + "。") if out else text[:max_chars]

def ensure_term(name, taxonomy):
    # taxonomy: 'tags' or 'categories'
    r = requests.get(f"{WP_URL}/wp-json/wp/v2/{taxonomy}", params={"search": name, "per_page": 1},
                     headers=wp_auth_header(), timeout=15)
    r.raise_for_status()
    data = r.json()
    if data and data[0]["name"].lower() == name.lower():
        return data[0]["id"]
    # create
    r = requests.post(f"{WP_URL}/wp-json/wp/v2/{taxonomy}",
                      headers={**wp_auth_header(), "Content-Type": "application/json"},
                      json={"name": name}, timeout=15)
    r.raise_for_status()
    return r.json()["id"]

def upload_image(img_url):
    img_bin = requests.get(img_url, timeout=20).content
    filename = img_url.split("?")[0].split("/")[-1] or "image.jpg"
    r = requests.post(f"{WP_URL}/wp-json/wp/v2/media",
                      headers={**wp_auth_header(), "Content-Disposition": f'attachment; filename="{filename}"'},
                      data=img_bin, timeout=30)
    r.raise_for_status()
    return r.json()["id"]

def create_or_update_post(title, content_html, tag_names, category_name, featured_img_url=None, slug=None, status="publish", meta=None):
    tag_ids = [ensure_term(t, "tags") for t in set(tag_names)]
    cat_id = ensure_term(category_name, "categories") if category_name else None

    featured_id = upload_image(featured_img_url) if featured_img_url else None

    payload = {
        "title": title,
        "content": content_html,
        "status": status,
        "tags": tag_ids,
        "slug": slug,
        "meta": meta or {}
    }
    if cat_id: payload["categories"] = [cat_id]
    if featured_id: payload["featured_media"] = featured_id

    # 重複チェック（同slugがあれば更新）
    if slug:
        rr = requests.get(f"{WP_URL}/wp-json/wp/v2/posts", params={"slug": slug}, headers=wp_auth_header(), timeout=15)
        rr.raise_for_status()
        arr = rr.json()
        if arr:
            post_id = arr[0]["id"]
            r = requests.post(f"{WP_URL}/wp-json/wp/v2/posts/{post_id}",
                              headers={**wp_auth_header(), "Content-Type": "application/json"},
                              json=payload, timeout=20)
            r.raise_for_status()
            return r.json()

    r = requests.post(f"{WP_URL}/wp-json/wp/v2/posts",
                      headers={**wp_auth_header(), "Content-Type": "application/json"},
                      json=payload, timeout=20)
    r.raise_for_status()
    return r.json()

def build_post_from_product(product_url, title, tags, image_urls):
    html = get_html(product_url)
    desc = extract_description(html, product_url)
    summary = summarize(desc) if desc else ""
    hero = image_urls[0] if image_urls else None

    content = ""
    if summary:
        content += f"<p>{summary}</p>\n"
    content += f'<p>出典：<a href="{product_url}" target="_blank" rel="nofollow">商品ページ</a></p>\n'
    if image_urls:
        imgs = "".join([f'<p><img src="{u}" referrerpolicy="no-referrer"></p>\n' for u in image_urls[:6]])
        content += imgs

    slug = re.sub(r"[^a-z0-9\-]+", "-", title.lower()).strip("-")
    return content, hero, slug, tags

# --- 使い方（既存の収集部分に続けて） ---
# product_url, title, tags, image_urls = ... # 既存ロジックで取得済みとする
# content, hero, slug, tags = build_post_from_product(product_url, title, tags, image_urls)
# create_or_update_post(title, content, tags, os.getenv("CATEGORY"), featured_img_url=hero, slug=slug, status="publish",
#                       meta={"source_url": product_url})
