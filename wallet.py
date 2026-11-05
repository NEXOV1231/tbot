"""Бизнес-логика кошелька TBank.

Баланс обеспечен реальными средствами StaticGram:
- пополнение — оплата счёта (Stars через XTR, Gram через GRAM-инвойс);
- вывод — withdrawStarBalance / withdrawGramBalance (расширения StaticGram Bot API);
- переводы — внутренние между пользователями TBank.

Единицы хранения: Stars — целые звёзды, Gram — нанотоны (1 коин = 1_000_000_000 нанотон).
Суммы в API принимаются в «монетах» (для Gram — дробные), внутри хранятся целыми.
"""
import secrets

from database import connect
from config import STATICGRAM_TOKEN, ADMIN_IDS
import staticgram

DEMO_MODE = not STATICGRAM_TOKEN

NANOTON = 1_000_000_000
MIN_STARS = 1
MAX_STARS = 100_000            # лимит счёта StaticGram
MIN_GRAM_NANOTONS = 10_000_000  # 0.01 коина
MAX_GRAM_NANOTONS = 10_000 * NANOTON

# Кэшбэк: за каждые CASHBACK_STARS_PER_POINT отправленных Stars — 1 балл.
CASHBACK_STARS_PER_POINT = 100
MIN_CONVERT_POINTS = 10        # обмен баллов только пачкой от 10
POINTS_TO_STARS_RATE = 1       # 1 балл = 1 Star при обмене

CURRENCIES = ("STARS", "GRAM")
COL = {"STARS": "stars", "GRAM": "gram"}  # колонка в users


class WalletError(Exception):
    """Ошибка кошелька (текст показывается пользователю)."""


def require_real_mode():
    if DEMO_MODE:
        raise WalletError("Реальные платежи недоступны: не задан STATICGRAM_TOKEN (demo-режим)")


def currency_ok(currency: str):
    if currency not in CURRENCIES:
        raise WalletError("Валюта должна быть STARS или GRAM")


def to_units(currency: str, amount) -> int:
    """Переводит сумму из монет в единицы хранения (stars / nanotons)."""
    currency_ok(currency)
    try:
        value = float(amount)
    except (TypeError, ValueError):
        raise WalletError("Некорректная сумма")
    if value <= 0:
        raise WalletError("Сумма должна быть больше нуля")
    if currency == "STARS":
        if value != int(value):
            raise WalletError("Stars указываются целым числом")
        units = int(value)
        if not (MIN_STARS <= units <= MAX_STARS):
            raise WalletError(f"Stars: от {MIN_STARS} до {MAX_STARS}")
        return units
    units = int(round(value * NANOTON))
    if not (MIN_GRAM_NANOTONS <= units <= MAX_GRAM_NANOTONS):
        raise WalletError("Gram: от 0.01 до 10000")
    return units


def to_coins(currency: str, units: int) -> str:
    """Форматирует единицы хранения для показа пользователю."""
    if currency == "STARS":
        return str(units)
    value = units / NANOTON
    return f"{value:.9f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))


def ensure_user(uid: str, username: str = ""):
    if not uid or len(uid) > 64:
        raise WalletError("Некорректный идентификатор пользователя")
    # Создаём пользователя; непустой username обновляет существующую запись
    # (раньше INSERT OR IGNORE навсегда оставлял username пустым — бот «не видел» его).
    with connect() as con:
        con.execute(
            """INSERT INTO users(user_id, username) VALUES(?,?)
               ON CONFLICT(user_id) DO UPDATE SET
               username = CASE WHEN excluded.username != ''
                               THEN excluded.username ELSE users.username END""",
            (uid, username[:64])
        )


def get_display_name(uid: str) -> str:
    """@username для показа или id, если username неизвестен."""
    with connect() as con:
        row = con.execute("SELECT username FROM users WHERE user_id=?", (uid,)).fetchone()
    if row and row["username"]:
        return "@" + row["username"].lstrip("@")
    return uid


