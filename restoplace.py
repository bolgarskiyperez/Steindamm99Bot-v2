"""Restoplace adapter. Explicitly restricted to a dedicated test venue on Render."""
import os
import httpx
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import HTTPException

BASE="https://api.restoplace.cc"
def mode():
    return os.getenv("RESTOPLACE_MODE","mock").strip().lower()
def write_enabled():
    return mode()=="test" and os.getenv("RESTOPLACE_TEST_WRITES","false").lower()=="true" and os.getenv("RESTOPLACE_TEST_VENUE_CONFIRMED")=="yes" and bool(os.getenv("RESTOPLACE_TEST_API_KEY",""))
def configured():
    return mode()=="test" and bool(os.getenv("RESTOPLACE_TEST_API_KEY",""))
async def call(method,path,params=None,payload=None,write=False):
    if mode()!="test":
        raise HTTPException(503,"Restoplace: тестовый API не подключён")
    if not configured():
        raise HTTPException(503,"Добавьте RESTOPLACE_TEST_API_KEY в Render Environment")
    if write and not write_enabled():
        raise HTTPException(403,"Тестовая запись отключена настройкой RESTOPLACE_TEST_WRITES")
    if not path.startswith("/") or "://" in path:
        raise HTTPException(400,"Некорректный путь API")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0,connect=5.0),follow_redirects=False) as client:
            response=await client.request(method,BASE+path,
                headers={"X-API-Key":os.environ["RESTOPLACE_TEST_API_KEY"],"Accept":"application/json"},
                params=params,json=payload)
            response.raise_for_status()
            data=response.json()
    except (httpx.TimeoutException,httpx.TransportError) as exc:
        raise HTTPException(504,"Restoplace не ответил. Не повторяйте создание брони до сверки.") from exc
    except (httpx.HTTPStatusError,ValueError) as exc:
        raise HTTPException(502,"Ошибка ответа Restoplace") from exc
    if not isinstance(data,dict) or data.get("error"):
        raise HTTPException(422,"Restoplace: "+str(data.get("error","некорректный ответ"))[:200])
    return data.get("responseData")

# Only the configured restaurant's paired odd-hour slots are bookable.
ALLOWED_STARTS=tuple(f"{h:02d}:{m:02d}" for h in (11,13,15,17,19,21) for m in (0,30))

def reservation_minutes(time):
    if time not in ALLOWED_STARTS:
        raise HTTPException(422,"Выберите время из расписания ресторана: 11:00, 11:30, 13:00, 13:30 и далее до 21:30")
    return 90 if time=="21:30" else 120

def timeslot_start(date,time):
    try:
        start=datetime.strptime(date+" "+time,"%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise HTTPException(422,"Некорректная дата или время") from exc
    reservation_minutes(time)
    now=datetime.now(ZoneInfo("Europe/Kaliningrad")).replace(tzinfo=None)
    if start<=now or start>now+timedelta(days=90):
        raise HTTPException(422,"Выберите будущую дату в пределах 90 дней")
    return start

async def slots(date,count):
    if count<1 or count>4: raise HTTPException(422,"Для 5–6 гостей доступен лист ожидания")
    try:
        datetime.strptime(date,"%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(422,"Некорректная дата") from exc
    normal=await call("GET","/times/",params={"date":date,"length":120,"shift":30})
    # The closing slot is 90 minutes, so its availability must be checked separately.
    closing=await call("GET","/times/",params={"date":date,"length":90,"shift":30})
    def available(records,time,minutes):
        for entry in records if isinstance(records,list) else []:
            raw=str(entry.get("from",""))
            finish=str(entry.get("to",""))
            if raw[:16]!=date+" "+time:
                continue
            try:
                a=datetime.strptime(raw[:19],"%Y-%m-%d %H:%M:%S")
                b=datetime.strptime(finish[:19],"%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if int((b-a).total_seconds()/60)!=minutes:
                continue
            return entry.get("counts",{}).get(str(count))=="free"
        return False
    result=[]
    for time in ALLOWED_STARTS:
        try:
            timeslot_start(date,time)
        except HTTPException:
            continue
        minutes=reservation_minutes(time)
        records=closing if time=="21:30" else normal
        result.append({"time":time,"available":available(records,time,minutes)})
    return result

async def create(name,phone,date,time,count,waitlist=False):
    start=timeslot_start(date,time)
    if count>4 and not waitlist:
        raise HTTPException(422,"Компания от 5 гостей оформляется в лист ожидания")
    if count<=4 and waitlist:
        raise HTTPException(422,"Для 1–4 гостей используйте обычное бронирование")
    payload={"from":start.strftime("%Y-%m-%d %H:%M:%S"),
             "to":(start+timedelta(minutes=reservation_minutes(time))).strftime("%Y-%m-%d %H:%M:%S"),
             "name":name,"phone":phone,"count":count,
             "source":"telegram.ru","waitlist":waitlist,"deposit":False}
    return await call("POST","/reserves/",payload=payload,write=True)

async def list_reserves(page=1):
    return await call("GET","/reserves",params={"page":page})

async def get_reserve(reserve_id):
    if not str(reserve_id).isdigit(): raise HTTPException(422,"Некорректный ID")
    return await call("GET","/reserves/"+str(reserve_id))
