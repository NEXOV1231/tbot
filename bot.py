"""Бот @tbank_bot для StaticGram.

Управление меню кнопками (reply keyboard) и inline-кнопками.
Отправка — пошагово: @username сообщением -> валюта кнопкой -> сумма сообщением.
Переводы по @username, подарочные чеки фото-карточкой с кодом.

Команды: /start /balance /topup /withdraw /send /cheque /activate /cancel /help.
"""
import html
import time
import traceback

from config import MINI_APP_URL, STATICGRAM_TOKEN
import staticgram
import wallet

try:
    import imggen
except ImportError:  # Pillow не установлен — чеки текстом
    imggen = None

# ---- Меню (reply keyboard) ----

BTN_APP = "🏦 Открыть Mini App"
BTN_SEND = "📤 Отправить"
BTN_TOPUP = "💰 Пополнить"
BTN_WITHDRAW = "💸 Вывести"
BTN_CHEQUE = "🎟 Чек"
BTN_POINTS = "💎 Баллы"
BTN_BALANCE = "📊 Баланс"
BTN_HISTORY = "📜 История"
BTN_HELP = "❓ Помощь"
BTN_ADMIN = "🛠 Админ-панель"


def main_menu(uid: str = ""):
    rows = [[{"text": BTN_APP, "web_app": {"url": MINI_APP_URL}}]] if MINI_APP_URL else []
    rows += [
        [{"text": BTN_SEND}, {"text": BTN_TOPUP}],
        [{"text": BTN_WITHDRAW}, {"text": BTN_CHEQUE}],
        [{"text": BTN_POINTS}, {"text": BTN_BALANCE}],
        [{"text": BTN_HISTORY}, {"text": BTN_HELP}],
    ]
    if uid and wallet.is_admin(uid):
        rows.append([{"text": BTN_ADMIN}])
    return {"keyboard": rows, "resize_keyboard": True}


def _inline(rows):
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row]
                                for row in rows]}


def admin_panel_keyboard():
    return _inline([
        [("⭐ Выдать Stars", "adm:give:STARS"), ("🪙 Выдать Gram", "adm:give:GRAM")],
        [("💎 Выдать баллы", "adm:give:POINTS"), ("📊 Статистика", "adm:stats")],
    ])


def currency_keyboard(action: str):
    return _inline([[("⭐ Stars", f"cur:{action}:STARS"),
                     ("🪙 Gram", f"cur:{action}:GRAM")]])


HELP = (
    "<b>Кошелёк TBank</b>\n"
    "Stars и Gram: реальное пополнение, вывод, переводы по @username и чеки.\n\n"
    "<b>Меню:</b>\n"
    f"• {BTN_SEND} — перевод по @username\n"
    f"• {BTN_TOPUP} — пополнить счёт (⭐ или 🪙)\n"
    f"• {BTN_WITHDRAW} — вывести Stars/Gram на аккаунт\n"
    f"• {BTN_CHEQUE} — чек с кодом (фото) или активация\n"
    f"• {BTN_POINTS} — баллы кэшбэка: обмен на Stars\n"
    f"• {BTN_BALANCE} — баланс · {BTN_HISTORY} — операции\n\n"
    "<b>Кэшбэк:</b> за каждые "
    f"{wallet.CASHBACK_STARS_PER_POINT} ⭐ в переводах — 1 балл; "
    f"от {wallet.MIN_CONVERT_POINTS} баллов меняете их на Stars 1:1.\n\n"
    "<b>Команды:</b>\n"
    "/send @user stars 10 · /topup gram 2.5\n"
    "/withdraw stars 50 · /cheque stars 25\n"
    "/activate КОД · /cancel — отмена шага"
)

# ---- Сценарии ----

STATES = {}   # chat_id -> "send:to" | "send:cur" | "send:amount:STARS" | ...
PENDING = {}  # chat_id -> {"recipient": user_id} (для многошагового перевода)

