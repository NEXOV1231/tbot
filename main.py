import hashlib
import hmac
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from database import init_db
from config import CORS_ORIGINS, STATICGRAM_TOKEN
import staticgram
import wallet

DEMO_MODE = wallet.DEMO_MODE
SESSION_TTL = 86400  # 24 часа
SITE_FILE = Path(__file__).with_name("index.html")

app = FastAPI(title="TBank API", docs_url="/docs" if DEMO_MODE else None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,  # Для публикации задайте CORS_ORIGINS="https://ваш-домен".
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
init_db()


def ensure_user(uid: str, username: str = ""):
    wallet.ensure_user(uid, username)


# ---------- Сессии (подписанный токен вместо открытого user_id) ----------

def _session_key() -> bytes:
    return hashlib.sha256(STATICGRAM_TOKEN.encode()).digest()


def make_session(uid: str) -> str:
    exp = int(time.time()) + SESSION_TTL
    sig = hmac.new(_session_key(), f"{uid}:{exp}".encode(), hashlib.sha256).hexdigest()
    return f"{uid}:{exp}:{sig}"


def verify_session(session: str) -> str:
    try:
        uid, exp, sig = session.split(":")
        exp = int(exp)
    except ValueError:
        raise HTTPException(401, "Невалидная сессия")
    expected = hmac.new(_session_key(), f"{uid}:{exp}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(401, "Невалидная сессия")
    if exp < time.time():
        raise HTTPException(401, "Сессия истекла")
    if not uid or len(uid) > 64:
        raise HTTPException(401, "Невалидная сессия")
    return uid


def resolve_user(session: str = "", user_id: str = "") -> str:
    """Возвращает user_id: из сессии (всегда) или из тела запроса (только в demo-режиме)."""
    if session:
        return verify_session(session)
    if DEMO_MODE and user_id:
        if len(user_id) > 64:
            raise HTTPException(400, "Некорректный идентификатор пользователя")
        return user_id
    raise HTTPException(401, "Требуется авторизация через Mini App")


# ---------- Модели запросов ----------

class AuthIn(BaseModel):
    init_data: str = Field(min_length=1, max_length=8192)

class UserIn(BaseModel):
    session: str = Field(default="", max_length=512)
    user_id: str = Field(default="", max_length=64)
    username: str = Field(default="", max_length=64)

class TransferIn(BaseModel):
    session: str = Field(default="", max_length=512)
    user_id: str = Field(default="", max_length=64)
    recipient: str = Field(min_length=1, max_length=64)
    currency: str = Field(min_length=1, max_length=8)
    amount: float = Field(gt=0)

class TopUpIn(BaseModel):
    session: str = Field(default="", max_length=512)
    user_id: str = Field(default="", max_length=64)
    currency: str = Field(min_length=1, max_length=8)
    amount: float = Field(gt=0)

class WithdrawIn(BaseModel):
    session: str = Field(default="", max_length=512)
    user_id: str = Field(default="", max_length=64)
    currency: str = Field(min_length=1, max_length=8)
    amount: float = Field(gt=0)

class ChequeCreateIn(BaseModel):
    session: str = Field(default="", max_length=512)
    user_id: str = Field(default="", max_length=64)
    currency: str = Field(min_length=1, max_length=8)
    amount: float = Field(gt=0)

class ChequeRedeemIn(BaseModel):
    session: str = Field(default="", max_length=512)
    user_id: str = Field(default="", max_length=64)
    code: str = Field(min_length=1, max_length=32)

class PointsConvertIn(BaseModel):
    session: str = Field(default="", max_length=512)
    user_id: str = Field(default="", max_length=64)
    points: int = Field(gt=0)

class AdminGiveIn(BaseModel):
    session: str = Field(default="", max_length=512)
    user_id: str = Field(default="", max_length=64)
    target: str = Field(min_length=1, max_length=64)
    kind: str = Field(min_length=1, max_length=8)   # STARS | GRAM | POINTS
    amount: float = Field(gt=0)


def _units(currency: str, amount: float) -> int:
    try:
        return wallet.to_units(currency, amount)
    except wallet.WalletError as e:
        raise HTTPException(400, str(e))


# ---------- Эндпоинты ----------

@app.get("/")
def root():
    return {"name": "TBank API", "mode": "DEMO" if DEMO_MODE else "STATICGRAM",
            "real_payments": not DEMO_MODE, "mini_app": "/app"}


@app.get("/app")
def mini_app():
    """Отдаёт страницу Mini App: на своём сервере хостинг не нужен —
    один адрес для приложения и API."""
    if not SITE_FILE.exists():
        raise HTTPException(404, "index.html не найден рядом с main.py")
    return FileResponse(SITE_FILE, media_type="text/html; charset=utf-8")


@app.post("/api/auth")
def auth(data: AuthIn):
    """Авторизация Mini App: проверяем подпись initData и выдаём сессию."""
    try:
        payload = staticgram.validate_init_data(data.init_data)
    except ValueError as e:
        raise HTTPException(401, f"initData не прошла проверку: {e}")
    user = payload.get("user") or {}
    uid = str(user.get("id") or "")
    if not uid:
        raise HTTPException(401, "В initData нет user.id")
    username = user.get("username") or user.get("first_name") or ""
    ensure_user(uid, username)
    return {"ok": True, "session": make_session(uid),
            "account": wallet.account_payload(uid), "demo": DEMO_MODE}


@app.post("/api/register")
def register(data: UserIn):
    """Демо-регистрация. Работает только в demo-режиме (без токена бота)."""
    if not DEMO_MODE:
        raise HTTPException(403, "Регистрация отключена: используйте авторизацию Mini App (/api/auth)")
    ensure_user(data.user_id, data.username)
    return {"ok": True, "demo": True}


@app.get("/api/account")
def account(session: str = "", user_id: str = ""):
    uid = resolve_user(session, user_id)
    return wallet.account_payload(uid)


@app.post("/api/transfer")
def transfer(data: TransferIn):
    uid = resolve_user(data.session, data.user_id)
    units = _units(data.currency, data.amount)
    try:
        recipient_id = wallet.resolve_recipient(data.recipient)
        op_id = wallet.transfer(uid, recipient_id, data.currency, units)
    except wallet.WalletError as e:
        raise HTTPException(400, str(e))
    # Уведомляем получателя (если он когда-либо писал боту).
    if not DEMO_MODE:
        sender_name = uid
        try:
            sender_name = wallet.get_display_name(uid)
        except wallet.WalletError:
            pass
        amount = wallet.to_coins(data.currency, units)
        staticgram.notify(
            recipient_id,
            "📥 <b>Вам поступил перевод</b>\n\n"
            f"Отправитель: <b>{sender_name}</b>\n"
            f"Сумма: <b>{amount}</b> {data.currency}\n"
            f"ID операции: <code>{op_id}</code>")
    return {"ok": True, "demo": DEMO_MODE, "operation_id": op_id}


@app.post("/api/topup")
def topup(data: TopUpIn):
    """Создаёт счёт StaticGram и возвращает ссылку для оплаты в Mini App."""
    uid = resolve_user(data.session, data.user_id)
    units = _units(data.currency, data.amount)
    try:
        link = wallet.create_topup_invoice_link(uid, data.currency, units)
    except wallet.WalletError as e:
        raise HTTPException(400, str(e))
    except staticgram.StaticGramError as e:
        raise HTTPException(502, f"StaticGram: {e}")
    return {"ok": True, "invoice_url": link, "amount_units": units, "demo": DEMO_MODE}


@app.post("/api/withdraw")
def withdraw(data: WithdrawIn):
    """Реальный вывод Stars/Gram на аккаунт StaticGram пользователя."""
    uid = resolve_user(data.session, data.user_id)
    units = _units(data.currency, data.amount)
    try:
        result = wallet.withdraw(uid, data.currency, units)
    except wallet.WalletError as e:
        raise HTTPException(400, str(e))
    # Уведомление пользователю в чат бота (тот же Bot API).
    if not DEMO_MODE:
        try:
            staticgram.api_call("sendMessage", {
                "chat_id": int(uid),
                "text": f"Вывод {wallet.to_coins(data.currency, units)} {data.currency} "
                        f"на ваш аккаунт StaticGram оформлен ✅",
            })
        except staticgram.StaticGramError:
            pass  # уведомление не критично
    return {"ok": True, "amount_units": units, "request_id": result["request_id"],
            "balance": wallet.account_payload(uid)}


@app.post("/api/cheque/create")
def cheque_create(data: ChequeCreateIn):
    """Создаёт одноразовый чек: списывает сумму и возвращает код."""
    uid = resolve_user(data.session, data.user_id)
    units = _units(data.currency, data.amount)
    try:
        code = wallet.create_cheque(uid, data.currency, units)
    except wallet.WalletError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "code": code, "code_display": wallet.format_code(code),
            "currency": data.currency, "amount_units": units,
            "balance": wallet.account_payload(uid)}


@app.post("/api/cheque/redeem")
def cheque_redeem(data: ChequeRedeemIn):
    """Активирует чек по коду."""
    uid = resolve_user(data.session, data.user_id)
    try:
        currency, units = wallet.redeem_cheque(uid, data.code)
    except wallet.WalletError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "currency": currency, "amount_units": units,
            "amount": wallet.to_coins(currency, units),
            "balance": wallet.account_payload(uid)}


@app.post("/api/points/convert")
def points_convert(data: PointsConvertIn):
    """Обмен баллов кэшбэка на Stars (1 балл = 1 Star, минимум 10)."""
    uid = resolve_user(data.session, data.user_id)
    try:
        stars = wallet.convert_points(uid, data.points)
    except wallet.WalletError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "points": data.points, "stars_received": stars,
            "balance": wallet.account_payload(uid)}


@app.post("/api/admin/give")
def admin_give(data: AdminGiveIn):
    """Админ выдаёт пользователю Stars / Gram / баллы."""
    uid = resolve_user(data.session, data.user_id)
    if not wallet.is_admin(uid):
        raise HTTPException(403, "Только для администраторов")
    try:
        target_id, units = wallet.admin_give(data.target, data.kind, data.amount)
    except wallet.WalletError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "target": wallet.get_display_name(target_id),
            "kind": data.kind.upper(), "units": units}
