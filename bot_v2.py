# -*- coding: utf-8 -*-
# Бот для игры "Шпион" (Assassin) в школе
# Библиотека: pip install pyTelegramBotAPI
import telebot
from telebot import types
import json, os, random
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))

def now_msk():
    return datetime.now(MSK)

# ================== НАСТРОЙКИ ==================
TOKEN = os.getenv("TOKEN", "заглушка")
ADMIN_IDS = [8709725751]  # через запятую вставь свои user_id админов
import base64
import threading
import time
import requests

GH_TOKEN = os.getenv("GITHUB_TOKEN", "")
GH_REPO = os.getenv("GITHUB_REPOSITORY", "")
GH_BRANCH = "main"

_state_dirty = False

def _gh_headers():
    return {"Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json"}

def load_state_from_github():
    global STATE
    if not (GH_TOKEN and GH_REPO):
        return # локальный запуск — работаем с файлом на диске
    try:
        url = f"https://api.github.com/repos/{GH_REPO}/contents/game_state.json"
        r = requests.get(url, headers=_gh_headers(),
                         params={"ref": GH_BRANCH}, timeout=15)
        if r.status_code == 200:
            STATE = json.loads(base64.b64decode(r.json()["content"]).decode("utf-8"))
            print("Состояние загружено с GitHub — цели на месте")
        else:
            print("Сохранённого состояния нет — начинаем с чистого листа")
    except Exception as e:
        print("Не удалось загрузить состояние:", e)

def push_state_to_github():
    global _state_dirty
    if not (GH_TOKEN and GH_REPO):
        return
    url = f"https://api.github.com/repos/{GH_REPO}/contents/game_state.json"
    try:
        r = requests.get(url, headers=_gh_headers(),
                         params={"ref": GH_BRANCH}, timeout=15)
        sha = r.json().get("sha") if r.status_code == 200 else None
        body = {
            "message": "сохранение состояния игры",
            "branch": GH_BRANCH,
            "content": base64.b64encode(
                json.dumps(STATE, ensure_ascii=False).encode("utf-8")
            ).decode("ascii"),
        }
        if sha:
            body["sha"] = sha
        r = requests.put(url, headers=_gh_headers(), json=body, timeout=15)
        if r.status_code in (200, 201):
            _state_dirty = False
        else:
            print("Не удалось сохранить состояние:", r.status_code)
    except Exception as e:
        print("Ошибка сохранения состояния:", e)

def _autosave_loop():
    while True:
        time.sleep(120)
        if _state_dirty:
            push_state_to_github()

DB_FILE = "game_state.json"
START_TIME = 8 * 60    # 8:00
END_TIME = 18 * 60    # 18:00
MAX_WARNINGS = 3

bot = telebot.TeleBot(TOKEN)

# ================== ХРАНИЛИЩЕ ==================
def default_state():
    return {"players": {}, "round_active": False, "paused": False,
            "started_date": None, "ended_date": None, "log": []}

def load_state():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return default_state()

def save_state():
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(STATE, f, ensure_ascii=False, indent=2)

STATE = load_state()
STATE.setdefault("players", {})
STATE.setdefault("round_active", False)
STATE.setdefault("paused", False)
STATE.setdefault("started_date", None)
STATE.setdefault("ended_date", None)
STATE.setdefault("log", [])

STATES = {}    # uid -> этап регистрации
PENDING = {}   # ev -> событие подтверждения
EV_COUNTER = 0

def P(uid):
    return STATE["players"].get(str(uid))

def fmt(p):
    return f"{p['surname']} {p['name']} ({p['klass']})"

def save():
    save_state()
    _state_dirty = True

# ================== КЛАВИАТУРЫ ==================
def game_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🔪 Я убил", "💀 Меня убили")
    kb.add("🚨 Моя жертва — фейк", "🚪 Выйти из игры")
    return kb

def skip_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add("Пропустить")
    return kb

def notify_admins(text, kb=None):
    for aid in ADMIN_IDS:
        try:
            bot.send_message(aid, text, reply_markup=kb)
        except Exception:
            pass