PROMPTS = {
    "send:to": ("<b>📤 Перевод · шаг 1 из 3</b>\n\n"
                "Введите <b>@username</b> получателя в StaticGram.\n"
                "Получатель должен хотя бы раз открыть бота или Mini App.\n\n"
                "Отмена: /cancel"),
    "amount": "<b>{step}</b>\n\nВведите сумму числом.\nНапример: <code>50</code>\n\nОтмена: /cancel",
    "cheque_amount": ("<b>🎟 Создание чека</b>\n\n"
                      "Введите валюту и сумму одной строкой:\n"
                      "<code>stars 25</code> или <code>gram 1.5</code>\n\n"
                      "Отмена: /cancel"),
    "cheque_code": ("<b>✅ Активация чека</b>\n\n"
                    "Введите код чека (12 символов, можно с дефисами).\n\n"
                    "Отмена: /cancel"),
    "points_convert": ("<b>💎 Обмен баллов на Stars</b>\n\n"
                       f"1 балл = 1 ⭐, минимум {wallet.MIN_CONVERT_POINTS} баллов за обмен.\n"
                       "Введите, сколько баллов обменять.\n\n"
                       "Отмена: /cancel"),
    "admin_to": ("<b>🛠 Админ-панель</b>\n\n"
                 "Кому выдать? Введите <b>@username</b> получателя.\n\n"
                 "Отмена: /cancel"),
    "admin_amount": ("<b>🛠 Админ-панель</b>\n\n"
                     "Введите сумму числом.\n\n"
                     "Отмена: /cancel"),
}


def _send(chat_id, text: str, reply_markup=None) -> None:
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        params["reply_markup"] = reply_markup
    staticgram.api_call("sendMessage", params)


def set_menu_button(chat_id) -> None:
    """Прикрепляет к чату постоянную кнопку меню с Mini App (без ссылок)."""
    if not MINI_APP_URL or wallet.DEMO_MODE:
        return
    try:
        staticgram.api_call("setChatMenuButton", {
            "chat_id": chat_id,
            "menu_button": {
                "type": "web_app",
                "text": "TBank",
                "web_app": {"url": MINI_APP_URL},
            },
        })
    except staticgram.StaticGramError:
        pass  # не критично: кнопку всегда можно открыть из профиля бота


def _error(chat_id, msg: str) -> None:
    _send(chat_id, f"❌ <b>Не получилось</b>\n\n{html.escape(msg)}")


def _parse_currency_amount(args: list[str]) -> tuple[str, float]:
    """'<stars|gram> <сумма>' -> (currency, сумма монетами)."""
    if len(args) != 2:
        raise wallet.WalletError("Нужно две части: <code>stars 25</code>")
    currency = args[0].upper()
    if currency not in wallet.CURRENCIES:
        raise wallet.WalletError("Валюта: stars или gram")
    return currency, float(args[1].replace(",", "."))


def _finish(chat_id, text: str) -> None:
    STATES.pop(chat_id, None)
    PENDING.pop(chat_id, None)
    _send(chat_id, text)


# ---- Действия ----

def do_balance(chat_id, uid: str) -> None:
    acc = wallet.account_payload(uid)
    _send(chat_id,
          "<b>📊 Ваш баланс</b>\n\n"
          f"⭐ Stars — <b>{acc['stars']}</b>\n"
          f"🪙 Gram — <b>{html.escape(wallet.to_coins('GRAM', acc['gram']))}</b>\n"
          f"💎 Баллы — <b>{acc['points']}</b>")


def do_history(chat_id, uid: str) -> None:
    ops = wallet.account_payload(uid)["operations"][:10]
    if not ops:
        _send(chat_id, "📜 Операций пока нет.")
        return
    KINDS = {"transfer_out": "📤 Перевод", "transfer_in": "📥 Получено",
             "topup": "💰 Пополнение", "withdraw": "💸 Вывод",
             "cheque_create": "🎟 Чек создан", "cheque_redeem": "🎉 Чек активирован",
             "cashback": "💎 Кэшбэк", "points_convert": "💎 Обмен баллов",
             "admin_give": "🛠 Выдано админом"}
    marks = {"completed": "✅", "pending": "⏳", "failed": "❌"}
    lines = []
    for o in ops:
        kind = KINDS.get(o["kind"], o["kind"])
        amount = wallet.to_coins(o["currency"], o["amount"])
        lines.append(f"{marks.get(o['status'], '•')} {kind} — "
                     f"<b>{html.escape(amount)}</b> {o['currency']}")
    _send(chat_id, "<b>📜 Последние операции</b>\n\n" + "\n".join(lines))


