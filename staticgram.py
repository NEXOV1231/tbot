"""Клиент StaticGram Bot API + проверка подписи Mini App initData.

StaticGram совместим с Telegram Bot API: https://docs.staticgram.top
Проверка initData — стандартный telegram-алгоритм (HMAC-SHA256).
"""
import hashlib
import hmac
import json
import secrets
import time
import urllib.parse
import urllib.request

from config import STATICGRAM_TOKEN

API_ROOT = "https://api.staticgram.top"
WEB_APP_SCRIPT = "https://staticgram.top/js/telegram-web-app.js"


class StaticGramError(Exception):
    """Ошибка Bot API (поле description из ответа)."""


def _bot_url(method: str) -> str:
    if not STATICGRAM_TOKEN:
        raise StaticGramError("STATICGRAM_TOKEN не задан (переменная окружения)")
    return f"{API_ROOT}/bot{STATICGRAM_TOKEN}/{method}"


def api_call(method: str, params: dict | None = None,
             files: dict | None = None) -> dict:
    """Вызов метода Bot API. Возвращает содержимое поля result.

    files: {поле: (имя_файла, байты)} — отправка multipart/form-data
    (например, sendPhoto)."""
    if files:
        boundary = secrets.token_hex(16)
        body = bytearray()
        for key, value in (params or {}).items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                     f'name="{key}"\r\n\r\n{value}\r\n').encode("utf-8")
        for key, (filename, content) in files.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                     f'name="{key}"; filename="{filename}"\r\n'
                     f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
            body += content + b"\r\n"
        body += f"--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(
            _bot_url(method), data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                     "User-Agent": "TBankDemoBot/1.0"},
        )
    else:
        data = json.dumps(params or {}).encode("utf-8")
        req = urllib.request.Request(
            _bot_url(method), data=data,
            headers={"Content-Type": "application/json",
                     "User-Agent": "TBankDemoBot/1.0"},
        )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
            raise StaticGramError(payload.get("description", f"HTTP {e.code}"))
        except (ValueError, UnicodeDecodeError):
            raise StaticGramError(f"HTTP {e.code}") from e
    if not payload.get("ok"):
        raise StaticGramError(payload.get("description", "unknown error"))
    return payload["result"]


def send_photo(chat_id, png_bytes: bytes, caption: str = "") -> dict:
    """Отправляет фото (байты PNG) в чат."""
    params = {"chat_id": chat_id}
    if caption:
        params["caption"] = caption
        params["parse_mode"] = "HTML"
    return api_call("sendPhoto", params, files={"photo": ("cheque.png", png_bytes)})


def notify(uid, text: str) -> None:
    """Отправляет пользователю сообщение. Ошибки (не начинал чат с ботом)
    молча игнорируются — уведомление не критично."""
    try:
        api_call("sendMessage", {
            "chat_id": int(uid), "text": text, "parse_mode": "HTML",
        })
    except Exception:
        pass


def get_me() -> dict:
    return api_call("getMe")


def validate_init_data(init_data: str, max_age: int = 86400) -> dict:
    """Проверяет подпись initData Mini App и возвращает dict с полями,
    включая распарсенный user. Бросает ValueError при невалидных данных."""
    if not STATICGRAM_TOKEN:
        raise ValueError("STATICGRAM_TOKEN не задан, проверка initData невозможна")
    data = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise ValueError("в initData нет поля hash")
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", STATICGRAM_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise ValueError("невалидная подпись initData")
    try:
        auth_date = int(data.get("auth_date", "0"))
    except ValueError:
        raise ValueError("некорректный auth_date")
    if auth_date <= 0 or time.time() - auth_date > max_age:
        raise ValueError("initData устарела")
    if "user" in data:
        try:
            data["user"] = json.loads(data["user"])
        except (ValueError, TypeError):
            raise ValueError("некорректное поле user")
    return data
