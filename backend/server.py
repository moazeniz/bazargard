import os,json,secrets,time,asyncio,re
from pathlib import Path
from typing import Literal
from collections import defaultdict,deque
from urllib.parse import urlsplit
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import StreamingResponse,FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from catalog import Catalog,TEHRAN
import devtrace
from memory import Memory,validate_profile
from agent import Agent
from features import Features
from runs import Runs
from ops import Operations
import guard
import llm
import obs,resilience
from jobqueue import JobQueue
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
obs.setup()
RELEASE=obs.RELEASE

ROOT=Path(__file__).resolve().parent.parent
DATA=Path(os.getenv('DATA_DIR',str(ROOT/'data')));DATA.mkdir(parents=True,exist_ok=True)
cat=Catalog(DATA/'catalog.db');mem=Memory(DATA/'memory.db');agent=Agent(cat,mem)
features=Features(cat,mem,agent);agent.features=features
from freshness import Freshness
fresh=Freshness(DATA/'memory.db',cat);cat.freshness=fresh  # «تازه‌سازی قیمت»: global seller/web/crowd price updates
app=FastAPI(title='Hamrah Shopping Agent',docs_url='/api/docs',redoc_url=None,openapi_url='/api/openapi.json')
locks={};limits=defaultdict(deque);chat_gate=asyncio.Semaphore(4);CHAT_HEARTBEAT_SECONDS=10
# Durable queue (retries, dead letter, restart recovery) for every background job.
jobs=JobQueue(DATA/'memory.db')
runs=Runs(mem,chat_gate,jobs)
runs.replay=lambda u,s,mode:agent.response(u,s,'',mode=mode,replay=True)
traces=devtrace.Traces(DATA/'memory.db');obs.SINKS.append(traces.capture)  # developer console (DEV_CONSOLE=1 only)
ops=Operations(cat,mem,ROOT)

def uid(req):
    token=req.cookies.get('hamrah_user','')
    with mem.conn() as c:exists=c.execute('select 1 from users where id=?',(token,)).fetchone()
    if not exists:raise HTTPException(401,'ابتدا صفحه را دوباره باز کنید')
    return token

def session(req,sid,read=False):
    u=uid(req)
    try:
        if mem.session(u,sid)['kind']!='shopping':raise KeyError('Shopping session required')
    except KeyError:raise HTTPException(404,'گفت‌وگو پیدا نشد')
    # A chat closed for abuse answers every later request with one fixed message (no model call).
    if not read and guard.terminated(mem,u,sid):raise HTTPException(423,guard.TERMINATED,headers={'X-Chat-Terminated':'1'})
    return u

def known_user(token):
    if not token:return False
    with mem.conn() as c:return bool(c.execute('select 1 from users where id=?',(token,)).fetchone())

def guard_status(u,sid):
    s=guard.state(mem,u,sid)
    return {'terminated':bool(s['terminated']),'warned':bool(s['warned']),'message':guard.TERMINATED if s['terminated'] else None}

# Per-IP hourly backstops only for costly background work (denial-of-wallet); chat,
# new users and new chats are not rate-limited so shared judge networks are never blocked.
def rate(req,kind='chat',maxcount=50):
    key=(req.client.host if req.client else 'local',kind);now=time.monotonic();q=limits[key]
    while q and q[0]<now-3600:q.popleft()
    if len(q)>=maxcount:raise HTTPException(429,'کمی صبر کنید و دوباره تلاش کنید')
    q.append(now)

@app.middleware('http')
async def protections(req,call_next):
    if req.method in ['POST','PATCH','DELETE','PUT']:
        origin=req.headers.get('origin')
        if origin and urlsplit(origin).netloc!=req.headers.get('host'):return JSONResponse({'detail':'Origin not allowed'},403)
        if req.headers.get('sec-fetch-site')=='cross-site':return JSONResponse({'detail':'Origin not allowed'},403)
        try:length=int(req.headers.get('content-length','0'))
        except ValueError:return JSONResponse({'detail':'Invalid request'},400)
        if length>(3_000_000 if req.url.path=='/api/identify-image' else 30000):return JSONResponse({'detail':'Request too large'},413)
    response=await call_next(req)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['Permissions-Policy']='camera=(), microphone=()'
    # No inline script: also neutralises javascript: links from catalogue/web data.
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    response.headers['X-Frame-Options']='DENY'
    if req.url.path.startswith('/api/'):response.headers['Cache-Control']='no-store'
    return response

CRASH_MESSAGE='خطای غیرمنتظره‌ای رخ داد؛ لطفاً دوباره تلاش کنید. اگر تکرار شد، شناسه درخواست را گزارش کنید.'
QUIET={'/healthz','/api/health'}
@app.middleware('http')
async def observe(req,call_next):
    """Outermost: X-Request-ID/traceparent in and out, one http.request log per request,
    and a JSON 500 (no stack trace) for anything that escaped the handlers."""
    rid,traceparent,tokens=obs.start_request(req.headers,req.cookies.get('hamrah_user'))
    started=time.monotonic();response=None
    try:
        try:response=await call_next(req)
        except Exception as exc:
            obs.error('app.crash',exc,level='critical',route=req.url.path,method=req.method)
            response=JSONResponse({'detail':CRASH_MESSAGE,'request_id':rid},500)
        response.headers['X-Request-ID']=rid;response.headers['traceparent']=traceparent
        return response
    finally:
        status=response.status_code if response is not None else 500;route=req.scope.get('route')
        ip=(req.headers.get('x-forwarded-for','').split(',')[0].strip() or (req.client.host if req.client else ''))
        obs.log('http.request','debug' if req.url.path in QUIET and status<400 else 'error' if status>=500 else 'warn' if status>=400 else 'info',
                req.method+' '+getattr(route,'path',req.url.path),route=getattr(route,'path',req.url.path),method=req.method,status=status,duration_ms=round((time.monotonic()-started)*1000),
                bytes=int(response.headers.get('content-length')) if response is not None and response.headers.get('content-length','').isdigit() else None,
                client_ip_hash=obs.digest(ip) if ip else None,user_agent=(req.headers.get('user-agent') or '')[:120])
        obs.end_request(tokens)

@app.exception_handler(StarletteHTTPException)
async def http_error(req,exc):
    if exc.status_code>=500:obs.log('http.error','error',str(exc.detail),status=exc.status_code,route=req.url.path)
    return JSONResponse({'detail':exc.detail,'request_id':obs.request_id.get()},exc.status_code,headers=getattr(exc,'headers',None))

@app.exception_handler(RequestValidationError)
async def validation_error(req,exc):
    return JSONResponse({'detail':'درخواست معتبر نیست؛ مقادیر واردشده را بررسی کنید.','errors':[{'loc':[str(x) for x in e.get('loc',[])][-3:],'type':e.get('type')} for e in exc.errors()][:10],
                         'request_id':obs.request_id.get()},422)

@app.exception_handler(Exception)
async def crash(req,exc):
    obs.error('app.crash',exc,level='critical',route=req.url.path,method=req.method)
    return JSONResponse({'detail':CRASH_MESSAGE,'request_id':obs.request_id.get()},500)

@app.on_event('startup')
async def started():
    jobs.ensure_worker();obs.log('app.start','info','ready',queue_depth=jobs.metrics()['depth'],provider=llm.active())
    import dataset_facts
    if (DATA/'catalog.db').exists():asyncio.get_running_loop().run_in_executor(None,dataset_facts.facts,DATA/'catalog.db',DATA)  # warm the about-data numbers