def account_payload(uid: str) -> dict:
    ensure_user(uid)
    with connect() as con:
        user = con.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        ops = con.execute(
            "SELECT id,kind,currency,amount,status,details,created_at "
            "FROM operations WHERE user_id=? ORDER BY id DESC LIMIT 30",
            (uid,)
        ).fetchall()
    return {
        "user_id": user["user_id"], "username": user["username"],
        "stars": user["stars"], "gram": user["gram"], "points": user["points"],
        "is_admin": is_admin(uid),
        "operations": [dict(x) for x in ops],
        "demo": DEMO_MODE,
        "cashback": {"stars_per_point": CASHBACK_STARS_PER_POINT,
                     "min_convert": MIN_CONVERT_POINTS,
                     "rate": POINTS_TO_STARS_RATE},
    }


def is_admin(uid: str) -> bool:
    try:
        return int(uid) in ADMIN_IDS
    except (ValueError, TypeError):
        return False


def _balance(uid: str, currency: str) -> int:
    with connect() as con:
        row = con.execute(
            f"SELECT {COL[currency]} FROM users WHERE user_id=?", (uid,)
        ).fetchone()
    return row[0] if row else 0


def resolve_recipient(recipient: str) -> str:
    """user_id или @username -> user_id. Ошибка, если не найден."""
    name = recipient.strip().lstrip("@")
    if not name:
        raise WalletError("Укажите получателя: @username")
    with connect() as con:
        row = con.execute("SELECT user_id FROM users WHERE user_id=?", (name,)).fetchone()
        if row:
            return row["user_id"]
        row = con.execute(
            "SELECT user_id FROM users WHERE lower(username)=lower(?) ORDER BY created_at LIMIT 1",
            (name,),
        ).fetchone()
    if row:
        return row["user_id"]
    raise WalletError(f"Пользователь @{name} не найден. Пусть сначала откроет "
                      "бота или Mini App — тогда перевод пройдёт.")


def transfer(uid: str, recipient: str, currency: str, units: int) -> int:
    """Перевод по user_id или @username получателя. Возвращает id операции.
    За отправленные Stars начисляется кэшбэк баллами."""
    currency_ok(currency)
    ensure_user(uid)
    recipient_id = resolve_recipient(recipient)
    if uid == recipient_id:
        raise WalletError("Нельзя переводить самому себе")
    col = COL[currency]
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        sender = con.execute(
            f"SELECT {col} FROM users WHERE user_id=?", (uid,)
        ).fetchone()
        if sender[col] < units:
            raise WalletError("Недостаточно средств")
        con.execute(f"UPDATE users SET {col}={col}-? WHERE user_id=?", (units, uid))
        con.execute(f"UPDATE users SET {col}={col}+? WHERE user_id=?", (units, recipient_id))
        cur = con.execute(
            "INSERT INTO operations(user_id,kind,currency,amount,status,details) VALUES(?,?,?,?,?,?)",
            (uid, "transfer_out", currency, units, "completed", f"Получатель: {recipient_id}")
        )
        op_id = cur.lastrowid
        con.execute(
            "INSERT INTO operations(user_id,kind,currency,amount,status,details) VALUES(?,?,?,?,?,?)",
            (recipient_id, "transfer_in", currency, units, "completed", f"Отправитель: {uid}")
        )
        # Кэшбэк: баллы за отправленные Stars.
        if currency == "STARS":
            points = units // CASHBACK_STARS_PER_POINT
            if points > 0:
                con.execute("UPDATE users SET points=points+? WHERE user_id=?", (points, uid))
                con.execute(
                    "INSERT INTO operations(user_id,kind,currency,amount,status,details) VALUES(?,?,?,?,?,?)",
                    (uid, "cashback", "POINTS", points, "completed",
                     f"Кэшбэк за перевод #{op_id}")
                )
    return op_id


# ---------- Баллы кэшбэка ----------