def in_time_window():
    now = now_msk()
    t = now.hour * 60 + now.minute
    return START_TIME <= t < END_TIME

def registration_open():
    """Регистрация: воскресенье, 8:00–23:55 по мск."""
    t = now_msk()
    if t.weekday() != 6: # 6 = воскресенье
        return False
    minutes = t.hour * 60 + t.minute
    return 8 * 60 <= minutes <= 23 * 60 + 55

def has_pending(uid):
    for ev, evd in PENDING.items():
        if uid in (evd["killer"], evd["victim"]) and evd.get("stage") != "done":
            return True
    return False

def is_admin(uid):
    return uid in ADMIN_IDS

def registered_in_round(uid):
    p = P(uid)
    return p and p.get("in_round") and p.get("alive")

# ================== РЕГИСТРАЦИЯ ==================
@bot.message_handler(commands=["start"])
def cmd_start(m):
    uid = m.chat.id
    if P(uid):
        if P(uid).get("banned"):
            bot.send_message(uid, "🚫 Вы забанены. Обратитесь к админам.")
        else:
            bot.send_message(uid, "Вы уже зарегистрированы. Ждите начала раунда.", reply_markup=game_kb())
        return
    if not registration_open():
        bot.send_message(uid,
            "📅 Регистрация открыта только в воскресенье с 8:00 до 23:55."
            "⏳ Дождитесь игрового тайминга.")
        return
    STATES[uid] = "surname"
    bot.send_message(uid, "📝 Регистрация.\nВведите вашу фамилию:")

@bot.message_handler(commands=["cancel"])
def cmd_cancel(m):
    STATES.pop(m.chat.id, None)
    bot.send_message(m.chat.id, "Регистрация отменена. /start — начать заново.")

@bot.message_handler(commands=["help", "rules"])
def cmd_help(m):
    bot.send_message(m.chat.id,
        "🎯 Игра «Шпион» — правила:\n"
        "• Бот выдаёт цель (Фамилия Имя, класс). Цель нужно «ликвидировать».\n"
        "• Ликвидация — только касание рукой плеча цели, БЕЗ свидетелей в радиусе 10 м.\n"
        "• ⏰ Игра идёт ежедневно с 8:00 до 18:00. Вне окна убийства не засчитываются.\n"
        "• 🚫 Нельзя убивать в туалете и за территорией школы.\n"
        "• 🚫 Нельзя мешать урокам, применять силу, передавать цель или врать про убийство.\n"
        "• 3 предупреждения = автоматический бан.\n"
        "• Если ваша цель не учится в школе — кнопка «🚨 Моя жертва — фейк».\n"
        "• Управление — только кнопки и команды. Текстовые сообщения не принимаются.")

def finish_registration(uid, username=None):
    st = STATES.pop(uid)
    STATE["players"][str(uid)] = {
        "surname": st["surname"], "name": st["name"], "klass": st["klass"],
        "username": username, "warnings": 0,
        "banned": False, "banned_round": False,
        "in_round": False, "alive": False, "out": False,
        "target": None, "hunter": None,
    }
    save()
    notify_admins(
        f"📋 Новая регистрация\n"
        f"Фамилия Имя: {st['surname']} {st['name']}\n"
        f"Класс: {st['klass']}\n"
        f"Telegram: {username or 'нет'} | id:{uid}\n"
        f"Всего зарегистрировано: {len(STATE['players'])}")
    bot.send_message(uid,
        "✅ Регистрация завершена!\nЖдите начала раунда — понедельник 8:00, вы получите свою цель.",
        reply_markup=game_kb())

@bot.message_handler(commands=["mytarget"])
def cmd_mytarget(m):
    p = P(m.chat.id)
    if not p:
        bot.send_message(m.chat.id, "Вы не зарегистрированы. /start")
        return
    if p["target"] and registered_in_round(m.chat.id):
        bot.send_message(m.chat.id, f"🎯 Ваша текущая жертва: {fmt(P(p['target']))}")
    else:
        bot.send_message(m.chat.id, "Сейчас у вас нет активной цели.")