@app.on_event('shutdown')
async def stopping():
    """Graceful shutdown: unanswered chat turns and running jobs are persisted for the next process."""
    runs.stopping=True
    pending=list(runs.tasks.values())
    for t in pending:t.cancel()
    if pending:await asyncio.gather(*pending,return_exceptions=True)
    await jobs.shutdown()
    obs.log('app.stop','info','stopped',value=len(pending))

@app.get('/healthz')
def healthz():return {'status':'ok','release':RELEASE}

@app.get('/api/dataset-facts')
async def dataset_facts_endpoint():
    """Key catalogue numbers for «درباره داده‌ها» (computed from the database, cached)."""
    import dataset_facts
    try:return await asyncio.to_thread(dataset_facts.facts,DATA/'catalog.db',DATA)
    except Exception as e:
        obs.error('dataset.facts_failed',e);raise HTTPException(503,'آمار داده هنوز آماده نیست؛ کمی بعد دوباره امتحان کنید.')

@app.get('/api/health')
async def health():
    # Active provider (anthropic|boxapi), model and cached reachability of both; no secrets.
    return {'status':'ok','release':RELEASE,'catalog_ready':(DATA/'catalog.db').exists(),**(await llm.health())}

@app.get('/api/internal/llm-stats')
def llm_stats(req:Request):
    # Operators only: direct loopback requests (not proxied public traffic).
    if (req.client.host if req.client else '') not in ('127.0.0.1','::1') or req.headers.get('x-forwarded-for') or req.headers.get('x-real-ip'):raise HTTPException(404)
    return llm.stats()

@app.get('/api/bootstrap')
async def bootstrap(req:Request):
    u=mem.user(req.cookies.get('hamrah_user'));sessions=mem.sessions(u)
    if not sessions:sessions=[mem.session(u)]
    with cat.conn() as c:
        neighborhoods=[r[0] for r in c.execute("select distinct location_label from shops where location_label is not null and trim(location_label)!='' order by location_label")]
    out=JSONResponse({'release':RELEASE,'stats':cat.stats(),'sessions':sessions,'facts':mem.facts(u),'neighborhoods':neighborhoods,'notices':['نام اغلب فروشگاه‌ها و همه شماره‌ها در دیتاست ساختگی است.','قیمت‌ها ثبت‌شده‌اند؛ موجودی زنده تأیید نشده است.','پوشش محدود: حدود ۱۰٪ فروشگاه‌های تهران و حداکثر ۳۰۰ محصول از هر فروشگاه.'],'integrations':{'seller_calling':False,'google_places':False,'maps_links':True,'simulator':True,'seller_reply_links':True,'image_search':True,'road_estimates':'osrm_direct_when_available','offline_trip_pass':True}})
    out.set_cookie('hamrah_user',u,httponly=True,samesite='lax',secure=req.headers.get('x-forwarded-proto',req.url.scheme)=='https',max_age=60*60*24*90)
    return out

@app.get('/api/sessions/{sid}')
def get_session(sid:str,req:Request):
    u=session(req,sid,read=True);return {'guard':guard_status(u,sid),'session':mem.session(u,sid),'turns':mem.turns(u,sid,100),'results':agent.overlay(u,sid,mem.results(u,sid)),'events':mem.events(u,sid),'facts':mem.facts(u,sid),'mission':features.mission(u,sid),'quotes':features.quotes(u,sid),'basket':features.basket(u,sid)}

class ModeBody(BaseModel):mode:Literal['simple','advanced']
@app.patch('/api/sessions/{sid}/mode')
def chat_mode(sid:str,body:ModeBody,req:Request):
    u=session(req,sid)
    with mem.conn() as c:
        if c.execute("select 1 from assistant_runs where user_id=? and session_id=? and status in ('queued','running')",(u,sid)).fetchone():raise HTTPException(409,'پس از پایان پاسخ، حالت را تغییر دهید')
        c.execute('update sessions set chat_mode=? where id=? and user_id=?',(body.mode,sid,u))
    return {'mode':body.mode,'question':features.decision(u,sid)}

@app.post('/api/sessions')
def new_session(req:Request):
    u=uid(req);return mem.session(u)

class ProfileBody(BaseModel):
    values:dict
    session_id:str|None=None
    scope:Literal['trip','memory']='trip'
@app.patch('/api/profile')
def profile(body:ProfileBody,req:Request):
    u=uid(req)
    if body.session_id:session(req,body.session_id)
    target=body.session_id if body.scope=='trip' else None
    try:mem.update(u,[{'key':k,'value':v,'quote':'تنظیم توسط کاربر'} for k,v in body.values.items()],sid=target)
    except (ValueError,TypeError):raise HTTPException(422,'مقدار فیلتر معتبر نیست')
    if body.scope=='memory' and body.session_id:mem.clear_trip_overrides(u,body.session_id,body.values.keys())
    return {'facts':mem.facts(u,body.session_id)}

@app.delete('/api/memory')
async def clear_memory(req:Request,all_data:bool=False):
    u=uid(req)
    if all_data:
        await runs.cancel_user(u)
        ops.clear_user(u)
    mem.clear(u,all_data);return {'status':'cleared'}

class SearchBody(BaseModel):
    session_id:str
    query:str=Field(default='',max_length=200)
    sort:str='balanced'
    product_ids:list[str]=Field(default_factory=list,max_length=4)
    recognized_model:str|None=Field(default=None,max_length=100)
@app.post('/api/search')
def search(body:SearchBody,req:Request):
    rate(req,'search',500);u=session(req,body.session_id)
    from price_policy import initial
    spread=not body.product_ids and initial(body.query,body.query,mem.results(u,body.session_id),body.sort,mem.profile(u,body.session_id))
    result=cat.search(body.query,mem.profile(u,body.session_id),exclude_shops=agent.exclusions(u,body.session_id),product_ids=body.product_ids or None,sort=body.sort,exclude_pairs=agent.unavailable_pairs(u,body.session_id),seller_events=mem.events(u,body.session_id),discovery=spread)
    fresh.demand(result.get('offers'),'search')
    if not result['offers'] and body.recognized_model:
        result=cat.search(body.recognized_model,mem.profile(u,body.session_id),seller_events=mem.events(u,body.session_id))
        result['notice']='عبارت کامل نتیجه نداشت؛ جست‌وجو با مدل خوانده‌شده از عکس انجام شد. فیلترهای خرید حفظ شده‌اند؛ نوع و مدل دقیق را بررسی کنید.'
    result=agent.overlay(u,body.session_id,result);result['product_ids']=body.product_ids
    if body.recognized_model:
        from catalog import normalize
        for o in result['offers']:o['image_match']='exact_model' if re.search(r'(?<!\w)'+re.escape(normalize(body.recognized_model))+r'(?!\w)',normalize(' '.join(str(o.get(k) or '') for k in ('model','persian_title','seller_title')))) else 'approximate'
        result['image_identification']={'confirmed_by_user':True,'model':body.recognized_model}
    mem.results(u,body.session_id,result);return result

class AreaBody(BaseModel):
    session_id:str
    query:str=Field(min_length=1,max_length=120)
@app.post('/api/shopping-areas')
def shopping_areas(body:AreaBody,req:Request):
    u=session(req,body.session_id);rate(req,'shopping_areas',300)
    return agent.shopping_areas(u,body.session_id,body.query)