def convert_points(uid: str, points: int) -> int:
    """Обменивает баллы на Stars (1 балл = 1 Star). Возвращает получено Stars."""
    ensure_user(uid)
    if points < MIN_CONVERT_POINTS:
        raise WalletError(f"Обмен от {MIN_CONVERT_POINTS} баллов")
    stars = points * POINTS_TO_STARS_RATE
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT points FROM users WHERE user_id=?", (uid,)).fetchone()
        if row["points"] < points:
            raise WalletError(f"Недостаточно баллов: у вас {row['points']}")
        con.execute("UPDATE users SET points=points-? WHERE user_id=?", (points, uid))
        con.execute("UPDATE users SET stars=stars+? WHERE user_id=?", (stars, uid))
        con.execute(
            "INSERT INTO operations(user_id,kind,currency,amount,status,details) VALUES(?,?,?,?,?,?)",
            (uid, "points_convert", "STARS", stars, "completed", f"Обмен {points} баллов")
        )
    return stars


# ---------- Админ-выдача ----------

ADMIN_KINDS = ("STARS", "GRAM", "POINTS")


def admin_give(target: str, kind: str, amount) -> tuple[str, int]:
    """Админ начисляет Stars/Gram/баллы пользователю. Возвращает (target_id, units)."""
    kind = kind.upper()
    if kind not in ADMIN_KINDS:
        raise WalletError("Тип: stars, gram или points")
    target_id = resolve_recipient(target)
    if kind == "POINTS":
        try:
            units = int(amount)
        except (TypeError, ValueError):
            raise WalletError("Баллы — целое число")
        if units <= 0:
            raise WalletError("Сумма должна быть больше нуля")
        col = "points"
    else:
        units = to_units(kind, amount)
        col = COL[kind]
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute(f"UPDATE users SET {col}={col}+? WHERE user_id=?", (units, target_id))
        cur = con.execute(
            "INSERT INTO operations(user_id,kind,currency,amount,status,details) VALUES(?,?,?,?,?,?)",
            (target_id, "admin_give", kind, units, "completed", "Выдано администратором")
        )
    return target_id, units


# ---------- Чеки ----------

_CHEQUE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # без похожих символов
CODE_LEN = 12


def _new_code() -> str:
    return "".join(secrets.choice(_CHEQUE_ALPHABET) for _ in range(CODE_LEN))


def format_code(code: str) -> str:
    """K7QXM92T4LPD -> K7QX-M92T-4LPD"""
    c = normalize_code(code)
    return "-".join(c[i:i + 4] for i in range(0, len(c), 4))


def normalize_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch in _CHEQUE_ALPHABET)


def create_cheque(uid: str, currency: str, units: int) -> str:
    """Списывает сумму и создаёт одноразовый чек. Возвращает код."""
    currency_ok(currency)
    ensure_user(uid)
    col = COL[currency]
    code = _new_code()
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(f"SELECT {col} FROM users WHERE user_id=?", (uid,)).fetchone()
        if row[col] < units:
            raise WalletError("Недостаточно средств")
        con.execute(f"UPDATE users SET {col}={col}-? WHERE user_id=?", (units, uid))
        con.execute(
            "INSERT INTO cheques(code,creator_id,currency,amount) VALUES(?,?,?,?)",
            (code, uid, currency, units)
        )
        con.execute(
            "INSERT INTO operations(user_id,kind,currency,amount,status,details) VALUES(?,?,?,?,?,?)",
            (uid, "cheque_create", currency, units, "completed", f"Чек {format_code(code)}")
        )
    return code


def redeem_cheque(uid: str, code: str) -> tuple[str, int]:
    """Активирует чек: зачисляет сумму. Возвращает (currency, units)."""
    ensure_user(uid)
    norm = normalize_code(code)
    if len(norm) != CODE_LEN:
        raise WalletError("Некорректный код чека")
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT * FROM cheques WHERE code=?", (norm,)
        ).fetchone()
        if not row:
            raise WalletError("Чек не найден. Проверьте код.")
        if row["status"] != "active":
            raise WalletError("Чек уже активирован.")
        if row["creator_id"] == uid:
            raise WalletError("Нельзя активировать собственный чек.")
        col_name = COL[row["currency"]]
        con.execute(f"UPDATE users SET {col_name}={col_name}+? WHERE user_id=?",
                    (row["amount"], uid))
        con.execute(
            "UPDATE cheques SET status='redeemed', redeemed_by=? WHERE code=?",
            (uid, norm)
        )
        con.execute(
            "INSERT INTO operations(user_id,kind,currency,amount,status,details) VALUES(?,?,?,?,?,?)",
            (uid, "cheque_redeem", row["currency"], row["amount"], "completed",
             f"Чек {format_code(norm)}")
        )
    return row["currency"], row["amount"]


