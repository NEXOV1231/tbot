"""Юнит-тесты wallet.py и обработчиков бота (Bot API подменён заглушкой).

Запуск: py test_wallet.py
"""
import json
import os
import sys
import tempfile

import database
import staticgram
import wallet

# Изолированная временная база для тестов.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
database.DB_PATH = database.Path(_tmp.name)
database.init_db()

CALLS = []


def fake_api(method, params=None, files=None):
    CALLS.append((method, params or {}))
    if method == "createInvoiceLink":
        return "https://staticgram.top/invoice/test-stars"
    if method == "createGramInvoiceLink":
        return "https://staticgram.top/invoice/test-gram"
    if method == "withdrawStarBalance":
        return {"star_count": params["star_count"], "remaining_balance": 0}
    if method == "withdrawGramBalance":
        return {"nanoton_amount": params["nanoton_amount"], "remaining_balance": 0}
    if method in ("answerPreCheckoutQuery", "answerCallbackQuery"):
        return True
    if method == "setChatMenuButton":
        return True
    if method == "getMe":
        return {"username": "tbank_bot", "id": 1}
    if method == "sendPhoto":
        return {"photo": [{}]}
    if method in ("sendInvoice", "sendGramInvoice"):
        return {"message_id": 2}
    if method == "sendMessage":
        return {"message_id": 1}
    raise AssertionError(f"неожиданный метод {method}")


staticgram.api_call = fake_api  # wallet вызывает staticgram.api_call — подменяем

PASS = 0


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print("PASS " + name)


# ---------- to_units / to_coins ----------
check("STARS: 10 -> 10", wallet.to_units("STARS", 10) == 10)
check("GRAM: 1.5 -> 1500000000 нанотон", wallet.to_units("GRAM", "1.5") == 1_500_000_000)
try:
    wallet.to_units("STARS", 1.5); check("STARS дробные отклонены", False)
except wallet.WalletError:
    check("STARS дробные отклонены", True)
try:
    wallet.to_units("GRAM", 0.001); check("GRAM < 0.01 отклонён", False)
except wallet.WalletError:
    check("GRAM < 0.01 отклонён", True)
check("to_coins GRAM", wallet.to_coins("GRAM", 1_500_000_000) == "1.5")
check("to_coins STARS", wallet.to_coins("STARS", 42) == "42")

# ---------- payload счёта ----------
CALLS.clear()
link = wallet.create_topup_invoice_link("555000111", "STARS", 100)
check("счёт Stars: createInvoiceLink", CALLS[-1][0] == "createInvoiceLink")
check("ссылка на счёт", link.startswith("https://"))
CALLS.clear()
wallet.create_topup_invoice_link("555000111", "GRAM", 2_000_000_000)
check("счёт Gram: createGramInvoiceLink", CALLS[-1][0] == "createGramInvoiceLink")
check("nanoton_amount целым числом", CALLS[-1][1]["nanoton_amount"] == 2_000_000_000)
uid, cur = wallet.parse_invoice_payload(CALLS[-1][1]["payload"])
check("payload roundtrip", uid == "555000111" and cur == "GRAM")

# ---------- pre_checkout ----------
CALLS.clear()
wallet.process_pre_checkout({"id": "q1", "invoice_payload": "tbank-topup-XTR-555000111", "total_amount": 50})
check("pre_checkout ok=True", CALLS[-1][1]["ok"] is True)
CALLS.clear()
wallet.process_pre_checkout({"id": "q2", "invoice_payload": "чужой-счёт", "total_amount": 50})
check("pre_checkout чужой счёт -> ok=False", CALLS[-1][1]["ok"] is False)

# ---------- зачисление и идемпотентность ----------
pay = {"currency": "XTR", "total_amount": 50,
       "invoice_payload": "tbank-topup-XTR-555000111",
       "telegram_payment_charge_id": "stx-test-1"}