class LocationBody(BaseModel):session_id:str;shop_id:str=Field(max_length=100)
@app.get('/api/locations/{shop_id}')
def location_view(shop_id:str,session_id:str,req:Request):
    try:return features.locations.result(session(req,session_id),session_id,shop_id)
    except ValueError as e:raise HTTPException(404,str(e))
@app.post('/api/locations/enrich')
async def location_enrich(body:LocationBody,req:Request):
    u=session(req,body.session_id);rate(req,'place_context',300)
    try:return await features.locations.enrich(u,body.session_id,body.shop_id)
    except ValueError as e:raise HTTPException(404,str(e))

@app.get('/api/categories')
def categories(q:str=''):return cat.categories(q[:100])

class CompareBody(BaseModel):
    session_id:str|None=None
    product_ids:list[str]|None=Field(default=None,min_length=2,max_length=12)
@app.post('/api/compare')
async def compare(body:CompareBody,req:Request):
    u=session(req,body.session_id) if body.session_id else uid(req)
    try:
        if body.session_id:return comparison_jobs().automatic(u,body.session_id,agent.comparison(u,body.session_id,body.product_ids))
        from comparison import build_comparison
        return build_comparison(cat,[],body.product_ids or [])
    except ValueError as e:raise HTTPException(422,str(e))

class ReviewBody(BaseModel):
    session_id:str
    product_id:str=Field(max_length=160)

research_gate=asyncio.Semaphore(2)
@app.post('/api/buying-review')
async def buying_review(body:ReviewBody,req:Request):
    u=session(req,body.session_id)
    rate(req,'research',200)
    try:
        async with research_gate:
            card=await agent.review(u,body.session_id,body.product_id)
        # A direct UI investigation is a durable, immutable reply too.
        mem.save_turn(u,body.session_id,'assistant','بررسی شواهد «'+card['title']+'»؛ منابع و محدودیت‌ها در کارت بررسی ثبت شد.',{'buying_reviews':[card]})
        return card
    except ValueError as e:raise HTTPException(422,str(e))

class ResearchComparisonBody(BaseModel):
    session_id:str
    snapshot_id:str=Field(max_length=160)
    product_ids:list[str]=Field(min_length=2,max_length=5)
    view_product_ids:list[str]|None=Field(default=None,min_length=2,max_length=12)

@app.post('/api/compare/research')
async def research_comparison(body:ResearchComparisonBody,req:Request):
    u=session(req,body.session_id);rate(req,'research',200)
    try:
        async with research_gate:
            data=await agent.researched_comparison(u,body.session_id,body.snapshot_id,body.product_ids,body.view_product_ids)
        mem.save_turn(u,body.session_id,'assistant','بررسی تکمیلی مدل‌های منتخب به نسخه ثابت مقایسه افزوده شد؛ جدول اصلی و قیود خرید حفظ شدند.',{'comparison':data})
        return data
    except ValueError as e:raise HTTPException(422,str(e))

class ExternalBody(BaseModel):
    session_id:str
    query:str=Field(min_length=1,max_length=160)
    product_id:str|None=Field(default=None,max_length=160)
@app.post('/api/external-discovery')
async def external_discovery(body:ExternalBody,req:Request):
    u=session(req,body.session_id);rate(req,'research',200)
    try:
        async with research_gate:data=await agent.external_discovery(u,body.session_id,body.query,body.product_id)
        mem.save_turn(u,body.session_id,'assistant','سرنخ‌های عمومی خرید حضوری تهران؛ آفرهای دیتاست در اولویت و اطلاعات بیرونی تأییدنشده‌اند.',{'external_discovery':data})
        return data
    except ValueError as e:raise HTTPException(422,str(e))

class ContactBody(BaseModel):shop_id:str;product_id:str;session_id:str|None=None
@app.post('/api/contact')
def contact(body:ContactBody,req:Request):
    u=uid(req);visit=None
    if body.session_id:
        session(req,body.session_id,read=True)
        visit=mem.profile(u,body.session_id).get('visit_at') or features.mission(u,body.session_id).get('departure')
    try:return agent.contact(body.shop_id,body.product_id,visit)
    except ValueError as e:raise HTTPException(404,str(e))

class FollowUpBody(BaseModel):
    session_id:str;shop_id:str=Field(max_length=100);status:Literal['not_asked','asked','answered','confirmed','no_answer']
    product_id:str|None=Field(default=None,max_length=100);channel:str|None=Field(default=None,max_length=30);note:str|None=Field(default=None,max_length=500)
@app.get('/api/followups/{sid}')
def get_followups(sid:str,req:Request):return {'followups':features.followups(session(req,sid,read=True),sid)}
@app.post('/api/followups')
def post_followup(body:FollowUpBody,req:Request):
    u=session(req,body.session_id)
    try:return {'followups':features.set_followup(u,body.session_id,body.shop_id,body.status,body.product_id,body.channel,body.note)}
    except ValueError as e:raise HTTPException(422,str(e))

class EventBody(BaseModel):
    session_id:str;shop_id:str;product_id:str
    status:Literal['reported','no_answer','closed','unavailable']='reported'
    price:float|None=Field(default=None,ge=0,le=1e13)
    available:bool|None=None
    hours:str=Field(default='',max_length=300)
    note:str=Field(default='',max_length=2000)
@app.post('/api/seller-events')
def seller_event(body:EventBody,req:Request):
    u=session(req,body.session_id)
    try:agent.contact(body.shop_id,body.product_id)
    except ValueError:raise HTTPException(404)
    mem.event(u,body.session_id,body.model_dump())
    if body.status=='reported' and body.price:fresh.crowd(u,body.shop_id,body.product_id,None,body.price)  # counts globally only when shoppers agree
    r=agent.alternatives(u,body.session_id,body.product_id)
    r=agent.overlay(u,body.session_id,r);mem.results(u,body.session_id,r)
    return {'events':mem.events(u,body.session_id),'results':r,'source':'user_reported'}

class SimulationBody(BaseModel):
    session_id:str;shop_id:str;product_id:str
    scenario:Literal['available','unavailable','no_answer','closed','price_changed','variant_mismatch','credit_declined']
    price_toman:float|None=Field(default=None,gt=0,le=1e13)
@app.post('/api/simulate-seller')
def simulate(body:SimulationBody,req:Request):
    u=session(req,body.session_id)
    from seller_simulation import simulate as run_simulation
    try:return run_simulation(agent,u,body.session_id,body.shop_id,body.product_id,body.scenario,body.price_toman)
    except ValueError as e:raise HTTPException(422,str(e))

class ResetSimulationBody(BaseModel):session_id:str
@app.post('/api/simulate-seller/reset')
def reset_simulation(body:ResetSimulationBody,req:Request):
    u=session(req,body.session_id)
    prior=next((e for e in mem.events(u,body.session_id) if e['source']=='simulated'),None)
    with mem.conn() as db:db.execute("delete from seller_events where user_id=? and session_id=? and source='simulated'",(u,body.session_id))
    result=agent.alternatives(u,body.session_id,prior['product_id']) if prior else agent.overlay(u,body.session_id,mem.results(u,body.session_id))
    mem.results(u,body.session_id,result)
    return {'results':result,'events':mem.events(u,body.session_id),'notice':'سناریوهای ساختگی همین سفر پاک شدند؛ گزارش‌های واقعی کاربر حفظ شدند.'}

class RouteBody(BaseModel):
    shop_ids:list[str]=Field(max_length=4)
    session_id:str
