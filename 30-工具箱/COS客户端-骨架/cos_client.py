# -*- coding: utf-8 -*-
"""腾讯云 COS 最小客户端（HMAC-SHA1 header auth，纯标准库）。

凭证从环境变量读：
    COS_SECRET_ID
    COS_SECRET_KEY
    COS_BUCKET      例如 your-bucket-1250000000
    COS_REGION      例如 ap-hongkong

用法：
    python cos_client.py ls [prefix]
    python cos_client.py put <本地文件> <对象键> [content-type]
    python cos_client.py get <对象键> [输出文件]
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SID = os.environ.get("COS_SECRET_ID", "")
SKEY = os.environ.get("COS_SECRET_KEY", "")
BUCKET = os.environ.get("COS_BUCKET", "")
REGION = os.environ.get("COS_REGION", "ap-hongkong")
HOST = "%s.cos.%s.myqcloud.com" % (BUCKET, REGION)


def _sha1_hex(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def _hmac_sha1_hex(key: str, msg: str) -> str:
    return hmac.new(key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha1).hexdigest()


def _q(s: str) -> str:
    return urllib.parse.quote(s, safe="-_.~")


def _auth(method: str, path: str, params: str, headers: dict | None) -> str:
    if not (SID and SKEY and BUCKET):
        raise SystemExit("缺少 COS_SECRET_ID / COS_SECRET_KEY / COS_BUCKET")
    now = int(time.time())
    sign_time = "%d;%d" % (now, now + 3600)
    sign_key = _hmac_sha1_hex(SKEY, sign_time)

    hdrs = {k.lower(): v for k, v in (headers or {}).items()}
    hdrs["host"] = HOST
    header_list = sorted(hdrs)
    header_str = "&".join("%s=%s" % (_q(k), _q(hdrs[k])) for k in header_list)

    pathname = urllib.parse.quote(path, safe="/")
    pairs = sorted(urllib.parse.parse_qsl(params or "", keep_blank_values=True))
    param_str = "&".join("%s=%s" % (_q(k), _q(v)) for k, v in pairs)
    param_list = ";".join(k for k, _ in pairs)

    http_string = "\n".join([method.lower(), pathname, param_str, header_str]) + "\n"
    string_to_sign = "sha1\n%s\n%s\n" % (sign_time, _sha1_hex(http_string))
    signature = _hmac_sha1_hex(sign_key, string_to_sign)

    return (
        "q-sign-algorithm=sha1&q-ak=%s&q-sign-time=%s&q-key-time=%s"
        "&q-header-list=%s&q-url-param-list=%s&q-signature=%s"
        % (SID, sign_time, sign_time, ";".join(header_list), param_list, signature)
    )


def request(method: str, path: str, params: str = "", data: bytes | None = None,
            headers: dict | None = None, raw: bool = False):
    h = dict(headers or {})
    h["Authorization"] = _auth(method, path, params, headers)
    url = "https://%s%s" % (HOST, path) + (("?" + params) if params else "")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            body = r.read()
            return r.status, (body if raw else body.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read()
        return e.code, (body if raw else body.decode("utf-8", "replace"))


def put(key: str, body: bytes | str, ctype: str = "text/html; charset=utf-8"):
    if isinstance(body, str):
        body = body.encode("utf-8")
    return request("PUT", "/" + key, data=body, headers={"Content-Type": ctype})


def get(key: str):
    return request("GET", "/" + key)


def ls(prefix: str = "", delimiter: str = ""):
    ps = "prefix=%s" % _q(prefix)
    if delimiter:
        ps += "&delimiter=%s" % _q(delimiter)
    return request("GET", "/", ps)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    if cmd == "ls":
        status, body = ls(sys.argv[2] if len(sys.argv) > 2 else "", "/")
        print("STATUS", status)
        print(body[:6000])
    elif cmd == "put":
        if len(sys.argv) < 4:
            print("用法: put <本地文件> <对象键> [content-type]")
            return 1
        local, key = sys.argv[2], sys.argv[3]
        ctype = sys.argv[4] if len(sys.argv) > 4 else "text/html; charset=utf-8"
        with open(local, "rb") as f:
            body = f.read()
        status, resp = put(key, body, ctype)
        print("PUT", key, len(body), "HTTP", status, resp[:300])
    elif cmd == "get":
        if len(sys.argv) < 3:
            print("用法: get <对象键> [输出文件]")
            return 1
        key = sys.argv[2]
        status, body = request("GET", "/" + key, raw=True)
        print("GET", key, "HTTP", status, len(body))
        if len(sys.argv) > 3:
            with open(sys.argv[3], "wb") as f:
                f.write(body)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
