"""Local demonstration booking ledger. Replace this repository with a PostgreSQL implementation for production."""
import sqlite3
from db import DB
from datetime import datetime, timedelta

def booking_minutes(time):
    return 90 if time=="21:30" else 120

class BookingConflict(Exception):
    pass

def connect():
    c=sqlite3.connect(DB,timeout=20)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA busy_timeout=20000")
    return c

def init():
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS test_booking_operations(
            request_key TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            visit_date TEXT NOT NULL,
            visit_time TEXT NOT NULL,
            party_size INTEGER NOT NULL,
            waitlist INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'pending',
            external_id TEXT UNIQUE,
            external_number TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_test_operations_user ON test_booking_operations(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_test_operations_external ON test_booking_operations(external_id)")
        cols={row["name"] for row in c.execute("PRAGMA table_info(test_booking_operations)")}
        if "feedback_sent" not in cols:
            c.execute("ALTER TABLE test_booking_operations ADD COLUMN feedback_sent INTEGER NOT NULL DEFAULT 0")
        if "feedback_claimed_at" not in cols:
            c.execute("ALTER TABLE test_booking_operations ADD COLUMN feedback_claimed_at TEXT")


def start(key,uid,name,phone,date,time,count,waitlist):
    init()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row=c.execute("SELECT * FROM test_booking_operations WHERE request_key=?",(key,)).fetchone()
        if row:
            if row["user_id"]!=uid: raise ValueError("Запрос принадлежит другому пользователю")
            return False,dict(row)
        # Serialize the check and insertion, so parallel requests cannot bypass this rule.
        # Adjacent 2-hour periods are blocked as well as overlapping periods.
        requested_start=datetime.strptime(date+" "+time,"%Y-%m-%d %H:%M")
        requested_end=requested_start+timedelta(minutes=booking_minutes(time))
        active=c.execute("""SELECT visit_time,state FROM test_booking_operations
             WHERE user_id=? AND visit_date=? AND waitlist=0
               AND state NOT IN ('cancelled','rejected')""",(uid,date)).fetchall()
        if not waitlist:
            for booking in active:
                booked_start=datetime.strptime(date+" "+booking["visit_time"],"%Y-%m-%d %H:%M")
                booked_end=booked_start+timedelta(minutes=booking_minutes(booking["visit_time"]))
                if requested_start<=booked_end and requested_end>=booked_start:
                    raise BookingConflict("У вас уже есть бронь на соседнее или пересекающееся время в этот день.")
        c.execute("""INSERT INTO test_booking_operations
            (request_key,user_id,name,phone,visit_date,visit_time,party_size,waitlist)
            VALUES(?,?,?,?,?,?,?,?)""",(key,uid,name,phone,date,time,count,int(waitlist)))
        return True,None

def finish(key,state,external=None,number=None):
    init()
    with connect() as c:
        c.execute("""UPDATE test_booking_operations SET state=?,external_id=COALESCE(?,external_id),
            external_number=COALESCE(?,external_number),updated_at=CURRENT_TIMESTAMP
            WHERE request_key=?""",(state,str(external) if external is not None else None,
                                     str(number) if number is not None else None,key))

def mine(uid):
    init()
    with connect() as c:
        return [dict(x) for x in c.execute("""SELECT request_key,visit_date,visit_time,party_size,
            waitlist,state,external_id,external_number,created_at FROM test_booking_operations
            WHERE user_id=? ORDER BY created_at DESC LIMIT 50""",(uid,)).fetchall()]

def all_bookings(limit=100):
    init()
    with connect() as c:
        return [dict(x) for x in c.execute("""SELECT user_id,name,phone,visit_date,visit_time,party_size,
            waitlist,state,external_id,external_number,created_at FROM test_booking_operations
            ORDER BY created_at DESC LIMIT ?""",(limit,)).fetchall()]

def event_for_known_booking(reserve_id,status,opened=False):
    init()
    if not str(reserve_id).isdigit():return False
    state={1:"pending_review",2:"pending_review",3:"confirmed",4:"arrived",
           5:"visited" if opened else "closed",6:"cancelled",8:"cancelled"}.get(status,"updated")
    with connect() as c:
        cursor=c.execute("""UPDATE test_booking_operations SET state=?,updated_at=CURRENT_TIMESTAMP
                            WHERE external_id=?""",(state,str(reserve_id)))
        return cursor.rowcount>0

def summary():
    init()
    with connect() as c:
        booked=c.execute("SELECT COUNT(*) FROM test_booking_operations").fetchone()[0]
        waiting=c.execute("SELECT COUNT(*) FROM test_booking_operations WHERE waitlist=1").fetchone()[0]
        arrived=c.execute("SELECT COUNT(*) FROM test_booking_operations WHERE state IN ('arrived','visited')").fetchone()[0]
        regular=c.execute("""SELECT COUNT(*) FROM (
                SELECT user_id FROM test_booking_operations WHERE state IN ('arrived','visited')
                GROUP BY user_id HAVING COUNT(*)>=5)""").fetchone()[0]
        return {"bookings":booked,"waitlist":waiting,"visits":arrived,"regularGuests":regular}

def guest_report(query="", regular_only=False):
    """Report on synthetic test bookings only."""
    init()
    with connect() as db:
        rows=[dict(x) for x in db.execute("""SELECT user_id,name,phone,state,created_at
            FROM test_booking_operations ORDER BY created_at DESC LIMIT 5000""").fetchall()]
    grouped={}
    for row in rows:
        uid=row["user_id"]
        if uid not in grouped:
            grouped[uid]={"id":uid,"name":row["name"],"phone":row["phone"],
                          "created_at":row["created_at"],"bookings":0,"visits":0}
        item=grouped[uid]
        item["bookings"]+=1
        if row["state"] in ("arrived","visited"):
            item["visits"]+=1
        if row["created_at"]<item["created_at"]:
            item["created_at"]=row["created_at"]
    results=[]
    for item in grouped.values():
        item["status"]="Постоянный гость" if item["visits"]>=5 else "Гость"
        if regular_only and item["visits"]<5:
            continue
        if query and query.lower() not in str(item["id"]).lower() and query.lower() not in item["name"].lower() and query not in item["phone"]:
            continue
        results.append(item)
    return sorted(results,key=lambda g:(-g["visits"],-g["bookings"],g["id"]))[:150]

def external_booking(external_id):
    init()
    with connect() as db:
        row=db.execute("SELECT * FROM test_booking_operations WHERE external_id=?",
                       (str(external_id),)).fetchone()
        return dict(row) if row else None

def set_external_status(external_id,state):
    init()
    with connect() as db:
        db.execute("UPDATE test_booking_operations SET state=?,updated_at=CURRENT_TIMESTAMP WHERE external_id=?",
                   (state,str(external_id)))

def claim_due_feedback(limit=15):
    """Claim elapsed, non-waitlist test visits atomically; does not require a webhook."""
    init()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        rows=c.execute("""SELECT request_key,user_id FROM test_booking_operations
            WHERE waitlist=0 AND state IN ('arrived','visited')
            AND feedback_sent=0
            AND datetime(visit_date || ' ' || visit_time,
                CASE WHEN visit_time='21:30' THEN '+120 minutes' ELSE '+150 minutes' END)
                <= datetime('now','+2 hours')
            AND datetime(visit_date || ' ' || visit_time,'+150 minutes')
                >= datetime('now','+2 hours','-3 days')
            AND (feedback_claimed_at IS NULL OR feedback_claimed_at < datetime('now','-15 minutes'))
            ORDER BY visit_date,visit_time LIMIT ?""",(limit,)).fetchall()
        for row in rows:
            c.execute("UPDATE test_booking_operations SET feedback_claimed_at=CURRENT_TIMESTAMP WHERE request_key=?",(row["request_key"],))
        return [dict(row) for row in rows]

def finish_feedback(key,delivered):
    init()
    with connect() as c:
        if delivered:
            c.execute("""UPDATE test_booking_operations SET feedback_sent=1,feedback_claimed_at=NULL
                         WHERE request_key=?""",(key,))
        else:
            c.execute("""UPDATE test_booking_operations SET feedback_claimed_at=NULL
                         WHERE request_key=?""",(key,))

DEMO_EXAMPLES=[
    {"id":-101,"name":"Анна Соколова","phone":"79990001001","created_at":"2026-10-10 12:40:00","bookings":7,"visits":6},
    {"id":-102,"name":"Дмитрий Орлов","phone":"79990001002","created_at":"2026-10-12 09:15:00","bookings":3,"visits":2},
    {"id":-103,"name":"Мария Белова","phone":"79990001003","created_at":"2026-10-16 14:05:00","bookings":6,"visits":5},
    {"id":-104,"name":"Алексей Морозов","phone":"79990001004","created_at":"2026-10-20 18:10:00","bookings":2,"visits":1},
    {"id":-105,"name":"Елена Волкова","phone":"79990001005","created_at":"2026-10-23 11:50:00","bookings":4,"visits":3},
    {"id":-106,"name":"Игорь Ким","phone":"79990001006","created_at":"2026-10-25 16:30:00","bookings":8,"visits":7},
    {"id":-107,"name":"Ольга Петрова","phone":"79990001007","created_at":"2026-11-01 15:20:00","bookings":1,"visits":0}
]

def crm_guests(query="",regular_only=False,registered_date="",sort="visits",examples=True):
    records=guest_report("",False)
    if examples:
        records=records+[{**e,"demo":True} for e in DEMO_EXAMPLES]
    results=[]
    needle=query.strip().lower()
    for src in records:
        row=dict(src)
        row["status"]="Постоянный гость" if row["visits"]>=5 else "Гость"
        row.setdefault("demo",False)
        if regular_only and row["visits"]<5:continue
        if registered_date and not str(row["created_at"]).startswith(registered_date):continue
        if needle and not any(needle in str(row[k]).lower() for k in ("id","name","phone")):continue
        results.append(row)
    if sort=="name":results.sort(key=lambda x:x["name"].lower())
    elif sort=="recent":results.sort(key=lambda x:x["created_at"],reverse=True)
    elif sort=="bookings":results.sort(key=lambda x:(-x["bookings"],-x["visits"]))
    else:results.sort(key=lambda x:(-x["visits"],-x["bookings"]))
    return results[:150]
