import os, json, hmac, hashlib, time, logging, html, csv, io, asyncio
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
APP_VERSION="2026-11-03-restoplace-v3-feedback-mobile"
class Base(BaseModel):
    initData:str=""
class AdminQuery(Base):
    query:str=Field(default="",max_length=100)
    date:str=Field(default="",max_length=10)
    regularOnly:bool=False
    includeExamples:bool=True
    sort:str=Field(default="visits",pattern="^(visits|bookings|name|recent)$")
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
    if rp.mode()=="test" and os.getenv("ALLOW_LEGACY_EXPORT")!="1":
        raise HTTPException(403,"В тестовой версии экспорт реальных контактов отключён")
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


async def send_due_feedback_once():
    if os.getenv("FEEDBACK_AUTO_DEMO_ENABLED")!="1" or rp.mode()!="test" or not TOKEN:
        return
    claimed=tb.claim_due_feedback()
    if not claimed:
        return
    for entry in claimed:
        try:
            if polling_application is not None:
                bot=polling_application.bot
                await bot.send_message(
                    chat_id=entry["user_id"],
                    text="Спасибо, что посетили Steindamm 99! Как прошёл ваш вечер? Будем рады вашей оценке и отзыву ⭐",
                    reply_markup=__import__("telegram").InlineKeyboardMarkup([[
                        __import__("telegram").InlineKeyboardButton(
                            "⭐ Оставить отзыв",
                            web_app=__import__("telegram").WebAppInfo(
                                url=os.getenv("WEBAPP_URL","").rstrip("/") + "/app-demo-v3-20261103?section=review")
                        )
                    ]])
                )
            else:
                async with Bot(token=TOKEN) as bot:
                    await bot.send_message(
                        chat_id=entry["user_id"],
                        text="Спасибо, что посетили Steindamm 99! Как прошёл ваш вечер? Будем рады вашей оценке и отзыву ⭐"
                    )
            tb.finish_feedback(entry["request_key"],True)
        except Exception:
            tb.finish_feedback(entry["request_key"],False)
            logger.warning("Failed to deliver due feedback message; will retry later")

async def feedback_scheduler():
    while True:
        try:
            await send_due_feedback_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Feedback scheduler check failed")
        await asyncio.sleep(60)

feedback_task=None

@app.on_event("startup")
async def start_feedback_scheduler():
    global feedback_task
    if os.getenv("FEEDBACK_AUTO_DEMO_ENABLED")=="1":
        feedback_task=asyncio.create_task(feedback_scheduler())

@app.on_event("shutdown")
async def stop_feedback_scheduler():
    global feedback_task
    if feedback_task is not None:
        feedback_task.cancel()
        try:
            await feedback_task
        except asyncio.CancelledError:
            pass
        feedback_task=None

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

@app.get("/app-demo-v3-20261103")
def demo_v3_app():
    return FileResponse(Path(__file__).parent/"web"/"v3.html",headers={"Cache-Control":"no-store, max-age=0"})

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

    message="⭐ Новый отзыв Steindamm 99. Подробности доступны в защищённой админ-панели."
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
    count:int=Field(ge=1,le=6)
class BookingLookup(Base):
    reserveId:str=Field(min_length=1,max_length=30,pattern=r"^[0-9]+$")