# ---------- Пополнение: счета и зачисление ----------

def _invoice_payload(uid: str, currency: str) -> str:
    # currency в payload: XTR для Stars, TON для Gram (как в SuccessfulPayment).
    code = "XTR" if currency == "STARS" else "TON"
    return f"tbank-topup-{code}-{uid}"[:128]


def parse_invoice_payload(payload: str) -> tuple[str, str]:
    """Возвращает (uid, currency STARS/GRAM) или бросает ValueError."""
    parts = (payload or "").split("-")
    if len(parts) != 4 or parts[0] != "tbank" or parts[1] != "topup":
        raise ValueError("неизвестный payload счёта")
    code, uid = parts[2], parts[3]
    currency = {"XTR": "STARS", "TON": "GRAM"}.get(code)
    if not currency or not uid:
        raise ValueError("неизвестный payload счёта")
    return uid, currency


def create_topup_invoice_link(uid: str, currency: str, units: int) -> str:
    """Ссылка на счёт для Mini App (Telegram.WebApp.openInvoice)."""
    require_real_mode()
    ensure_user(uid)
    if currency == "STARS":
        return staticgram.api_call("createInvoiceLink", {
            "title": "Пополнение TBank",
            "description": f"Зачисление {units} Stars на счёт TBank",
            "payload": _invoice_payload(uid, currency),
            "currency": "XTR",
            "prices": [{"label": "Stars", "amount": units}],
        })
    return staticgram.api_call("createGramInvoiceLink", {
        "title": "Пополнение TBank",
        "description": f"Зачисление {to_coins(currency, units)} Gram на счёт TBank",
        "payload": _invoice_payload(uid, currency),
        "nanoton_amount": units,
        "label": "Gram",
    })


def send_topup_invoice(chat_id, uid: str, currency: str, units: int) -> None:
    """Счёт прямо в чат с ботом."""
    require_real_mode()
    ensure_user(uid)
    method, params = (
        ("sendInvoice", {
            "chat_id": chat_id,
            "title": "Пополнение TBank",
            "description": f"Зачисление {units} Stars на счёт TBank",
            "payload": _invoice_payload(uid, currency),
            "currency": "XTR",
            "prices": [{"label": "Stars", "amount": units}],
        }) if currency == "STARS" else
        ("sendGramInvoice", {
            "chat_id": chat_id,
            "title": "Пополнение TBank",
            "description": f"Зачисление {to_coins(currency, units)} Gram на счёт TBank",
            "payload": _invoice_payload(uid, currency),
            "nanoton_amount": units,
            "label": "Gram",
        })
    )
    staticgram.api_call(method, params)


def process_pre_checkout(query: dict) -> None:
    """Отвечает на pre_checkout_query: ok, если счёт наш и сумма в лимитах."""
    qid = query.get("id")
    try:
        uid, currency = parse_invoice_payload(query.get("invoice_payload", ""))
        units = int(query.get("total_amount") or 0)
        currency_ok(currency)
        if currency == "STARS" and not (MIN_STARS <= units <= MAX_STARS):
            raise ValueError("сумма вне лимитов")
        if currency == "GRAM" and not (MIN_GRAM_NANOTONS <= units <= MAX_GRAM_NANOTONS):
            raise ValueError("сумма вне лимитов")
        ok, msg = True, ""
    except ValueError as e:
        ok, msg = False, str(e)
    staticgram.api_call("answerPreCheckoutQuery",
                        {"pre_checkout_query_id": qid, "ok": ok, "error_message": msg})


