import os
import requests, xml.etree.ElementTree as ET
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts

def fetch_dmm_videos(hits=5):
    url = 'https://api.dmm.com/affiliate/v3/ItemList'
    params = {
        'api_id': os.getenv('DMM_API_ID'),
        'affiliate_id': os.getenv('DMM_AFFILIATE_ID'),
        'site': 'DMM.com',
        'floor': 'video',
        'hits': hits,
        'sort': 'release_date',
        'output': 'xml'
    }
    res = requests.get(url, params=params)
    root = ET.fromstring(res.text)
    items = []
    for item in root.findall('.//item'):
        items.append({
            'title':       item.findtext('title'),
            'url':         item.findtext('affiliateURL'),
            'image_url':   item.findtext('largeImageURL'),
            'description': item.findtext('description'),
            'genres':      [g.text for g in item.findall('genre')],
            'actors':      [a.text for a in item.findall('actor')]
        })
    return items

def post_to_wp(item):
    client = Client(os.getenv('WP_URL'), os.getenv('WP_USER'), os.getenv('WP_PASS'))
    # 画像アップロード
    img_bytes = requests.get(item['image_url']).content
    data = {'name': os.path.basename(item['image_url']), 'type': 'image/jpeg'}
    media_item = media.UploadFile(data, img_bytes)
    response = client.call(media_item)
    # 投稿
    post = WordPressPost()
    post.title = item['title']
    post.content = (
        f'<p><a href="{item["url"]}" target="_blank">'
        f'<img src="{response.url}" alt="{item["title"]}"></a></p>'
        f'<p>{item["description"]}</p>'
        f'<p><a href="{item["url"]}" target="_blank">▶ 購入はこちら</a></p>'
    )
    post.thumbnail = response.id
    post.terms_names = {
        'category': ['DMM動画', '自動投稿'],
        'post_tag': item['genres'] + item['actors']
    }
    post.post_status = 'publish'
    client.call(posts.NewPost(post))

def main():
    for vid in fetch_dmm_videos():
        post_to_wp(vid)

if __name__ == '__main__':
    main()
