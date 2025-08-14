# --- 追加: IDを環境変数から受け取れるように ---
CATEGORY_ID_ENV = os.getenv("CATEGORY_ID", "").strip()
TAG_IDS_ENV = os.getenv("TAG_IDS", "").strip()

def _parse_ids(csv):
    if not csv: return []
    out = []
    for x in csv.split(","):
        x = x.strip()
        if x.isdigit(): out.append(int(x))
    return out

# --- 変更: ターム作成を試みず、401なら None を返す ---
def ensure_term(name, taxonomy):
    # 権限が無い環境では新規作成できないので常に None を返してスキップ
    return None

def create_post(*, title, content_html, tag_names=None, category_name=None,
                featured_img_url=None, slug=None, status="publish", meta=None):
    # 既存IDを優先して使う（環境変数）
    fixed_cat_ids = [int(CATEGORY_ID_ENV)] if CATEGORY_ID_ENV.isdigit() else []
    fixed_tag_ids = _parse_ids(TAG_IDS_ENV)

    # 画像アップはOKならやる
    featured_id = upload_image(featured_img_url) if featured_img_url else None
    safe_slug = re.sub(r"[^a-z0-9\\-]+", "-", (slug or title).lower()).strip("-")

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
    if featured_id:
        payload["featured_media"] = featured_id

    r = _post_any(_rest_candidates("wp/v2/posts"),
                  json=payload, headers={"Content-Type": "application/json"})
    return r.json()