@app.post("/api/restoplace/config")
def restoplace_config(body:Base):
    p=auth(body.initData,require_registration=False)
    return {"realContactTest":bool(p["id"] and os.getenv("REAL_CONTACT_TEST_ENABLED")=="1"),"mode":rp.mode(),"testConnected":rp.configured(),
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
    # Real contact mode is available to every signed-in Telegram guest when enabled.
    # The bot verifies that the contact belongs to the same Telegram account.
    use_real=os.getenv("REAL_CONTACT_TEST_ENABLED")=="1"
    if use_real:
        profile=user(p["id"])
        if not profile or not profile.get("phone"):
            raise HTTPException(409,"Сначала отправьте свой номер кнопкой в чате бота.")
        test_name=(profile.get("name") or p.get("first_name") or "Гость").strip()[:120]
        test_phone=profile["phone"]
    else:
        test_name="Тестовый гость Steindamm99"
        test_phone="79990000000"
    if body.count<=4:
        listed=await rp.slots(body.date,body.count)
        if not any(s["time"]==body.time and s["available"] for s in listed):
            raise HTTPException(409,"Выбранное время уже недоступно. Выберите другой слот.")
    else:
        rp.timeslot_start(body.date,body.time)
    waitlist=body.count>=5
    try:
        new,existing=tb.start(body.requestKey,p["id"],test_name,test_phone,
                              body.date,body.time,body.count,waitlist)
    except tb.BookingConflict as exc:
        raise HTTPException(409,str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409,"Некорректная или повторная операция бронирования") from exc
    if not new:
        return {"ok":existing["state"] in ("confirmed","waitlist","pending_payment"),
                "state":existing["state"],"externalId":existing["external_id"],
                "number":existing["external_number"],"alreadyProcessed":True}
    try:
        result=await rp.create(test_name,test_phone,body.date,body.time,body.count,waitlist)
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

@app.post("/api/restoplace/admin/export")
def demo_export(body:Base):
    require_admin(body.initData,"admin")
    stream=io.StringIO()
    writer=csv.writer(stream,delimiter=";")
    writer.writerow(["telegram_id","test_name","test_phone","created_at","test_bookings","test_visits","status"])
    for g in tb.guest_report():
        def safe(value):
            v=str(value if value is not None else "")
            return "'"+v if v.lstrip().startswith(("=","+","-","@")) else v
        writer.writerow([safe(g[k]) for k in ("id","name","phone","created_at","bookings","visits","status")])
    return Response(content=(chr(65279)+stream.getvalue()).encode("utf-8"),
                    media_type="text/csv",headers={"Content-Disposition":'attachment; filename="demo-guests.csv"',
                    "Cache-Control":"no-store"})

@app.post("/api/restoplace/admin/guests")
def demo_guests(body:AdminQuery):
    require_admin(body.initData,"manager")
    return {"items":tb.crm_guests(body.query,body.regularOnly,body.date,body.sort,body.includeExamples)}

class DemoStatusChange(Base):
    reserveId:str=Field(min_length=1,max_length=30,pattern=r"^[0-9]+$")
    status:int=Field(ge=4,le=6)

@app.post("/api/restoplace/admin/status")
async def demo_status_change(body:DemoStatusChange):
    require_admin(body.initData,"manager")
    if body.status not in (4,6):
        raise HTTPException(422,"Для демонстрации доступны только приход и отмена")
    if not rp.write_enabled():
        raise HTTPException(403,"Тестовая запись выключена")
    existing=tb.external_booking(body.reserveId)
    if not existing:
        raise HTTPException(404,"Бронь отсутствует в тестовой базе бота")
    if existing["state"]=="unknown":
        raise HTTPException(409,"Сначала необходимо проверить исход предыдущей операции")
    desired="arrived" if body.status==4 else "cancelled"
    if existing["state"]==desired:
        return {"ok":True,"state":desired,"alreadyProcessed":True}
    if existing["state"]=="cancelled":
        raise HTTPException(409,"Отменённую бронь нельзя изменить")
    # One request only; on an ambiguous network failure do not automatically retry.
    await rp.call("PUT","/reserves/"+body.reserveId+"/status",
                  payload={"status":body.status},write=True)
    tb.set_external_status(body.reserveId,desired)
    return {"ok":True,"state":desired}

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
    matched=tb.event_for_known_booking(reserve_id,status,opened=bool(data.get("time_open")))
    return {"ok":True,"matched":matched}

@app.post("/api/booking")
def booking(body:Base):
    auth(body.initData)
    raise HTTPException(503,"Бронирование будет доступно после подключения Restoplace")