def do_send(chat_id, uid: str, recipient_id: str, currency: str, coins: float) -> None:
    units = wallet.to_units(currency, coins)
    recipient_name = wallet.get_display_name(recipient_id)
    op_id = wallet.transfer(uid, recipient_id, currency, units)
    acc = wallet.account_payload(uid)
    _finish(chat_id,
            "✅ <b>Перевод отправлен</b>\n\n"
            f"Получатель: <b>{html.escape(recipient_name)}</b>\n"
            f"Сумма: <b>{html.escape(wallet.to_coins(currency, units))}</b> {currency}\n"
            f"ID операции: <code>{op_id}</code>\n\n"
            f"⭐ Stars — {acc['stars']} · "
            f"🪙 Gram — {html.escape(wallet.to_coins('GRAM', acc['gram']))}")
    # Уведомляем получателя (если он когда-либо писал боту).
    if not wallet.DEMO_MODE:
        sender_name = html.escape(wallet.get_display_name(uid))
        amount = html.escape(wallet.to_coins(currency, units))
        staticgram.notify(
            recipient_id,
            "📥 <b>Вам поступил перевод</b>\n\n"
            f"Отправитель: <b>{sender_name}</b>\n"
            f"Сумма: <b>{amount}</b> {currency}\n"
            f"ID операции: <code>{op_id}</code>")


def do_send_command(chat_id, uid: str, args: list[str]) -> None:
    """Однострочная команда: /send @user stars 10."""
    if len(args) != 3:
        raise wallet.WalletError("Формат: <code>@username stars 10</code>")
    currency = args[1].upper()
    if currency not in wallet.CURRENCIES:
        raise wallet.WalletError("Валюта: stars или gram")
    recipient_id = wallet.resolve_recipient(args[0])
    do_send(chat_id, uid, recipient_id, currency, float(args[2].replace(",", ".")))


def do_topup(chat_id, uid: str, currency: str, coins: float) -> None:
    if wallet.DEMO_MODE:
        raise wallet.WalletError("Демо-режим: пополнение недоступно")
    units = wallet.to_units(currency, coins)
    wallet.send_topup_invoice(chat_id, uid, currency, units)
    STATES.pop(chat_id, None)
    _send(chat_id,
          "🧾 <b>Счёт на оплату создан</b>\n\n"
          f"Сумма: <b>{html.escape(wallet.to_coins(currency, units))}</b> {currency}\n"
          "Нажмите «Оплатить» в сообщении со счётом — средства зачислятся сразу.")


def do_withdraw(chat_id, uid: str, currency: str, coins: float) -> None:
    if wallet.DEMO_MODE:
        raise wallet.WalletError("Демо-режим: вывод недоступен")
    units = wallet.to_units(currency, coins)
    result = wallet.withdraw(uid, currency, units)
    STATES.pop(chat_id, None)
    _send(chat_id,
          "✅ <b>Вывод оформлен</b>\n\n"
          f"Сумма: <b>{html.escape(wallet.to_coins(currency, units))}</b> {currency}\n"
          f"ID операции: <code>{html.escape(result['request_id'])}</code>\n\n"
          "Средства уже летят на ваш аккаунт StaticGram 🚀")


def do_cheque_create(chat_id, uid: str, currency: str, coins: float) -> None:
    units = wallet.to_units(currency, coins)
    code = wallet.create_cheque(uid, currency, units)
    STATES.pop(chat_id, None)
    amount = wallet.to_coins(currency, units)
    code_display = wallet.format_code(code)
    caption = (f"🎫 <b>Чек на {html.escape(amount)} {currency}</b>\n\n"
               f"Код: <code>{code_display}</code>\n\n"
               "Переошлите фото другу — он активирует чек у "
               f"@{html.escape(staticgram.get_me().get('username', 'tbank_bot'))}. "
               "Чек одноразовый.")
    if imggen:
        try:
            png = imggen.make_cheque_image(currency, amount, code_display)
            staticgram.send_photo(chat_id, png, caption)
            return
        except Exception:
            traceback.print_exc()
    _send(chat_id, caption)


