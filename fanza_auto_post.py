# fanza_auto_post.py （抜粋・サンプル）
import os, re
from bs4 import BeautifulSoup
import requests
from wp_rest_client import create_or_update_post

CATEGORY = os.getenv("CATEGORY", "")
# ここはあなたの既存ロジック: product_url, title, tags, image_urls を用意する想定
# product_url, title, tags, image_urls = ...

def extract_description(html):
    soup = BeautifulSoup(html, "lxml")
    # よくあるセレクタ候補
    cands = []
    for tag in soup.find_all(True, attrs={"id": True}):
        if re.search(r"(desc|detail|intro|comment|text|lead|summary)", tag["id"], re.I):
            cands.append(tag)
    for tag in soup.find_all(True, attrs={"class": True}):
        cls = " ".join(tag.get("class", []))
        if re.search(r"(desc|detail|intro|comment|text|lead|summary|article|content)", cls, re.I):
            cands.append(tag)

    def clean(t):
        t = re.sub(r"\s+", " ", t).strip()
        t = re.sub(r"(シェア|Tweet|注意|無断転載).*?$", "", t)
        return t

    texts = []
    for el in cands[:50]:
        txt = clean(el.get_text(" ", strip=True))
        if 80 <= len(txt) <= 1500:
            texts.append(txt)

    if not texts:
        for sel in [('meta', {'property': 'og:description'}), ('meta', {'name': 'description'})]:
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
        if acc + len(s) > max_chars: break
        out.append(s); acc += len(s)
    return "。".join(out).strip("。") + ("。" if out else "")

def build_content(product_url, images, description_text):
    body = ""
    if description_text:
        body += f"<p>{description_text}</p>\n"
    body += f'<p>出典：<a href="{product_url}" target="_blank" rel="nofollow">商品ページ</a></p>\n'
    for u in (images or [])[:6]:
        body += f'<p><img src="{u}" referrerpolicy="no-referrer"></p>\n'
    return body

def post_to_wp(product_url, title, tags, images):
    html = requests.get(product_url, headers={"User-Agent":"Mozilla/5.0"}, timeout=20).text
    desc = extract_description(html)
    summary = summarize(desc) if desc else ""
    content_html = build_content(product_url, images, summary)
    slug = re.sub(r"[^a-z0-9\-]+", "-", title.lower()).strip("-")

    create_or_update_post(
        title=title,
        content_html=content_html,
        tag_names=tags,
        category_name=CATEGORY or None,
        featured_img_url=(images[0] if images else None),
        slug=slug,
        status="publish",
        meta={"source_url": product_url}
    )

# --- どこかのループ内で ---
# post_to_wp(product_url, title, tags, image_urls)
