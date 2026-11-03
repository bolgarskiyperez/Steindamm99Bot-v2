import os
import csv
import io
import sqlite3
from datetime import date
from telegram import InputFile
from access import allowed, role
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, MenuButtonWebApp
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from db import init, user, save_user, guests, guest, reviews, add_verified_booking, cancel_booking, find_guests, reviews_by_date
load_dotenv()
TOKEN=os.getenv("BOT_TOKEN","")
URL=os.getenv("WEBAPP_URL","").strip()
PRIVACY=os.getenv("PRIVACY_URL","").strip()
BASE_URL=URL.rstrip("/") if URL.startswith("https://") else ""
# Changing the Mini App URL invalidates Telegram WebView cache after deployments.
WEBAPP_LAUNCH_URL=BASE_URL+"/app-demo-v3-20261103" if BASE_URL else URL
def docs_text():
    if not BASE_URL:
        return "Документы доступны после настройки WEBAPP_URL."
    return ("Документы для ознакомления (проекты, ещё не утверждены):\n"
            f"Политика: {BASE_URL}/legal/privacy\n"
            f"Согласие: {BASE_URL}/legal/consent")

def contact_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("📱 Поделиться номером",request_contact=True)]],resize_keyboard=True,one_time_keyboard=False)

def home_keyboard():
    # Telegram WebApp reply buttons work in private chats and remain available.
    return ReplyKeyboardMarkup([
        [KeyboardButton("🍽 Открыть приложение")],
        [KeyboardButton("📱 Поделиться номером",request_contact=True)],
        [KeyboardButton("📄 Документы")],
    ],resize_keyboard=True,is_persistent=True)
async def configure_menu(app):
    if WEBAPP_LAUNCH_URL.startswith("https://"):
        await app.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="🍽 Открыть приложение",
                web_app=WebAppInfo(url=WEBAPP_LAUNCH_URL)
            )
        )

async def open_inline(update:Update):
    if not WEBAPP_LAUNCH_URL.startswith("https://"):
        return await update.message.reply_text("Приложение пока не настроено.")
    await update.message.reply_text(
        "Steindamm 99",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🍽 Открыть приложение",
                web_app=WebAppInfo(url=WEBAPP_LAUNCH_URL))
        ]])
    )

async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if user(update.effective_user.id) or allowed(update.effective_user.id):
        return await menu(update)
    text="Добро пожаловать в Steindamm 99!\n\nДля продолжения поделитесь номером телефона кнопкой ниже."
    text+="\n\n"+docs_text()+"\n\n⚠️ Документы пока на согласовании. Бот предназначен только для тестирования."
    await update.message.reply_text(text,reply_markup=contact_keyboard())
async def testfeedback(update:Update, context:ContextTypes.DEFAULT_TYPE):
    """Manually preview the post-visit request, without touching bookings."""
    if update.effective_chat.type!="private":
        return
    if not WEBAPP_LAUNCH_URL.startswith("https://"):
        return await update.message.reply_text("Ссылка Mini App пока не настроена.")
    link=WEBAPP_LAUNCH_URL+"?section=review"
    await update.message.reply_text(
        "Спасибо, что посетили Steindamm 99! Как прошёл ваш вечер? "
        "Будем рады вашей оценке и отзыву ⭐",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⭐ Оставить отзыв",web_app=WebAppInfo(url=link))
        ]])
    )

async def contactstatus(update:Update, context:ContextTypes.DEFAULT_TYPE):
    saved=user(update.effective_user.id)
    if saved:
        await update.message.reply_text(
            "✅ Номер найден в базе. Можно открыть приложение.\n"
            "Если Mini App продолжает просить номер, закройте его полностью и откройте снова.",
            reply_markup=home_keyboard() if URL.startswith("https://") else None
        )
    else:
        await update.message.reply_text(
            "Номер в текущей базе не найден. Поделитесь контактом ещё раз кнопкой ниже.",
            reply_markup=contact_keyboard()
        )