def credit_payment(charge_id: str, uid: str, currency: str, units: int) -> bool:
    """Зачисляет оплату на счёт. Возвращает False, если charge_id уже обработан
    (защита от повторной доставки successful_payment)."""
    require_real_mode()
    currency_ok(currency)
    col = COL[currency]
    ensure_user(uid)
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        seen = con.execute(
            "SELECT 1 FROM payments WHERE charge_id=?", (charge_id,)
        ).fetchone()
        if seen:
            return False
        con.execute(
            "INSERT INTO payments(charge_id,user_id,currency,amount) VALUES(?,?,?,?)",
            (charge_id, uid, currency, units)
        )
        con.execute(f"UPDATE users SET {col}={col}+? WHERE user_id=?", (units, uid))
        con.execute(
            "INSERT INTO operations(user_id,kind,currency,amount,status,details) VALUES(?,?,?,?,?,?)",
            (uid, "topup", currency, units, "completed", f"charge: {charge_id}")
        )
    return True


def process_successful_payment(payer_uid: str, payment: dict) -> bool:
    """Обрабатывает successful_payment из чата. Возвращает True при зачислении."""
    uid, payload_currency = parse_invoice_payload(payment.get("invoice_payload", ""))
    pay_currency = payment.get("currency")
    if (pay_currency == "XTR") != (payload_currency == "STARS"):
        raise WalletError("валюта счёта не совпадает с платежом")
    if str(payer_uid) != uid:
        raise WalletError("плательщик не совпадает с получателем счёта")
    units = int(payment.get("total_amount") or 0)
    charge_id = payment.get("telegram_payment_charge_id") or f"unknown-{secrets.token_hex(8)}"
    return credit_payment(charge_id, uid, payload_currency, units)


# ---------- Вывод ----------

def withdraw(uid: str, currency: str, units: int) -> dict:
    """Реальный вывод со счёта бота пользователю через StaticGram API.
    Сначала вызывает API, при успехе списывает с внутреннего счёта."""
    require_real_mode()
    currency_ok(currency)
    if not uid.isdigit():
        raise WalletError("Вывод доступен только пользователям StaticGram")
    ensure_user(uid)
    col = COL[currency]
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(f"SELECT {col} FROM users WHERE user_id=?", (uid,)).fetchone()
        if row[col] < units:
            raise WalletError("Недостаточно средств")
        # Создаём запись в operations: её id станет request_id для идемпотентности.
        cur = con.execute(
            "INSERT INTO operations(user_id,kind,currency,amount,status,details) VALUES(?,?,?,?,?,?)",
            (uid, "withdraw", currency, units, "processing", "")
        )
        op_id = cur.lastrowid
    request_id = f"tbank-wd-{op_id}"

    def fail_op(msg: str):
        with connect() as con:
            con.execute(
                "UPDATE operations SET status='failed', details=? WHERE id=?",
                (f"{request_id}: {msg}", op_id)
            )

    try:
        if currency == "STARS":
            result = staticgram.api_call("withdrawStarBalance", {
                "user_id": int(uid), "star_count": units, "request_id": request_id,
            })
        else:
            result = staticgram.api_call("withdrawGramBalance", {
                "user_id": int(uid), "nanoton_amount": units, "request_id": request_id,
            })
    except staticgram.StaticGramError as e:
        fail_op(str(e))
        raise WalletError(f"StaticGram отклонил вывод: {e}")

    # API подтвердил вывод — списываем с внутреннего счёта (с повторной проверкой баланса).
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(f"SELECT {col} FROM users WHERE user_id=?", (uid,)).fetchone()
        if row[col] < units:
            fail_op("race: недостаточно средств после подтверждения API")
            raise WalletError("Недостаточно средств")
        con.execute(f"UPDATE users SET {col}={col}-? WHERE user_id=?", (units, uid))
        con.execute(
            "UPDATE operations SET status='completed', details=? WHERE id=?",
            (f"{request_id} ok", op_id)
        )
    return {"ok": True, "amount": units, "currency": currency,
            "request_id": request_id, "api_result": result}


def bot_balances() -> dict:
    """Балансы самого бота (обеспечение кошелька)."""
    require_real_mode()
    stars = staticgram.api_call("getMyStarBalance")
    gram = staticgram.api_call("getMyGramBalance")
    return {"stars": stars, "gram": gram}
