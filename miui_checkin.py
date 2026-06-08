#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cron: 30 8 * * * miui_checkin.py
new Env('小米社区签到')

小米社区签到 for 青龙面板 / ql-script-hub

环境变量：

单账号：
MIUI_ACCOUNT=手机号或小米账号
MIUI_PASSWORD=密码

多账号：
MIUI_ACCOUNTS=[{"account":"13800138000","password":"xxx"},{"account":"13900139000","password":"yyy"}]

兼容 WebMoniter 变量：
WEBMONITER_MIUI_ACCOUNT
WEBMONITER_MIUI_PASSWORD
WEBMONITER_MIUI_ACCOUNTS

依赖：
pycryptodome

说明：
部分小米社区签到逻辑移植自：
https://github.com/666fy666/WebMoniter
原项目许可证：MIT License
"""

import os
import re
import json
import time
import base64
import random
import string
import hashlib
import binascii
from datetime import datetime, timedelta

import requests

try:
    from Crypto.Cipher import AES, PKCS1_v1_5
    from Crypto.PublicKey import RSA
    from Crypto.Util.Padding import pad
except ImportError:
    AES = None
    PKCS1_v1_5 = None
    RSA = None
    pad = None


# ========== 青龙通知 ==========

try:
    from notify import send

    HAS_NOTIFY = True
    print("✅ 已加载 notify.py 通知模块")
except Exception:
    send = None
    HAS_NOTIFY = False
    print("⚠️ 未加载 notify.py，跳过通知推送")


def notify_user(title: str, content: str):
    print(f"\n{title}\n{content}\n")

    if HAS_NOTIFY and send:
        try:
            send(title, content)
            print("✅ 通知发送完成")
        except Exception as e:
            print(f"❌ 通知发送失败：{e}")


# ========== 小米社区核心参数 ==========

MIUI_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArxfNLkuAQ/BYHzkzVwtu
g+0abmYRBVCEScSzGxJIOsfxVzcuqaKO87H2o2wBcacD3bRHhMjTkhSEqxPjQ/FE
XuJ1cdbmr3+b3EQR6wf/cYcMx2468/QyVoQ7BADLSPecQhtgGOllkC+cLYN6Md34
Uii6U+VJf0p0q/saxUTZvhR2ka9fqJ4+6C6cOghIecjMYQNHIaNW+eSKunfFsXVU
+QfMD0q2EM9wo20aLnos24yDzRjh9HJc6xfr37jRlv1/boG/EABMG9FnTm35xWrV
R0nw3cpYF7GZg13QicS/ZwEsSd4HyboAruMxJBPvK3Jdr4ZS23bpN0cavWOJsBqZ
VwIDAQAB
-----END PUBLIC KEY-----"""


# ========== 工具函数 ==========

def _rand_str(length: int, chars: str = None) -> str:
    chars = chars or (
        string.ascii_letters
        + string.digits
        + "!@#$%^&*()-=_+~`{}[]|:<>?/"
    )
    return "".join(random.choice(chars) for _ in range(length))


def _aes_encrypt(key: str, data: str) -> str:
    if AES is None or pad is None:
        raise RuntimeError("未安装 pycryptodome")

    iv = b"0102030405060708"
    cipher = AES.new(key.encode(), AES.MODE_CBC, iv)
    padded = pad(data.encode(), AES.block_size, style="pkcs7")
    return base64.b64encode(cipher.encrypt(padded)).decode()


def _rsa_encrypt_pem(key_pem: str, data: str) -> str:
    if RSA is None or PKCS1_v1_5 is None:
        raise RuntimeError("未安装 pycryptodome")

    pub = RSA.import_key(key_pem)
    cipher = PKCS1_v1_5.new(pub)
    enc = cipher.encrypt(base64.b64encode(data.encode()))
    return base64.b64encode(enc).decode()


def mask_account(account: str) -> str:
    account = str(account)
    if len(account) >= 7:
        return account[:3] + "****" + account[-4:]
    return "***"