# ================== АДМИН-КОМАНДЫ ==================
def do_start_round():
    ids = [int(uid) for uid, p in STATE["players"].items()
           if not p["banned"] and not p["banned_round"]]
    if len(ids) < 2:
        return False, len(ids)
    random.shuffle(ids)
    n = len(ids)
    for i, uid in enumerate(ids):
        p = P(uid)
        p["in_round"] = True
        p["alive"] = True
        p["out"] = False
        p["banned_round"] = False
        p["target"] = ids[(i + 1) % n]     # следующий по кругу — жертва
        p["hunter"] = ids[(i - 1) % n]     # предыдущий — охотник
    STATE["round_active"] = True
    STATE["paused"] = False
    STATE["log"] = []
    save()
    for uid in ids:
        try:
            bot.send_message(uid, "🎯 Игра началась!\nВаша цель: " + fmt(P(P(uid)["target"])),
                             reply_markup=game_kb())
        except Exception:
            pass
    scheme = "\n".join(f"• {fmt(P(u))} → {fmt(P(P(u)['target']))}" for u in ids)
    notify_admins(f"🚀 Раунд запущен. Участников: {n}\nСхема целей:\n{scheme}")
    return True, n

@bot.message_handler(commands=["startround"])
def cmd_startround(m):
    if not is_admin(m.chat.id):
        bot.reply_to(m, "Команда только для админов.")
        return
    if STATE["round_active"]:
        bot.reply_to(m, "Раунд уже идёт.")
        return
    ok, n = do_start_round()
    bot.reply_to(m, f"Раунд запущен. Участников: {n}" if ok
                 else f"Недостаточно участников ({n}, нужно минимум 2).")

@bot.message_handler(commands=["pause"])
def cmd_pause(m):
    if not is_admin(m.chat.id):
        return
    STATE["paused"] = True
    save()
    for uid, p in STATE["players"].items():
        if p["in_round"]:
            try:
                bot.send_message(int(uid), "⏸ Игра приостановлена админом. Состояние сохранено.")
            except Exception:
                pass
    bot.reply_to(m, "Пауза включена.")

@bot.message_handler(commands=["resume"])
def cmd_resume(m):
    if not is_admin(m.chat.id):
        return
    STATE["paused"] = False
    save()
    for uid, p in STATE["players"].items():
        if p["in_round"] and p["alive"]:
            t = fmt(P(p["target"])) if p["target"] else "—"
            try:
                bot.send_message(int(uid), f"▶️ Игра возобновлена!\n🎯 Ваша жертва: {t}")
            except Exception:
                pass
    bot.reply_to(m, "Игра возобновлена.")

@bot.message_handler(commands=["players"])
def cmd_players(m):
    if not is_admin(m.chat.id):
        return
    lines = []
    for uid, p in STATE["players"].items():
        status = []
        if p["banned"]: status.append("БАН")
        if p["banned_round"]: status.append("БАН-раунд")
        if p["in_round"]:
            status.append("жив" if p["alive"] and not p["out"] else "выбыл")
        else:
            status.append("не в раунде")
        if p["warnings"]: status.append(f"⚠️{p['warnings']}")
        t = fmt(P(p["target"])) if p["target"] and P(p["target"]) else "—"
        lines.append(f"• {fmt(p)} [{', '.join(status)}] → цель: {t} | id:{uid}")
    bot.send_message(m.chat.id, "Список игроков:\n" + "\n".join(lines) if lines else "Пока никто не зарегистрирован.")

@bot.message_handler(commands=["unwarn"])
def cmd_unwarn(m):
    if not is_admin(m.chat.id):
        return
    try:
        uid = int(m.text.split()[1])
        P(uid)["warnings"] = 0
        save()
        bot.reply_to(m, "Предупреждения сброшены.")
    except Exception:
        bot.reply_to(m, "Формат: /unwarn <id игрока>")

@bot.message_handler(commands=["unban"])
def cmd_unban(m):
    if not is_admin(m.chat.id):
        return
    try:
        uid = int(m.text.split()[1])
        p = P(uid)
        p["banned"] = False
        p["banned_round"] = False
        save()
        bot.reply_to(m, "Игрок разбанен.")
    except Exception:
        bot.reply_to(m, "Формат: /unban <id игрока>")

