import sys
import subprocess

try:
    import python_wordpress_xmlrpc
    print("importできた")
except ImportError:
    print("pip再インストール中...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--force-reinstall", "python-wordpress-xmlrpc"])
    import python_wordpress_xmlrpc
    print("再インストール後、import成功！")
