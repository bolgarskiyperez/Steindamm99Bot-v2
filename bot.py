import os
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from db import init, user, save_user
load_dotenv()
TOKEN=os.getenv("BOT_TOKEN","")
URL=os.getenv("WEBAPP_URL","").strip()
PRIVACY=os.getenv("PRIVACY_URL","").strip()
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
def main():
    if not TOKEN or TOKEN=="PUT_NEW_BOT_TOKEN_HERE": raise RuntimeError("Укажи BOT_TOKEN в .env")
    init()
    app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(MessageHandler(filters.CONTACT,contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,message))
    app.run_polling(drop_pending_updates=True)
if __name__=="__main__": main()