# ================== ИГРОВЫЕ КНОПКИ ==================
def blocked_reply(m):
    if not STATE["round_active"]:
        bot.reply_to(m, "Сейчас нет активного раунда.")
    elif STATE["paused"]:
        bot.reply_to(m, "⏸ Игра приостановлена админом. Состояние сохранено.")

@bot.message_handler(func=lambda m: m.text == "🔪 Я убил")
def btn_kill(m):
    uid = m.chat.id
    p = P(uid)
    if not p:
        return
    if not STATE["round_active"] or STATE["paused"]:
        blocked_reply(m); return
    if not registered_in_round(uid):
        bot.reply_to(m, "Вы не участвуете в этом раунде."); return
    if not in_time_window():
        bot.reply_to(m, "⏳ Дождитесь игрового тайминга — убийства засчитываются с 8:00 до 18:00."); return
    if not p["target"]:
        bot.reply_to(m, "У вас нет текущей цели."); return
    if has_pending(uid):
        bot.reply_to(m, "Уже идёт подтверждение события. Дождитесь результата."); return
    victim = P(p["target"])
    if not registered_in_round(p["target"]):
        bot.reply_to(m, "Ваша цель недоступна. Сообщите админам."); return
    global EV_COUNTER
    EV_COUNTER += 1
    ev = str(EV_COUNTER)
    PENDING[ev] = {"type": "A", "killer": uid, "victim": p["target"], "stage": 1}
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Да, он меня убил", callback_data=f"cf_{ev}_y"))
    kb.add(types.InlineKeyboardButton("❌ Нет, это не так", callback_data=f"cf_{ev}_n"))
    try:
        bot.send_message(p["target"], f"⚠️ {fmt(p)} утверждает, что убил вас. Подтверждаете?", reply_markup=kb)
    except Exception:
        pass
    bot.reply_to(m, "Запрос отправлен вашей цели. Ожидайте подтверждения.")

@bot.message_handler(func=lambda m: m.text == "💀 Меня убили")
def btn_dead(m):
    uid = m.chat.id
    p = P(uid)
    if not p:
        return
    if not STATE["round_active"] or STATE["paused"]:
        blocked_reply(m); return
    if not registered_in_round(uid):
        bot.reply_to(m, "Вы не участвуете в этом раунде."); return
    if not p["hunter"]:
        bot.reply_to(m, "У вас нет известного охотника."); return
    if has_pending(uid):
        bot.reply_to(m, "Уже идёт подтверждение события. Дождитесь результата."); return
    if not in_time_window():
        bot.reply_to(m, "⏳ Дождитесь игрового тайминга — убийства засчитываются с 8:00 до 18:00. Заявку отклоняю."); return
    global EV_COUNTER
    EV_COUNTER += 1
    ev = str(EV_COUNTER)
    PENDING[ev] = {"type": "B", "killer": p["hunter"], "victim": uid, "stage": 1}
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Да, я его убил", callback_data=f"cf_{ev}_y"))
    kb.add(types.InlineKeyboardButton("❌ Нет, это не так", callback_data=f"cf_{ev}_n"))
    try:
        bot.send_message(p["hunter"], f"⚠️ {fmt(p)} сообщает, что вы его убили. Это правда?", reply_markup=kb)
    except Exception:
        pass
    bot.reply_to(m, "Запрос отправлен вашему охотнику. Ожидайте подтверждения.")

