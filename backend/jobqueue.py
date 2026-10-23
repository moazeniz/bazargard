"""Durable SQLite job queue (outbox + dead letter) for background work that can fail.

States: queued -> running -> succeeded | retrying -> (running ...) | dead,
plus discarded (admin/cancel). Jobs carry an idempotency key; enqueueing an
existing key returns the live job instead of duplicating work. Transient
failures are retried with exponential backoff and full jitter; permanent
failures and exhausted attempts go to the dead letter (admin can replay or
discard). The app runs one process (uvicorn workers=1): on start every job
left `running` by a crash is moved back to retrying; on graceful shutdown
running jobs are cancelled and persisted as queued, without spending an
attempt. Queue metrics are logged every minute.
"""
import asyncio,json,secrets,sqlite3,time
import obs
from catalog import ClosingConnection
from resilience import retryable,backoff

ACTIVE=('queued','running','retrying')

class JobQueue:
 def __init__(self,path,metrics_every=60):
  self.path=str(path);self.handlers={};self.tasks={};self.gates={};self.stopping=False;self.worker=None;self.metrics_every=metrics_every
  self.boot=secrets.token_hex(6)
  with self.conn() as db:
   db.executescript('''CREATE TABLE IF NOT EXISTS job_queue(id TEXT PRIMARY KEY,kind TEXT,idem_key TEXT UNIQUE,state TEXT,payload TEXT,attempts INTEGER,max_attempts INTEGER,
    next_at REAL,created REAL,updated REAL,started REAL,finished REAL,last_error TEXT,error_type TEXT,result TEXT,progress TEXT,data TEXT,boot TEXT);
    CREATE INDEX IF NOT EXISTS job_queue_due ON job_queue(state,next_at);''')
   # Crash recovery: a previous process cannot still be running these.
   stale=db.execute("select id,kind from job_queue where state='running' and coalesce(boot,'')!=?",(self.boot,)).fetchall()
   db.execute("update job_queue set state='retrying',next_at=?,updated=?,last_error=coalesce(last_error,'interrupted by restart') where state='running' and coalesce(boot,'')!=?",(time.time(),time.time(),self.boot))
  for row in stale:obs.log('job.recovered','warn','running job recovered after restart',job_id=row[0],queue=row[1])
 def conn(self):
  c=sqlite3.connect(self.path,timeout=20,factory=ClosingConnection);c.row_factory=sqlite3.Row;return c

 # ---- registration / enqueue ----
 def register(self,kind,run,max_attempts=4,concurrency=3,timeout=900,base=15.0,cap=600.0,on_retry=None,on_dead=None):
  """run(payload, job) -> JSON-able result. on_retry(job, delay, error) / on_dead(job, error) update feature state."""
  self.handlers[kind]=dict(run=run,max_attempts=max_attempts,timeout=timeout,base=base,cap=cap,on_retry=on_retry,on_dead=on_dead)
  self.handlers[kind]['concurrency']=concurrency
 def enqueue(self,kind,payload,idem_key=None,max_attempts=None,reuse_seconds=None,delay=0.0,start=True):
  """Idempotent: an existing job with this key is returned unless it is dead/
  discarded or older than reuse_seconds; then the old row keeps its history."""
  if kind not in self.handlers:raise KeyError('Unknown job kind '+kind)
  now=time.time();idem_key=idem_key or kind+':'+secrets.token_hex(12)
  with self.conn() as db:
   row=db.execute('select * from job_queue where idem_key=?',(idem_key,)).fetchone()
   if row:
    fresh=reuse_seconds is None or now-row['created']<reuse_seconds or row['state'] in ACTIVE
    if row['state'] not in ('dead','discarded') and fresh:return self.public(row)
    db.execute('update job_queue set idem_key=? where id=?',(idem_key+'#'+row['id'],row['id']))
   jid=secrets.token_urlsafe(16)
   db.execute('insert into job_queue(id,kind,idem_key,state,payload,attempts,max_attempts,next_at,created,updated,progress,data) values(?,?,?,?,?,?,?,?,?,?,?,?)',
              (jid,kind,idem_key,'queued',json.dumps(payload,ensure_ascii=False),0,max_attempts or self.handlers[kind]['max_attempts'],now+delay,now,now,'[]','{}'))
  obs.log('job.enqueued','info',kind,job_id=jid,queue=kind)
  if start and not delay:self.kick(jid)
  self.ensure_worker()
  return self.get(jid)

 # ---- reading ----
 @staticmethod
 def public(row):
  d=dict(row)
  for k,default in (('payload',{}),('result',None),('progress',[]),('data',{})):
   d[k]=json.loads(d[k]) if d.get(k) else default
  return d
 def get(self,jid):
  with self.conn() as db:row=db.execute('select * from job_queue where id=?',(jid,)).fetchone()
  return self.public(row) if row else None
 def by_key(self,idem_key):
  with self.conn() as db:row=db.execute('select * from job_queue where idem_key=?',(idem_key,)).fetchone()
  return self.public(row) if row else None
 def list(self,state=None,kind=None,limit=50):
  q='select * from job_queue where 1=1'+(' and state=?' if state else '')+(' and kind=?' if kind else '')+' order by updated desc limit ?'
  with self.conn() as db:rows=db.execute(q,[x for x in (state,kind) if x]+[min(500,limit)]).fetchall()
  return [self.public(r) for r in rows]
 def metrics(self):
  now=time.time()
  with self.conn() as db:
   counts={r[0]:r[1] for r in db.execute('select state,count(*) from job_queue group by state')}
   oldest=db.execute("select min(created) from job_queue where state in ('queued','retrying')").fetchone()[0]
  return {'depth':sum(counts.get(s,0) for s in ACTIVE),'queued':counts.get('queued',0),'running':counts.get('running',0),'retrying':counts.get('retrying',0),
          'dead':counts.get('dead',0),'succeeded':counts.get('succeeded',0),'oldest_pending_seconds':round(now-oldest) if oldest else 0}

 # ---- progress / feature data written by handlers ----
 def progress(self,jid,event,keep=40):
  with self.conn() as db:
   row=db.execute('select progress from job_queue where id=?',(jid,)).fetchone()
   if not row:return
   items=(json.loads(row[0] or '[]')+[dict(event,at=time.time())])[-keep:]
   db.execute('update job_queue set progress=?,updated=? where id=?',(json.dumps(items,ensure_ascii=False),time.time(),jid))
 def update_data(self,jid,**fields):
  with self.conn() as db:
   row=db.execute('select data from job_queue where id=?',(jid,)).fetchone()
   if not row:return
   data=json.loads(row[0] or '{}');data.update(fields)
   db.execute('update job_queue set data=?,updated=? where id=?',(json.dumps(data,ensure_ascii=False),time.time(),jid))

 # ---- execution ----
 def gate(self,kind,limit):
  # One semaphore per kind and event loop (tests and reloads may use several loops).
  if not limit:return None
  key=(kind,id(asyncio.get_running_loop()))
  return self.gates.setdefault(key,asyncio.Semaphore(limit))
 def kick(self,jid):
  try:loop=asyncio.get_running_loop()
  except RuntimeError:return
  if jid in self.tasks or self.stopping:return
  task=loop.create_task(self._execute(jid));self.tasks[jid]=task;task.add_done_callback(lambda _:self.tasks.pop(jid,None))
 def _claim(self,jid):
  now=time.time()
  with self.conn() as db:
   cur=db.execute("update job_queue set state='running',attempts=attempts+1,started=?,updated=?,boot=? where id=? and state in ('queued','retrying') and next_at<=?",(now,now,self.boot,jid,now+0.001))
   if not cur.rowcount:return None
   return self.public(db.execute('select * from job_queue where id=?',(jid,)).fetchone())
 async def _execute(self,jid):
  job=self.get(jid)
  if not job or job['kind'] not in self.handlers:return
  h=self.handlers[job['kind']];gate=self.gate(job['kind'],h['concurrency'])
  async def body():
   job=self._claim(jid)
   if not job:return
   token=obs.job_id.set(jid);started=time.monotonic()
   obs.log('job.start','info',job['kind'],queue=job['kind'],attempt=job['attempts'],max_attempts=job['max_attempts'])
   try:
    result=await asyncio.wait_for(h['run'](job['payload'],job),h['timeout'])
   except asyncio.CancelledError:
    if self.stopping:
     # Graceful shutdown: persist as queued; this attempt does not count.
     with self.conn() as db:db.execute("update job_queue set state='queued',attempts=max(0,attempts-1),next_at=?,updated=? where id=?",(time.time(),time.time(),jid))
     obs.log('job.persisted','warn','persisted for restart',queue=job['kind'])
    else:
     with self.conn() as db:db.execute("update job_queue set state='discarded',finished=?,updated=?,last_error='cancelled' where id=?",(time.time(),time.time(),jid))
     obs.log('job.cancelled','info',job['kind'],queue=job['kind'])
    raise
   except Exception as exc:
    self._failed(job,h,exc,round((time.monotonic()-started)*1000))
   else:
    with self.conn() as db:db.execute("update job_queue set state='succeeded',result=?,finished=?,updated=?,last_error=null where id=?",(json.dumps(result,ensure_ascii=False,default=str),time.time(),time.time(),jid))
    obs.log('job.succeeded','info',job['kind'],queue=job['kind'],attempt=job['attempts'],duration_ms=round((time.monotonic()-started)*1000))
   finally:obs.job_id.reset(token)
  if gate:
   async with gate:await body()
  else:await body()
 def _failed(self,job,h,exc,duration):
  jid=job['id'];message=str(exc)[:300] or type(exc).__name__
  transient=retryable(exc) or isinstance(exc,asyncio.TimeoutError)
  if transient and job['attempts']<job['max_attempts']:
   delay=max(1.0,backoff(job['attempts'],h['base'],h['cap'],getattr(exc,'retry_after',None)))
   with self.conn() as db:db.execute("update job_queue set state='retrying',next_at=?,updated=?,last_error=?,error_type=? where id=?",(time.time()+delay,time.time(),message,type(exc).__name__,jid))
   obs.log('job.retry','warn',job['kind'],queue=job['kind'],attempt=job['attempts'],max_attempts=job['max_attempts'],backoff_ms=round(delay*1000),error_type=type(exc).__name__,error=message,duration_ms=duration)
   if h['on_retry']:
    try:h['on_retry'](self.get(jid),delay,exc)
    except Exception as e:obs.error('job.hook_failed',e,queue=job['kind'])
   self.ensure_worker()
  else:
   with self.conn() as db:db.execute("update job_queue set state='dead',finished=?,updated=?,last_error=?,error_type=? where id=?",(time.time(),time.time(),message,type(exc).__name__,jid))
   obs.error('job.dead_letter',exc,job['kind'],queue=job['kind'],attempt=job['attempts'],max_attempts=job['max_attempts'],duration_ms=duration)
   if h['on_dead']:
    try:h['on_dead'](self.get(jid),exc)
    except Exception as e:obs.error('job.hook_failed',e,queue=job['kind'])

 # ---- retry worker, metrics, shutdown ----
 def ensure_worker(self):
  try:loop=asyncio.get_running_loop()
  except RuntimeError:return
  if self.stopping or (self.worker and not self.worker.done() and self.worker.get_loop() is loop):return
  self.worker=loop.create_task(self.run_worker())
 def due(self):
  with self.conn() as db:
   return [r[0] for r in db.execute("select id from job_queue where state in ('queued','retrying') and next_at<=? order by next_at limit 20",(time.time(),))]
 async def run_worker(self,interval=2.0):
  last=0.0
  while not self.stopping:
   try:
    for jid in self.due():
     if jid not in self.tasks and self.get(jid)['kind'] in self.handlers:self.kick(jid)
    if time.monotonic()-last>=self.metrics_every:
     last=time.monotonic();m=self.metrics()
     obs.log('queue.metrics','info','queue depth',metric='queue.depth',value=m['depth'],queue_dead=m['dead'],**{'queue.retrying':m['retrying'],'queue.running':m['running'],'queue.oldest_pending_seconds':m['oldest_pending_seconds']})
     obs.log('queue.metrics','info','dead letters',metric='queue.dead',value=m['dead'])
   except asyncio.CancelledError:raise
   except Exception as exc:obs.error('queue.worker_error',exc)
   await asyncio.sleep(interval)
 async def shutdown(self,grace=5.0):
  """Stop picking work; give running jobs `grace` seconds, then persist them."""
  self.stopping=True
  if self.worker and not self.worker.done():self.worker.cancel()
  running=list(self.tasks.values())
  if running:
   done,pending=await asyncio.wait(running,timeout=grace)
   for t in pending:t.cancel()
   if pending:await asyncio.gather(*pending,return_exceptions=True)
  obs.log('queue.shutdown','info','queue stopped',value=len(running))

 # ---- admin ----
 def replay(self,jid):
  with self.conn() as db:
   cur=db.execute("update job_queue set state='queued',attempts=0,next_at=?,updated=?,finished=null where id=? and state in ('dead','discarded')",(time.time(),time.time(),jid))
  if not cur.rowcount:raise ValueError('Only dead or discarded jobs can be replayed')
  obs.log('job.replay','info','admin replay',job_id=jid);self.kick(jid);self.ensure_worker();return self.get(jid)
 def discard(self,jid):
  task=self.tasks.get(jid)
  if task:task.cancel()
  with self.conn() as db:cur=db.execute("update job_queue set state='discarded',finished=?,updated=? where id=? and state not in ('succeeded','discarded')",(time.time(),time.time(),jid))
  if not cur.rowcount:raise ValueError('Job not found or already succeeded')
  obs.log('job.discard','info','discarded',job_id=jid);return self.get(jid)
 def cancel_key(self,idem_key):
  job=self.by_key(idem_key)
  if job and job['state'] in ACTIVE:self.discard(job['id'])