@app.post('/api/route')
async def route(body:RouteBody,req:Request):
    u=session(req,body.session_id);fresh.demand([{'shop_id':s} for s in body.shop_ids],'visit')  # an upcoming visit raises refresh priority
    return await features.plan(u,body.session_id,body.shop_ids)

class RequestBody(BaseModel):
    session_id:str;offer_id:str
    hold_until:str|None=None
@app.post('/api/merchant-requests')
def merchant_request(body:RequestBody,req:Request):
    u=session(req,body.session_id);rate(req,'merchant_request',100)
    base=str(req.base_url)
    prefix=req.headers.get('x-forwarded-prefix','')
    if prefix:base=base.rstrip('/')+prefix+'/'
    else:base=os.getenv('PUBLIC_BASE_URL') or base
    try:return features.create_request(u,body.session_id,body.offer_id,body.hold_until,base)
    except ValueError as e:raise HTTPException(422,str(e))

@app.get('/api/merchant-requests/{token}')
def merchant_view(token:str):
    try:return features.public_request(token)
    except ValueError as e:raise HTTPException(404,str(e))

class IndoorBody(BaseModel):
    entrance:str=Field(default='',max_length=500);floor:str=Field(default='',max_length=100);unit:str=Field(default='',max_length=100)
    landmark:str=Field(default='',max_length=500);step_free:Literal['','بله','خیر']='';elevator:Literal['','بله','خیر']='';photo_url:str=Field(default='',max_length=1000)
class PaymentBody(BaseModel):
    upfront:float|None=Field(default=None,ge=0,le=1e13);monthly:float|None=Field(default=None,ge=0,le=1e13)
    months:int|None=Field(default=None,ge=1,le=120);fees:float|None=Field(default=None,ge=0,le=1e13)
class MerchantReplyBody(BaseModel):
    available:bool;exact_variant:str=Field(default='',max_length=300);price_toman:float|None=Field(default=None,ge=0,le=1e13)
    valid_until:str=Field(max_length=50);ready_at:str|None=Field(default=None,max_length=50)
    accept_hold:bool=False;hold_until:str|None=Field(default=None,max_length=50)
    open_at:str|None=Field(default=None,max_length=50);close_at:str|None=Field(default=None,max_length=50)
    hours:str=Field(default='',max_length=300);warranty:str=Field(default='',max_length=500);note:str=Field(default='',max_length=1000)
    indoor:IndoorBody=Field(default_factory=IndoorBody);payment:PaymentBody=Field(default_factory=PaymentBody)
@app.post('/api/merchant-requests/{token}/reply')
def merchant_reply(token:str,body:MerchantReplyBody,req:Request):
    rate(req,'merchant_reply',100)
    try:return features.merchant_reply(token,body.model_dump())
    except (ValueError,TypeError,KeyError) as e:raise HTTPException(422,str(e))

# ---- «تازه‌سازی قیمت»: seller one-tap links, refresh queue, crowd reports, web check, estimates
def public_base(req):
    prefix=req.headers.get('x-forwarded-prefix','')
    return str(req.base_url).rstrip('/')+prefix+'/' if prefix else os.getenv('PUBLIC_BASE_URL') or str(req.base_url)
class OffersBody(BaseModel):
    session_id:str;offer_ids:list[str]=Field(default_factory=list,max_length=30)
@app.post('/api/offers/refresh')
def offers_refresh(body:OffersBody,req:Request):
    """Current price, basis and freshness of plan items: seller updates flow back into the plan and tracker."""
    u=session(req,body.session_id,read=True)
    if not body.offer_ids:return {'offers':[]}
    r=cat.search(profile={},offer_ids=body.offer_ids,seller_events=mem.events(u,body.session_id),full=True)
    fresh.demand(r['offers'],'plan',30);out=[]
    for o in r['offers']:
        if o.get('price_confirmed_today') and o.get('price_basis')=='merchant_link':
            try:
                if (features.followups(u,body.session_id).get(o['shop_id']) or {}).get('status')!='confirmed':features.set_followup(u,body.session_id,o['shop_id'],'confirmed',o['product_id'],'price_link')
            except ValueError:pass
        out.append({k:o.get(k) for k in CARD_FIELDS+('price_confirmed_at','price_confirmed_today','freshness_label','price_estimate','price_stale','price_source_url')})
    return {'offers':out,'missing':[i for i in body.offer_ids if i not in {o['offer_id'] for o in out}]}
class AskBody(BaseModel):
    session_id:str;shop_id:str=Field(max_length=80)
@app.post('/api/freshness/ask')
def freshness_ask(body:AskBody,req:Request):
    u=session(req,body.session_id,read=True);rate(req,'price_ask',60)
    try:r=fresh.ask(body.shop_id,public_base(req),'shopper')
    except ValueError as e:raise HTTPException(404,str(e))
    try:features.set_followup(u,body.session_id,body.shop_id,'asked',channel='price_link')
    except ValueError:pass
    return r
@app.get('/api/shop-link/{token}')
def shop_link(token:str):
    try:return fresh.shop_page(token)
    except ValueError as e:raise HTTPException(404,str(e))
class ShopUpdateItem(BaseModel):
    offer_id:str=Field(max_length=80);action:Literal['same','price','unavailable'];price:float|None=Field(default=None,ge=0,le=1e13)
class ShopUpdateBody(BaseModel):
    items:list[ShopUpdateItem]=Field(min_length=1,max_length=60)
@app.post('/api/shop-link/{token}')
def shop_link_update(token:str,body:ShopUpdateBody,req:Request):
    rate(req,'shop_link',300)
    try:return fresh.shop_update(token,[i.model_dump() for i in body.items],req.headers.get('user-agent',''))
    except ValueError as e:raise HTTPException(422,str(e))
@app.get('/api/freshness/queue')
def freshness_queue(limit:int=30):
    return {'queue':fresh.queue(min(max(limit,1),60)),'stats':fresh.stats()}
class AutoAskBody(BaseModel):
    top:int=Field(default=5,ge=1,le=10)
@app.post('/api/freshness/auto-ask')
def freshness_auto_ask(body:AutoAskBody,req:Request):
    rate(req,'price_auto_ask',30);return {'sent':fresh.auto_ask(public_base(req),body.top)}
class WebCheckBody(BaseModel):
    offer_id:str=Field(max_length=80)
@app.post('/api/freshness/web-check')
async def freshness_web_check(body:WebCheckBody,req:Request):
    rate(req,'price_web_check',20)
    try:return await fresh.web_check(body.offer_id)
    except ValueError as e:raise HTTPException(404,str(e))
    except llm.LLMError:raise HTTPException(503,'بررسی وب الان در دسترس نیست؛ کمی بعد دوباره امتحان کنید.')

@app.get('/api/quotes/{sid}')
def quotes(sid:str,req:Request):return {'quotes':features.quotes(session(req,sid),sid)}

class MissionBody(BaseModel):session_id:str;values:dict
@app.patch('/api/mission')
def mission(body:MissionBody,req:Request):
    try:return features.mission(session(req,body.session_id),body.session_id,body.values)
    except (ValueError,TypeError) as e:raise HTTPException(422,str(e))

class BasketBody(BaseModel):
    session_id:str;query:str=Field(min_length=1,max_length=200);product_id:str|None=None;quantity:int=Field(default=1,ge=1,le=20)
@app.post('/api/basket')
def basket_add(body:BasketBody,req:Request):
    try:return {'items':features.add_item(session(req,body.session_id),body.session_id,body.query,body.product_id,body.quantity)}
    except ValueError as e:raise HTTPException(422,str(e))
