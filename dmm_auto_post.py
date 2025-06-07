#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import collections
import collections.abc
# WordPress XML-RPC の互換性パッチ
collections.Iterable = collections.abc.Iterable

import os
import re
import time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from wordpress_xmlrpc import Client, WordPressPost
from wordpress_xmlrpc.methods import media, posts
from wordpress_xmlrpc.methods.posts import GetPosts
from wordpress_xmlrpc.compat import xmlrpc_client
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# ───────────────────────────────────────────────────────────
# 環境変数読み込み & 定数定義
# ───────────────────────────────────────────────────────────
load_dotenv()
WP_URL     = os.getenv("WP_URL")
WP_USER    = os.getenv("WP_USER")
WP_PASS    = os.getenv("WP_PASS")
AFF_ID     = os.getenv("DMM_AFFILIATE_ID")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
LIST_URL   = "http://video.dmm.co.jp/amateur/list/?sort=date"  # http でリクエスト
