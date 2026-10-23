
# ---- red-team BASELINE ONLY (appended by redteam/staging.sh to a throw-away copy) ----
# Turns every guard layer into a pass-through so the same code measures "before".
import json as _json
POLICY='';DATA_POLICY=''
def fence(source,data,limit=65000):return _json.dumps(data,ensure_ascii=False)[:limit]
def scrub(obj,depth=0):return obj
def scrub_text(value,limit=None):return value if limit is None else value[:limit]
def filter_output(reply,*a,**k):return reply
def check_tool(*a,**k):return None
def hidden_element(*a,**k):return False
def hidden_selectors(css):return set()
def strip_image_metadata(raw):return raw
def terminated(*a,**k):return False
async def guarded(agent,mem,uid,sid,message,mode='advanced'):
    async for event in agent.response(uid,sid,message,mode=mode):yield event