@app.delete('/api/basket/{sid}/{item_id}')
def basket_delete(sid:str,item_id:str,req:Request):
    u=session(req,sid)
    with mem.conn() as c:c.execute('delete from basket_items where id=? and user_id=? and session_id=?',(item_id,u,sid))
    return {'items':features.basket(u,sid)}
@app.get('/api/basket/{sid}/plans')
def basket_plans(sid:str,req:Request):return features.basket_plans(session(req,sid),sid)

class CostBody(BaseModel):session_id:str;offer_ids:list[str]=Field(max_length=12);values:dict=Field(default_factory=dict)
@app.post('/api/costs')
def costs(body:CostBody,req:Request):
    try:return features.costs(session(req,body.session_id),body.session_id,body.offer_ids,body.values)
    except (ValueError,TypeError) as e:raise HTTPException(422,str(e))

class ClarificationBody(BaseModel):
    session_id:str;key:str=Field(max_length=150);value:str|float|None=None;skip:bool=False;strict:bool=True
@app.post('/api/clarification')
def answer_clarification(body:ClarificationBody,req:Request):
    u=session(req,body.session_id)
    with mem.conn() as c:
        if c.execute("select 1 from assistant_runs where user_id=? and session_id=? and status in ('queued','running')",(u,body.session_id)).fetchone():raise HTTPException(409,'پس از پایان پاسخ، این پرسش را پاسخ دهید')
    try:return features.clarification.answer(u,body.session_id,body.key,body.value,body.skip,body.strict)
    except ValueError as e:raise HTTPException(422,str(e))

@app.get('/api/decision/{sid}')
def decision(sid:str,req:Request):return features.decision(session(req,sid),sid)

class ImageBody(BaseModel):session_id:str;image:str=Field(max_length=2_700_000);media_type:str
@app.post('/api/identify-image')
async def identify_image(body:ImageBody,req:Request):
    session(req,body.session_id);rate(req,'vision',100)
    try:return await features.identify(body.image,body.media_type)
    except ValueError as e:raise HTTPException(422,str(e))

@app.get('/api/shopper-profiles')
def shopper_profiles(req:Request):return {'profiles':features.profiles(uid(req))}
class ShopperBody(BaseModel):session_id:str;name:str=Field(min_length=1,max_length=80)
@app.post('/api/shopper-profiles')
def save_shopper_profile(body:ShopperBody,req:Request):
    u=session(req,body.session_id);preferences=mem.profile(u,body.session_id)
    for key in ('lat','lon','visit_at'):preferences.pop(key,None)
    with mem.conn() as c:c.execute('insert into shopper_profiles values(?,?,?,?)',(secrets.token_urlsafe(16),u,body.name,json.dumps(preferences,ensure_ascii=False)))
    return {'profiles':features.profiles(u)}
class ApplyProfileBody(BaseModel):session_id:str
@app.post('/api/shopper-profiles/{pid}/apply')
def apply_shopper_profile(pid:str,body:ApplyProfileBody,req:Request):
    u=session(req,body.session_id)
    with mem.conn() as c:r=c.execute('select * from shopper_profiles where id=? and user_id=?',(pid,u)).fetchone()
    if not r:raise HTTPException(404)
    values=json.loads(r['preferences']);values['recipient']=r['name']
    mem.update(u,[{'key':k,'value':v,'quote':'انتخاب پروفایل ذخیره‌شده'} for k,v in values.items()],sid=body.session_id)
    return {'facts':mem.facts(u,body.session_id)}
@app.delete('/api/shopper-profiles/{pid}')
def delete_shopper_profile(pid:str,req:Request):
    u=uid(req)
    with mem.conn() as c:c.execute('delete from shopper_profiles where id=? and user_id=?',(pid,u))
    return {'profiles':features.profiles(u)}

class ChatBody(BaseModel):
    session_id:str;message:str=Field(min_length=1,max_length=5000)
    mode:Literal['simple','advanced']|None=None
@app.post('/api/chat-runs')
async def start_chat_run(body:ChatBody,req:Request):
    u=session(req,body.session_id)
    selected=body.mode or mem.session(u,body.session_id)['chat_mode']
    try:accepted=runs.start(u,body.session_id,'shopping',lambda:guard.guarded(agent,mem,u,body.session_id,body.message,selected),retry={'mode':selected})
    except ValueError as e:raise HTTPException(409,str(e))
    if devtrace.enabled():traces.note_start(accepted['run_id'],body.message,selected)
    if body.mode:
        with mem.conn() as c:c.execute('update sessions set chat_mode=? where id=? and user_id=?',(selected,body.session_id,u))
    return dict(accepted,mode=selected)
@app.get('/api/chat-runs/{rid}')
def get_chat_run(rid:str,req:Request,after:int=0):
    try:return runs.get(uid(req),rid,after)
    except KeyError:raise HTTPException(404)

@app.post('/api/chat')
async def chat(body:ChatBody,req:Request):
    u=session(req,body.session_id)
    key=(u,body.session_id)
    # Never allow overlapping turns to overwrite conversation state.
    lock=locks.setdefault(key,asyncio.Lock())
    if lock.locked():raise HTTPException(409,'پاسخ قبلی هنوز آماده نشده است')
    async def stream():
        async with lock,chat_gate:
            iterator=guard.guarded(agent,mem,u,body.session_id,body.message,body.mode or mem.session(u,body.session_id)['chat_mode']).__aiter__();pending=None
            try:
                while True:
                    if pending is None:pending=asyncio.ensure_future(iterator.__anext__())
                    ready,_=await asyncio.wait({pending},timeout=CHAT_HEARTBEAT_SECONDS)
                    if not ready:
                        yield ': keepalive\n\n'
                        continue
                    try:event=pending.result()
                    except StopAsyncIteration:break
                    pending=None
                    yield 'data: '+json.dumps(event,ensure_ascii=False)+'\n\n'
            except Exception as e:
                print('chat_failed',type(e).__name__,flush=True)
                yield 'data: '+json.dumps({'type':'error','message':'در پردازش پیام خطایی پیش آمد؛ دوباره تلاش کنید.'},ensure_ascii=False)+'\n\n'
            finally:
                if pending is not None and not pending.done():
                    pending.cancel();await asyncio.gather(pending,return_exceptions=True)
                await iterator.aclose()
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no','Cache-Control':'no-cache'})

def ops_session(req,sid):
    u=uid(req)
    try:ops.session(u,sid)
    except KeyError:raise HTTPException(404,'نشست مدیریت پیدا نشد')
    return u

@app.get('/api/ready')
@app.get('/readyz')
async def ready():
    """Dependencies: catalogue, memory DB, disk, model provider (cached probe) and queue backlog."""
    try:
        with cat.conn() as c:
            count=c.execute('select count(*) from metadata').fetchone()[0]
        ok=count>0
    except Exception:ok=False
    try:
        with mem.conn() as c:c.execute('select 1').fetchone()
        db_ok=True
    except Exception:db_ok=False
    import shutil
    disk=shutil.disk_usage(DATA);queue=jobs.metrics()
    try:provider=await asyncio.wait_for(llm.health(),6)
    except Exception:provider={'provider':llm.active(),'provider_status':'unknown'}
    ready_ok=ok and db_ok
    return JSONResponse({'ready':ready_ok,'catalog_ready':ok,'database_ready':db_ok,'persistent_data_dir':str(DATA),'disk_free_mb':disk.free//2**20,
                         'provider_configured':llm.configured(),'provider':provider.get('provider'),'provider_status':provider.get('provider_status'),'model':provider.get('model'),
                         'queue':queue,'circuits':{k:v['state'] for k,v in resilience.circuits().items() if not k.startswith('web:') or v['state']!='closed'},'release':RELEASE},
                        status_code=200 if ready_ok else 503)