@bot.message_handler(func=lambda m: m.text == "🚨 Моя жертва — фейк")
def btn_fake(m):
    uid = m.chat.id
    p = P(uid)
    if not p:
        return
    if not STATE["round_active"] or STATE["paused"]:
        blocked_reply(m); return
    if not registered_in_round(uid):
        bot.reply_to(m, "Вы не участвуете в этом раунде."); return
    if not p["target"]:
        bot.reply_to(m, "У вас нет текущей цели."); return
    tid = p["target"]
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🚫 Бан на текущую игру", callback_data=f"rep_{tid}_r"))
    kb.add(types.InlineKeyboardButton("⛔️ Бан навсегда", callback_data=f"rep_{tid}_f"))
    kb.add(types.InlineKeyboardButton("✅ Нарушений нет", callback_data=f"rep_{tid}_n"))
    notify_admins(f"🚨 РЕПОРТ\n{fmt(p)} жалуется, что цель не является учеником:\n"
                  f"{fmt(P(tid))} | юз: {P(tid)['username'] or 'нет'} | id:{tid}", kb)
    bot.reply_to(m, "🚨 Репорт отправлен админам. Ожидайте решения.")

@bot.message_handler(func=lambda m: m.text == "🚪 Выйти из игры")
def btn_exit(m):
    uid = m.chat.id
    p = P(uid)
    if not p:
        return
    if not STATE["round_active"] or STATE["paused"]:
        blocked_reply(m); return
    if not registered_in_round(uid):
        bot.reply_to(m, "Вы и так не участвуете в этом раунде."); return
    remove_from_round(uid, reason="вышел сам")
    bot.reply_to(m, "🚪 Вы покинули игру до следующего раунда. Вернуться можно только в следующем раунде.")

def remove_from_round(uid, reason="выбыл"):
    p = P(uid)
    if not p or not p.get("in_round"):
        return
    p["in_round"] = False
    p["alive"] = False
    STATE.setdefault("log", []).append(
        f"{now_msk().strftime('%d.%m %H:%M')} — {fmt(p)}: {reason}")
    if reason == "вышел сам":
        notify_admins(f"🚪 Выбывший ({reason})\n"
                      f"Фамилия Имя: {fmt(p)}\n"
                      f"Telegram: {p['username'] or 'нет'} | id:{uid}\n"
                      f"Цель передана его охотнику.")
    # цель выбывшего переходит к его охотнику
    hunter_id = p["hunter"]
    if hunter_id and P(hunter_id) and registered_in_round(hunter_id):
        hp = P(hunter_id)
        hp["target"] = p["target"]
        if p["target"] and P(p["target"]):
            try:
                bot.send_message(hunter_id, f"🚪 Ваша цель вышла из игры.\n🎯 Ваша новая жертва: {fmt(P(p['target']))}")
            except Exception:
                pass
    # чистим зависшие события
    for ev in list(PENDING):
        evd = PENDING[ev]
        if uid in (evd["killer"], evd["victim"]):
            PENDING.pop(ev)
    save()
    check_end()

def add_warning(uid):
    p = P(uid)
    p["warnings"] = p.get("warnings", 0) + 1
    if p["warnings"] >= MAX_WARNINGS:
        p["banned"] = True
        try:
            bot.send_message(uid, f"🚫 Вы получили {MAX_WARNINGS} предупреждения — АВТОМАТИЧЕСКИЙ БАН.")
        except Exception:
            pass
        notify_admins(f"🚫 Игрок {fmt(p)} (id:{uid}) забанен автоматически: 3 предупреждения.")
        remove_from_round(uid, reason="автобан: 3 предупреждения")
    else:
        try:
            bot.send_message(uid, f"⚠️ Вам выдано предупреждение ({p['warnings']}/{MAX_WARNINGS}). "
                                  "За ложные заявления выдают предупреждения. 3 = автоматический бан.")
        except Exception:
            pass
    save()

def resolve_kill(ev):
    evd = PENDING.pop(ev, None)
    if not evd:
        return
    k, v = evd["killer"], evd["victim"]
    kp, vp = P(k), P(v)
    vp["alive"] = False
    kp["target"] = vp["target"]
    STATE.setdefault("log", []).append(
        f"{now_msk().strftime('%d.%m %H:%M')} — {fmt(vp)} убит охотником {fmt(kp)}")
    notify_admins(f"☠️ Убийство засчитано\n"
                  f"Жертва: {fmt(vp)} | юз: {vp['username'] or 'нет'} | id:{v}\n"
                  f"Охотник: {fmt(kp)} | id:{k}\n"
                  f"Осталось живых: {len([1 for q in STATE['players'].values() if q['in_round'] and q['alive'] and not q['out']])}")
    save()
    try:
        bot.send_message(v, "💀 Убийство засчитано. Вы выбыли из раунда.")
        if kp["target"] and registered_in_round(k):
            bot.send_message(k, f"✅ Убийство засчитано!\n🎯 Ваша следующая жертва: {fmt(P(kp['target']))}")
        elif registered_in_round(k):
            bot.send_message(k, "✅ Убийство засчитано!")
    except Exception:
        pass
    check_end()

