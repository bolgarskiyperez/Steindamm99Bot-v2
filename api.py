import os, json, hmac, hashlib, time, logging, html, csv, io
import httpx
from telegram import Bot
from access import allowed, role
from pathlib import Path
from urllib.parse import parse_qsl
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from db import init, user, feedback, guest, admin_guest_list, admin_review_list, admin_stats
import restoplace as rp
import test_bookings as tb
load_dotenv()
init()
app=FastAPI(docs_url=None,redoc_url=None)
TOKEN=os.getenv("BOT_TOKEN","")
LOCAL=os.getenv("LOCAL_PREVIEW")=="1"
REVIEW_CHAT_ID=os.getenv("REVIEW_CHAT_ID","").strip()

logger=logging.getLogger(__name__)
APP_VERSION="2026-11-03-restoplace-test-v3"
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
    from bot import build_application, configure_menu
    application=build_application()
    await application.initialize()
    try:
        await configure_menu(application)
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

@app.get("/app-signed-20261101")
def signed_telegram_app():
    return FileResponse(Path(__file__).parent/"web"/"index.html",headers={"Cache-Control":"no-store, max-age=0"})

@app.get("/app-restored-20261101")
def restored_guest_app():
    return FileResponse(Path(__file__).parent/"web"/"index.html",headers={"Cache-Control":"no-store, max-age=0"})

@app.get("/app-contact-status-v3")
def contact_status_v3():
    return FileResponse(Path(__file__).parent/"web"/"index.html",headers={"Cache-Control":"no-store, max-age=0"})

@app.get("/app-contact-review-v2")
def contact_review_v2():
    return FileResponse(Path(__file__).parent/"web"/"index.html",headers={"Cache-Control":"no-store, max-age=0"})

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
    p=auth(body.initData, require_registration=False)
    if p["id"]==0:
        return {"ok":True,"preview":True,"notificationDelivered":False}
    if not user(p["id"]):
        raise HTTPException(status_code=409,detail="Номер не найден в базе. Поделитесь номером повторно.")
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


# Restoplace demo integration. Never enable writes with a production venue key.
class SlotsQuery(Base):
    date:str=Field(min_length=10,max_length=10)
    count:int=Field(ge=1,le=4)
class BookingCreate(Base):
    requestKey:str=Field(min_length=32,max_length=64,pattern=r"^[a-fA-F0-9-]+$")
    date:str=Field(min_length=10,max_length=10)
    time:str=Field(min_length=5,max_length=5)
    count:int=Field(ge=1,le=20)
class BookingLookup(Base):
    reserveId:str=Field(min_length=1,max_length=30,pattern=r"^[0-9]+$")

@app.post("/api/restoplace/config")
def restoplace_config(body:Base):
    p=auth(body.initData,require_registration=False)
    return {"mode":rp.mode(),"testConnected":rp.configured(),
            "writesEnabled":rp.write_enabled(),
            "isAdmin":allowed(p["id"]),"demoOnly":True}

@app.post("/api/restoplace/slots")
async def restoplace_slots(body:SlotsQuery):
    auth(body.initData,require_registration=False)
    return {"items":await rp.slots(body.date,body.count),"source":"restoplace-test"}

@app.post("/api/restoplace/create")
async def restoplace_create(body:BookingCreate):
    p=auth(body.initData,require_registration=False)
    if not p["id"]:raise HTTPException(403,"Для бронирования откройте приложение через Telegram")
    if not rp.write_enabled():raise HTTPException(403,"Запись в тестовый Restoplace пока отключена")
    contact=user(p["id"])
    if not contact or not contact.get("phone"):
        raise HTTPException(409,"Отправьте контакт через кнопку Telegram-бота перед созданием брони")
    if body.count<=4:
        listed=await rp.slots(body.date,body.count)
        if not any(s["time"]==body.time and s["available"] for s in listed):
            raise HTTPException(409,"Выбранное время уже недоступно. Выберите другой слот.")
    else:
        rp.timeslot_start(body.date,body.time)
    waitlist=body.count>=5
    new,existing=tb.start(body.requestKey,p["id"],contact["name"],contact["phone"],
                          body.date,body.time,body.count,waitlist)
    if not new:
        return {"ok":existing["state"] in ("confirmed","waitlist","pending_payment"),
                "state":existing["state"],"externalId":existing["external_id"],
                "number":existing["external_number"],"alreadyProcessed":True}
    try:
        result=await rp.create(contact["name"],contact["phone"],body.date,body.time,body.count,waitlist)
        external=result.get("id") if isinstance(result,dict) else None
        if not external:
            tb.finish(body.requestKey,"unknown")
            raise HTTPException(502,"Restoplace не вернул ID. Требуется ручная сверка, не повторяйте запрос.")
        state=("pending_payment" if result.get("paymentNeed") else "waitlist" if waitlist else "confirmed")
        tb.finish(body.requestKey,state,external,result.get("number"))
        return {"ok":True,"state":state,"externalId":str(external),
                "number":str(result.get("number","")),
                "paymentNeeded":bool(result.get("paymentNeed")),
                "paymentLink":result.get("paymentLink") if result.get("paymentNeed") else None}
    except HTTPException as exc:
        # Never retry automatically on ambiguous API failures.
        tb.finish(body.requestKey,"unknown" if exc.status_code>=500 else "rejected")
        raise
    except Exception:
        tb.finish(body.requestKey,"unknown")
        logger.exception("Unexpected test booking creation error")
        raise HTTPException(502,"Не удалось подтвердить бронь, требуется ручная сверка")

@app.post("/api/restoplace/mine")
def restoplace_mine(body:Base):
    p=auth(body.initData,require_registration=False)
    return {"items":tb.mine(p["id"]) if p["id"] else []}

@app.post("/api/restoplace/admin/bookings")
def restoplace_admin_bookings(body:Base):
    require_admin(body.initData,"manager")
    return {"items":tb.all_bookings(),"summary":tb.summary()}

@app.post("/api/restoplace/admin/reconcile")
async def restoplace_reconcile(body:BookingLookup):
    require_admin(body.initData,"manager")
    return {"reserve":await rp.get_reserve(body.reserveId)}

@app.post("/api/restoplace/webhook/{secret}")
async def restoplace_webhook(secret:str,request:Request):
    configured_secret=os.getenv("RESTOPLACE_WEBHOOK_SECRET","")
    if len(configured_secret)<32 or not hmac.compare_digest(secret,configured_secret):
        raise HTTPException(404,"Not found")
    if rp.mode()!="test":raise HTTPException(404,"Not found")
    raw=await request.body()
    if len(raw)>65536:raise HTTPException(413,"Too large")
    try: data=json.loads(raw)
    except (ValueError,UnicodeDecodeError):raise HTTPException(400,"Invalid JSON")
    if not isinstance(data,dict) or data.get("event") not in ("reserve.created","reserve.updated"):
        return {"ok":True,"ignored":True}
    # Never store unsolicited PII. Only update reservations created by our test bot.
    reserve_id=data.get("id")
    status=data.get("status")
    if not isinstance(status,int):
        return {"ok":True,"ignored":True}
    return {"ok":True,"matched":tb.event_for_known_booking(reserve_id,status)}

@app.post("/api/booking")
def booking(body:Base):
    auth(body.initData)
    raise HTTPException(503,"Бронирование будет доступно после подключения Restoplace")