def admin(req):
    """Admin endpoints require Authorization: Bearer $ADMIN_TOKEN (disabled when unset)."""
    token=os.getenv('ADMIN_TOKEN','')
    if not token:raise HTTPException(404)
    if not secrets.compare_digest(req.headers.get('authorization',''),'Bearer '+token):
        obs.log('auth.fail','warn','admin token rejected',route=req.url.path);raise HTTPException(401,'دسترسی مدیر لازم است')

@app.get('/api/admin/queue')
def admin_queue(req:Request):
    admin(req);return {'metrics':jobs.metrics(),'circuits':resilience.circuits(),'llm':llm.stats()}
@app.get('/api/admin/jobs')
def admin_jobs(req:Request,state:str|None='dead',kind:str|None=None,limit:int=50):
    admin(req);return {'jobs':jobs.list(state if state!='all' else None,kind,limit)}
@app.get('/api/admin/jobs/{job_id}')
def admin_job(job_id:str,req:Request):
    admin(req);job=jobs.get(job_id)
    if not job:raise HTTPException(404,'کار پیدا نشد')
    return job
@app.post('/api/admin/jobs/{job_id}/replay')
def admin_replay(job_id:str,req:Request):
    admin(req)
    try:return jobs.replay(job_id)
    except ValueError as e:raise HTTPException(409,str(e))
@app.post('/api/admin/jobs/{job_id}/discard')
def admin_discard(job_id:str,req:Request):
    admin(req)
    try:return jobs.discard(job_id)
    except ValueError as e:raise HTTPException(409,str(e))

# ---- developer console (internal testing): 404 unless DEV_CONSOLE=1; a browser reads only its own chats
def dev(req):
    if not devtrace.enabled():raise HTTPException(404)
    return uid(req)
@app.get('/api/dev/status')
def dev_status(req:Request):
    if not devtrace.enabled():raise HTTPException(404)
    return {'enabled':True,'retention_hours':devtrace.hours()}
@app.get('/api/dev/traces')
def dev_traces(req:Request,session_id:str,limit:int=15):
    u=dev(req);session(req,session_id,read=True)
    return traces.session(u,session_id,min(max(limit,1),50))
@app.get('/api/dev/runs/{rid}')
def dev_run(rid:str,req:Request):
    u=dev(req)
    try:return traces.run(u,rid)
    except KeyError:raise HTTPException(404)

@app.get('/api/ops/bootstrap')
async def ops_bootstrap(req:Request):
    u=mem.user(req.cookies.get('hamrah_user'));s=ops.session(u)
    response=JSONResponse(await ops.state(u,s['id']))
    response.set_cookie('hamrah_user',u,httponly=True,samesite='lax',secure=req.headers.get('x-forwarded-proto',req.url.scheme)=='https',max_age=60*60*24*90)
    return response

@app.get('/api/ops/state/{sid}')
async def ops_state(sid:str,req:Request):return await ops.state(ops_session(req,sid),sid)

class OpsActionBody(BaseModel):session_id:str;kind:str=Field(max_length=50)
@app.post('/api/ops/actions')
def ops_propose(body:OpsActionBody,req:Request):
    u=ops_session(req,body.session_id);rate(req,'ops_proposal',150)
    try:return ops.propose(u,body.session_id,body.kind)
    except ValueError as e:raise HTTPException(422,str(e))

class OpsApprovalBody(BaseModel):session_id:str;nonce:str=Field(max_length=100);accept:bool
@app.post('/api/ops/actions/{aid}/approve')
async def ops_approve(aid:str,body:OpsApprovalBody,req:Request):
    u=ops_session(req,body.session_id);rate(req,'ops_approval',100)
    try:return await ops.approve(u,body.session_id,aid,body.nonce,body.accept)
    except KeyError:raise HTTPException(404)
    except ValueError as e:raise HTTPException(409,str(e))

@app.post('/api/ops/chat-runs')
async def ops_chat(body:ChatBody,req:Request):
    u=ops_session(req,body.session_id);rate(req,'ops_chat',40)
    message=guard.clean(body.message,guard.MAX_CHARS)
    try:return runs.start(u,body.session_id,'ops',lambda:ops.response(u,body.session_id,message))
    except ValueError as e:raise HTTPException(409,str(e))

@app.get('/api/ops/dns-guide')
def dns_guide(req:Request,domain:str=''):
    uid(req)
    try:return ops.dns_guide(domain)
    except ValueError as e:raise HTTPException(422,str(e))

@app.get('/api/export/{sid}')
def export(sid:str,req:Request):
    u=session(req,sid,read=True);s=mem.session(u,sid)
    return {'title':s['title'],'preferences':mem.facts(u,sid),'conversation':mem.turns(u,sid,1000),'offers':agent.overlay(u,sid,mem.results(u,sid)),'seller_events':mem.events(u,sid),'mission':features.mission(u,sid),'quotes':features.quotes(u,sid),'basket':features.basket(u,sid),'notice':'Dataset prices and synthetic identities; reports retain source labels.'}


class RebuildBody(BaseModel):
    session_id:str
    snapshot_id:str=Field(max_length=160)
    view_product_ids:list[str]|None=Field(default=None,min_length=2,max_length=12)
@app.post('/api/compare/rebuild')
async def rebuild_comparison(body:RebuildBody,req:Request):
    u=session(req,body.session_id)
    try:
        base=mem.comparison_snapshot(u,body.session_id,body.snapshot_id)
        from comparison import build_comparison
        data=build_comparison(cat,[],base['product_ids'])
        data['rows'][0]=next(r for r in base['rows'] if r['id']=='price')
        view=body.view_product_ids or base.get('view_product_ids',base['product_ids'])
        if any(pid not in base['product_ids'] for pid in view):raise ValueError('ستون نامعتبر')
        data.update(base_snapshot_id=base['snapshot_id'],origin_snapshot_id=base.get('origin_snapshot_id',base['snapshot_id']),view_product_ids=view,price_scope='original_snapshot_prices')
        data=mem.save_comparison(u,body.session_id,data)
        mem.save_turn(u,body.session_id,'assistant','جدول تازه همین محصول‌ها با قواعد جدید؛ اصل پاسخ قبلی حفظ شد.',{'comparison':data})
        return comparison_jobs().automatic(u,body.session_id,data)
    except ValueError as e:raise HTTPException(422,str(e))

from spec_jobs import SpecJobs
spec_jobs=SpecJobs(mem,agent,jobs)
agent.spec_jobs=spec_jobs
def comparison_jobs():
    global spec_jobs
    if spec_jobs.mem is not mem or spec_jobs.agent is not agent:spec_jobs=SpecJobs(mem,agent);agent.spec_jobs=spec_jobs
    return spec_jobs
class SpecFetchBody(BaseModel):
    retry:bool=False
    session_id:str
    snapshot_id:str=Field(max_length=160)
    view_product_ids:list[str]|None=Field(default=None,min_length=2,max_length=12)
