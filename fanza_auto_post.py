#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FANZA（DMM）アフィリエイトAPIで素人動画（floor=videoc）を自動取得→WordPress投稿
・日本時間（JST）で動作
・APIサンプル画像/タグもiteminfo配下から自動抽出
・ジャンルに「熟女」が含まれる場合は必ずスキップ
・本文には「商品個別の説明文だけ」記載、DMMの注意書きや短文は自動除外
・config.yml等の設定ファイル不要、全て環境変数（GitHub Secrets等）で管理
・サンプル画像は全てWordPressにアップロードし、直リンクは使用しない
"""

import os
import requests
import re
import json
from datetime import datetime
import pytz
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client

DMM_API_URL = "https://api.dmm.com/affiliate/v3/ItemList"

NG_DESCRIPTIONS = [
    "From here on, it will be an adult site",
    "18歳未満",
    "アダルト商品を取り扱う",
    "未成年",
    "成人向け",
    "アダルトサイト",
    "ご利用は18歳以上",
    "18才未満",
]

def now_jst():
    return datetime.now(pytz.timezone('Asia/Tokyo'))

def get_env(key, required=True, default=None):
    val = os.environ.get(key, default)
    if required and not val:
        raise RuntimeError(f"環境変数 {key} が設定されていません")
    return val

def fetch_amateur_videos():
    API_ID = get_env("DMM_API_ID")
    AFF_ID = get_env("DMM_AFFILIATE_ID")
    params = {
        "api_id": API_ID,
        "affiliate_id": AFF_ID,
        "site": "FANZA",
        "service": "digital",
        "floor": "videoc",    # 素人動画
        "sort": "date",
        "output": "json",
        "hits": 20,
    }
    resp = requests.get(DMM_API_URL, params=params, timeout=10)
    try:
        resp.raise_for_status()
    except Exception:
        print("---- DMM API Error ----")
        print(resp.text)
        print("----------------------")
        raise

    items = resp.json().get("result", {}).get("items", [])
    print(f"API取得件数: {len(items)}")
    for item in items:
        print("==== APIアイテム全体 ====")
        print(item)
        siu = item.get("sampleImageURL", {})
        if "sample_l" in siu and "image" in siu["sample_l"]:
            print("sample_l images:", siu["sample_l"]["image"])
        if "sample_s" in siu and "image" in siu["sample_s"]:
            print("sample_s images:", siu["sample_s"]["image"])
    return items

def is_released(item):
    date_str = item.get("date")
    if not date_str:
        return False
    try:
        jst = pytz.timezone('Asia/Tokyo')
        release_date = jst.localize(datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S"))
        return release_date <= now_jst()
    except Exception:
        return True

def contains_jukujo(item):
    # iteminfo->genreから"熟女"を判定
    ii = item.get("iteminfo", {})
    genres = [g.get("name", "") for g in ii.get("genre", []) if "name" in g]
    return "熟女" in genres

def make_affiliate_link(url, aff_id):
    from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs["affiliate_id"] = aff_id
    new_query = urlencode(qs)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

def upload_image(wp, url):
    """
    画像をダウンロードしてWordPressにアップロードする。
    戻り値: (media_id, wp_url) のタプル。失敗時は (None, None)。
    wp_url は必ずWordPress上のURL（FANZAの直リンクは絶対に返さない）。
    """
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.content
        name = os.path.basename(url.split("?")[0])
        if not name or "." not in name:
            name = "image.jpg"
        media_data = {"name": name, "type": "image/jpeg", "bits": xmlrpc_client.Binary(data)}
        res = wp.call(media.UploadFile(media_data))
        print(f"  UploadFile レスポンス keys: {list(res.keys()) if isinstance(res, dict) else res}")
        media_id = res.get("id") if isinstance(res, dict) else None
        # "url" が最優先（WordPressの直接画像URL）
        wp_url = None
        if isinstance(res, dict):
            wp_url = res.get("url") or res.get("link") or res.get("guid")
        if not wp_url:
            print(f"  警告: wp_urlが取得できませんでした。レスポンス: {res}")
            return None, None
        # 念のため、取得したURLがFANZAドメインでないことを確認
        if "dmm.co.jp" in wp_url or "dmm.com" in wp_url or "fanza" in wp_url.lower():
            print(f"  エラー: 取得URLがFANZAドメインです（スキップ）: {wp_url}")
            return None, None
        print(f"  アップロード成功: {name} → {wp_url}")
        return media_id, wp_url
    except Exception as e:
        print(f"画像アップロード失敗: {url} ({e})")
        return None, None

def is_valid_description(desc):
    if not desc:
        return False
    if len(desc) < 30:
        return False
    for ng in NG_DESCRIPTIONS:
        if ng in desc:
            return False
    return True

def _strip_tags(html_fragment):
    """HTMLタグを除去してテキストのみ返す"""
    text = re.sub(r'<[^>]+>', ' ', html_fragment)
    return re.sub(r'\s+', ' ', text).strip()

def _extract_content_id(url):
    """
    URLからFANZAコンテンツIDを抽出する。
    例: video.dmm.co.jp/amateur/content/?id=zarj076 → zarj076
         www.dmm.co.jp/digital/videoc/-/detail/=/cid=zarj076/ → zarj076
    """
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    # ?id=xxx 形式
    qs = parse_qs(parsed.query)
    if "id" in qs:
        return qs["id"][0]
    # /cid=xxx/ 形式
    m = re.search(r'/cid=([^/]+)/', url)
    if m:
        return m.group(1)
    return None

def _parse_sizes_from_html(html):
    """
    HTML全体から身長・バスト・カップ・ウエスト・ヒップを抽出する。
    戻り値: (height, bust, cup, waist, hip) すべてstr or None
    """
    height = bust = cup = waist = hip = None

    # ── 一括パターン: "T161 B88(E) W61 H86" がそのまま存在 ──
    m = re.search(r'T(\d{2,3})\s*B(\d{2,3})\(([A-Z]{1,2})\)\s*W(\d{2,3})\s*H(\d{2,3})', html)
    if m:
        return m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)

    # ── 全metaタグのcontent属性を検索 ──
    for meta_content in re.findall(r'<meta[^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE):
        m = re.search(r'T(\d{2,3})\s*B(\d{2,3})\(([A-Z]{1,2})\)\s*W(\d{2,3})\s*H(\d{2,3})', meta_content)
        if m:
            return m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        # "161/88(E)/61/86" 形式
        m = re.search(r'(\d{2,3})/(\d{2,3})\(([A-Z]{1,2})\)/(\d{2,3})/(\d{2,3})', meta_content)
        if m:
            return m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)

    # ── JSON-LD / inline JSON を検索 ──
    for json_block in re.findall(
        r'(?:<script[^>]*(?:ld\+json|application/json)[^>]*>)(.*?)(?:</script>)',
        html, re.DOTALL | re.IGNORECASE
    ):
        m = re.search(r'T(\d{2,3})\s*B(\d{2,3})\(([A-Z]{1,2})\)\s*W(\d{2,3})\s*H(\d{2,3})', json_block)
        if m:
            return m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        # JSON内の各フィールドを個別に探す
        try:
            jd = json.loads(json_block)
            def find_in_json(obj, key):
                if isinstance(obj, dict):
                    if key in obj:
                        return str(obj[key])
                    for v in obj.values():
                        r = find_in_json(v, key)
                        if r: return r
                elif isinstance(obj, list):
                    for v in obj:
                        r = find_in_json(v, key)
                        if r: return r
                return None
            for hkey in ("height", "身長"):
                v = find_in_json(jd, hkey)
                if v and re.match(r'\d{2,3}', v):
                    height = height or re.search(r'\d{2,3}', v).group()
        except Exception:
            pass

    # ── テーブル行: <th>ラベル</th><td>値</td> ──
    for label, target in [("身長", "height"), ("バスト", "bust"), ("カップ", "cup"),
                           ("ウエスト", "waist"), ("ヒップ", "hip")]:
        m = re.search(
            rf'<(?:th|dt)[^>]*>\s*{label}\s*</(?:th|dt)>\s*<(?:td|dd)[^>]*>(.*?)</(?:td|dd)>',
            html, re.DOTALL | re.IGNORECASE
        )
        if m:
            raw = _strip_tags(m.group(1))
            if target == "height":
                nums = re.findall(r'\d{2,3}', raw)
                if nums: height = nums[0]
            elif target == "bust":
                nums = re.findall(r'\d{2,3}', raw)
                cups = re.findall(r'\b([A-Z]{1,2})\b', raw)
                if nums: bust = nums[0]
                if cups: cup = cups[0]
            elif target == "cup":
                cups = re.findall(r'\b([A-Z]{1,2})\b', raw)
                if cups: cup = cups[0]
            elif target == "waist":
                nums = re.findall(r'\d{2,3}', raw)
                if nums: waist = nums[0]
            elif target == "hip":
                nums = re.findall(r'\d{2,3}', raw)
                if nums: hip = nums[0]

    # ── フリーテキスト検索 ──
    if not height:
        m = re.search(r'身長[^\d]{0,5}(\d{2,3})', html)
        if m: height = m.group(1)
    if not bust:
        m = re.search(r'バスト[^\d]{0,5}(\d{2,3})', html)
        if m: bust = m.group(1)
    if not cup:
        m = re.search(r'カップ[^A-Z]{0,5}([A-Z]{1,2})', html)
        if m: cup = m.group(1)
    if not bust or not cup:
        m = re.search(r'B(\d{2,3})\(([A-Z]{1,2})\)', html)
        if m:
            bust = bust or m.group(1)
            cup  = cup  or m.group(2)
    if not waist:
        m = re.search(r'ウエスト[^\d]{0,5}(\d{2,3})', html)
        if m: waist = m.group(1)
    if not hip:
        m = re.search(r'ヒップ[^\d]{0,5}(\d{2,3})', html)
        if m: hip = m.group(1)

    return height, bust, cup, waist, hip

def _fetch_html(url):
    """共通HTTPヘッダーでHTMLを取得する"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    r = requests.get(url, timeout=15, headers=headers)
    r.raise_for_status()
    return r.text

