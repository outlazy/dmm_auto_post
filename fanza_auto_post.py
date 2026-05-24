#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FANZA（DMM）アフィリエイトAPIで素人動画（floor=videoc）を自動取得→WordPress投稿
・日本時間（JST）で動作
・APIサンプル画像/タグもiteminfo配下から自動抽出
・ジャンルに「熟女」が含まれる場合は必ずスキップ
・本文には「商品個別の説明文だけ」記載、DMMの注意書きや短文は自動除外
・config.yml等の設定ファイル不要、全て環境変数（GitHub Secrets等）で管理
・タイトル: 名前 T158 B87(G) W58 H85 形式
"""

import os
import requests
import re
import json
from datetime import datetime, date
import pytz
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client

DMM_API_URL        = "https://api.dmm.com/affiliate/v3/ItemList"
ACTRESS_SEARCH_URL = "https://api.dmm.com/affiliate/v3/ActressSearch"

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
    ii = item.get("iteminfo", {})
    genres = [g.get("name", "") for g in ii.get("genre", []) if "name" in g]
    return "熟女" in genres

def search_actress_profile(name, api_id, aff_id):
    """ActressSearch APIでサイズ情報を取得する。"""
    try:
        params = {
            "api_id": api_id,
            "affiliate_id": aff_id,
            "keyword": name,
            "output": "json",
        }
        resp = requests.get(ACTRESS_SEARCH_URL, params=params, timeout=10)
        resp.raise_for_status()
        actresses = resp.json().get("result", {}).get("actress", [])
        print(f"  ActressSearch「{name}」→ {len(actresses)}件")
        if not actresses:
            return None
        a = actresses[0]
        height = str(a.get("height", "") or "").strip()
        bust   = str(a.get("bust",   "") or "").strip()
        cup    = str(a.get("cup",    "") or "").strip()
        waist  = str(a.get("waist",  "") or "").strip()
        hip    = str(a.get("hip",    "") or "").strip()
        if height and bust and cup and waist and hip:
            size_str = f"T{height} B{bust}({cup}) W{waist} H{hip}"
            print(f"  → size_str={size_str}")
            return size_str
    except Exception as e:
        print(f"  ActressSearch失敗: {e}")
    return None

def fetch_size_from_product_page(url):
    """商品ページのHTMLから「T152 B82(B) W54 H80」形式のサイズを取得する。"""
    try:
        resp = requests.get(url, timeout=10, allow_redirects=True)
        print(f"  商品ページ取得: {resp.url} (status={resp.status_code})")
        html = resp.text
        # HTMLタグ・エンティティを除去してプレーンテキスト化
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'&nbsp;', ' ', text)
        text = re.sub(r'[　\xa0]', ' ', text)  # 全角スペース等
        text = re.sub(r'\s+', ' ', text)
        # 「T152 B82(B) W54 H80」パターンを検索
        m = re.search(r'T(\d+)\s*B(\d+)\(([A-Za-z]+)\)\s*W(\d+)\s*H(\d+)', text)
        if m:
            size_str = f"T{m.group(1)} B{m.group(2)}({m.group(3)}) W{m.group(4)} H{m.group(5)}"
            print(f"  サイズ(商品ページ): {size_str}")
            return size_str
        else:
            print(f"  商品ページにサイズ情報なし（パターン不一致）")
    except Exception as e:
        print(f"  商品ページサイズ取得失敗: {e}")
    return None

def build_title(item):
    """
    "名前 T158 B87(G) W58 H85" 形式のタイトルを返す。
    取得順:
      1. item["title"]       → 名前（年齢表記は除去）
      2. iteminfo.actress[0] → サイズ
      3. 商品ページ          → サイズ（HTML直接スクレイピング）
      4. ActressSearch API   → サイズ
      ※ サイズが取れなければ名前のみ
    """
    API_ID = get_env("DMM_API_ID")
    AFF_ID = get_env("DMM_AFFILIATE_ID")
    ii = item.get("iteminfo", {})

    # Step 1: 名前（年齢表記 (21) などを除去）
    name = item.get("title", "").strip()
    if not name:
        actresses = [a.get("name") for a in ii.get("actress", []) if a.get("name")]
        name = actresses[0] if actresses else "不明"
    display_name = re.sub(r'\(\d+\)', '', name).strip()
    print(f"  名前: {display_name}")

    # Step 2: iteminfo.actress[0] のサイズ
    size_str = None
    if ii.get("actress"):
        a = ii["actress"][0]
        h   = str(a.get("height", "") or "").strip()
        b   = str(a.get("bust",   "") or "").strip()
        c   = str(a.get("cup",    "") or "").strip()
        w   = str(a.get("waist",  "") or "").strip()
        hip = str(a.get("hip",    "") or "").strip()
        if h and b and c and w and hip:
            size_str = f"T{h} B{b}({c}) W{w} H{hip}"
            print(f"  サイズ(iteminfo): {size_str}")

    # Step 3: 商品ページから直接スクレイピング
    if not size_str:
        size_str = fetch_size_from_product_page(item.get("URL", ""))

    # Step 4: ActressSearch API
    if not size_str:
        base_name = re.sub(r'\(\d+\)', '', name).strip()
        for candidate in list(dict.fromkeys([base_name, name])):
            size_str = search_actress_profile(candidate, API_ID, AFF_ID)
            if size_str:
                break

    result = f"{display_name} {size_str}" if size_str else display_name
    print(f"  生成タイトル: {result}")
    return result

def make_affiliate_link(url, aff_id):
    from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
    parsed = urlparse(url)
    qs = dict(parse_qsl(parsed.query))
    qs["affiliate_id"] = aff_id
    new_query = urlencode(qs)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

def upload_image(wp, url):
    try:
        data = requests.get(url, timeout=10).content
        name = os.path.basename(url.split("?")[0])
        media_data = {"name": name, "type": "image/jpeg", "bits": xmlrpc_client.Binary(data)}
        res = wp.call(media.UploadFile(media_data))
        return res.get("id")
    except Exception as e:
        print(f"画像アップロード失敗: {url} ({e})")
        return None

def is_valid_description(desc):
    if not desc:
        return False
    if len(desc) < 30:
        return False
    for ng in NG_DESCRIPTIONS:
        if ng in desc:
            return False
    return True

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
    WP_URL   = get_env('WP_URL')
    WP_USER  = get_env('WP_USER')
    WP_PASS  = get_env('WP_PASS')
    CATEGORY = get_env('CATEGORY')
    AFF_ID   = get_env('DMM_AFFILIATE_ID')

    wp = Client(WP_URL, WP_USER, WP_PASS)
    title = build_title(item)

    # 投稿済みチェック
    existing = wp.call(GetPosts({"post_status": "publish", "s": title}))
    if any(p.title == title for p in existing):
        print(f"→ 既投稿: {title}（スキップ）")
        return False

    # サンプル画像
    images = []
    siu = item.get("sampleImageURL", {})
    if "sample_l" in siu and "image" in siu["sample_l"]:
        images = siu["sample_l"]["image"]
    elif "sample_s" in siu and "image" in siu["sample_s"]:
        images = siu["sample_s"]["image"]

    if not images:
        print(f"→ サンプル画像なし: {title}（スキップ）")
        return False

    thumb_id = upload_image(wp, images[0]) if images else None

    # タグ（レーベル・メーカー・女優・ジャンル）はiteminfo配下から抽出
    tags = set()
    ii = item.get("iteminfo", {})
    if "label" in ii and ii["label"]:
        for l in ii["label"]:
            if "name" in l:
                tags.add(l["name"])
    if "maker" in ii and ii["maker"]:
        for m in ii["maker"]:
            if "name" in m:
                tags.add(m["name"])
    if "actress" in ii and ii["actress"]:
        for a in ii["actress"]:
            if "name" in a:
                tags.add(a["name"])
    if "genre" in ii and ii["genre"]:
        for g in ii["genre"]:
            if "name" in g:
                tags.add(g["name"])

    aff_link = make_affiliate_link(item["URL"], AFF_ID)

    # 本文：説明文のみ
    desc = fetch_description_from_detail_page(item["URL"], item)
    if not desc:
        desc = "FANZA（DMM）素人動画の自動投稿です。"

    parts = []
    parts.append(f'<p><a href="{aff_link}" target="_blank"><img src="{images[0]}" alt="{title}"></a></p>')
    parts.append(f'<p><a href="{aff_link}" target="_blank">{title}</a></p>')
    if desc:
        parts.append(f'<div>{desc}</div>')
    for img in images[1:]:
        parts.append(f'<p><img src="{img}" alt="{title}"></p>')
    parts.append(f'<p><a href="{aff_link}" target="_blank"><img src="{images[0]}" alt="{title}"></a></p>')
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