def do_cheque_redeem(chat_id, uid: str, code: str) -> None:
    currency, units = wallet.redeem_cheque(uid, code)
    STATES.pop(chat_id, None)
    amount = wallet.to_coins(currency, units)
    acc = wallet.account_payload(uid)
    _send(chat_id,
          "🎉 <b>Чек активирован!</b>\n\n"
          f"Зачислено: <b>{html.escape(amount)}</b> {currency}\n\n"
          f"⭐ Stars — {acc['stars']} · "
          f"🪙 Gram — {html.escape(wallet.to_coins('GRAM', acc['gram']))}")


def do_points_convert(chat_id, uid: str, points: int) -> None:
    stars = wallet.convert_points(uid, points)
    STATES.pop(chat_id, None)
    acc = wallet.account_payload(uid)
    _send(chat_id,
          "💎 <b>Баллы обменяны!</b>\n\n"
          f"Обменено: <b>{points}</b> баллов\n"
          f"Получено: <b>{stars}</b> STARS\n\n"
          f"⭐ Stars — {acc['stars']} · 💎 Баллы — {acc['points']}")


def do_admin_give(chat_id, admin_uid: str, args: list[str]) -> None:
    """/give @user stars 50 | gram 2.5 | points 30 — только для админов."""
    if not wallet.is_admin(admin_uid):
        raise wallet.WalletError("Команда только для администраторов")
    if len(args) != 3:
        raise wallet.WalletError("Формат: <code>@user stars 50</code> | <code>gram 2.5</code> | <code>points 30</code>")
    target_id, units = wallet.admin_give(args[0], args[1], args[2].replace(",", "."))
    name = wallet.get_display_name(target_id)
    kind = args[1].upper()
    amount = units if kind == "POINTS" else html.escape(wallet.to_coins(kind, units))
    _send(chat_id,
          "🛠 <b>Выдано</b>\n\n"
          f"Получатель: <b>{html.escape(name)}</b>\n"
          f"Начислено: <b>{amount}</b> {kind}")
    if not wallet.DEMO_MODE:
        staticgram.notify(target_id,
                          "🛠 <b>Администратор начислил вам</b>\n\n"
                          f"Сумма: <b>{amount}</b> {kind}")


def do_admin_stats(chat_id) -> None:
    """Сводка по кошельку: пользователи и обороты."""
    from database import connect
    with connect() as con:
        users = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        stars = con.execute("SELECT COALESCE(SUM(stars),0) s FROM users").fetchone()["s"]
        gram = con.execute("SELECT COALESCE(SUM(gram),0) g FROM users").fetchone()["g"]
        points = con.execute("SELECT COALESCE(SUM(points),0) p FROM users").fetchone()["p"]
        ops = con.execute("SELECT COUNT(*) c FROM operations").fetchone()["c"]
    _send(chat_id,
          "<b>📊 Статистика TBank</b>\n\n"
          f"Пользователей: <b>{users}</b>\n"
          f"Всего Stars на счетах: <b>{stars}</b>\n"
          f"Всего Gram на счетах: <b>{html.escape(wallet.to_coins('GRAM', gram))}</b>\n"
          f"Всего баллов: <b>{points}</b>\n"
          f"Операций: <b>{ops}</b>")


# ---- Обработка сообщений ----

