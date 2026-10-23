"""Owner-isolated persisted progress for explicit comparison-spec jobs.
Execution goes through the durable job queue (kind spec_completion): transient
failures show retry_queued and are retried; jobs survive restarts."""
import asyncio,json,secrets,time,hashlib,copy
import obs
from jobqueue import JobQueue
from resilience import retryable
from pathlib import Path
from comparison import build_comparison
from product_data import resolve,VERSION
from spec_fetcher import SpecFetcher
from basic_specs import overlay
from catalogue_first import CatalogueFirst
from spec_harmonizer import Harmonizer,VERSION as HARMONIZER_VERSION

class SpecJobs:
 def __init__(self,mem,agent,queue=None):
  self.mem=mem;self.agent=agent;self.tasks={};self.gate=asyncio.Semaphore(2);self.queue=queue or JobQueue(mem.path)
  self.queue.register('spec_completion',self.handle,max_attempts=3,concurrency=2,timeout=900,base=20.0,cap=300.0,on_retry=self.retrying,on_dead=self.dead)
  with mem.conn() as db:
   db.execute('CREATE TABLE IF NOT EXISTS spec_fetch_jobs(id TEXT PRIMARY KEY,user_id TEXT,session_id TEXT,status TEXT,created REAL,updated REAL,payload TEXT)')
   # Jobs the durable queue still owns resume after a restart; the rest were interrupted.
   db.execute("""UPDATE spec_fetch_jobs SET status='interrupted' WHERE status IN ('queued','running','retry_queued') AND id NOT IN
                 (SELECT json_extract(payload,'$.jid') FROM job_queue WHERE kind='spec_completion' AND state IN ('queued','running','retrying'))""")
 def owned(self,u,s,jid):
  self.mem.session(u,s)
  with self.mem.conn() as db:r=db.execute('select * from spec_fetch_jobs where id=? and user_id=? and session_id=?',(jid,u,s)).fetchone()
  if not r:raise ValueError('فرایند تکمیل مشخصات پیدا نشد.')
  row=dict(r);data=json.loads(row.pop('payload'));return dict(row,**data)
 def progress(self,jid,event):
  with self.mem.conn() as db:
   row=db.execute('select payload from spec_fetch_jobs where id=?',(jid,)).fetchone()
   if not row:return
   data=json.loads(row[0]);data['progress']=(data.get('progress',[])+[dict(event,at=time.time())])[-120:]
   db.execute('update spec_fetch_jobs set payload=?,updated=? where id=?',(json.dumps(data,ensure_ascii=False),time.time(),jid))
 def preview(self,jid,result,stats):
  # Intermediate tables/stats for the live progress panel; never a saved snapshot.
  with self.mem.conn() as db:
   row=db.execute('select payload from spec_fetch_jobs where id=?',(jid,)).fetchone()
   if not row:return
   data=json.loads(row[0]);data['stats']=dict(stats,at=time.time())
   if result is not None:
    result=dict(result);result.pop('snapshot_id',None);result.pop('spec_job',None)
    data['preview']=result;data['preview_version']=data.get('preview_version',0)+1
   db.execute('update spec_fetch_jobs set payload=?,updated=? where id=?',(json.dumps(data,ensure_ascii=False),time.time(),jid))
 def finish(self,jid,status,**extra):
  with self.mem.conn() as db:
   row=db.execute('select payload from spec_fetch_jobs where id=?',(jid,)).fetchone()
   if not row:return
   data=json.loads(row[0]);data.update(extra);db.execute('update spec_fetch_jobs set status=?,payload=?,updated=? where id=?',(status,json.dumps(data,ensure_ascii=False),time.time(),jid))
 def start(self,u,s,snapshot_id,view,retry=False):
  base=self.mem.comparison_snapshot(u,s,snapshot_id);ids=list(dict.fromkeys(view or base.get('view_product_ids',base['product_ids'])))
  if not 2<=len(ids)<=12 or any(pid not in base['product_ids'] for pid in ids):raise ValueError('دو تا دوازده ستون از همین مقایسه انتخاب کنید.')
  root=base.get('origin_snapshot_id',snapshot_id)
  identity=hashlib.sha256(json.dumps([root,sorted(ids),VERSION,HARMONIZER_VERSION]).encode()).hexdigest()
  with self.mem.conn() as db:
   rows=db.execute('select id,status,payload from spec_fetch_jobs where user_id=? and session_id=? order by created desc',(u,s)).fetchall()
   existing=next((r for r in rows if json.loads(r['payload']).get('identity')==identity),None)
   if existing and (not retry or existing['status'] in ('queued','running','completed')):return self.owned(u,s,existing['id'])
   if sum(r['status'] in ('queued','running','retry_queued') for r in rows)>=2:raise ValueError('دو فرایند تکمیل در جریان است؛ پس از پایان دوباره تلاش کنید.')
   jid=secrets.token_urlsafe(24);now=time.time();db.execute('insert into spec_fetch_jobs values(?,?,?,?,?,?,?)',(jid,u,s,'queued',now,now,json.dumps({'progress':[],'snapshot_id':snapshot_id,'origin_snapshot_id':root,'identity':identity,'view_product_ids':ids})))
  job=self.queue.enqueue('spec_completion',{'jid':jid,'u':u,'s':s,'snapshot_id':snapshot_id,'ids':ids},idem_key='spec:'+jid)
  task=self.queue.tasks.get(job['id'])
  if task:self.tasks[jid]=task;task.add_done_callback(lambda _:self.tasks.pop(jid,None))
  return self.owned(u,s,jid)
 async def handle(self,p,job):
  base=self.mem.comparison_snapshot(p['u'],p['s'],p['snapshot_id'])
  await self.run(p['jid'],p['u'],p['s'],base,p['ids']);return {'jid':p['jid']}
 def retrying(self,job,delay,error):
  self.finish(job['payload']['jid'],'retry_queued',retry_at=time.time()+delay,error='تکمیل مشخصات موقتاً ممکن نشد؛ در صف تلاش مجدد است.')
 def dead(self,job,error):
  self.finish(job['payload']['jid'],'failed',error_type=type(error).__name__,error='تکمیل مشخصات پس از چند تلاش پایان نیافت؛ نسخه قبلی حفظ شده است. دوباره تلاش کنید.')
 def automatic(self,u,s,base):
  # Automatic static completion is independent of model/deep-review budgets.
  # A completed derivative never recursively starts a new job.
  if base.get('web_enriched'):return base
  # Every new comparison gets catalogue harmonization plus web completion of
  # missing basic specs, for every category; no button press required.
  if len(base.get('product_ids',[]))<2 or not base.get('snapshot_id'):return base
  result=copy.deepcopy(base);result['auto_completion']=True
  result['spec_job']=self.start(u,s,base['snapshot_id'],base.get('view_product_ids'))
  return result
 def cancel(self,u,s,jid):
  row=self.owned(u,s,jid)
  if row['status'] in ('queued','running','retry_queued'):
   self.queue.cancel_key('spec:'+jid)
   self.finish(jid,'cancelled')
  return self.owned(u,s,jid)
 async def run(self,jid,u,s,base,ids):
  self.finish(jid,'running')
  try:
   # Normalize new projection while keeping the original price/view snapshot.
   if base.get('version',1)<2:
    normalized=build_comparison(self.agent.cat,[],base['product_ids'])
    price=next(r for r in base['rows'] if r['id']=='price');normalized['rows'][0]=price
    for k in ('origin_snapshot_id','buying_reviews'): 
     if k in base:normalized[k]=base[k]
   else:normalized=base
   for pid in ids:
    # Mandatory catalogue/hard-filter check before any public fetching.
    try:await CatalogueFirst(self.agent,u,s).for_product(pid)
    except Exception:pass
   try:harmonized=await Harmonizer(Path(self.mem.path).parent/'public-research.db').complete(self.agent.cat,normalized,ids,lambda e:self.progress(jid,e),on_preview=lambda r,st:self.preview(jid,r,st))
   except Exception as error:
    obs.error('spec.harmonizer_failed',error);harmonized=None
   if harmonized:
    result=harmonized;result.pop('snapshot_id',None);result.pop('spec_job',None);result.pop('auto_completion',None)
    result.update(created=time.time(),base_created=base['created'],base_snapshot_id=base['snapshot_id'],origin_snapshot_id=base.get('origin_snapshot_id',base['snapshot_id']),view_product_ids=ids,web_enriched=True)
    result['notice']='جدول یکسان‌سازی‌شده: هر خانه از متن کاتالوگ یا عنوان آگهی با نقل‌قول مستقیم استخراج شده و خانه‌های خالی از وب (با منبع) تکمیل شده‌اند. کاتالوگ و نسخه اولیه حفظ شده‌اند. وب موجودی یا وضعیت کالای فروشنده را ثابت نمی‌کند.'
    result=self.mem.save_comparison(u,s,result);self.mem.save_turn(u,s,'assistant','یکسان‌سازی مشخصات و تکمیل از وب؛ نسخه مستقل با منابع و تعارض‌ها.',{'comparison':result})
    self.finish(jid,'completed',result=result,preview=None,stats=dict((self.owned(u,s,jid).get('stats') or {}),stage='done',eta_seconds=0,products_done=len(ids)));return
   fetcher=SpecFetcher(Path(self.mem.path).parent/'public-research.db')
   async def product(pid):
    async with self.gate:
     # Mandatory catalogue/hard-filter check before any public fetching. This
     # does not rewrite the current shortlist or admit web-only offer variants.
     catalogue=await CatalogueFirst(self.agent,u,s).for_product(pid)
     p=self.agent.cat.product(pid)
     r=await asyncio.to_thread(fetcher.investigate,p,lambda e:self.progress(jid,dict(e,product_id=pid)))
     return {'product_id':pid,'title':p['persian_title'],'research':r,'catalogue_offer_count':len(catalogue.get('offers',[]))}
   cards=await asyncio.gather(*(product(pid) for pid in ids))
   result=overlay(normalized,cards);result.pop('snapshot_id',None)
   result.pop('spec_job',None);result.pop('auto_completion',None)
   result.update(created=time.time(),base_created=base['created'],base_snapshot_id=base['snapshot_id'],origin_snapshot_id=base.get('origin_snapshot_id',base['snapshot_id']),view_product_ids=ids,spec_research=cards,web_enriched=True)
   result['notice']='نسخه تازه با مشخصات هم‌نام‌شده و تکمیل وب. کاتالوگ و نسخه اولیه حفظ شده‌اند؛ شاهد و تعارض هر خانه را بخوانید. وب موجودی یا وضعیت همین کالای فروشنده را ثابت نمی‌کند.'
   for row in result['rows']:
    for pid,c in zip(result['product_ids'],row['cells']):
     if pid in ids and c['status']=='missing':c.update(completion_state='unavailable',value='پس از بررسی محدود، شاهد معتبر یافت نشد')
   result=self.mem.save_comparison(u,s,result);self.mem.save_turn(u,s,'assistant','تکمیل مشخصات پایه از وب؛ نسخه مستقل با منابع، محدودیت‌ها و تعارض‌ها.',{'comparison':result})
   self.finish(jid,'completed',result=result)
  except asyncio.CancelledError:
   # Graceful shutdown keeps the job queued for the next process.
   self.finish(jid,'queued' if self.queue.stopping else 'cancelled');raise
  except Exception as error:
   if retryable(error):self.finish(jid,'retry_queued',error_type=type(error).__name__,error='تکمیل مشخصات موقتاً ممکن نشد؛ در صف تلاش مجدد است.')
   else:self.finish(jid,'failed',error_type=type(error).__name__,error='تکمیل مشخصات پایان نیافت؛ نسخه قبلی حفظ شده است. دوباره تلاش کنید.')
   raise
