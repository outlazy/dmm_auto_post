# fanza_auto_post.py (minimal REST)
import os, re, requests
from bs4 import BeautifulSoup
from tenacity import retry, wait_exponential, stop_after_attempt
from slugify import slugify
from wp_rest_client import create_post

UA = {"User-Agent": "Mozilla/5.0"}

@retry(wait=wait_exponential(multiplier=1, min=2, max=20), stop=stop_after_attempt(3))
def fetch_html(url):
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    return r.text

def extract_description(html):
    soup = BeautifulSoup(html, "lxml")
    def clean(t):
        t = re.sub(r"\s+", " ", t).strip()
        return t
    # 候補領域
    for tag in soup.find_all(True, id=True):
        if re.search(r"(desc|detail|intro|comment|text|lead|summary)", tag["id"], re.I):
            txt = clean(tag.get_text(" ", strip=True))
            if len(txt) > 30:
                return txt
    # メタdescription
    m = soup.find("meta", {"property": "og:description"}) or soup.find("meta", {"name": "description"})
    if m and m.get("content"):
        return clean(m["content"])
    # 段落から最大のもの
    paras = [clean(p.get_text(" ", strip=True)) for p in soup.find_all("p")]
    paras = [p for p in paras if len(p) > 40]
    return max(paras, key=len) if paras else ""

def build_content(product_url, images, description_text):
    body = ""
    if description_text:
        body += f"<p>{description_text}</p>\n"
    body += f'<p>出典：<a href="{product_url}" target="_blank" rel="nofollow noopener">商品ページ</a></p>\n'
    for u in (images or [])[:6]:
        body += f'<p><img src="{u}" referrerpolicy="no-referrer"></p>\n'
    return body

def post_product(product_url: str, title: str, tags: list[str], image_urls: list[str], status: str = "publish"):
    html = fetch_html(product_url)
    desc = extract_description(html)
    content_html = build_content(product_url, image_urls, desc)
    slug = slugify(title) or re.sub(r"[^a-z0-9\-]+", "-", title.lower()).strip("-")
    create_post(
        title=title,
        content_html=content_html,
        tag_names=[],                # ← ターム作成しない
        category_name=None,          # ← カテゴリ作成しない
        featured_img_url=None,       # ← メディアAPI使わない
        slug=slug,
        status=status,
        meta={"source_url": product_url}
    )

if __name__ == "__main__":
    url = os.getenv("TEST_PRODUCT_URL")
    if url:
        title = os.getenv("TEST_TITLE", "テスト投稿")
        tags = [t.strip() for t in os.getenv("TEST_TAGS", "").split(",") if t.strip()]
        imgs = [u.strip() for u in os.getenv("TEST_IMAGES", "").split(",") if u.strip()]
        post_product(url, title, tags, imgs)
        print("[OK] posted one via TEST_* envs (minimal REST)")
    else:
        print("Runner OK: set TEST_PRODUCT_URL to post once (minimal REST)")