def getenv_first(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return ""


def load_accounts():
    accounts_raw = getenv_first(
        "MIUI_ACCOUNTS",
        "WEBMONITER_MIUI_ACCOUNTS",
    )

    if accounts_raw:
        try:
            data = json.loads(accounts_raw)

            if isinstance(data, dict):
                data = [data]

            accounts = []
            for item in data:
                if not isinstance(item, dict):
                    continue

                account = str(item.get("account", "")).strip()
                password = str(item.get("password", "")).strip()

                if account and password:
                    accounts.append(
                        {
                            "account": account,
                            "password": password,
                        }
                    )

            if accounts:
                return accounts

        except Exception as e:
            raise RuntimeError(f"MIUI_ACCOUNTS 解析失败：{e}")

    account = getenv_first(
        "MIUI_ACCOUNT",
        "WEBMONITER_MIUI_ACCOUNT",
    )
    password = getenv_first(
        "MIUI_PASSWORD",
        "WEBMONITER_MIUI_PASSWORD",
    )

    if account and password:
        return [
            {
                "account": account,
                "password": password,
            }
        ]

    return []


def wait_random_delay():
    random_signin = os.getenv("RANDOM_SIGNIN", "true").lower() == "true"
    max_random_delay = int(os.getenv("MAX_RANDOM_DELAY", "3600"))

    if not random_signin:
        return

    delay = random.randint(0, max_random_delay)

    if delay <= 0:
        return

    run_at = datetime.now() + timedelta(seconds=delay)

    print(f"⏱️ 随机延迟 {delay} 秒")
    print(f"⏰ 预计执行时间：{run_at.strftime('%H:%M:%S')}")

    time.sleep(delay)


# ========== 小米登录与签到 ==========

def _phone_login(account: str, password: str) -> dict:
    password_md5 = hashlib.md5(password.encode()).hexdigest().upper()

    session = requests.Session()

    session.headers.update(
        {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": (
                "Dalvik/2.1.0 "
                "(Linux; U; Android 12; M2007J17C Build/SKQ1.211006.001) "
                "APP/xiaomi.vipaccount APPV/220301 "
                "MK/UmVkbWkgTm90ZSA5IFBybw== "
                "PassportSDK/3.7.8 passport-ui/3.7.8"
            ),
            "Cookie": (
                "deviceId=X0jMu7b0w-jcne-S; "
                "pass_o=2d25bb648d023d7f; "
                "sdkVersion=accountsdk-2020.01.09"
            ),
            "Host": "account.xiaomi.com",
        }
    )

    data = {
        "cc": "+86",
        "qs": "%3F_json%3Dtrue%26sid%3Dmiui_vip%26_locale%3Dzh_CN",
        "callback": "https://api.vip.miui.com/sts",
        "_json": "true",
        "user": account,
        "hash": password_md5,
        "sid": "miui_vip",
        "_sign": "ZJxpm3Q5cu0qDOMkKdWYRPeCwps%3D",
        "_locale": "zh_CN",
    }

    response = session.post(
        "https://account.xiaomi.com/pass/serviceLoginAuth2",
        data=data,
        timeout=15,
    )
    response.raise_for_status()

    text = response.text.replace("&&&START&&&", "")
    auth = json.loads(text)

    ssecurity = auth.get("ssecurity")
    nonce = auth.get("nonce")

    if not ssecurity or nonce is None:
        print("\n========== 小米登录接口返回诊断 ==========")
        
        safe_keys = [
            "code",
            "desc",
            "description",
            "message",
            "reason",
            "status",
            "result",
            "captchaUrl",
            "notificationUrl",
            "location",
            "pwd",
            "userId",
            "securityStatus",
        ]
        
        for key in safe_keys:
            if key in auth:
                value = str(auth.get(key))
                print(f"{key}: {value}")
                
                if key == "notificationUrl" and value and value != "None":
                    with open("miui_notification_url.txt", "w", encoding="utf-8") as f:
                        f.write(value)
                    print("完整安全验证链接已保存到：miui_notification_url.txt")
        
        print("返回字段：", list(auth.keys()))
        print("========================================\n")
        
        raise RuntimeError(
            "小米登录接口未返回 ssecurity/nonce。"
            "通常原因：验证码、二次验证、账号风控、接口参数失效，或该账号不允许这种 SDK 登录。"
        )

    sha1_value = hashlib.sha1(
        f"nonce={nonce}&{ssecurity}".encode()
    ).hexdigest()

    client_sign = (
        base64.encodebytes(binascii.a2b_hex(sha1_value.encode()))
        .decode()
        .strip()
    )

    location = auth.get("location", "")
    if not location:
        return {}

    next_url = (
        location
        + "&_userIdNeedEncrypt=true&clientSign="
        + client_sign
    )

    session.get(next_url, timeout=15)

    return requests.utils.dict_from_cookiejar(session.cookies)


def _get_miui_token() -> str:
    if RSA is None or AES is None or PKCS1_v1_5 is None or pad is None:
        raise RuntimeError("未安装 pycryptodome")

    key = _rand_str(16)
    ts = round(time.time() * 1000)
    uid = _rand_str(27)
    t = round(time.time())
    r = round(time.time())

    payload = (
        "{"
        f'"type":0,'
        f'"startTs":{ts},'
        f'"endTs":{ts},'
        '"env":{"p19":5,"p22":5},'
        '"action":{},'
        '"force":false,'
        '"talkBack":false,'
        f'"uid":"{uid}",'
        f'"nonce":{{"t":{t},"r":{r}}},'
        '"version":"2.0",'
        '"scene":"GROW_UP_CHECKIN"'
        "}"
    )

    s = _rsa_encrypt_pem(MIUI_PUBLIC_KEY, key)
    d = _aes_encrypt(key, payload)

    response = requests.post(
        "https://verify.sec.xiaomi.com/captcha/v2/data"
        "?k=3dc42a135a8d45118034d1ab68213073&locale=zh_CN",
        data={
            "s": s,
            "d": d,
            "a": "GROW_UP_CHECKIN",
        },
        timeout=15,
    )

    if response.status_code != 200:
        return ""

    result = response.json()

    if result.get("msg") == "参数错误":
        return ""

    return (result.get("data") or {}).get("token", "")