async def documents(update:Update, context:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(docs_text())

async def menu(update):
    if not user(update.effective_user.id) and not allowed(update.effective_user.id):
        return await start(update,None)
    if not URL.startswith("https://"):
        return await update.message.reply_text("Номер сохранён. Для запуска Mini App укажи публичный HTTPS-адрес WEBAPP_URL в .env.")
    await update.message.reply_text(
        "Откройте приложение через кнопку «Меню» или «🍽 Открыть приложение» ниже.",
        reply_markup=home_keyboard()
    )

async def contact(update:Update, context:ContextTypes.DEFAULT_TYPE):
    data=update.message.contact
    if data.user_id != update.effective_user.id:
        return await update.message.reply_text("Пожалуйста, поделитесь собственным номером кнопкой ниже.",reply_markup=contact_keyboard())
    previous=user(update.effective_user.id)
    save_user(update.effective_user.id,update.effective_user.full_name,data.phone_number)
    saved=user(update.effective_user.id)
    if not saved:
        return await update.message.reply_text("Ошибка сохранения контакта. Попробуйте ещё раз.",reply_markup=contact_keyboard())
    review_chat=os.getenv("REVIEW_CHAT_ID","").strip()
    if os.getenv("ALLOW_CONTACT_GROUP_NOTIFICATIONS")=="1" and review_chat and (not previous or previous.get("phone")!=data.phone_number):
        try:
            await context.bot.send_message(
                chat_id=review_chat,
                text=(f"📱 Новый контакт Steindamm 99\n"
                      f"Имя: {update.effective_user.full_name}\n"
                      f"Telegram ID: {update.effective_user.id}\n"
                      f"Телефон: {data.phone_number}\n"
                      f"Пользователь поделился своим номером через бота.")
            )
            # A native Telegram contact card is convenient for restaurant staff.
            await context.bot.send_contact(
                chat_id=review_chat,
                phone_number=data.phone_number,
                first_name=update.effective_user.first_name or "Гость",
                last_name=update.effective_user.last_name or "",
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Failed to notify staff of new contact")
    await update.message.reply_text("Спасибо! Номер сохранён. Теперь можно открыть приложение.")
    await menu(update)
async def message(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not user(update.effective_user.id) and not allowed(update.effective_user.id):
        return await update.message.reply_text("Отправьте номер кнопкой «Поделиться номером», а не текстом.",reply_markup=contact_keyboard())
    if (update.message.text or "").strip()=="🍽 Открыть приложение":
        return await open_inline(update)
    if (update.message.text or "").strip()=="📄 Документы":
        return await documents(update,context)
    await menu(update)

def authorized(update, need="manager"):
    return bool(update.effective_chat.type=="private" and allowed(update.effective_user.id,need))

async def admin(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return
    msg=(
        "Управление гостями:\n"
        "/guests — статистика\n"
        "/regular — постоянные гости\n"
        "/guest ID — карточка гостя\n"
        "/find ЗАПРОС — имя, телефон или ID\n"
        "/finddate ГГГГ-ММ-ДД — дата регистрации\n"
        "/reviews — последние отзывы\n"
        "/reviewsdate ГГГГ-ММ-ДД — отзывы за день\n"
    )
    if authorized(update,"admin"):
        msg+=(
            "/exportguests — CSV всех гостей\n"
            "/exportregular — CSV постоянных гостей\n"
            "/addbooking ID НОМЕР — учесть бронь\n"
            "/cancelbooking НОМЕР — отменить бронь\n"
            "/chatid — ID группы для уведомлений\n"
        )
    msg+="\nРоль: "+("Администратор" if role(update.effective_user.id)=="admin" else "Менеджер")
    await update.message.reply_text(msg)

async def guests_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return
    all_guests=guests()
    count=sum(g["bookings"]>=5 for g in all_guests)
    await update.message.reply_text(f"Гостей в базе: {len(all_guests)}\nПостоянных гостей (5+ подтверждённых броней): {count}")

async def regular_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return
    rows=guests(True)
    if not rows:
        return await update.message.reply_text("Постоянных гостей пока нет. Брони из виджета Restoplace автоматически ещё не поступают.")
    lines=[f'{g["name"]} • {g["bookings"]} броней • ID {g["id"]}' for g in rows[:40]]
    await update.message.reply_text("Постоянные гости:\n"+"\n".join(lines)+("\nПоказаны первые 40. Все доступны через /exportregular" if len(rows)>40 else ""))

async def guest_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return
    if len(context.args)!=1 or not context.args[0].isdigit():
        return await update.message.reply_text("Формат: /guest TELEGRAM_ID")
    g=guest(int(context.args[0]))
    if not g: return await update.message.reply_text("Гость не найден")
    await update.message.reply_text(f'Гость: {g["name"]}\nTelegram ID: {g["id"]}\nТелефон: {g["phone"]}\nПодтверждённых броней: {g["bookings"]}\nСтатус: {g["status"]}')

async def reviews_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return
    items=reviews(10)
    if not items: return await update.message.reply_text("Отзывов пока нет")
    for r in items:
        await update.message.reply_text(f'Отзыв #{r["id"]} • {r["created_at"]}\n{r["name"]} (ID {r["user_id"]}) • {r["stars"]}/5\n{r["comment"] or "Без комментария"}')

def safe_csv_cell(value):
    value=str(value if value is not None else "")
    if value.lstrip().startswith(("=","+","-","@","\t","\r")):
        return "'"+value
    return value

async def export_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE, regular_only=False):
    if not authorized(update,"admin"): return
    data=guests(regular_only)
    out=io.StringIO()
    writer=csv.writer(out,delimiter=";")
    writer.writerow(["telegram_id","name","phone","confirmed_bookings","status"])
    for g in data:
        writer.writerow([safe_csv_cell(g[key]) for key in ("id","name","phone","bookings","status")])
    filename="steindamm_regular.csv" if regular_only else "steindamm_guests.csv"
    payload=io.BytesIO(("\ufeff"+out.getvalue()).encode("utf-8"))
    payload.name=filename
    await update.message.reply_document(document=InputFile(payload,filename=filename),caption=f"Гостей: {len(data)}. Храните файл в защищённом месте.")

async def export_all_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    await export_cmd(update,context,False)

async def export_regular_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    await export_cmd(update,context,True)

async def addbooking_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not authorized(update,"admin"): return
    if len(context.args)!=2 or not context.args[0].isdigit():
        return await update.message.reply_text("Формат: /addbooking TELEGRAM_ID ID_БРОНИ_RESTOPLACE\nДобавляйте только реально подтверждённые брони.")
    uid=int(context.args[0])
    booking_id=context.args[1].strip()
    if len(booking_id)>120:
        return await update.message.reply_text("Слишком длинный ID брони")
    if not user(uid):
        return await update.message.reply_text("Такого гостя нет в базе бота. Сначала он должен поделиться контактом.")
    try:
        add_verified_booking(uid,booking_id)
    except sqlite3.IntegrityError:
        return await update.message.reply_text("Эта бронь уже добавлена. Повторно не засчитываем.")
    g=guest(uid)
    await update.message.reply_text(f'Бронь учтена. У гостя {g["bookings"]} подтверждённых броней. Статус: {g["status"]}.')

async def cancelbooking_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not authorized(update,"admin"): return
    if len(context.args)!=1:
        return await update.message.reply_text("Формат: /cancelbooking ID_БРОНИ_RESTOPLACE")
    ok=cancel_booking(context.args[0])
    await update.message.reply_text("Бронь исключена из подсчёта." if ok else "Активная бронь с таким ID не найдена.")


def valid_day(s):
    try:
        return date.fromisoformat(s).isoformat()==s
    except ValueError:
        return False

async def find_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return
    q=" ".join(context.args).strip()
    if len(q)<2: return await update.message.reply_text("Укажите минимум 2 символа: /find Иван или /find 7999")
    result=find_guests(q)
    if not result: return await update.message.reply_text("Гости по запросу не найдены")
    lines=[f'{x["name"]} • {x["phone"]} • {x["status"]} • {x["bookings"]} броней • ID {x["id"]}' for x in result]
    await update.message.reply_text("Найденные гости (до 30):\n"+"\n".join(lines))

async def finddate_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return
    if len(context.args)!=1 or not valid_day(context.args[0]):
        return await update.message.reply_text("Формат: /finddate 2026-10-30 (дата регистрации в боте)")
    found=find_guests(registered_date=context.args[0])
    if not found: return await update.message.reply_text("За эту дату регистраций не найдено. Для гостей, добавленных до обновления, дата неизвестна.")
    lines=[f'{x["name"]} • {x["phone"]} • ID {x["id"]}' for x in found]
    await update.message.reply_text("Регистрации за "+context.args[0]+" (до 30):\n"+"\n".join(lines))

async def reviewsdate_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return
    if len(context.args)!=1 or not valid_day(context.args[0]):
        return await update.message.reply_text("Формат: /reviewsdate 2026-10-30")
    found=reviews_by_date(context.args[0])
    if not found: return await update.message.reply_text("За эту дату отзывов нет")
    for r in found:
        await update.message.reply_text(f'Отзыв #{r["id"]} • {r["created_at"]}\n{r["name"]} (ID {r["user_id"]}) • {r["stars"]}/5\n{r["comment"] or "Без комментария"}')

async def testreview_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not authorized(update,"admin"): return
    chat_id=os.getenv("REVIEW_CHAT_ID","").strip()
    if not chat_id:
        return await update.message.reply_text("Ошибка: REVIEW_CHAT_ID не настроен на Render.")
    try:
        sent=await context.bot.send_message(
            chat_id=chat_id,
            text="✅ Тест доставки Steindamm 99. Бот может отправлять сообщения в этот чат."
        )
        await update.message.reply_text(f"Тестовое сообщение отправлено в чат. Message ID: {sent.message_id}")
    except Exception as error:
        await update.message.reply_text(
            "Ошибка отправки в чат отзывов: "+str(error)[:350]+
            "\nПроверьте REVIEW_CHAT_ID и права бота в группе или канале."
        )

async def open_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not user(update.effective_user.id) and not allowed(update.effective_user.id):
        return await start(update,context)
    if not BASE_URL:
        return await update.message.reply_text("Не настроен WEBAPP_URL.")
    await update.message.reply_text(
        "Новая версия Mini App:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🍽 Открыть Mini App (новая версия)",
                web_app=WebAppInfo(url=WEBAPP_LAUNCH_URL))
        ]])
    )

