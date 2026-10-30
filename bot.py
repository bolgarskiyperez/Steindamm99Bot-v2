import os
import csv
import io
import sqlite3
from telegram import InputFile
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from db import init, user, save_user, guests, guest, reviews, add_verified_booking, cancel_booking
load_dotenv()
TOKEN=os.getenv("BOT_TOKEN","")
URL=os.getenv("WEBAPP_URL","").strip()
PRIVACY=os.getenv("PRIVACY_URL","").strip()
ADMIN_ID=int(os.getenv("ADMIN_CHAT_ID") or "0")
def contact_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("📱 Поделиться номером",request_contact=True)]],resize_keyboard=True)
async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if user(update.effective_user.id):
        return await menu(update)
    text="Добро пожаловать в Steindamm 99!\n\nДля продолжения поделитесь номером телефона кнопкой ниже."
    if PRIVACY:
        text+="\n\nПолитика обработки персональных данных: "+PRIVACY
    else:
        text+="\n\n⚠️ Политика обработки данных пока не настроена. Не запускайте бота для гостей."
    await update.message.reply_text(text,reply_markup=contact_keyboard())
async def menu(update):
    if not user(update.effective_user.id):
        return await start(update,None)
    if not URL.startswith("https://"):
        return await update.message.reply_text("Номер сохранён. Для запуска Mini App укажи публичный HTTPS-адрес WEBAPP_URL в .env.")
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("🍽 Открыть приложение",web_app=WebAppInfo(url=URL))]])
    await update.message.reply_text("Откройте приложение ресторана:",reply_markup=kb)
async def contact(update:Update, context:ContextTypes.DEFAULT_TYPE):
    data=update.message.contact
    if data.user_id != update.effective_user.id:
        return await update.message.reply_text("Пожалуйста, поделитесь собственным номером кнопкой ниже.",reply_markup=contact_keyboard())
    save_user(update.effective_user.id,update.effective_user.full_name,data.phone_number)
    await update.message.reply_text("Спасибо! Номер сохранён.",reply_markup=ReplyKeyboardRemove())
    await menu(update)
async def message(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not user(update.effective_user.id):
        return await update.message.reply_text("Отправьте номер кнопкой «Поделиться номером», а не текстом.",reply_markup=contact_keyboard())
    await menu(update)

def authorized(update):
    return bool(ADMIN_ID and update.effective_user.id == ADMIN_ID and update.effective_chat.id == ADMIN_ID)

async def admin(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not authorized(update): return
    await update.message.reply_text(
        "Управление гостями:\n"
        "/guests — количество гостей и постоянных гостей\n"
        "/regular — список постоянных гостей\n"
        "/guest ID — карточка гостя по Telegram ID\n"
        "/reviews — последние отзывы\n"
        "/exportguests — выгрузка CSV всех гостей\n"
        "/exportregular — выгрузка CSV постоянных гостей\n"
        "/addbooking ID НОМЕР — учесть подтверждённую бронь из Restoplace\n"
        "/cancelbooking НОМЕР — отменить учтённую бронь\n\n"
        "Данные доступны только администратору. Не пересылайте выгрузку посторонним."
    )

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
    if not authorized(update): return
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
    if not authorized(update): return
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
    if not authorized(update): return
    if len(context.args)!=1:
        return await update.message.reply_text("Формат: /cancelbooking ID_БРОНИ_RESTOPLACE")
    ok=cancel_booking(context.args[0])
    await update.message.reply_text("Бронь исключена из подсчёта." if ok else "Активная бронь с таким ID не найдена.")

def main():
    if not TOKEN or TOKEN=="PUT_NEW_BOT_TOKEN_HERE": raise RuntimeError("Укажи BOT_TOKEN в .env")
    init()
    app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("admin",admin))
    app.add_handler(CommandHandler("guests",guests_cmd))
    app.add_handler(CommandHandler("regular",regular_cmd))
    app.add_handler(CommandHandler("guest",guest_cmd))
    app.add_handler(CommandHandler("reviews",reviews_cmd))
    app.add_handler(CommandHandler("exportguests",export_all_cmd))
    app.add_handler(CommandHandler("exportregular",export_regular_cmd))
    app.add_handler(CommandHandler("addbooking",addbooking_cmd))
    app.add_handler(CommandHandler("cancelbooking",cancelbooking_cmd))
    app.add_handler(MessageHandler(filters.CONTACT,contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,message))
    app.run_polling(drop_pending_updates=True)
if __name__=="__main__": main()