def check_end():
    alive = [int(uid) for uid, p in STATE["players"].items() if registered_in_round(int(uid))]
    if len(alive) <= 1:
        STATE["round_active"] = False
        STATE["paused"] = False
        winner = alive[0] if alive else None
        PENDING.clear()
        save()
        text = ("🏆 Раунд завершён!\n"
                f"Победитель: {fmt(P(winner))}\nПоздравляем! Ждите следующего раунда."
                if winner else "🏁 Раунд завершён. Победителя нет.")
        for uid, p in STATE["players"].items():
            if p["in_round"]:
                p["in_round"] = False
                try:
                    bot.send_message(int(uid), text)
                except Exception:
                    pass
        save()

# ================== ОБРАБОТКА КНОПОК (inline) ==================
@bot.callback_query_handler(func=lambda c: True)
def callbacks(c):
    data = c.data
    uid = c.from_user.id

    if data.startswith("cf_") or data.startswith("cf2_"):
        prefix, ev, ans = data.split("_")
        evd = PENDING.get(ev)
        if not evd:
            bot.answer_callback_query(c.id, "Событие устарело.")
            return
        if evd["type"] == "A":
            #stage 1 подтверждает жертва, stage 2 — убийца
            expected = evd["victim"] if prefix == "cf" else evd["killer"]
            if uid != expected:
                bot.answer_callback_query(c.id, "Это запрос не для вас."); return
            if prefix == "cf" and ans == "y":
                bot.edit_message_reply_markup("inline_message_id" if False else c.message.chat.id,
                                              c.message.message_id) if False else None
                resolve_kill(ev)
                bot.answer_callback_query(c.id, "Подтверждено.")
            elif prefix == "cf" and ans == "n":
                evd["stage"] = 2
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("✅ Да, я его убил", callback_data=f"cf2_{ev}_y"))
                kb.add(types.InlineKeyboardButton("❌ Нет, ошибся", callback_data=f"cf2_{ev}_n"))
                try:
                    bot.send_message(evd["killer"],
                        f"❓ {fmt(P(evd['victim']))} утверждает, что вы его не убивали. Вы точно его убили?",
                        reply_markup=kb)
                except Exception:
                    pass
                bot.answer_callback_query(c.id, "Ответ передан.")
            elif prefix == "cf2" and ans == "y":
                # спор — админам на ручной разбор
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("✔️ Засчитать убийство", callback_data=f"sol_{ev}_k"))
                kb.add(types.InlineKeyboardButton("✖️ Отклонить (без наказания)", callback_data=f"sol_{ev}_n"))
                kb.add(types.InlineKeyboardButton("⚠️ Предупредить убийцу", callback_data=f"sol_{ev}_w"))
                notify_admins(f"⚖️ СПОР на ручной разбор:\n"
                              f"Убийца: {fmt(P(evd['killer']))} (id:{evd['killer']})\n"
                              f"Жертва: {fmt(P(evd['victim']))} (id:{evd['victim']})\n"
                              f"Жертва отрицает убийство, убийца настаивает.", kb)
                evd["stage"] = 3
                bot.answer_callback_query(c.id, "Отправлено админам на разбор.")
            elif prefix == "cf2" and ans == "n":
                PENDING.pop(ev)
                add_warning(evd["killer"])
                bot.answer_callback_query(c.id, "Принято.")

        elif evd["type"] == "B":
            # stage 1 подтверждает убийца, stage 2 — жертва
            expected = evd["killer"] if prefix == "cf" else evd["victim"]
            if uid != expected:
                bot.answer_callback_query(c.id, "Это запрос не для вас."); return
            if prefix == "cf" and ans == "y":
                resolve_kill(ev)
                bot.answer_callback_query(c.id, "Подтверждено.")
            elif prefix == "cf" and ans == "n":
                evd["stage"] = 2
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("✅ Да, меня убили", callback_data=f"cf2_{ev}_y"))
                kb.add(types.InlineKeyboardButton("❌ Нет, ошибся", callback_data=f"cf2_{ev}_n"))
                try:
                    bot.send_message(evd["victim"],
                        f"❓ {fmt(P(evd['killer']))} утверждает, что не убивал вас. Вы точно были убиты?",
                        reply_markup=kb)
                except Exception:
                    pass
                bot.answer_callback_query(c.id, "Ответ передан.")
            elif prefix == "cf2" and ans == "y":
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("✔️ Засчитать убийство", callback_data=f"sol_{ev}_k"))
                kb.add(types.InlineKeyboardButton("✖️ Отклонить (без наказания)", callback_data=f"sol_{ev}_n"))
                kb.add(types.InlineKeyboardButton("⚠️ Предупредить заявителя", callback_data=f"sol_{ev}_w"))
                notify_admins(f"⚖️ СПОР на ручной разбор (жертва настаивает):\n"
                              f"Убийца: {fmt(P(evd['killer']))} (id:{evd['killer']})\n"
                              f"Жертва: {fmt(P(evd['victim']))} (id:{evd['victim']})", kb)
                evd["stage"] = 3
                bot.answer_callback_query(c.id, "Отправлено админам на разбор.")
            elif prefix == "cf2" and ans == "n":
                PENDING.pop(ev)
                add_warning(evd["victim"])
                bot.answer_callback_query(c.id, "Принято.")

    elif data.startswith("sol_"):
        if not is_admin(uid):
            bot.answer_callback_query(c.id, "Только для админов."); return
        _, ev, act = data.split("_")
        evd = PENDING.pop(ev, None)
        if not evd:
            bot.answer_callback_query(c.id, "Событие уже разобрано."); return
        if act == "k":
            PENDING[ev] = evd
            resolve_kill(ev)
            bot.answer_callback_query(c.id, "Убийство засчитано.")
        elif act == "n":
            bot.answer_callback_query(c.id, "Отклонено.")
            try:
                bot.send_message(evd["killer"], "⚖️ Админы разобрали спор: изменений нет.")
            except Exception:
                pass
        elif act == "w":
            add_warning(evd["killer"])
            bot.answer_callback_query(c.id, "Предупреждение выдано.")

    elif data.startswith("rep_"):
        if not is_admin(uid):
            bot.answer_callback_query(c.id, "Только для админов."); return
        _, pid, act = data.split("_")
        pid = int(pid)
        victim_p = P(pid)
        reporter = None
        for r_uid, rp in STATE["players"].items():
            if rp["target"] == pid:
                reporter = int(r_uid)
        try:
            bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id)
        except Exception:
            pass
        if act == "r" and victim_p:
            victim_p["banned_round"] = True
            remove_from_round(pid, reason="фейк: бан на текущую игру")
            text = f"🚫 Игрок {fmt(victim_p)} забанен на текущую игру."
        elif act == "f" and victim_p:
            victim_p["banned"] = True
            remove_from_round(pid, reason="фейк: бан навсегда")
            text = f"⛔️ Игрок {fmt(victim_p)} забанен навсегда."
        else:
            text = "✅ Нарушений не найдено. Изменений нет."
        save()
        if reporter:
            try:
                bot.send_message(reporter, text)
            except Exception:
                pass
        bot.answer_callback_query(c.id, "Решение сохранено.")
    else:
        bot.answer_callback_query(c.id)