check("successful_payment зачислен", wallet.process_successful_payment("555000111", pay) is True)
check("повторный тот же charge_id пропущен", wallet.process_successful_payment("555000111", pay) is False)
acc = wallet.account_payload("555000111")
check("баланс Stars = 50", acc["stars"] == 50)
check("операция topup в истории", any(o["kind"] == "topup" and o["status"] == "completed" for o in acc["operations"]))

try:
    wallet.process_successful_payment("другой-юзер", pay)
    check("плательщик != получатель отклонён", False)
except wallet.WalletError:
    check("плательщик != получатель отклонён", True)

# ---------- перевод ----------
wallet.ensure_user("555000222")
wallet.credit_payment("stx-gram-1", "555000111", "GRAM", 2_000_000_000)  # 2 Gram
wallet.transfer("555000111", "555000222", "GRAM", 1_000_000_000)
a1, a2 = wallet.account_payload("555000111"), wallet.account_payload("555000222")
check("перевод GRAM: отправитель списан", a1["gram"] == 1_000_000_000)
check("перевод GRAM: получатель получил 1 Gram", a2["gram"] == 1_000_000_000)
try:
    wallet.transfer("555000111", "555000222", "STARS", 999)
    check("овердрафт отклонён", False)
except wallet.WalletError:
    check("овердрафт отклонён", True)

# ---------- вывод ----------
try:
    wallet.withdraw("555000111", "STARS", 1000)
    check("вывод без средств отклонён", False)
except wallet.WalletError:
    check("вывод без средств отклонён", True)

CALLS.clear()
res = wallet.withdraw("555000111", "STARS", 20)
check("вызван withdrawStarBalance", CALLS[-1][0] == "withdrawStarBalance")
check("star_count и request_id", CALLS[-1][1]["star_count"] == 20 and CALLS[-1][1]["request_id"].startswith("tbank-wd-"))
check("баланс после вывода 30", wallet.account_payload("555000111")["stars"] == 30)
check("операция withdraw completed", any(o["kind"] == "withdraw" and o["status"] == "completed" for o in wallet.account_payload("555000111")["operations"]))

# ошибка API -> операция failed, баланс не списан
def failing_api(method, params=None):
    if method in ("withdrawStarBalance", "withdrawGramBalance"):
        raise staticgram.StaticGramError("BALANCE_TOO_LOW")
    return fake_api(method, params)

staticgram.api_call = failing_api
wallet.credit_payment("stx-test-2", "555000111", "STARS", 10)
before = wallet.account_payload("555000111")["stars"]
try:
    wallet.withdraw("555000111", "STARS", 5)
    check("ошибка API поднята", False)
except wallet.WalletError as e:
    check("ошибка API поднята", "StaticGram" in str(e))
check("баланс не изменился при ошибке API", wallet.account_payload("555000111")["stars"] == before)
check("операция withdraw failed", any(o["kind"] == "withdraw" and o["status"] == "failed" for o in wallet.account_payload("555000111")["operations"]))
staticgram.api_call = fake_api

# ---------- перевод по @username ----------
wallet.ensure_user("555000333", "ivan")  # пользователь с username
op_id = wallet.transfer("555000111", "@ivan", "STARS", 5)
check("перевод по @username", wallet.account_payload("555000333")["stars"] == 5)
check("перевод возвращает id операции", isinstance(op_id, int) and op_id > 0)
try:
    wallet.resolve_recipient("@nobody")
    check("неизвестный username отклонён", False)
except wallet.WalletError:
    check("неизвестный username отклонён", True)

# ---------- чеки ----------
wallet.ensure_user("777000111")
wallet.credit_payment("stx-chq-1", "777000111", "STARS", 30)
code = wallet.create_cheque("777000111", "STARS", 25)
check("чек создан, баланс списан", wallet.account_payload("777000111")["stars"] == 5)
check("код 12 символов", len(code) == 12)
check("формат кода с дефисами", wallet.format_code(code) == f"{code[:4]}-{code[4:8]}-{code[8:]}")
currency, units = wallet.redeem_cheque("555000333", wallet.format_code(code))
check("чек активирован получателем", (currency, units) == ("STARS", 25))
check("сумма зачислена получателю", wallet.account_payload("555000333")["stars"] == 30)
try:
    wallet.redeem_cheque("555000333", code)
    check("повторная активация отклонена", False)
