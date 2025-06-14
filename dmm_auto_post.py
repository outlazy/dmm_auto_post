import sys
import subprocess

# site-packagesの絶対パスを強制追加
sys.path.append('/opt/hostedtoolcache/Python/3.13.3/x64/lib/python3.13/site-packages')

def ensure(pkg, import_name=None):
    import_name = import_name or pkg.replace('-', '_')
    try:
        __import__(import_name)
    except ImportError:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', pkg])
        __import__(import_name)

ensure('requests')
ensure('lxml')
ensure('python-wordpress-xmlrpc', 'python_wordpress_xmlrpc')

import requests
from lxml import html
from python_wordpress_xmlrpc import Client, WordPressPost
from python_wordpress_xmlrpc.methods.posts import NewPost

# ---（以下いつもの自動投稿処理）---

print("全てimport成功！")
