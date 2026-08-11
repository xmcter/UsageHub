# -*- coding: utf-8 -*-
"""Chrome cookie 提取公共逻辑（macOS）。

从 Chrome 的 Cookies SQLite 里解密指定域名的 cookie。
供 grok / monocloud 等「读浏览器登录态」的 provider 复用。

依赖 cryptography；不可用时相关函数返回 None。
"""
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

try:
    from hashlib import pbkdf2_hmac
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

CHROME_BASE = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
CHROME_PROFILES = ["Default", "Profile 1", "Profile 2", "Profile 3"]


def chrome_safe_storage_pass():
    """Chrome Safe Storage 密码：每台机器随机生成、存在钥匙串里，不能硬编码。"""
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "Chrome Safe Storage", "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return out.stdout.strip()


def read_chrome_cookies(host_filter):
    """扫描全部 Chrome profile，取匹配 host_filter 的 cookie 请求头。

    host_filter: SQL LIKE 片段，如 "%grok.com" 或 "%monocloud.com"。
    返回 (cookie_header, max_last_access_utc)；失败返回 (None, -1)。
    """
    if not _HAS_CRYPTO:
        return None, -1
    safe_pass = chrome_safe_storage_pass()
    if not safe_pass:
        return None, -1
    key = pbkdf2_hmac("sha1", safe_pass.encode("utf-8"), b"saltysalt", 1003, dklen=16)

    best_cookie, best_ts = None, -1
    for profile_name in CHROME_PROFILES:
        db = CHROME_BASE / profile_name / "Cookies"
        if not db.exists():
            continue
        try:
            cookie_str, max_ts = _read_cookie_db(db, key, host_filter)
            if cookie_str and max_ts > best_ts:
                best_ts = max_ts
                best_cookie = cookie_str
        except Exception:
            continue
    return best_cookie, best_ts


def _read_cookie_db(cookie_db, key, host_filter):
    """读单个 profile 的 Cookies 库，返回 (cookie_string, max_last_access_utc)。"""
    # 复制到临时文件避免锁冲突（Chrome 运行时主库可能被锁）
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
    try:
        os.close(tmp_fd)
        shutil.copy2(str(cookie_db), tmp_path)
        conn = sqlite3.connect("file:{}?mode=ro".format(tmp_path), uri=True)
        rows = conn.execute(
            "SELECT name, encrypted_value, last_access_utc FROM cookies "
            "WHERE host_key LIKE ? ORDER BY last_access_utc DESC",
            (host_filter,),
        ).fetchall()
        conn.close()
    except Exception:
        return None, -1
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if not rows:
        return None, -1

    pairs, max_ts = [], -1
    for name, encrypted_value, last_access in rows:
        if last_access and last_access > max_ts:
            max_ts = last_access
        if not encrypted_value:
            continue
        try:
            value = _decrypt_cookie(encrypted_value, key)
            if value:
                pairs.append("{}={}".format(name, value))
        except Exception:
            continue
    if not pairs:
        return None, -1
    return "; ".join(pairs), max_ts


def _decrypt_cookie(encrypted_value, key):
    """解密单个 Chrome cookie（macOS v10 格式：'v10' 前缀 + AES-CBC，IV 为 16 个空格）。"""
    if len(encrypted_value) <= 3:
        return None
    ciphertext = encrypted_value[3:]
    iv = b" " * 16
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(ciphertext) + decryptor.finalize()
    if decrypted:
        pad_len = decrypted[-1]
        if isinstance(pad_len, int) and 1 <= pad_len <= 16:
            decrypted = decrypted[:-pad_len]
    if len(decrypted) > 32:
        return decrypted[32:].decode("utf-8", errors="replace")
    return decrypted.decode("utf-8", errors="replace")