except wallet.WalletError:
    check("повторная активация отклонена", True)
try:
    wallet.redeem_cheque("777000111", code)
    check("свой чек активировать нельзя", False)
except wallet.WalletError:
    check("свой чек активировать нельзя", True)
try:
    wallet.redeem_cheque("555000333", "AAAA-BBBB-CCCC")
    check("несуществующий чек отклонён", False)
except wallet.WalletError:
    check("несуществующий чек отклонён", True)
try:
    wallet.create_cheque("777000111", "STARS", 100)
    check("чек без средств отклонён", False)
except wallet.WalletError:
    check("чек без средств отклонён", True)

# ---------- username: обновление при повторном открытии ----------
wallet.ensure_user("888000111")          # создан без username
wallet.ensure_user("888000111", "")      # пустой username не затирает
wallet.ensure_user("888000111", "petr")  # непустой сохраняется
check("username обновляется, пустой не затирает",
      wallet.get_display_name("888000111") == "@petr")

# ---------- кэшбэк и баллы ----------
wallet.credit_payment("stx-cb-1", "555000111", "STARS", 500)
wallet.transfer("555000111", "555000222", "STARS", 250)
acc = wallet.account_payload("555000111")
check("кэшбэк: 2 балла за 250 Stars", acc["points"] == 2)
check("кэшбэк в истории", any(o["kind"] == "cashback" and o["amount"] == 2 for o in acc["operations"]))
wallet.ensure_user("1780244365", "boss")
wallet.admin_give("555000111", "points", 20)  # докидываем баллов для теста обмена
try:
    wallet.convert_points("555000111", 5)
    check("обмен меньше 10 баллов отклонён", False)
except wallet.WalletError:
    check("обмен меньше 10 баллов отклонён", True)
stars_before = wallet.account_payload("555000111")["stars"]
got = wallet.convert_points("555000111", 10)
check("обмен 10 баллов -> 10 Stars", got == 10)
acc = wallet.account_payload("555000111")
check("баллы списаны, Stars начислены",
      acc["points"] == 12 and acc["stars"] == stars_before + 10)
check("обмен в истории", any(o["kind"] == "points_convert" for o in acc["operations"]))
wallet.convert_points("555000111", 10)
try:
    wallet.convert_points("555000111", 10)
    check("обмен без баллов отклонён", False)
except wallet.WalletError:
    check("обмен без баллов отклонён", True)

# ---------- админ-выдача ----------
ADMIN_UID = "1780244365"  # из .env ADMIN_IDS
wallet.ensure_user("555000444", "mike")
check("is_admin: админ распознан", wallet.is_admin(ADMIN_UID))
check("is_admin: обычный пользователь не админ", not wallet.is_admin("555000444"))
wallet.admin_give("@mike", "stars", 50)
wallet.admin_give("555000444", "gram", 2.5)
target_id, units = wallet.admin_give("@mike", "points", 30)
acc = wallet.account_payload("555000444")
check("админ выдал Stars", acc["stars"] == 50)
check("админ выдал Gram (нанотоны)", acc["gram"] == 2_500_000_000)
check("админ выдал баллы", acc["points"] == 30)
check("выдача в истории", any(o["kind"] == "admin_give" for o in acc["operations"]))
try:
    wallet.admin_give("@ghost", "stars", 5)
    check("выдача неизвестному отклонена", False)
except wallet.WalletError:
    check("выдача неизвестному отклонена", True)

# ---------- обработчики бота ----------
import bot

CALLS.clear()
bot.MINI_APP_URL = "https://example.com/app"  # для проверки кнопки меню
bot.handle_update({"update_id": 1, "message": {
    "chat": {"id": 555000111}, "text": "/start",
    "from": {"id": 555000111, "first_name": "T"}}})
