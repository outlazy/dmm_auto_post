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

def fetch_name_and_size_from_detail_page(url, item):
    """
    FANZA商品ページ（video.dmm.co.jp/amateur/content/ 等）から
    女優名・身長・バスト・カップ・ウエスト・ヒップを取得し
    "名前 T身長 Bバスト(カップ) Wウエスト Hヒップ" 形式のタイトルを返す。
    例: "せるぴこ T161 B88(E) W61 H86"
    """
    name = None
    height = bust = cup = waist = hip = None

    try:
        r = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        html = r.text

        # デバッグ用: 身長・サイズ周辺のHTML断片を出力
        for kw in ["身長", "バスト", "カップ", "ウエスト", "ヒップ", "T1", "B8", "W6", "H8"]:
            idx = html.find(kw)
            if idx != -1:
                print(f"  [{kw}] ...{html[max(0,idx-40):idx+80]}...")

        # ===================== 名前の抽出 =====================

        # パターンA: <th>名前</th> or <th>出演者</th> or <th>女優</th> の次の <td>
        for label in ["名前", "出演者", "女優", "キャスト", "performer"]:
            m = re.search(
                rf'<th[^>]*>\s*{label}\s*</th>\s*<td[^>]*>(.*?)</td>',
                html, re.DOTALL | re.IGNORECASE
            )
            if m:
                candidate = _strip_tags(m.group(1))
                if candidate:
                    name = candidate
                    break

        # パターンB: data-performer や class="performer" / "actress" 等
        if not name:
            m = re.search(
                r'class="[^"]*(?:performer|actress|cast|name)[^"]*"[^>]*>\s*([^<]{2,30})\s*<',
                html, re.IGNORECASE
            )
            if m:
                candidate = m.group(1).strip()
                if candidate and not re.search(r'[<>{]', candidate):
                    name = candidate

        # パターンC: JSON-LD の actor / performer
        if not name:
            for m_script in re.finditer(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                html, re.DOTALL
            ):
                try:
                    jd = json.loads(m_script.group(1))
                    for key in ("actor", "performer"):
                        actors = jd.get(key, [])
                        if isinstance(actors, dict):
                            actors = [actors]
                        if isinstance(actors, list) and actors:
                            n = actors[0].get("name", "")
                            if n:
                                name = n
                                break
                    if name:
                        break
                except Exception:
                    pass

        # パターンD: <h1> や <title> から名前らしき部分
        if not name:
            m = re.search(r'<h1[^>]*>([^<]{2,40})</h1>', html)
            if m:
                candidate = m.group(1).strip()
                # 数字だらけや長すぎる場合は除外
                if candidate and len(candidate) <= 20:
                    name = candidate

        # ===================== 身長の抽出 =====================

        # パターンA: <th>身長</th> の次の <td>
        m = re.search(
            r'<th[^>]*>\s*身長\s*</th>\s*<td[^>]*>(.*?)</td>',
            html, re.DOTALL | re.IGNORECASE
        )
        if m:
            nums = re.findall(r'\d{2,3}', _strip_tags(m.group(1)))
            if nums:
                height = nums[0]

        # パターンB: "身長：161" / "身長 161" 等
        if not height:
            m = re.search(r'身長[^\d]{0,5}(\d{2,3})', html)
            if m:
                height = m.group(1)

        # パターンC: "T161" 形式がHTMLに直接存在する
        if not height:
            m = re.search(r'\bT(\d{2,3})\b', html)
            if m:
                height = m.group(1)

        # ===================== バスト・カップの抽出 =====================

        # パターンA: <th>バスト</th> の次の <td>
        m = re.search(
            r'<th[^>]*>\s*バスト\s*</th>\s*<td[^>]*>(.*?)</td>',
            html, re.DOTALL | re.IGNORECASE
        )
        if m:
            raw = _strip_tags(m.group(1))
            nums = re.findall(r'\d{2,3}', raw)
            cups = re.findall(r'\b([A-Z]{1,2})\b', raw)
            if nums:
                bust = nums[0]
            if cups:
                cup = cups[0]

        # パターンB: カップを別セルで取得
        if not cup:
            m = re.search(
                r'<th[^>]*>\s*カップ\s*</th>\s*<td[^>]*>(.*?)</td>',
                html, re.DOTALL | re.IGNORECASE
            )
            if m:
                raw = _strip_tags(m.group(1))
                cups = re.findall(r'\b([A-Z]{1,2})\b', raw)
                if cups:
                    cup = cups[0]

        # パターンC: "バスト：88" / "バスト 88" / "B88(E)" 等
        if not bust:
            m = re.search(r'バスト[^\d]{0,5}(\d{2,3})', html)
            if m:
                bust = m.group(1)
        if not cup:
            m = re.search(r'カップ[^A-Z]{0,5}([A-Z]{1,2})', html)
            if m:
                cup = m.group(1)

        # パターンD: "B88(E)" 形式がHTMLに直接存在する
        if not bust or not cup:
            m = re.search(r'\bB(\d{2,3})\(([A-Z]{1,2})\)', html)
            if m:
                bust = bust or m.group(1)
                cup = cup or m.group(2)

        # ===================== ウエストの抽出 =====================

        m = re.search(
            r'<th[^>]*>\s*ウエスト\s*</th>\s*<td[^>]*>(.*?)</td>',
            html, re.DOTALL | re.IGNORECASE
        )
        if m:
            nums = re.findall(r'\d{2,3}', _strip_tags(m.group(1)))
            if nums:
                waist = nums[0]
        if not waist:
            m = re.search(r'ウエスト[^\d]{0,5}(\d{2,3})', html)
            if m:
                waist = m.group(1)
        if not waist:
            m = re.search(r'\bW(\d{2,3})\b', html)
            if m:
                waist = m.group(1)

        # ===================== ヒップの抽出 =====================

        m = re.search(
            r'<th[^>]*>\s*ヒップ\s*</th>\s*<td[^>]*>(.*?)</td>',
            html, re.DOTALL | re.IGNORECASE
        )
        if m:
            nums = re.findall(r'\d{2,3}', _strip_tags(m.group(1)))
            if nums:
                hip = nums[0]
        if not hip:
            m = re.search(r'ヒップ[^\d]{0,5}(\d{2,3})', html)
            if m:
                hip = m.group(1)
        if not hip:
            # H86 形式: "H" の後に2〜3桁の数字、ただし HTML タグの H で誤検知しないよう word boundary を使用
            m = re.search(r'\bH(\d{2,3})\b', html)
            if m:
                hip = m.group(1)

        # ===================== スリーサイズ一括パターン =====================
        # "T161 B88(E) W61 H86" 形式がそのまま含まれている場合
        if not (height and bust and cup and waist and hip):
            m = re.search(
                r'T(\d{2,3})\s+B(\d{2,3})\(([A-Z]{1,2})\)\s+W(\d{2,3})\s+H(\d{2,3})',
                html
            )
            if m:
                height = height or m.group(1)
                bust   = bust   or m.group(2)
                cup    = cup    or m.group(3)
                waist  = waist  or m.group(4)
                hip    = hip    or m.group(5)

        print(f"  取得結果 → 名前:{name} 身長:{height} バスト:{bust} カップ:{cup} ウエスト:{waist} ヒップ:{hip}")

    except Exception as e:
        print(f"名前・サイズ取得失敗: {e}")

    # ===================== APIデータでフォールバック =====================
    if not name:
        ii = item.get("iteminfo", {})
        actresses = [a.get("name") for a in ii.get("actress", []) if a.get("name")]
        if actresses:
            name = actresses[0]
    if not name:
        name = item.get("title", "不明")

    # ===================== タイトルを組み立て =====================
    size_parts = []
    if height:
        size_parts.append(f"T{height}")
    if bust and cup:
        size_parts.append(f"B{bust}({cup})")
    elif bust:
        size_parts.append(f"B{bust}")
    if waist:
        size_parts.append(f"W{waist}")
    if hip:
        size_parts.append(f"H{hip}")

    if size_parts:
        return f"{name} {' '.join(size_parts)}"
    else:
        return name


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