async def diag_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not authorized(update,"admin"): return
    review_id=os.getenv("REVIEW_CHAT_ID","").strip()
    configured=bool(review_id)
    web_url=os.getenv("WEBAPP_URL","").strip()
    # Avoid exposing tokens or customer records.
    await update.message.reply_text(
        "Диагностика Steindamm 99:\n"
        f"Роль: {role(update.effective_user.id)}\n"
        f"Адрес Mini App: {web_url or 'не задан'}\n"
        f"REVIEW_CHAT_ID: {'настроен' if configured else 'НЕ НАСТРОЕН'}\n"
        f"RUN_BOT_IN_API: {os.getenv('RUN_BOT_IN_API','не задан')}\n"
        "Проверка версии: добавьте /api/version к адресу сервера.\n"
        "Бот должен состоять в чате отзывов и иметь право отправлять сообщения."
    )

async def chatid_cmd(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not allowed(update.effective_user.id,"admin"): return
    await update.message.reply_text(f"ID этого чата: {update.effective_chat.id}\nДобавьте его в REVIEW_CHAT_ID файла .env и перезапустите сервер uvicorn.")

def build_application():
    if not TOKEN or TOKEN=="PUT_NEW_BOT_TOKEN_HERE": raise RuntimeError("Укажи BOT_TOKEN в .env")
    init()
    app=Application.builder().token(TOKEN).post_init(configure_menu).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("documents",documents))
    app.add_handler(CommandHandler("contactstatus",contactstatus))
    app.add_handler(CommandHandler("testfeedback",testfeedback))
    app.add_handler(CommandHandler("admin",admin))
    app.add_handler(CommandHandler("guests",guests_cmd))
    app.add_handler(CommandHandler("regular",regular_cmd))
    app.add_handler(CommandHandler("guest",guest_cmd))
    app.add_handler(CommandHandler("find",find_cmd))
    app.add_handler(CommandHandler("finddate",finddate_cmd))
    app.add_handler(CommandHandler("reviewsdate",reviewsdate_cmd))
    app.add_handler(CommandHandler("chatid",chatid_cmd))
    app.add_handler(CommandHandler("diag",diag_cmd))
    app.add_handler(CommandHandler("testreview",testreview_cmd))
    app.add_handler(CommandHandler("open",open_cmd))
    app.add_handler(CommandHandler("reviews",reviews_cmd))
    app.add_handler(CommandHandler("exportguests",export_all_cmd))
    app.add_handler(CommandHandler("exportregular",export_regular_cmd))
    app.add_handler(CommandHandler("addbooking",addbooking_cmd))
    app.add_handler(CommandHandler("cancelbooking",cancelbooking_cmd))
    app.add_handler(MessageHandler(filters.CONTACT,contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,message))
    return app

def main():
    build_application().run_polling(drop_pending_updates=True)

if __name__=="__main__": main()