check("бот /start показывает меню-клавиатуру",
      CALLS[-1][0] == "sendMessage" and "keyboard" in CALLS[-1][1].get("reply_markup", {}))
menu_btn = next(c for c in CALLS if c[0] == "setChatMenuButton")
check("бот /start прикрепляет кнопку меню Mini App",
      menu_btn[1]["menu_button"]["web_app"]["url"] == "https://example.com/app")
bot.MINI_APP_URL = ""

CALLS.clear()
bot.handle_update({"update_id": 2, "message": {
    "chat": {"id": 555000111}, "text": bot.BTN_BALANCE,
    "from": {"id": 555000111}}})
check("бот: кнопка Баланс отвечает балансом", "Stars" in CALLS[-1][1]["text"])

# бот запоминает username собеседника
bot.handle_update({"update_id": 3, "message": {
    "chat": {"id": 555000333}, "text": "/balance",
    "from": {"id": 555000333, "username": "ivan"}}})
check("бот: username собеседника сохранён",
      wallet.get_display_name("555000333") == "@ivan")

# пошаговый сценарий «Отправить»: @username -> валюта кнопкой -> сумма
CALLS.clear()
bot.handle_update({"update_id": 4, "message": {
    "chat": {"id": 555000111}, "text": bot.BTN_SEND, "from": {"id": 555000111}}})
check("бот: шаг 1 просит @username", "шаг 1 из 3" in CALLS[-1][1]["text"])
before = wallet.account_payload("555000333")["stars"]
bot.handle_update({"update_id": 5, "message": {
    "chat": {"id": 555000111}, "text": "@ivan", "from": {"id": 555000111}}})
check("бот: @username принят, шаг 2 — валюта",
      bot.STATES.get(555000111) == "send:cur"
      and "inline_keyboard" in CALLS[-1][1].get("reply_markup", {}))
bot.handle_update({"update_id": 6, "callback_query": {
    "id": "cb1", "from": {"id": 555000111},
    "message": {"chat": {"id": 555000111}}, "data": "cur:send:STARS"}})
check("бот: валюта выбрана, шаг 3 — сумма",
      bot.STATES.get(555000111) == "send:amount:STARS")
CALLS.clear()
bot.handle_update({"update_id": 7, "message": {
    "chat": {"id": 555000111}, "text": "2", "from": {"id": 555000111}}})
check("бот: сумма -> перевод по @username",
      wallet.account_payload("555000333")["stars"] == before + 2)
check("бот: сценарий завершён", 555000111 not in bot.STATES)
notify = next(c for c in CALLS if c[0] == "sendMessage" and c[1].get("chat_id") == 555000333)
check("бот: получателю ушло уведомление", "поступил перевод" in notify[1]["text"])
check("бот: в уведомлении ID операции", "ID операции" in notify[1]["text"])
sender_confirm = next(c for c in CALLS if c[0] == "sendMessage"
                      and c[1].get("chat_id") == 555000111 and "Перевод отправлен" in c[1]["text"])
check("бот: в подтверждении ID операции", "ID операции" in sender_confirm[1]["text"])

# неизвестный @username на шаге 1: ошибка + повтор подсказки, сценарий продолжается
CALLS.clear()
bot.handle_update({"update_id": 8, "message": {
    "chat": {"id": 555000111}, "text": bot.BTN_SEND, "from": {"id": 555000111}}})
bot.handle_update({"update_id": 9, "message": {
    "chat": {"id": 555000111}, "text": "@ghost", "from": {"id": 555000111}}})
check("бот: неизвестный @username -> ошибка и повтор шага",
      bot.STATES.get(555000111) == "send:to"
      and any("Не получилось" in (c[1].get("text") or "") for c in CALLS))
bot.handle_update({"update_id": 10, "message": {
    "chat": {"id": 555000111}, "text": "/cancel", "from": {"id": 555000111}}})

# сценарий «Пополнить»: кнопка -> inline-выбор валюты -> сумма
CALLS.clear()
bot.handle_update({"update_id": 11, "message": {
    "chat": {"id": 555000111}, "text": bot.BTN_TOPUP, "from": {"id": 555000111}}})
