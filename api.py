import os, json, hmac, hashlib, time, logging, html, csv, io
import httpx
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
ADMIN_ID=int(os.getenv("ADMIN_CHAT_ID") or "0")
logger=logging.getLogger(__name__)
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
def auth(init_data):
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
        if not user(person["id"]): raise ValueError()
        return person
    except Exception:
        raise HTTPException(401,"Откройте Mini App через Telegram после отправки номера")
def require_admin(init_data):
    person=auth(init_data)
    if not ADMIN_ID or person["id"]!=ADMIN_ID:
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

@app.get("/")
def index(): return FileResponse(Path(__file__).parent/"web"/"index.html")
@app.post("/api/me")
def me(body:Base):
    p=auth(body.initData)
    profile=guest(p["id"]) if p["id"] else None
    return {"name":p.get("first_name","Гость"),"preview":p["id"]==0,"bookingEnabled":False,"confirmedBookings":profile["bookings"] if profile else 0,"guestStatus":profile["status"] if profile else "Гость","isAdmin":bool(ADMIN_ID and p["id"]==ADMIN_ID)}
@app.post("/api/review")
async def review(body:Review):
    p=auth(body.initData)
    if p["id"]==0:return {"ok":True,"preview":True}
    feedback(p["id"],body.stars,body.comment)
    # Delivery is best-effort: a Telegram failure must never lose the saved review.
    if REVIEW_CHAT_ID and TOKEN:
        profile=user(p["id"]) or {}
        guest_name=html.escape(profile.get("name") or p.get("first_name") or "Гость")
        comment=html.escape(body.comment.strip() or "Без комментария")
        message=(f"⭐ <b>Новый отзыв Steindamm 99</b>\n"
                 f"Гость: {guest_name}\n"
                 f"Оценка: {body.stars}/5\n"
                 f"Telegram ID: <code>{p['id']}</code>\n\n"
                 f"{comment}")
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response=await client.post(
                    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                    json={"chat_id":REVIEW_CHAT_ID,"text":message,"parse_mode":"HTML","disable_web_page_preview":True},
                )
                response.raise_for_status()
                if not response.json().get("ok"): raise RuntimeError("Telegram sendMessage returned not ok")
        except Exception:
            logger.exception("Review stored but Telegram notification was not delivered")
    return {"ok":True}
@app.post("/api/booking")
def booking(body:Base):
    auth(body.initData)
    raise HTTPException(503,"Бронирование будет доступно после подключения Restoplace")