@app.post('/api/compare/spec-fetch')
async def start_spec_fetch(body:SpecFetchBody,req:Request):
    u=session(req,body.session_id);rate(req,'spec_fetch',120)
    try:return spec_jobs.start(u,body.session_id,body.snapshot_id,body.view_product_ids,body.retry)
    except ValueError as e:raise HTTPException(422,str(e))
@app.get('/api/compare/spec-fetch/{job_id}')
def get_spec_fetch(job_id:str,session_id:str,req:Request):
    u=session(req,session_id)
    try:return spec_jobs.owned(u,session_id,job_id)
    except ValueError as e:raise HTTPException(404,str(e))

@app.post('/api/compare/spec-fetch/{job_id}/cancel')
async def cancel_spec_fetch(job_id:str,body:RebuildBody,req:Request):
    u=session(req,body.session_id)
    try:return spec_jobs.cancel(u,body.session_id,job_id)
    except ValueError as e:raise HTTPException(404,str(e))
@app.get('/api/compare/snapshot/{snapshot_id}')
def original_comparison(snapshot_id:str,session_id:str,req:Request):
    u=session(req,session_id)
    try:return mem.comparison_snapshot(u,session_id,snapshot_id)
    except ValueError as e:raise HTTPException(404,str(e))

class SearchAllBody(BaseModel):
    session_id:str
    query:str=Field(min_length=1,max_length=200)
    sort:Literal['relevance','price_asc','price_desc','distance','sellers','newest','credit']='relevance'
    page:int=Field(default=1,ge=1,le=500)
    page_size:int=Field(default=24,ge=6,le=60)
SEARCH_ALL_CACHE={}
CARD_FIELDS=('offer_id','product_id','shop_id','persian_title','category_name','image_urls','effective_price_toman','price_toman','price_basis','price_band','price_age_days','last_price_changed_at','reported_price_toman','shop_name','address','location_label','lat','lon','distance_km','opening','in_person_credit','availability','seller_report','seller_report_expired','why','warranty','inspection','image_match','identity_status')
def card_offers(offers):
    """Only what offer cards render; keeps list payloads small."""
    out=[{k:o.get(k) for k in CARD_FIELDS} for o in offers]
    for o in out:o['image_urls']=(o.get('image_urls') or [])[:1]
    return out
def _sort_all(offers,sort):
    """Every sort keeps the requested product ahead of related items and spare parts."""
    price=lambda o:o.get('effective_price_toman') or o.get('price_toman') or 0
    group=lambda o:tuple(o.get('match_group') or (0,))
    if sort=='price_asc':return sorted(offers,key=lambda o:(group(o),price(o)))
    if sort=='price_desc':return sorted(offers,key=lambda o:(group(o),-price(o)))
    if sort=='distance':return sorted(offers,key=lambda o:(group(o),o.get('distance_km') is None,o.get('distance_km') or 0,price(o)))
    if sort=='newest':
        stamp=lambda o:o.get('last_price_changed_at') or ''
        ordered=sorted(offers,key=stamp,reverse=True);return sorted(ordered,key=group)
    if sort=='credit':return sorted(offers,key=lambda o:(group(o),not o.get('in_person_credit'),price(o)))
    if sort=='sellers':
        counts={}
        for o in offers:counts[o['product_id']]=counts.get(o['product_id'],0)+1
        return sorted(offers,key=lambda o:(group(o),-counts[o['product_id']],price(o)))
    return list(offers)