check("бот: кнопка Пополнить предлагает выбор валюты",
      "inline_keyboard" in CALLS[-1][1].get("reply_markup", {}))
bot.handle_update({"update_id": 12, "callback_query": {
    "id": "cb2", "from": {"id": 555000111},
    "message": {"chat": {"id": 555000111}}, "data": "cur:topup:GRAM"}})
check("бот: callback выбора валюты -> состояние", bot.STATES.get(555000111) == "topup:GRAM")
check("бот: callback подтверждён", any(c[0] == "answerCallbackQuery" for c in CALLS))
CALLS.clear()
bot.handle_update({"update_id": 13, "message": {
    "chat": {"id": 555000111}, "text": "0.5", "from": {"id": 555000111}}})
check("бот: сумма -> sendGramInvoice", any(c[0] == "sendGramInvoice" for c in CALLS))
gram_inv = next(c for c in CALLS if c[0] == "sendGramInvoice")
check("сумма в нанотонах", gram_inv[1]["nanoton_amount"] == 500_000_000)

# сценарий «Вывести»: ошибка -> подсказка повторяется, /cancel отменяет
CALLS.clear()
bot.handle_update({"update_id": 14, "message": {
    "chat": {"id": 555000111}, "text": bot.BTN_WITHDRAW, "from": {"id": 555000111}}})
bot.handle_update({"update_id": 15, "callback_query": {
    "id": "cb3", "from": {"id": 555000111},
    "message": {"chat": {"id": 555000111}}, "data": "cur:withdraw:STARS"}})
bot.handle_update({"update_id": 16, "message": {
    "chat": {"id": 555000111}, "text": "100000", "from": {"id": 555000111}}})
check("бот: ошибка вывода повторяет подсказку", "Отмена" in CALLS[-1][1]["text"])
check("бот: сценарий остался активен", bot.STATES.get(555000111) == "withdraw:STARS")
bot.handle_update({"update_id": 17, "message": {
    "chat": {"id": 555000111}, "text": "/cancel", "from": {"id": 555000111}}})
check("бот: /cancel отменяет сценарий", 555000111 not in bot.STATES)

# чеки в боте: создание (фото) и активация
CALLS.clear()
bot.handle_update({"update_id": 18, "message": {
    "chat": {"id": 777000111}, "text": "/cheque stars 3", "from": {"id": 777000111}}})
check("бот: /cheque отправляет фото", CALLS[-1][0] == "sendPhoto")
stars_777 = wallet.account_payload("777000111")["stars"]
check("бот: сумма чека списана", stars_777 == 2)
ops = wallet.account_payload("777000111")["operations"]
cheque_op = next(o for o in ops if o["kind"] == "cheque_create")
ch_code = wallet.normalize_code(cheque_op["details"].split()[-1])
CALLS.clear()
bot.handle_update({"update_id": 19, "message": {
    "chat": {"id": 555000333}, "text": f"/activate {ch_code}", "from": {"id": 555000333}}})
check("бот: /activate зачисляет чек",
      wallet.account_payload("555000333")["stars"] == before + 2 + 3)

# успешная оплата через сообщение в чате
CALLS.clear()
bot.handle_update({"update_id": 20, "message": {
    "chat": {"id": 555000222},
    "from": {"id": 555000222},
    "successful_payment": {"currency": "XTR", "total_amount": 7,
                           "invoice_payload": "tbank-topup-XTR-555000222",
                           "telegram_payment_charge_id": "stx-test-3"}}})
check("successful_payment в чате зачисляет", wallet.account_payload("555000222")["stars"] == 257)

# баллы: кнопка показывает баланс и сценарий обмена
CALLS.clear()
bot.handle_update({"update_id": 21, "message": {
    "chat": {"id": 555000444}, "text": bot.BTN_POINTS, "from": {"id": 555000444}}})
check("бот: кнопка Баллы показывает баланс баллов",
      "30" in CALLS[-1][1]["text"] and "Обмен" in CALLS[-1][1]["text"])
