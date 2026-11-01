import os, json, hmac, hashlib, time, logging, html, csv, io
import httpx
from telegram import Bot
from access import allowed, role
from pathlib import Path
from urllib.parse import parse_qsl
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from db import init, user, feedback, guest, admin_guest_list, admin_review_list, admin_stats
load_dotenv()
init()
app=FastAPI(docs_url=None,redoc_url=None)
TOKEN=os.getenv("BOT_TOKEN","")
LOCAL=os.getenv("LOCAL_PREVIEW")=="1"
REVIEW_CHAT_ID=os.getenv("REVIEW_CHAT_ID","").strip()

logger=logging.getLogger(__name__)
APP_VERSION="2026-11-01-mobile-admin-review-check"
class Base(BaseModel):
    initData:str=""
class AdminQuery(Base):
    query:str=Field(default="",max_length=100)
    date:str=Field(default="",max_length=10)
    regularOnly:bool=False
    offset:int=Field(default=0,ge=0,le=100000)
class Review(Base):
    stars:int=Field(ge=1,le=5)
    comment:str=Field(default="",max_length=2000)
def auth(init_data, require_registration=True):
    if LOCAL and not init_data: return {"id":0,"first_name":"Тестовый гость"}
    try:
        data=dict(parse_qsl(init_data,keep_blank_values=True))
        received=data.pop("hash")
        if abs(time.time()-int(data["auth_date"]))>3600: raise ValueError()
        check="\n".join(f"{k}={v}" for k,v in sorted(data.items()))
        secret=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest()
        expected=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(received,expected): raise ValueError()
        person=json.loads(data["user"])
        # Staff roles are configured in environment variables. Their access must not
        # depend on a guest row in ephemeral SQLite storage on Render Free.
        if require_registration and not allowed(person["id"]) and not user(person["id"]): raise ValueError()
        return person
    except Exception:
        raise HTTPException(401,"Откройте Mini App через Telegram после отправки номера")
def require_admin(init_data,need="manager"):
    person=auth(init_data)
    if not allowed(person["id"],need):
        raise HTTPException(403,"Доступ только для администратора")

@app.post("/api/admin/stats")
def admin_stats_api(body:Base):
    require_admin(body.initData)
    return admin_stats()

@app.post("/api/admin/guests")
def admin_guests_api(body:AdminQuery):
    require_admin(body.initData)
    return {"items":admin_guest_list(body.query,body.date or None,body.regularOnly,50,body.offset)}

@app.post("/api/admin/reviews")
def admin_reviews_api(body:AdminQuery):
    require_admin(body.initData)
    return {"items":admin_review_list(50,body.offset)}

@app.post("/api/admin/export")
def admin_export_api(body:AdminQuery):
    require_admin(body.initData,"admin")
    stream=io.StringIO()
    writer=csv.writer(stream,delimiter=";")
    writer.writerow(["telegram_id","name","phone","registered_utc","bookings","status"])
    def safe(value):
        result=str(value if value is not None else "")
        return "'"+result if result.lstrip().startswith(("=","+","-","@")) else result
    offset=0
    while True:
        rows=admin_guest_list(body.query,body.date or None,body.regularOnly,200,offset)
        for entry in rows:
            writer.writerow([safe(entry[k]) for k in ("id","name","phone","created_at","bookings","status")])
        if len(rows)<200: break
        offset+=200
    filename="regular.csv" if body.regularOnly else "guests.csv"
    return Response(content=(chr(65279)+stream.getvalue()).encode("utf-8"),
       media_type="text/csv",headers={"Content-Disposition":f'attachment; filename="{filename}"',"Cache-Control":"no-store"})


# Optional single-process mode for one small demo web service.
# On local Windows run bot.py and uvicorn separately as before.
polling_application=None