def handle_message(message: dict) -> None:
    chat_id = (message.get("chat") or {}).get("id")
    user = message.get("from") or {}
    uid = str(user.get("id") or "")
    if not chat_id or not uid:
        return

    # Запоминаем пользователя и его username — иначе переводы по @username не работают.
    wallet.ensure_user(uid, user.get("username") or "")

    # Успешная оплата счёта.
    if message.get("successful_payment"):
        try:
            credited = wallet.process_successful_payment(uid, message["successful_payment"])
        except (wallet.WalletError, ValueError) as e:
            _error(chat_id, str(e))
            return
        if credited:
            pay = message["successful_payment"]
            currency = "STARS" if pay.get("currency") == "XTR" else "GRAM"
            amount = wallet.to_coins(currency, int(pay["total_amount"]))
            _send(chat_id,
                  "💰 <b>Оплата получена</b>\n\n"
                  f"Зачислено: <b>{html.escape(amount)}</b> {currency} ✅")
        return

    text = (message.get("text") or "").strip()
    if not text:
        return
    cmd, _, rest = text.partition(" ")
    cmd_key = cmd.split("@")[0].lower()
    args = rest.split()

    # 1. Явные команды.
    if cmd_key == "/start":
        set_menu_button(chat_id)
        name = html.escape(user.get("first_name") or user.get("username") or "друг")
        _send(chat_id,
              f"👋 Привет, <b>{name}</b>!\n\n"
              "Это кошелёк <b>TBank</b>: Stars и Gram с реальным пополнением, "
              "выводом, переводами по @username и подарочными чеками 🎁\n\n"
              "Выбирайте действие кнопками меню 👇",
              reply_markup=main_menu(uid))
        return
    if cmd_key == "/cancel":
        STATES.pop(chat_id, None)
        PENDING.pop(chat_id, None)
        _send(chat_id, "↩️ Отменено.")
        return
    if cmd_key == "/help":
        _send(chat_id, HELP)
        return
    if cmd_key == "/balance":
        do_balance(chat_id, uid)
        return
    if cmd_key == "/history":
        do_history(chat_id, uid)
        return
    if cmd_key == "/topup":
        try:
            currency, coins = _parse_currency_amount(args)
            do_topup(chat_id, uid, currency, coins)
        except (wallet.WalletError, ValueError) as e:
            _error(chat_id, str(e))
        return
    if cmd_key == "/withdraw":
        try:
            currency, coins = _parse_currency_amount(args)
            do_withdraw(chat_id, uid, currency, coins)
        except (wallet.WalletError, ValueError) as e:
            _error(chat_id, str(e))
        return
    if cmd_key == "/send":
        try:
            do_send_command(chat_id, uid, args)
        except (wallet.WalletError, ValueError) as e:
            _error(chat_id, str(e))
        return
    if cmd_key == "/cheque":
        try:
            currency, coins = _parse_currency_amount(args)
            do_cheque_create(chat_id, uid, currency, coins)
        except (wallet.WalletError, ValueError) as e:
            _error(chat_id, str(e))
        return
    if cmd_key == "/activate":
        try:
            do_cheque_redeem(chat_id, uid, rest.strip())
        except (wallet.WalletError, ValueError) as e:
            _error(chat_id, str(e))
        return
    if cmd_key == "/give":
        try:
            do_admin_give(chat_id, uid, args)
        except (wallet.WalletError, ValueError) as e:
            _error(chat_id, str(e))
        return

    # 2. Кнопки меню.
    if text == BTN_APP:
        _send(chat_id, "Откройте Mini App кнопкой меню слева от поля ввода "
                       "или из профиля бота.")
        return
    if text == BTN_HELP:
        _send(chat_id, HELP)
        return
    if text == BTN_BALANCE:
        do_balance(chat_id, uid)
        return
    if text == BTN_HISTORY:
        do_history(chat_id, uid)
        return
    if text == BTN_SEND:
        STATES[chat_id] = "send:to"
        PENDING.pop(chat_id, None)
        _send(chat_id, PROMPTS["send:to"])
        return
    if text in (BTN_TOPUP, BTN_WITHDRAW):
        action = "topup" if text == BTN_TOPUP else "withdraw"
        if wallet.DEMO_MODE:
            _error(chat_id, "Демо-режим: пополнение и вывод недоступны.")
            return
        STATES[chat_id] = action
        word = "пополнить" if action == "topup" else "вывести"
        _send(chat_id,
              f"<b>{'💰 Пополнение' if action == 'topup' else '💸 Вывод'} · шаг 1 из 2</b>\n\n"
              f"Что хотите {word}? Выберите валюту:",
              reply_markup=currency_keyboard(action))
        return
    if text == BTN_CHEQUE:
        STATES.pop(chat_id, None)
        _send(chat_id,
              "<b>🎟 Чеки</b>\n\nЧек — одноразовый код на сумму Stars или Gram. "
              "Передайте код другу — он зачислит сумму себе.",
              reply_markup=_inline([[("🎫 Создать чек", "cheque:create"),
                                     ("✅ Активировать чек", "cheque:redeem")]]))
        return
    if text == BTN_POINTS:
        acc = wallet.account_payload(uid)
        STATES[chat_id] = "points:convert"
        _send(chat_id,
              f"<b>💎 Баллы кэшбэка</b>\n\n"
              f"У вас: <b>{acc['points']}</b> баллов\n"
              f"За переводы Stars: 1 балл за каждые "
              f"{wallet.CASHBACK_STARS_PER_POINT} ⭐\n\n" +
              PROMPTS["points_convert"])
        return
    if text == BTN_ADMIN:
        if not wallet.is_admin(uid):
            _error(chat_id, "Админ-панель доступна только администраторам.")
            return
        STATES.pop(chat_id, None)
        PENDING.pop(chat_id, None)
        _send(chat_id,
              "<b>🛠 Админ-панель</b>\n\nВыберите действие:",
              reply_markup=admin_panel_keyboard())
        return

    # 3. Ответ в рамках сценария.
    state = STATES.get(chat_id)
    if state:
        try:
            if state == "send:to":
                recipient_id = wallet.resolve_recipient(text)
                PENDING[chat_id] = {"recipient": recipient_id}
                STATES[chat_id] = "send:cur"
                name = wallet.get_display_name(recipient_id)
                _send(chat_id,
                      "✅ Получатель: "
                      f"<b>{html.escape(name)}</b>\n\n"
                      "<b>📤 Перевод · шаг 2 из 3</b>\n\nВыберите валюту:",
                      reply_markup=currency_keyboard("send"))
            elif state == "send:cur":
                raise wallet.WalletError("Выберите валюту кнопками выше.")
            elif state.startswith("send:amount:"):
                currency = state.split(":")[-1]
                recipient_id = (PENDING.get(chat_id) or {}).get("recipient")
                if not recipient_id:
                    STATES[chat_id] = "send:to"
                    raise wallet.WalletError("Сначала укажите получателя.")
                do_send(chat_id, uid, recipient_id, currency,
                        float(text.replace(",", ".")))
            elif state in ("topup", "withdraw"):
                raise wallet.WalletError("Выберите валюту кнопками выше.")
            elif state.startswith("topup:"):
                do_topup(chat_id, uid, state.split(":")[1],
                         float(text.replace(",", ".")))
            elif state.startswith("withdraw:"):
                do_withdraw(chat_id, uid, state.split(":")[1],
                            float(text.replace(",", ".")))
            elif state == "cheque":
                currency, coins = _parse_currency_amount(text.split())
                do_cheque_create(chat_id, uid, currency, coins)
            elif state == "cheque:redeem":
                do_cheque_redeem(chat_id, uid, text)
            elif state == "points:convert":
                do_points_convert(chat_id, uid, int(text.replace(",", ".")))
            elif state == "admin:to":
                target_id = wallet.resolve_recipient(text)
                PENDING[chat_id]["target"] = target_id
                STATES[chat_id] = "admin:amount"
                name = wallet.get_display_name(target_id)
                kind = PENDING[chat_id]["kind"]
                label = {"STARS": "⭐ Stars", "GRAM": "🪙 Gram", "POINTS": "💎 баллов"}[kind]
                _send(chat_id,
                      f"✅ Получатель: <b>{html.escape(name)}</b>\n"
                      f"Что выдаём: <b>{label}</b>\n\n" + PROMPTS["admin_amount"])
            elif state == "admin:amount":
                pending = PENDING.get(chat_id) or {}
                target = pending.get("target")
                kind = pending.get("kind")
                if not target or not kind:
                    STATES.pop(chat_id, None)
                    raise wallet.WalletError("Начните заново: кнопка Админ-панель.")
                amount = text.replace(",", ".")
                target_id, units = wallet.admin_give(target, kind, amount)
                STATES.pop(chat_id, None)
                PENDING.pop(chat_id, None)
                name = wallet.get_display_name(target_id)
                shown = units if kind == "POINTS" else html.escape(wallet.to_coins(kind, units))
                _send(chat_id,
                      "🛠 <b>Выдано</b>\n\n"
                      f"Получатель: <b>{html.escape(name)}</b>\n"
                      f"Начислено: <b>{shown}</b> {kind}")
                if not wallet.DEMO_MODE:
                    staticgram.notify(target_id,
                                      "🛠 <b>Администратор начислил вам</b>\n\n"
                                      f"Сумма: <b>{shown}</b> {kind}")
        except (wallet.WalletError, ValueError) as e:
            prompt_key = ("cheque_code" if state == "cheque:redeem"
                          else "cheque_amount" if state == "cheque"
                          else "points_convert" if state == "points:convert"
                          else "admin_to" if state == "admin:to"
                          else "admin_amount" if state == "admin:amount"
                          else "send:to" if state == "send:to"
                          else "amount")
            prompt = PROMPTS[prompt_key]
            if prompt_key == "amount":
                step = ("📤 Перевод · шаг 3 из 3" if state.startswith("send:amount")
                        else "💰 Пополнение · шаг 2 из 2" if state.startswith("topup")
                        else "💸 Вывод · шаг 2 из 2")
                prompt = prompt.format(step=step)
            _error(chat_id, str(e))
            _send(chat_id, prompt)
        return
    # неизвестный ввод — меню всегда под рукой


