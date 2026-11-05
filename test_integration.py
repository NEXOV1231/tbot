"""Интеграционный тест: поднимает uvicorn и прогоняет API в двух режимах.

Запуск: py test_integration.py
Прочее покрыто в test_wallet.py (здесь Bot API не подменяется, реальные деньги
не двигаются: /api/topup лишь создаёт неоплаченный счёт).
"""
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
TOKEN = "1780246272:HFOTwHkoyhzVkNlgXqaBTwG87euxOung8-B"
BASE = "http://127.0.0.1:8123"


def make_init_data(uid: int, username: str = "tester") -> str:
    """initData, подписанная токеном бота (как клиент StaticGram)."""
    user = json.dumps({"id": uid, "first_name": "T", "username": username},
                      separators=(",", ":"))
    fields = {"auth_date": str(int(time.time())), "query_id": "AAtest",
              "user": user}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


def req(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data,
                               headers={"Content-Type": "application/json"},
                               method=method)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def check(name: str, cond: bool):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        sys.exit(1)


def run_server(demo: bool) -> subprocess.Popen:
    env = dict(os.environ)
    env["STATICGRAM_TOKEN"] = "" if demo else TOKEN  # перекрывает .env
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
         "--port", "8123"], cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            req("GET", "/")
            return proc
        except Exception:
            time.sleep(0.2)
    print("Сервер не поднялся")
    sys.exit(1)


def main() -> None:
    # ---------- Режим StaticGram (токен задан) ----------
    proc = run_server(demo=False)
    try:
        _, root = req("GET", "/")
        check("auth-mode: mode=STATICGRAM", root["mode"] == "STATICGRAM")
        check("auth-mode: real_payments=true", root["real_payments"] is True)
        with urllib.request.urlopen(BASE + "/app", timeout=15) as resp:
            page = resp.read().decode("utf-8")
        check("auth-mode: /app отдаёт страницу Mini App",
              page.lstrip().lower().startswith("<!doctype html>") and "TBank" in page)

        s, auth_a = req("POST", "/api/auth", {"init_data": make_init_data(555000111)})
        check("auth-mode: /api/auth валидная initData", s == 200 and auth_a.get("session"))
        session_a = auth_a["session"]
        s, auth_b = req("POST", "/api/auth", {"init_data": make_init_data(555000222)})
        session_b = auth_b["session"]

        s, _ = req("POST", "/api/auth", {"init_data": make_init_data(555000111, "x").replace("555000111", "999")})
        check("auth-mode: подделанная initData отклонена (401)", s == 401)

        s, acc = req("GET", "/api/account?" + urllib.parse.urlencode({"session": session_a}))
        check("auth-mode: /api/account по сессии", s == 200 and acc["user_id"] == "555000111")

        s, _ = req("GET", "/api/account")
        check("auth-mode: /api/account без сессии отклонён (401)", s == 401)

        s, _ = req("POST", "/api/register", {"user_id": "hacker", "username": "x"})
        check("auth-mode: /api/register отключён (403)", s == 403)

        # /api/topup создаёт настоящий счёт StaticGram (неоплаченный).
        s, inv = req("POST", "/api/topup",
                     {"session": session_a, "currency": "STARS", "amount": 10})
        check("auth-mode: счёт Stars создан", s == 200 and inv.get("invoice_url", "").startswith("https://"))

        s, inv = req("POST", "/api/topup",
                     {"session": session_a, "currency": "GRAM", "amount": 1.5})
        check("auth-mode: счёт Gram создан", s == 200 and inv.get("invoice_url", "").startswith("https://"))

        s, _ = req("POST", "/api/topup", {"session": session_a, "currency": "GRAM", "amount": 0.001})
        check("auth-mode: Gram ниже минимума отклонён (400)", s == 400)

        # Перевод между двумя авторизованными пользователями (денег пока нет -> 400).
        s, _ = req("POST", "/api/transfer",
                   {"session": session_a, "recipient": "555000222",
                    "currency": "STARS", "amount": 10})
        check("auth-mode: овердрафт отклонён (400)", s == 400)

        s, _ = req("POST", "/api/transfer",
                   {"user_id": "hacker", "recipient": "555000111",
                    "currency": "STARS", "amount": 1})
        check("auth-mode: перевод по поддельному user_id отклонён (401)", s == 401)

        # Вывод при нулевом балансе отклоняется до обращения к StaticGram.
        s, _ = req("POST", "/api/withdraw",
                   {"session": session_a, "currency": "STARS", "amount": 5})
        check("auth-mode: вывод без средств отклонён (400)", s == 400)

        # Чеки: создание при нулевом балансе и активация несуществующего отклоняются.
        s, _ = req("POST", "/api/cheque/create",
                   {"session": session_a, "currency": "STARS", "amount": 5})
        check("auth-mode: чек без средств отклонён (400)", s == 400)
        s, _ = req("POST", "/api/cheque/redeem",
                   {"session": session_a, "code": "AAAA-BBBB-CCCC"})
        check("auth-mode: несуществующий чек отклонён (400)", s == 400)

        # Баллы: обмен без баллов отклоняется.
        s, _ = req("POST", "/api/points/convert",
                   {"session": session_a, "points": 10})
        check("auth-mode: обмен без баллов отклонён (400)", s == 400)

        # Админка: не-админу — 403.
        s, _ = req("POST", "/api/admin/give",
                   {"session": session_a, "target": "555000222",
                    "kind": "STARS", "amount": 5})
        check("auth-mode: выдача не-админу отклонена (403)", s == 403)
    finally:
        proc.terminate()

    # ---------- Демо-режим (без токена) ----------
    proc = run_server(demo=True)
    try:
        _, root = req("GET", "/")
        check("demo-mode: mode=DEMO", root["mode"] == "DEMO")
        check("demo-mode: real_payments=false", root["real_payments"] is False)

        s, _ = req("POST", "/api/register", {"user_id": "demo-a", "username": "alice"})
        check("demo-mode: /api/register работает", s == 200)

        s, acc = req("GET", "/api/account?user_id=demo-a")
        check("demo-mode: /api/account по user_id", s == 200)

        s, _ = req("POST", "/api/topup", {"user_id": "demo-a", "currency": "STARS", "amount": 5})
        check("demo-mode: /api/topup недоступен (400)", s == 400)

        s, _ = req("POST", "/api/withdraw", {"user_id": "demo-a", "currency": "STARS", "amount": 5})
        check("demo-mode: /api/withdraw недоступен (400)", s == 400)
    finally:
        proc.terminate()

    print("\nВсе проверки пройдены.")


if __name__ == "__main__":
    main()
