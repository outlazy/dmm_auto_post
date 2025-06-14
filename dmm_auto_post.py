import sys
import subprocess

print("Python:", sys.executable)
print("sys.path:", sys.path)
try:
    import python_wordpress_xmlrpc
    print("[OK] import python_wordpress_xmlrpc")
except ImportError as e:
    print("[NG] import python_wordpress_xmlrpc:", e)
    print("pip再インストール中...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--force-reinstall", "python-wordpress-xmlrpc"])
    sys.path.append('/opt/hostedtoolcache/Python/3.13.3/x64/lib/python3.13/site-packages')
    try:
        import python_wordpress_xmlrpc
        print("[OK] 再インストール後import成功！")
    except Exception as e2:
        print("[FAIL] 再importも失敗", e2)
        print("site-packages:", sys.path)