def fetch_name_and_size_from_detail_page(url, item):
    """
    video.dmm.co.jp/amateur/content/ ページから「名前」「サイズ」を取得し
    "キミカ(27) T158 B87(G) W58 H85" 形式のタイトルを返す。

    ページは年齢認証Cookie(ckcy=1)を付与してアクセスする。
    取得できなかった場合は item["title"] のみを使用。
    """
    # DMM年齢認証bypass Cookie
    AGE_COOKIES = "ckcy=1; cklg=1; age_check_done=1"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Cookie": AGE_COOKIES,
    }

    name = None
    size_str = None

    # ── video.dmm.co.jp/amateur/content/ から名前・サイズを取得 ──
    # アフィリエイトIDなしのクリーンURLで取得
    cid = _extract_content_id(url)
    clean_url = f"https://video.dmm.co.jp/amateur/content/?id={cid}" if cid else url

    try:
        r = requests.get(clean_url, headers=headers, timeout=15, allow_redirects=True)
        html = r.text
        print(f"  ページ取得: {clean_url} ({len(html)} bytes, status={r.status_code})")

        # 年齢認証ページか確認
        if "年齢認証" in html[:3000] or r.status_code in (301, 302, 403):
            print(f"  年齢認証ページまたはリダイレクト → Cookieでリトライ")
            # Cookieをセッションで送り直す
            session = requests.Session()
            session.headers.update(headers)
            # 年齢認証ページを一度踏んでCookieをセット
            session.get("https://www.dmm.co.jp/age_check/=/declared=yes/", timeout=10)
            r = session.get(clean_url, timeout=15)
            html = r.text
            print(f"  リトライ後: ({len(html)} bytes, status={r.status_code})")

        # デバッグ: 名前・サイズ周辺のHTML断片を表示
        for kw in ["名前", "サイズ", "キミカ", "T1", "B8", "W5", "H8"]:
            idx = html.find(kw)
            if idx != -1:
                print(f"  [{kw}] → {html[max(0,idx-30):idx+80].strip()!r}")

        # ── 名前の抽出 ──
        # パターン1: <th>名前</th><td>キミカ(27)</td>
        for label in ["名前", "出演者", "女優"]:
            m = re.search(
                rf'<(?:th|dt)[^>]*>\s*{label}\s*</(?:th|dt)>\s*<(?:td|dd)[^>]*>(.*?)</(?:td|dd)>',
                html, re.DOTALL | re.IGNORECASE
            )
            if m:
                candidate = _strip_tags(m.group(1)).strip()
                if candidate and "認証" not in candidate:
                    name = candidate
                    print(f"  名前取得(テーブル): {name}")
                    break

        # パターン2: "名前" の直後のテキスト
        if not name:
            m = re.search(r'名前[^\w]*([^\s<]{2,20}(?:\(\d+\))?)', html)
            if m:
                candidate = m.group(1).strip()
                if candidate and "認証" not in candidate:
                    name = candidate
                    print(f"  名前取得(テキスト): {name}")

        # ── サイズの抽出 ──
        # パターン1: <th>サイズ</th><td>T158 B87(G) W58 H85</td>
        m = re.search(
            r'<(?:th|dt)[^>]*>\s*サイズ\s*</(?:th|dt)>\s*<(?:td|dd)[^>]*>(.*?)</(?:td|dd)>',
            html, re.DOTALL | re.IGNORECASE
        )
        if m:
            size_str = _strip_tags(m.group(1)).strip()
            print(f"  サイズ取得(テーブル): {size_str}")

        # パターン2: "サイズ" の直後に T/B/W/H 形式
        if not size_str:
            m = re.search(
                r'サイズ[^\w]*(T\d{2,3}\s+B\d{2,3}\([A-Z]+\)\s+W\d{2,3}\s+H\d{2,3})',
                html
            )
            if m:
                size_str = m.group(1).strip()
                print(f"  サイズ取得(テキスト): {size_str}")

        # パターン3: T/B/W/H が直接ページに存在する場合（ラベルなし）
        if not size_str:
            m = re.search(
                r'(T\d{2,3}\s+B\d{2,3}\([A-Z]+\)\s+W\d{2,3}\s+H\d{2,3})',
                html
            )
            if m:
                size_str = m.group(1).strip()
                print(f"  サイズ取得(パターン): {size_str}")

    except Exception as e:
        print(f"  ページ取得失敗: {e}")

    # ── フォールバック: APIのタイトルを名前に使用 ──
    if not name:
        name = item.get("title", "").strip()
        if not name:
            ii = item.get("iteminfo", {})
            actresses = [a.get("name") for a in ii.get("actress", []) if a.get("name")]
            name = actresses[0] if actresses else "不明"
        print(f"  名前フォールバック(API): {name}")

    # ── タイトルを組み立て ──
    if size_str:
        result = f"{name} {size_str}"
    else:
        # サイズが取れなかった場合は個別フィールドから再構成
        h, b, c, w, hp = _parse_sizes_from_html(html) if 'html' in dir() else (None,)*5
        size_parts = []
        if h: size_parts.append(f"T{h}")
        if b and c: size_parts.append(f"B{b}({c})")
        elif b: size_parts.append(f"B{b}")
        if w: size_parts.append(f"W{w}")
        if hp: size_parts.append(f"H{hp}")
        result = f"{name} {' '.join(size_parts)}" if size_parts else name

    print(f"  生成タイトル: {result}")
    return result