# ================== ЛОВУШКА ТЕКСТА ==================
@bot.message_handler(content_types=["text"])
def text_catchall(m):
    uid = m.chat.id
    # этапы регистрации
    step = STATES.get(uid)
    if step == "surname":
        STATES[uid] = {"surname": m.text.strip(), "step": "name"}
        bot.send_message(uid, "Введите имя:")
        return
    if isinstance(step, dict):
        if step.get("step") == "name":
            step["name"] = m.text.strip()
            step["step"] = "klass"
            bot.send_message(uid, "Введите класс с буквой (например, 10А):")
            return
        if step.get("step") == "klass":
            kl = m.text.strip()
            import re as _re
            if not _re.fullmatch(r"\d{1,2}[А-Яа-яЁё]", kl):
                bot.send_message(uid, "Неверный формат. Пример: 10А")
                return
            step["klass"] = kl
            step["step"] = "uz"
            bot.send_message(uid, "Отправьте ваш юз (@username) или нажмите «Пропустить»:",
                             reply_markup=skip_kb())
            return
        if step.get("step") == "uz":
            if m.text.strip() == "Пропустить":
                finish_registration(uid)
            elif m.text.strip().startswith("@"):
                finish_registration(uid, m.text.strip())
            else:
                bot.send_message(uid, "Юз должен начинаться с @, либо нажмите «Пропустить».")
            return
    p = P(uid)
    if p and not p.get("banned"):
        bot.send_message(uid, "Текстовые сообщения не принимаются.")
    else:
        bot.send_message(uid, "Нажмите /start для регистрации." if not p else "Текстовые сообщения не принимаются.")