CALLS.clear()
bot.handle_update({"update_id": 22, "message": {
    "chat": {"id": 555000444}, "text": "10", "from": {"id": 555000444}}})
check("бот: обмен баллов работает",
      wallet.account_payload("555000444")["points"] == 20)

# /give: обычному отказ, админу — выдача (команда осталась для совместимости)
CALLS.clear()
bot.handle_update({"update_id": 23, "message": {
    "chat": {"id": 555000444}, "text": "/give @mike stars 5", "from": {"id": 555000444}}})
check("бот: /give обычному отклонён", "администратор" in CALLS[-1][1]["text"])
stars_mike = wallet.account_payload("555000444")["stars"]
bot.handle_update({"update_id": 24, "message": {
    "chat": {"id": 1780244365}, "text": "/give @mike stars 5",
    "from": {"id": 1780244365}}})
check("бот: админ выдал Stars",
      wallet.account_payload("555000444")["stars"] == stars_mike + 5)
check("бот: получатель уведомлён о выдаче",
      any(c[0] == "sendMessage" and c[1].get("chat_id") == 555000444
          and "администратор" in c[1]["text"] for c in CALLS))

# админ-панель кнопками: меню у админа и обычного, выдача через inline-кнопки
menu_admin = bot.main_menu("1780244365")
menu_user = bot.main_menu("555000444")
flat_admin = [b["text"] for row in menu_admin["keyboard"] for b in row]
flat_user = [b["text"] for row in menu_user["keyboard"] for b in row]
check("админ: кнопка панели в меню", bot.BTN_ADMIN in flat_admin)
check("пользователь: кнопки панели нет", bot.BTN_ADMIN not in flat_user)

CALLS.clear()
bot.handle_update({"update_id": 25, "message": {
    "chat": {"id": 1780244365}, "text": bot.BTN_ADMIN, "from": {"id": 1780244365}}})
check("админ: панель открывается кнопками",
      any("Выдать Stars" in b["text"] for row in CALLS[-1][1]["reply_markup"]["inline_keyboard"] for b in row))
bot.handle_update({"update_id": 26, "callback_query": {
    "id": "cb4", "from": {"id": 1780244365},
    "message": {"chat": {"id": 1780244365}}, "data": "adm:give:GRAM"}})
check("админ: выбрана выдача Gram, ждём получателя",
      bot.STATES.get(1780244365) == "admin:to")
bot.handle_update({"update_id": 27, "message": {
    "chat": {"id": 1780244365}, "text": "@mike", "from": {"id": 1780244365}}})
check("админ: получатель принят, ждём сумму",
      bot.STATES.get(1780244365) == "admin:amount")
gram_mike = wallet.account_payload("555000444")["gram"]
bot.handle_update({"update_id": 28, "message": {
    "chat": {"id": 1780244365}, "text": "1.5", "from": {"id": 1780244365}}})
check("админ: выдача Gram через кнопки",
      wallet.account_payload("555000444")["gram"] == gram_mike + 1_500_000_000)
check("админ: сценарий завершён", 1780244365 not in bot.STATES)

# не-админ, пытающийся открыть панель
CALLS.clear()
bot.handle_update({"update_id": 29, "message": {
    "chat": {"id": 555000444}, "text": bot.BTN_ADMIN, "from": {"id": 555000444}}})
check("пользователь: панель недоступна", "администратор" in CALLS[-1][1]["text"])

# статистика
CALLS.clear()
bot.handle_update({"update_id": 30, "callback_query": {
    "id": "cb5", "from": {"id": 1780244365},
    "message": {"chat": {"id": 1780244365}}, "data": "adm:stats"}})
check("админ: статистика показывает пользователей",
      "Пользователей" in CALLS[-1][1]["text"])

try:
    os.unlink(_tmp.name)
except OSError:
    pass  # файл ещё держит открытое соединение sqlite — удалится сам
print(f"\nВсе {PASS} проверок пройдены.")