def fetch_description_from_detail_page(url, item):
    """
    商品ページからdescription（metaタグまたはJSON-LD内）だけ抽出し、NG文の場合はAPIの説明にフォールバック
    """
    try:
        r = requests.get(url, timeout=10)
        html = r.text

        # 1. metaタグ
        m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if m:
            desc = m.group(1).strip()
            if is_valid_description(desc):
                return desc

        # 2. JSON-LD内の"description"
        m_script = re.search(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
        if m_script:
            try:
                jd = json.loads(m_script.group(1))
                desc = jd.get("description", "")
                if not desc and "subjectOf" in jd and isinstance(jd["subjectOf"], dict):
                    desc = jd["subjectOf"].get("description", "")
                if is_valid_description(desc):
                    return desc.strip()
            except Exception:
                pass
    except Exception as e:
        print(f"商品ページ説明抽出失敗: {e}")

    # 3. APIデータでフォールバック
    ii = item.get("iteminfo", {})
    for key in ("description", "comment", "story"):
        val = item.get(key) or ii.get(key)
        if is_valid_description(val):
            return val
    # 4. なければ自動生成
    cast = "、".join([a["name"] for a in ii.get("actress", []) if "name" in a])
    label = "、".join([l["name"] for l in ii.get("label", []) if "name" in l])
    genres = "、".join([g["name"] for g in ii.get("genre", []) if "name" in g])
    volume = item.get("volume", "")
    base = f"{item['title']}。ジャンル：{genres}。出演：{cast}。レーベル：{label}。収録時間：{volume}。"
    return base if len(base) > 10 else "FANZA（DMM）素人動画の自動投稿です。"

def create_wp_post(item):
    WP_URL = get_env('WP_URL')
    WP_USER = get_env('WP_USER')
    WP_PASS = get_env('WP_PASS')
    CATEGORY = get_env('CATEGORY')
    AFF_ID = get_env('DMM_AFFILIATE_ID')

    wp = Client(WP_URL, WP_USER, WP_PASS)

    # 商品ページから名前・サイズを取得してタイトルを生成
    title = fetch_name_and_size_from_detail_page(item["URL"], item)
    print(f"  生成タイトル: {title}")

    # 投稿済みチェック（生成タイトルで確認）
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if any(p.title == title for p in existing):
        print(f"→ 既投稿: {title}（スキップ）")
        return False

    # サンプル画像URLを取得
    fanza_images = []
    siu = item.get("sampleImageURL", {})
    if "sample_l" in siu and "image" in siu["sample_l"]:
        fanza_images = siu["sample_l"]["image"]
    elif "sample_s" in siu and "image" in siu["sample_s"]:
        fanza_images = siu["sample_s"]["image"]

    if not fanza_images:
        print(f"→ サンプル画像なし: {title}（スキップ）")
        return False

    # 全サンプル画像をWordPressにアップロードし、WP上のURLを取得する
    print(f"  サンプル画像 {len(fanza_images)} 枚をアップロード中...")
    wp_images = []  # (media_id, wp_url) のリスト
    for fanza_url in fanza_images:
        media_id, wp_url = upload_image(wp, fanza_url)
        if wp_url:
            wp_images.append((media_id, wp_url))
        else:
            # アップロード失敗時はスキップ（直リンクは使わない）
            print(f"  ↳ スキップ: {fanza_url}")

    if not wp_images:
        print(f"→ 画像アップロード全失敗: {title}（スキップ）")
        return False

    # サムネイルは1枚目のmedia_id
    thumb_id = wp_images[0][0]

    # タグ（レーベル・メーカー・女優・ジャンル）はiteminfo配下から抽出
    tags = set()
    ii = item.get("iteminfo", {})
    # レーベル
    if "label" in ii and ii["label"]:
        for l in ii["label"]:
            if "name" in l:
                tags.add(l["name"])
    # メーカー
    if "maker" in ii and ii["maker"]:
        for m in ii["maker"]:
            if "name" in m:
                tags.add(m["name"])
    # 女優
    if "actress" in ii and ii["actress"]:
        for a in ii["actress"]:
            if "name" in a:
                tags.add(a["name"])
    # ジャンル
    if "genre" in ii and ii["genre"]:
        for g in ii["genre"]:
            if "name" in g:
                tags.add(g["name"])

    aff_link = make_affiliate_link(item["URL"], AFF_ID)

    # 本文：説明文のみ
    desc = fetch_description_from_detail_page(item["URL"], item)
    if not desc:
        desc = "FANZA（DMM）素人動画の自動投稿です。"

    # 本文にはWordPressにアップロード済みのURLを使用（直リンクなし）
    first_wp_url = wp_images[0][1]
    parts = []
    parts.append(f'<p><a href="{aff_link}" target="_blank"><img src="{first_wp_url}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')
    if desc:
        parts.append(f'<div>{desc}</div>')
    for _, wp_url in wp_images[1:]:
        parts.append(f'<p><img src="{wp_url}" alt="{title}"></p>')
    parts.append(f'<p><a href="{aff_link}" target="_blank"><img src="{first_wp_url}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')

    post = WordPressPost()
    post.title = title
    post.content = "\n".join(parts)
    if thumb_id:
        post.thumbnail = thumb_id
    post.terms_names = {"category": [CATEGORY], "post_tag": list(tags)}
    post.post_status = "publish"
    wp.call(posts.NewPost(post))
    print(f"✔ 投稿完了: {title}")
    return True

def main():
    print(f"[{now_jst().strftime('%Y-%m-%d %H:%M:%S')}] 投稿開始")
    try:
        items = fetch_amateur_videos()
        posted = False
        for item in items:
            if not is_released(item):
                print(f"→ 未発売: {item.get('title')}")
                continue
            if contains_jukujo(item):
                print(f"→ 熟女ジャンル: {item.get('title')}（スキップ）")
                continue
            if create_wp_post(item):
                posted = True
                break  # 1件投稿で終了
        if not posted:
            print("新規投稿なし")
    except Exception as e:
        print(f"エラー: {e}")
    print(f"[{now_jst().strftime('%Y-%m-%d %H:%M:%S')}] 投稿終了")

if __name__ == "__main__":
    main()
