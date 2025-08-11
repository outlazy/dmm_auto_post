# fanza_auto_post.py
import os, re, sys, time, requests
from bs4 import BeautifulSoup
from tenacity import retry, wait_exponential, stop_after_attempt
from slugify import slugify
from wp_rest_client import create_or_update_post

CATEGORY = os.getenv("CATEGORY", "")

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
        t = re.sub(r"(シェア|Tweet|注意|無断転載|著作権|免責).*$", "", t)
        return t

    cands = []
    for tag in soup.find_all(True, attrs={"id": True}):
        if re.search(r"(desc|detail|intro|comment|text|lead|summary)", tag["id"], re.I):
            cands.append(tag)
    for tag in soup.find_all(True, attrs={"class": True}):
        cls = " ".join(tag.get("class", []))
        if re.search(r"(desc|detail|intro|comment|text|lead|summary|article|content)", cls, re.I):
            cands.append(tag)

    texts = []
    for el in cands[:50]:
        txt = clean(el.get_text(" ", strip=True))
        if 80 <= len(txt) <= 1500:
            texts.append(txt)

    if not texts:
        for sel in [("meta", {"property": "og:description"}), ("meta", {"name": "description"})]:
            m = soup.find(*sel)
            if m and m.get("content"):
                texts.append(clean(m["content"]))
                break

    if not texts:
        paras = [clean(p.get_text(" ", strip=True)) for p in soup.find_all("p")]
        paras = [p for p in paras if 60 <= len(p) <= 1000]
        if paras:
            texts.append(sorted(paras, key=len, reverse=True)[0])

    return texts[0] if texts else ""

def summarize(text, max_chars=320):
    sents = re.split(r"[。.!?]\s*", text)
    sents = [s for s in sents if 6 <= len(s) <= 200]
    out, acc = [], 0
    for s in sents:
        if acc + len(s) > max_chars:
            break
        out.append(s)
        acc += len(s)
    return ("。".join(out).strip("。") + ("。" if out else "")) if text else ""

def build_content(product_url, images, description_text):
    body = ""
    if description_text:
        body += f"<p>{description_text}</p>\n"
    body += f'<p>出典：<a href="{product_url}" target="_blank" rel="nofollow">商品ページ</a></p>\n'
    for u in (images or [])[:6]:
        body += f'<p><img src="{u}" referrerpolicy="no-referrer"></p>\n'
    return body

# === ここから下はあなたの取得ロジックに合わせて実装してください ===
# 例として、既に取得済みのデータを投稿する関数を用意します。

def post_product(product_url: str, title: str, tags: list[str], image_urls: list[str], status: str = "publish"):
    html = fetch_html(product_url)
    desc = extract_description(html)
    summary = summarize(desc) if desc else ""
    content_html = build_content(product_url, image_urls, summary)

    # スラッグは日本語でもOKだが、英数に正規化すると重複判定が安定
    base_slug = slugify(title) or re.sub(r"[^a-z0-9\-]+", "-", title.lower()).strip("-")

    create_or_update_post(
        title=title,
        content_html=content_html,
        tag_names=tags,
        category_name=(CATEGORY or None),
        featured_img_url=(image_urls[0] if image_urls else None),
        slug=base_slug,
        status=status,
        meta={"source_url": product_url}
    )

# === ダミー実行例（実運用ではあなたの収集部分から呼び出してください） ===
if __name__ == "__main__":
    # ここは例。あなたの収集部で得たデータを入れて試せます。
    # product_url = "https://example.com/some-product"
    # title = "サンプル商品タイトル"
    # tags = ["タグA", "タグB"]
    # image_urls = ["https://example.com/image1.jpg", "https://example.com/image2.jpg"]
    # post_product(product_url, title, tags, image_urls)
    print("Runner OK: 収集部から post_product(...) を呼んでください")