def _run_miui_sync(account: str, password: str):
    try:
        if RSA is None:
            return False, "未安装 pycryptodome，请在青龙依赖管理中安装 pycryptodome"

        cookies = _phone_login(account, password)

        if not cookies:
            return False, "登录失败，请检查账号密码，或账号是否需要验证码/二次验证"

        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())

        miui_vip_ph_match = re.findall(
            r"miui_vip_ph=(.*?);",
            cookie_str + ";",
            re.S,
        )

        miui_vip_ph = miui_vip_ph_match[0] if miui_vip_ph_match else ""

        if not miui_vip_ph:
            return False, "未获取到 miui_vip_ph"

        token = _get_miui_token()

        if not token:
            return False, "获取签到 token 失败"

        boundary = "WebKitFormBoundary" + _rand_str(
            16,
            string.ascii_letters + string.digits,
        )

        headers = {
            "Host": "api.vip.miui.com",
            "Accept": "application/json",
            "Cookie": cookie_str,
            "Content-Type": f"multipart/form-data; boundary=----{boundary}",
            "Origin": "https://web.vip.miui.com",
            "Referer": "https://web.vip.miui.com/",
        }

        params = {
            "ref": "vipAccountShortcut",
            "pathname": "/mio/checkIn",
            "version": "dev.231026",
            "miui_vip_ph": miui_vip_ph,
        }

        body = (
            f"------{boundary}\r\n"
            f'Content-Disposition: form-data; name="miui_vip_ph"\r\n'
            f"\r\n"
            f"{miui_vip_ph}\r\n"
            f"------{boundary}\r\n"
            f'Content-Disposition: form-data; name="token"\r\n'
            f"\r\n"
            f"{token}\r\n"
            f"------{boundary}--\r\n"
        )

        response = requests.post(
            "https://api.vip.miui.com/mtop/planet/vip/user/checkinV2",
            headers=headers,
            params=params,
            data=body,
            timeout=15,
        )

        response.raise_for_status()

        result = response.json()

        if result.get("status") == 200:
            msg = "签到成功，获得成长值+" + str(result.get("entity", ""))
        elif result.get("status") == 401:
            return False, "Cookie 失效"
        else:
            return False, result.get("message", "签到失败")

        # 附加任务：拔萝卜
        try:
            response2 = requests.post(
                "https://api.vip.miui.com/api/carrot/pull",
                headers=headers,
                params=params,
                timeout=15,
            )

            if response2.status_code == 200:
                result2 = response2.json()

                if result2.get("code") == 200:
                    carrot_msg = str(
                        (result2.get("entity") or {}).get("message", "")
                    )
                    if carrot_msg:
                        msg += "\n拔萝卜：" + carrot_msg

        except Exception as e:
            msg += f"\n拔萝卜失败：{e}"

        return True, msg

    except requests.RequestException as e:
        return False, f"请求失败：{e}"
    except Exception as e:
        return False, f"执行异常：{e}"


# ========== 主程序 ==========

def main():
    print("=" * 60)
    print("小米社区签到开始")
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)

    wait_random_delay()

    accounts = load_accounts()

    if not accounts:
        msg = (
            "未配置小米社区账号。\n"
            "请设置 MIUI_ACCOUNT / MIUI_PASSWORD，"
            "或设置 MIUI_ACCOUNTS。"
        )
        notify_user("小米社区签到失败", msg)
        return

    print(f"共发现 {len(accounts)} 个账号")

    success_count = 0
    result_lines = []

    for index, item in enumerate(accounts, start=1):
        account = item["account"]
        password = item["password"]
        masked = mask_account(account)

        print(f"\n开始执行第 {index} 个账号：{masked}")

        if index > 1:
            delay = random.randint(5, 15)
            print(f"账号间随机等待 {delay} 秒")
            time.sleep(delay)

        ok, msg = _run_miui_sync(account, password)

        if ok:
            success_count += 1
            title = f"小米社区签到成功 - 账号{index}"
        else:
            title = f"小米社区签到失败 - 账号{index}"

        content = (
            f"账号：{masked}\n"
            f"结果：{msg}\n"
            f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        notify_user(title, content)

        result_lines.append(
            f"账号{index} {masked}：{'成功' if ok else '失败'} - {msg}"
        )

    if len(accounts) > 1:
        summary = "\n".join(result_lines)
        summary += (
            f"\n\n总账号数：{len(accounts)}"
            f"\n成功：{success_count}"
            f"\n失败：{len(accounts) - success_count}"
        )
        notify_user("小米社区签到汇总", summary)

    print("\n" + "=" * 60)
    print(f"小米社区签到完成：成功 {success_count}/{len(accounts)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