def handle_callback(update: dict) -> None:
    """Inline-кнопки: выбор валюты и действия с чеками."""
    query = update.get("callback_query") or {}
    chat_id = (query.get("message") or {}).get("chat", {}).get("id")
    user = query.get("from") or {}
    uid = str(user.get("id") or "")
    data = query.get("data") or ""
    if not chat_id or not data:
        return
    staticgram.api_call("answerCallbackQuery", {"callback_query_id": query.get("id")})
    try:
        kind, action, value = (data.split(":") + [""])[:3]
        if kind == "cur" and action == "send" and value:
            STATES[chat_id] = f"send:amount:{value}"
            _send(chat_id, PROMPTS["amount"].format(step="📤 Перевод · шаг 3 из 3"))
        elif kind == "cur" and action in ("topup", "withdraw") and value:
            STATES[chat_id] = f"{action}:{value}"
            title = "💰 Пополнение · шаг 2 из 2" if action == "topup" else "💸 Вывод · шаг 2 из 2"
            _send(chat_id, PROMPTS["amount"].format(step=title))
        elif kind == "cheque" and action == "create":
            STATES[chat_id] = "cheque"
            _send(chat_id, PROMPTS["cheque_amount"])
        elif kind == "cheque" and action == "redeem":
            STATES[chat_id] = "cheque:redeem"
            _send(chat_id, PROMPTS["cheque_code"])
        elif kind == "adm":
            if not wallet.is_admin(uid):
                _error(chat_id, "Админ-панель доступна только администраторам.")
                return
            if action == "give" and value:
                PENDING[chat_id] = {"kind": value}
                STATES[chat_id] = "admin:to"
                label = {"STARS": "⭐ Stars", "GRAM": "🪙 Gram", "POINTS": "💎 баллов"}[value]
                _send(chat_id, f"Выдаём: <b>{label}</b>\n\n" + PROMPTS["admin_to"])
            elif action == "stats":
                do_admin_stats(chat_id)
    except (ValueError, KeyError):
        pass


