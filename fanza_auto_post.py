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
    """年齢認証クッキー付きでHTMLを取得する"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.dmm.co.jp/",
    }
    # FANZA年齢認証バイパス用クッキー
    cookies = {
        "ckcy": "1",          # 年齢確認済み
        "cklg": "ja",         # 言語
        "age_check_done": "1",
        "dmmtoolbar_disp": "1",
    }
    r = requests.get(url, timeout=15, headers=headers, cookies=cookies)
    r.raise_for_status()
    html = r.text
    # 年齢認証ページが返ってきていないか確認
    if "年齢認証" in html[:500] and len(html) < 5000:
        print(f"  警告: 年齢認証ページが返されました ({url})")
    return html

def _calc_age(birthday_str):
    """
    "YYYY-MM-DD" 形式の誕生日から現在の年齢（整数）を返す。
    計算できない場合は None。
    """
    try:
        from datetime import date
        bday = date.fromisoformat(birthday_str[:10])
        today = date.today()
        age = today.year - bday.year - (
            (today.month, today.day) < (bday.month, bday.day)
        )
        return age
    except Exception:
        return None


def fetch_item_by_cid(cid, api_id, aff_id):
    """
    FANZA ItemList API で CID を指定して商品1件の詳細データを取得する。
    comment フィールドなどに出演者情報が含まれていることがある。
    """
    try:
        params = {
            "api_id": api_id,
            "affiliate_id": aff_id,
            "site": "FANZA",
            "service": "digital",
            "floor": "videoc",
            "cid": cid,
            "output": "json",
        }
        resp = requests.get(DMM_API_URL, params=params, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("result", {}).get("items", [])
        if items:
            print(f"  CID取得成功: {cid}, comment={str(items[0].get('comment',''))[:100]}")
            return items[0]
    except Exception as e:
        print(f"  CID取得失敗: {e}")
    return None


def search_actress_profile(name, api_id, aff_id):
    """
    FANZA ActressSearch API で出演者名を検索し、
    {"size_str": "T158 B87(G) W58 H85", "age": 21} 形式の辞書を返す。
    見つからない場合は None を返す。
    """
    try:
        params = {
            "api_id": api_id,
            "affiliate_id": aff_id,
            "keyword": name,
            "output": "json",
        }
        resp = requests.get(
            "https://api.dmm.com/affiliate/v3/ActressSearch",
            params=params, timeout=10
        )
        resp.raise_for_status()
        actresses = resp.json().get("result", {}).get("actress", [])
        print(f"  ActressSearch「{name}」→ {len(actresses)}件")
        if not actresses:
            return None
        a = actresses[0]
        print(f"  ActressSearch hit: {a}")

        height   = str(a.get("height",   "")).strip()
        bust     = str(a.get("bust",     "")).strip()
        cup      = str(a.get("cup",      "")).strip()
        waist    = str(a.get("waist",    "")).strip()
        hip      = str(a.get("hip",      "")).strip()
        birthday = str(a.get("birthday", "")).strip()

        size_str = None
        if height and bust and cup and waist and hip:
            size_str = f"T{height} B{bust}({cup}) W{waist} H{hip}"

        age = _calc_age(birthday) if birthday else None
        print(f"  size_str={size_str}, age={age}, birthday={birthday}")
        return {"size_str": size_str, "age": age}

    except Exception as e:
        print(f"  ActressSearch失敗: {e}")
    return None


def fetch_name_and_size_from_detail_page(url, item):
    """
    出演者名と体型サイズを取得し
    "キミカ(27) T158 B87(G) W58 H85" 形式のタイトルを返す。

    取得順:
      1. item["title"] (APIタイトル) を名前として使用
      2. FANZA ActressSearch API でサイズ検索
      3. iteminfo.actress[0] の身長・スリーサイズフィールドを使用
      4. サイズが取れない場合は名前のみ
    """
    API_ID = get_env("DMM_API_ID")
    AFF_ID = get_env("DMM_AFFILIATE_ID")

    # ── Step1: 名前は API の item["title"] を優先 ──
    # APIのタイトルには正確な出演者名が入っている
    name = item.get("title", "").strip()
    ii = item.get("iteminfo", {})
    if not name:
        actresses = [a.get("name") for a in ii.get("actress", []) if a.get("name")]
        name = actresses[0] if actresses else "不明"
    print(f"  名前(APIタイトル): {name}")

    # 検索用ベース名（年齢表記を除去）
    base_name = re.sub(r'\(\d+\)', '', name).strip()

    # iteminfo.actress[0] の名前も検索候補に追加
    actress_names = [a.get("name", "").strip() for a in ii.get("actress", []) if a.get("name")]
    search_candidates = []
    for n in [base_name] + actress_names:
        if n and n not in search_candidates:
            search_candidates.append(n)
    print(f"  検索候補名: {search_candidates}")

    # ── Step2: iteminfo.actress にサイズが入っている場合はそれを使う ──
    size_str = None
    if ii.get("actress"):
        a = ii["actress"][0]
        height = str(a.get("height", "")).strip()
        bust   = str(a.get("bust",   "")).strip()
        cup    = str(a.get("cup",    "")).strip()
        waist  = str(a.get("waist",  "")).strip()
        hip    = str(a.get("hip",    "")).strip()
        print(f"  iteminfo.actress[0]: height={height} bust={bust} cup={cup} waist={waist} hip={hip}")
        if height and bust and cup and waist and hip:
            size_str = f"T{height} B{bust}({cup}) W{waist} H{hip}"
            print(f"  サイズ(iteminfo): {size_str}")

    SIZE_PATTERN = re.compile(
        r'T(\d{2,3})\s*B(\d{2,3})\(([A-Z]{1,2})\)\s*W(\d{2,3})\s*H(\d{2,3})'
    )
    AGE_PATTERN = re.compile(r'(?:年齢[：:　]?\s*|（)(\d{1,2})(?:歳|）)|\((\d{1,2})\)')

    page_age = None

    # ── Step2b: www.dmm.co.jp の静的HTMLページからサイズ・年齢を取得 ──
    # video.dmm.co.jp はJS SPAなので、コンテンツIDから www.dmm.co.jp URLを構築して取得する
    cid = _extract_content_id(url)
    if cid:
        dmm_url = f"https://www.dmm.co.jp/digital/videoc/-/detail/=/cid={cid}/"
        print(f"  取得URL(static): {dmm_url}")
        try:
            html = _fetch_html(dmm_url)

            # ページタイトル・og:title から 名前(年齢)T...B... パターンを探す
            for title_text in re.findall(
                r'<title[^>]*>([^<]+)</title>|<meta[^>]+(?:og:title|name=["\']title["\'])[^>]+content=["\']([^"\']+)["\']',
                html, re.IGNORECASE
            ):
                t = (title_text[0] or title_text[1]).strip()
                if not t:
                    continue
                print(f"  ページタイトル候補: {t}")
                # 名前(年齢)T158 B82(B) W59 H86 形式
                fm = re.search(
                    r'(.{1,20}?)\((\d{1,2})\)\s*(T\d{2,3}\s*B\d{2,3}\([A-Z]{1,2}\)\s*W\d{2,3}\s*H\d{2,3})',
                    t
                )
                if fm:
                    page_name = fm.group(1).strip()
                    page_age  = int(fm.group(2))
                    sm = SIZE_PATTERN.search(fm.group(3))
                    if sm and not size_str:
                        size_str = f"T{sm.group(1)} B{sm.group(2)}({sm.group(3)}) W{sm.group(4)} H{sm.group(5)}"
                    # ページから取得した名前をベース名として採用
                    if page_name and not re.search(r'[ａ-ｚＡ-Ｚ0-9]', page_name):
                        base_name = page_name
                        name = page_name
                    print(f"  ページから抽出: name={page_name}, age={page_age}, size={size_str}")
                    break
                # サイズだけ取れる場合
                if not size_str:
                    sm = SIZE_PATTERN.search(t)
                    if sm:
                        size_str = f"T{sm.group(1)} B{sm.group(2)}({sm.group(3)}) W{sm.group(4)} H{sm.group(5)}"
                        print(f"  サイズ(ページタイトル): {size_str}")

            # テーブルやフリーテキストからも探す
            if not size_str:
                h, b, c, w, hip_v = _parse_sizes_from_html(html)
                if h and b and c and w and hip_v:
                    size_str = f"T{h} B{b}({c}) W{w} H{hip_v}"
                    print(f"  サイズ(HTMLパース): {size_str}")

            if page_age is None:
                am = AGE_PATTERN.search(html[:5000])  # ページ冒頭5000文字だけ
                if am:
                    raw = int(am.group(1) or am.group(2))
                    if 18 <= raw <= 60:
                        page_age = raw
                        print(f"  年齢(HTML): {page_age}")

        except Exception as e:
            print(f"  静的ページ取得失敗: {e}")

    # ── Step2c: APIのテキストフィールドからもサイズ・年齢を抽出 ──
    api_text_age = None
    if not size_str or page_age is None:
        text_fields = []
        for key in ("comment", "description", "story"):
            val = item.get(key) or ""
            if val:
                text_fields.append(val)
        for key in ("comment", "description", "story"):
            val = ii.get(key) or ""
            if val:
                text_fields.append(val)
        # CID指定でAPI再取得して comment フィールドも確認
        if cid and (not size_str or page_age is None):
            cid_item = fetch_item_by_cid(cid, API_ID, AFF_ID)
            if cid_item:
                for key in ("comment", "description", "story"):
                    val = cid_item.get(key) or ""
                    if val and val not in text_fields:
                        text_fields.append(val)
        for text in text_fields:
            if not size_str:
                m = SIZE_PATTERN.search(text)
                if m:
                    size_str = f"T{m.group(1)} B{m.group(2)}({m.group(3)}) W{m.group(4)} H{m.group(5)}"
                    print(f"  サイズ(APIテキスト): {size_str}")
            if api_text_age is None:
                m = AGE_PATTERN.search(text)
                if m:
                    raw = int(m.group(1) or m.group(2))
                    if 18 <= raw <= 60:
                        api_text_age = raw
                        print(f"  年齢(APIテキスト): {api_text_age}")
            if size_str and api_text_age is not None:
                break

    # ── Step3: ActressSearch API でプロフィール（サイズ＋年齢）を検索 ──
    # サイズが既にあっても年齢取得のために常に検索する
    actress_api_age = None
    for candidate in search_candidates:
        profile = search_actress_profile(candidate, API_ID, AFF_ID)
        if profile:
            actress_api_age = profile.get("age")
            if not size_str:
                size_str = profile.get("size_str")
            print(f"  ActressSearch「{candidate}」→ age={actress_api_age}, size_str={size_str}")
            break

    # 年齢の優先順位: ページHTML > APIテキスト > ActressSearch
    age = page_age if page_age is not None else (actress_api_age if actress_api_age is not None else api_text_age)

    # ── Step4: 年齢を名前に付加 ──
    # すでに "みなみ(21)" 形式なら age は付けない（二重にならないよう）
    has_age_in_name = bool(re.search(r'\(\d+\)', name))
    if not has_age_in_name and age is not None:
        display_name = f"{base_name}({age})"
    else:
        display_name = name  # 元の名前（年齢付きならそのまま）

    # ── タイトルを組み立て ──
    result = f"{display_name} {size_str}" if size_str else display_name
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
