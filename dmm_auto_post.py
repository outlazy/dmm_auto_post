import sys
import os

print("Pythonバージョン:", sys.version)
print("sys.path:", sys.path)
print("環境変数PATH:", os.environ.get("PATH"))
print("site-packages一覧:")
import glob
for path in sys.path:
    if "site-packages" in path:
        for f in glob.glob(f"{path}/python*xmlrpc*"):
            print("  ", f)

try:
    import python_wordpress_xmlrpc
    print("[OK] import python_wordpress_xmlrpc")
except Exception as e:
    print("[NG] import python_wordpress_xmlrpc:", e)