def handle_update(update: dict) -> None:
    """Обрабатывает одно обновление Bot API."""
    if update.get("pre_checkout_query"):
        try:
            wallet.process_pre_checkout(update["pre_checkout_query"])
        except Exception:
            traceback.print_exc()
        return
    if update.get("callback_query"):
        try:
            handle_callback(update)
        except Exception:
            traceback.print_exc()
        return
    message = update.get("message") or {}
    if message:
        handle_message(message)


def main() -> None:
    if not STATICGRAM_TOKEN:
        raise SystemExit("Задайте STATICGRAM_TOKEN в .env или переменных окружения.")
    from database import init_db
    init_db()  # бот может работать без сервера — создаём таблицы сами
    me = staticgram.get_me()
    print(f"Бот @{me.get('username')} (id {me.get('id')}) подключён к StaticGram.", flush=True)
    print("Ожидание сообщений… (Ctrl+C для остановки)", flush=True)
    offset = 0
    while True:
        try:
            updates = staticgram.api_call("getUpdates", {
                "offset": offset, "timeout": 25,
            })
            for upd in updates:
                offset = upd["update_id"] + 1
                try:
                    handle_update(upd)
                except Exception:
                    traceback.print_exc()
        except KeyboardInterrupt:
            print("\nОстановлено.")
            return
        except staticgram.StaticGramError as e:
            print(f"Ошибка Bot API: {e}")
            time.sleep(5)
        except Exception:
            traceback.print_exc()
            time.sleep(5)


if __name__ == "__main__":
    main()
