"""Shared, server-enforced access roles for the bot and Mini App."""
import os

def parse_ids(name):
    result=set()
    for item in os.getenv(name,"").split(","):
        item=item.strip()
        if item and item.isdecimal() and int(item)>0:
            result.add(int(item))
    return result

# Backwards compatibility: the previous single administrator remains an owner.
OWNERS=parse_ids("ADMIN_CHAT_ID") | parse_ids("ADMIN_IDS")
MANAGERS=parse_ids("MANAGER_IDS") - OWNERS

def role(user_id):
    if user_id in OWNERS:
        return "admin"
    if user_id in MANAGERS:
        return "manager"
    return "guest"

def allowed(user_id,need="manager"):
    r=role(user_id)
    return r=="admin" or (need=="manager" and r=="manager")