@app.post('/api/search/all')
async def search_all(body:SearchAllBody,req:Request):
    """All retrieved offers for a query, sorted and paged for the results list."""
    u=session(req,body.session_id);rate(req,'search',500)
    profile=mem.profile(u,body.session_id);events=mem.events(u,body.session_id)
    exclusions=agent.exclusions(u,body.session_id);pairs=agent.unavailable_pairs(u,body.session_id)
    key=json.dumps([u,body.session_id,body.query,profile,len(events),sorted(exclusions),pairs],sort_keys=True,ensure_ascii=False,default=str)
    hit=SEARCH_ALL_CACHE.get(key)
    if not hit or time.time()-hit[0]>90:
        # Search already applies seller reports and eligibility; the page slice gets the overlay.
        result=await asyncio.to_thread(cat.search,body.query,profile,3000,exclude_shops=exclusions,exclude_pairs=pairs,seller_events=events,full=True)
        if len(SEARCH_ALL_CACHE)>40:SEARCH_ALL_CACHE.clear()
        SEARCH_ALL_CACHE[key]=hit=(time.time(),result)
    result=hit[1];offers=_sort_all(result['offers'],body.sort)
    total=len(offers);pages=max(1,-(-total//body.page_size));page=min(body.page,pages)
    start=(page-1)*body.page_size
    page_offers=card_offers(agent.overlay(u,body.session_id,{'offers':offers[start:start+body.page_size],'curated':True})['offers'])
    return {'offers':page_offers,'total':total,'page':page,'pages':pages,'page_size':body.page_size,'sort':body.sort,'query':body.query,
            'relaxed_terms':result.get('relaxed_terms',[]),'distance_available':profile.get('lat') is not None,'matching_offers':result.get('matching_offers',total),
            'products':len({o['product_id'] for o in offers})}

@app.get('/api/shops/{shop_id}')
async def shop_details_page(shop_id:str,session_id:str,req:Request,query:str=''):
    """Everything for the store page: record, opening, payments, indoor details and offers."""
    u=session(req,session_id)
    shop=cat.shop(shop_id)
    if not shop:raise HTTPException(404,'فروشگاه پیدا نشد')
    profile=mem.profile(u,session_id);query=query[:200]
    matching=await asyncio.to_thread(cat.shop_offers,shop_id,query,profile,40) if query.strip() else []
    others=await asyncio.to_thread(cat.shop_offers,shop_id,'',profile,24)
    seen={o['offer_id'] for o in matching};others=[o for o in others if o['offer_id'] not in seen]
    events=mem.events(u,session_id)
    reports=[e for e in events if e.get('shop_id')==shop_id][:10]
    try:location=features.locations.result(u,session_id,shop_id)
    except ValueError:location=None
    with cat.conn() as c:total=c.execute('select count(*) from offers where shop_id=?',(shop_id,)).fetchone()[0]
    keep=('shop_id','name','address','location_label','lat','lon','contacts','payment_options','weekly_hours','category_names','instagram','website_url','supports_fast_delivery','has_credit','opening','maps_url')
    return {'shop':{k:shop.get(k) for k in keep},'location':location,'matching_offers':card_offers(matching),'other_offers':card_offers(others),'offer_count':total,'query':query,'reports':reports,
            'directions_url':'https://www.google.com/maps/dir/?'+__import__('urllib.parse').parse.urlencode({'api':1,'destination':f"{shop['lat']},{shop['lon']}" if shop.get('lat') else shop['address']+' تهران'}),
            'notice':'نام و شماره بیشتر فروشگاه‌ها در دیتاست ساختگی است؛ قیمت ثبت‌شده و ساعت کاری را پیش از مراجعه تأیید کنید.'}

@app.get('/api/products/{product_id}')
async def product_page(product_id:str,session_id:str,req:Request):
    """Product page: record, images, raw specs by section and every seller offer."""
    u=session(req,session_id)
    product=cat.product(product_id)
    if not product:raise HTTPException(404,'محصول پیدا نشد')
    profile=mem.profile(u,session_id)
    sellers_profile={k:v for k,v in profile.items() if k not in ('budget_min_toman','budget_max_toman','radius_km','neighborhood','open_now')}
    result=await asyncio.to_thread(cat.search,'',sellers_profile,200,product_ids=[product_id],seller_events=mem.events(u,session_id),full=True)
    offers=agent.overlay(u,session_id,result).get('offers',[])
    offers.sort(key=lambda o:o.get('effective_price_toman') or o.get('price_toman') or 0)
    sections={}
    for spec in product.get('specs',[]):
        if spec.get('value') in (None,'','null'):continue
        sections.setdefault(spec.get('section') or 'مشخصات',[]).append({'key':spec.get('key'),'value':spec.get('value')})
    prices=[o.get('effective_price_toman') or o.get('price_toman') for o in offers if (o.get('effective_price_toman') or o.get('price_toman'))]
    return {'product':{k:product.get(k) for k in ('product_id','persian_title','brand_name','category_name','model','image_urls')},
            'sections':[{'title':k,'specs':v} for k,v in sections.items()],'offers':card_offers(offers),
            'price_min':min(prices) if prices else None,'price_max':max(prices) if prices else None,'seller_count':len({o['shop_id'] for o in offers})}

@app.post('/api/products/{product_id}/specs')
async def product_specs_start(product_id:str,session_id:str,req:Request):
    """Harmonize this product's specs and complete basic gaps from the web (same engine as comparisons)."""
    u=session(req,session_id);rate(req,'product_specs',600)
    product=cat.product(product_id)
    if not product:raise HTTPException(404,'محصول پیدا نشد')
    key='pspecs:'+obs.digest(u,16)+':'+session_id+':'+product_id;old=jobs.by_key(key)
    # A finished job without a harmonized table is recomputed; live/completed ones are reused.
    stale=old and old['state']=='succeeded' and (old['result'] or {}).get('status')!='completed'
    job=jobs.enqueue('product_specs',{'u':u,'s':session_id,'product_id':product_id},idem_key=key,reuse_seconds=0 if stale else None)
    return product_spec_view(job)
RETRY_MESSAGE='در صف تلاش مجدد؛ سرویس موقتاً در دسترس نیست و کار به‌صورت خودکار دوباره انجام می‌شود.'
def queue_status(job,done):
    return {'queued':'running','running':'running','retrying':'retry_queued','succeeded':done}.get(job['state'],'failed')
def product_spec_view(job):
    result=job['result'] or {};status=queue_status(job,result.get('status','completed'))
    out={'id':job['id'],'product_id':job['payload'].get('product_id'),'status':status,'created':job['created'],'stats':job['data'].get('stats',{}),
         'preview':job['data'].get('preview'),'result':result.get('result'),'progress':job['progress'][-20:]}
    if status=='retry_queued':out.update(message=RETRY_MESSAGE,retry_at=job['next_at'])
    return out
async def run_product_specs(payload,job):
    from spec_harmonizer import Harmonizer
    product=cat.product(payload['product_id'])
    if not product:raise resilience.NonRetryable('product not found')
    base={'version':2,'created':time.time(),'product_ids':[payload['product_id']],'products':[{k:product.get(k) for k in ('product_id','persian_title','brand_name','category_name','model')}],
          'rows':[{'id':'price','label':'کمترین قیمت دیده‌شده','unit':'toman','cells':[{'value':'—','numeric':None,'status':'missing','source':'dataset','raw':[]}]}],'detailed_rows':[],'charts':[],'reviews':[]}
    def preview(result,stats):jobs.update_data(job['id'],stats=dict(stats,at=time.time()),**({'preview':result} if result is not None else {}))
    out=await Harmonizer(Path(mem.path).parent/'public-research.db').complete(cat,base,[payload['product_id']],lambda e:jobs.progress(job['id'],e,20),on_preview=preview)
    # No table while a model provider is configured means it was unavailable: retry later.
    if out is None and llm.configured():raise resilience.Retryable('harmonizer unavailable')
    return {'status':'completed' if out else 'unavailable','result':out}
jobs.register('product_specs',run_product_specs,max_attempts=3,concurrency=3,timeout=600,base=20.0,cap=300.0)
@app.get('/api/products/specs/{job_id}')
def product_specs_status(job_id:str,session_id:str,req:Request):
    u=session(req,session_id);job=jobs.get(job_id)
    if not job or job['kind']!='product_specs' or (job['payload'].get('u'),job['payload'].get('s'))!=(u,session_id):raise HTTPException(404,'فرایند پیدا نشد')
    return product_spec_view(job)

def _review_job(product_id):
    job=jobs.by_key('reviews:'+product_id)
    if not job:return None
    out={'product_id':product_id,'status':queue_status(job,'completed'),'created':job['created'],'progress':job['progress'],'result':job['result'],'job_id':job['id']}
    if out['status']=='retry_queued':out.update(message=RETRY_MESSAGE,retry_at=job['next_at'])
    return out
async def run_reviews(payload,job):
    from user_reviews import UserReviews
    product=cat.product(payload['product_id'])
    if not product:raise resilience.NonRetryable('product not found')
    return await UserReviews(Path(mem.path).parent/'public-research.db').collect(product,lambda e:jobs.progress(job['id'],e))
jobs.register('reviews',run_reviews,max_attempts=3,concurrency=3,timeout=300,base=20.0,cap=300.0)
@app.post('/api/products/{product_id}/reviews')
async def product_reviews_start(product_id:str,session_id:str,req:Request):
    """Real buyer reviews (Digikala + translated English owner reviews) with a grounded summary.
    Reviews are public product data, so one job per product is shared by all sessions."""
    session(req,session_id);rate(req,'product_reviews',600)
    product=cat.product(product_id)
    if not product:raise HTTPException(404,'محصول پیدا نشد')
    # One shared job per product (public data), reused for an hour; durable and retried.
    jobs.enqueue('reviews',{'product_id':product_id},idem_key='reviews:'+product_id,reuse_seconds=3600)
    return _review_job(product_id)
@app.get('/api/products/{product_id}/reviews')
def product_reviews_status(product_id:str,session_id:str,req:Request):
    session(req,session_id)
    job=_review_job(product_id)
    if not job:raise HTTPException(404,'فرایند پیدا نشد')
    return job

from web_alternatives import WebAlternativeJobs
web_alternative_jobs=WebAlternativeJobs(mem,agent,jobs)
agent.web_alternatives=web_alternative_jobs
class WebAlternativeBody(BaseModel):
    session_id:str
    query:str=Field(min_length=2,max_length=200)
@app.post('/api/web-alternatives')
async def start_web_alternatives(body:WebAlternativeBody,req:Request):
    u=session(req,body.session_id);rate(req,'web_alternatives',200)
    try:return web_alternative_jobs.start(u,body.session_id,body.query)
    except ValueError as e:raise HTTPException(422,str(e))
@app.get('/api/web-alternatives/{job_id}')
def web_alternatives_status(job_id:str,session_id:str,req:Request):
    u=session(req,session_id)
    try:return web_alternative_jobs.owned(u,session_id,job_id)
    except ValueError as e:raise HTTPException(404,str(e))

DIST=ROOT/'frontend'/'dist'
if DIST.exists():
    app.mount('/assets',StaticFiles(directory=DIST/'assets'),name='assets')
    @app.get('/{path:path}')
    def frontend(path:str):
        if path.startswith('api/'):raise HTTPException(404)
        safe=(DIST/path).resolve()
        if safe.is_relative_to(DIST.resolve()) and safe.is_file():return FileResponse(safe)
        return FileResponse(DIST/'index.html')