def do_end_round():
    was_active = STATE["round_active"]
    if was_active:
        STATE["round_active"] = False
        STATE["paused"] = False
    alive = [fmt(p) for p in STATE["players"].values() if p["in_round"] and p["alive"] and not p["out"]]
    alive_text = "\n".join("• " + s for s in alive) or "нет"
    log_text = "\n".join(STATE.get("log", [])) or "выбывших нет"
    notify_admins(
        f"🏁 Раунд завершён (пятница 18:00).\n\n"
        f"Живые участники:\n{alive_text}\n\n"
        f"События недели:\n{log_text}\n\n"
        f"Объявите победителя в канале игры.\n"
        f"Следующая регистрация — воскресенье 8:00-18:00, старт раунда — понедельник 8:00.")
    for uid, p in list(STATE["players"].items()):
        if p["in_round"]:
            p["in_round"] = False
            try:
                bot.send_message(int(uid),
                    "🏁 Раунд завершён! Регистрация нового раунда — в воскресенье с 8:00 до 18:00.")
            except Exception:
                pass
    # новый раунд — участники регистрируются заново (бан навсегда сохраняется)
    for uid in list(STATE["players"].keys()):
        if not STATE["players"][uid]["banned"]:
            del STATE["players"][uid]
    STATE["log"] = []
    PENDING.clear()
    save()

def scheduler():
    """Фоновый таймер: автостарт в понедельник 8:00 (мск), автофиниш в пятницу 18:00."""
    while True:
        try:
            now = now_msk()
            today = now.date().isoformat()
            tmin = now.hour * 60 + now.minute
            if now.weekday() == 0 and 480 <= tmin <= 495 \
                    and STATE.get("started_date") != today and STATE["players"]:
                STATE["started_date"] = today
                save()
                ok, n = do_start_round()
                if not ok:
                    notify_admins(f"❗ Автостарт раунда в понедельник 8:00 не удался: "
                                  f"зарегистрировано {n} игроков (нужно минимум 2).")
            if now.weekday() == 4 and 1080 <= tmin <= 1095 \
                    and STATE.get("ended_date") != today:
                STATE["ended_date"] = today
                do_end_round()
        except Exception as e:
            print("Ошибка планировщика:", e)
        time.sleep(60)

if __name__ == "__main__":
    print("Бот запущен")
    load_state_from_github()
    threading.Thread(target=_autosave_loop, daemon=True).start()
    threading.Thread(target=scheduler, daemon=True).start()
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import os

class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *args):
        pass

def run_web():
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", 10000))), Health).serve_forever()

threading.Thread(target=run_web, daemon=True).start()
    bot.infinity_polling(skip_pending=True)