@app.on_event("startup")
async def start_embedded_bot():
    global polling_application
    if os.getenv("RUN_BOT_IN_API")!="1":
        return
    from bot import build_application
    application=build_application()
    await application.initialize()
    try:
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
    except Exception:
        await application.stop()
        await application.shutdown()
        raise
    polling_application=application

@app.on_event("shutdown")
async def stop_embedded_bot():
    global polling_application
    if polling_application is None:
        return
    await polling_application.updater.stop()
    await polling_application.stop()
    await polling_application.shutdown()
    polling_application=None

@app.get("/legal/privacy")
def legal_privacy():
    return FileResponse(Path(__file__).parent/"web"/"privacy.html")

@app.get("/legal/consent")
def legal_consent():
    return FileResponse(Path(__file__).parent/"web"/"consent.html")

@app.get("/api/version")
def version():
    return {"version":APP_VERSION,"reviewChatConfigured":bool(REVIEW_CHAT_ID),"botConfigured":bool(TOKEN),"webAppConfigured":bool(os.getenv("WEBAPP_URL","").startswith("https://"))}

@app.get("/health")
@app.head("/health")
def health():
    return {"ok":True}

@app.get("/app-contact-20261101")
def contact_gate_ui():
    return FileResponse(Path(__file__).parent/"web"/"index.html",headers={"Cache-Control":"no-store, max-age=0"})

@app.get("/app-admin-20261101-v2")
def admin_ui_updated():
    return FileResponse(Path(__file__).parent/"web"/"index.html",headers={"Cache-Control":"no-store, max-age=0"})

@app.get("/app-mobile-20261101")
def mobile_app_latest():
    return FileResponse(Path(__file__).parent/"web"/"index.html",headers={"Cache-Control":"no-store, max-age=0"})

@app.get("/")
def index():
    return FileResponse(Path(__file__).parent/"web"/"index.html",headers={"Cache-Control":"no-store, max-age=0"})
@app.post("/api/me")
def me(body:Base):
    p=auth(body.initData, require_registration=False)
    registered=bool(user(p["id"])) if p["id"] else bool(LOCAL)
    profile=guest(p["id"]) if registered and p["id"] else None
    return {"name":p.get("first_name","Гость"),"contactShared":registered,"preview":p["id"]==0,"bookingEnabled":False,"confirmedBookings":profile["bookings"] if profile else 0,"guestStatus":profile["status"] if profile else "Гость","isAdmin":allowed(p["id"]),"adminRole":role(p["id"])}
@app.post("/api/review")
async def review(body:Review):
    p=auth(body.initData)
    if p["id"]==0:
        return {"ok":True,"preview":True,"notificationDelivered":False}
    feedback(p["id"],body.stars,body.comment)
    if not REVIEW_CHAT_ID or not TOKEN:
        logger.error("Review stored without notification: missing chat ID or bot token")
        return {"ok":True,"notificationDelivered":False,"notificationError":"Уведомления не настроены на сервере"}

    profile=user(p["id"]) or {}
    guest_name=profile.get("name") or p.get("first_name") or "Гость"
    comment=body.comment.strip() or "Без комментария"
    message=(f"⭐ Новый отзыв Steindamm 99\n"
             f"Гость: {guest_name}\n"
             f"Оценка: {body.stars}/5\n"
             f"Telegram ID: {p['id']}\n\n"
             f"{comment}")
    try:
        # Use the same Telegram Bot API client as the working /testreview command.
        async with Bot(token=TOKEN) as client:
            await client.send_message(chat_id=REVIEW_CHAT_ID,text=message)
        return {"ok":True,"notificationDelivered":True}
    except Exception as exc:
        logger.exception("Review stored but sending staff notification failed")
        return {"ok":True,"notificationDelivered":False,
                "notificationError":"Telegram не принял уведомление. Проверьте журнал Render."}

@app.post("/api/booking")
def booking(body:Base):
    auth(body.initData)
    raise HTTPException(503,"Бронирование будет доступно после подключения Restoplace")
