"""Recoverable assistant runs: a dropped HTTP connection cannot discard the answer.

When the model provider is temporarily unavailable (or the server shuts down
mid-answer) the turn is put in the durable job queue (kind chat_retry); the
run shows retry_queued and the queued answer is appended to the same run.
"""
import asyncio,json,secrets,time
import obs,devtrace
from resilience import Retryable

RETRY_MESSAGE='مدل موقتاً در دسترس نیست؛ پیام شما در صف تلاش مجدد است و پاسخ همین‌جا نمایش داده می‌شود.'
GAVE_UP='پاسخ پس از چند تلاش آماده نشد؛ لطفاً پیام را دوباره بفرستید.'


class Runs:
    def __init__(self,memory,gate=None,queue=None):
        self.mem=memory;self.gate=gate or asyncio.Semaphore(4);self.tasks={};self.queue=queue;self.replay=None;self.stopping=False
        if queue:queue.register('chat_retry',self.retry_job,max_attempts=5,concurrency=2,timeout=420,base=20.0,cap=300.0,on_retry=self.retry_waiting,on_dead=self.retry_dead)
        with self.mem.conn() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS assistant_runs(id TEXT PRIMARY KEY,user_id TEXT,session_id TEXT,kind TEXT,status TEXT,created REAL,updated REAL);
            CREATE TABLE IF NOT EXISTS assistant_run_events(run_id TEXT,user_id TEXT,seq INTEGER,payload TEXT,PRIMARY KEY(run_id,seq));
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON assistant_runs(user_id,session_id) WHERE status IN ('queued','running');''')
            c.execute("update assistant_runs set status='interrupted' where status in ('queued','running')")

    def start(self,uid,sid,kind,factory,retry=None):
        with self.mem.conn() as c:
            if c.execute("select 1 from assistant_runs where user_id=? and session_id=? and status in ('queued','running','retry_queued')",(uid,sid)).fetchone():raise ValueError('پاسخ قبلی هنوز آماده نشده است')
            rid=secrets.token_urlsafe(24);now=time.time()
            c.execute('insert into assistant_runs values(?,?,?,?,?,?,?)',(rid,uid,sid,kind,'queued',now,now))
        token=obs.run_id.set(rid)  # the run's task inherits it: every event of this turn carries run_id
        try:self.tasks[rid]=asyncio.create_task(self.drive(rid,uid,factory,dict(retry,uid=uid,sid=sid) if retry is not None else None))
        finally:obs.run_id.reset(token)
        return {'run_id':rid,'status':'queued'}

    def append(self,rid,uid,event):
        with self.mem.conn() as c:
            seq=c.execute('select coalesce(max(seq),0)+1 from assistant_run_events where run_id=?',(rid,)).fetchone()[0]
            c.execute('insert into assistant_run_events values(?,?,?,?)',(rid,uid,seq,json.dumps(event,ensure_ascii=False)))
            status='completed' if event.get('type')=='done' else 'failed' if event.get('type')=='error' else 'retry_queued' if event.get('type')=='retry_queued' else 'running'
            c.execute('update assistant_runs set status=?,updated=? where id=?',(status,time.time(),rid))

    def queue_retry(self,rid,uid,retry,reason,delay=20.0):
        # The first retry waits a little: the provider was just failing.
        job=self.queue.enqueue('chat_retry',{'rid':rid,'uid':uid,'sid':retry['sid'],'mode':retry.get('mode','advanced')},idem_key='chat:'+rid,delay=delay)
        devtrace.link_job((job or {}).get('id'),rid)
        self.append(rid,uid,{'type':'retry_queued','message':RETRY_MESSAGE})
        obs.log('chat.retry_queued','warn',reason,run_id=rid)

    async def drive(self,rid,uid,factory,retry=None):
        finished=False;can_retry=bool(self.queue and self.replay and retry)
        try:
            async with self.gate,asyncio.timeout(360):
                async for event in factory():
                    if event.get('type')=='error' and event.get('retryable') and can_retry:
                        self.queue_retry(rid,uid,retry,'provider unavailable');finished=True;break
                    self.append(rid,uid,event)
                    if event.get('type') in ('done','error'):finished=True
                if not finished:self.append(rid,uid,{'type':'error','message':'پاسخ کامل نشد؛ وضعیت ذخیره شده و می‌توانید دوباره تلاش کنید.'})
        except asyncio.CancelledError:
            if self.stopping and can_retry and not finished:
                # Graceful shutdown: persist the unanswered turn for the next process.
                self.queue_retry(rid,uid,retry,'shutdown',0.0)
            else:
                with self.mem.conn() as c:c.execute("update assistant_runs set status='cancelled' where id=?",(rid,))
            raise
        except Exception as e:
            obs.error('chat.run_failed',e,run_id=rid)
            self.append(rid,uid,{'type':'error','message':'پاسخ کامل نشد؛ اطلاعات ثبت‌شده باقی است. دوباره تلاش کنید.'})
        finally:self.tasks.pop(rid,None)

    async def retry_job(self,payload,job):
        rid,uid,sid=payload['rid'],payload['uid'],payload['sid']
        turns=self.mem.turns(uid,sid,1)
        if not turns or turns[-1]['role']!='user':return {'run_id':rid,'skipped':'nothing to answer'}
        with self.mem.conn() as c:c.execute("update assistant_runs set status='running',updated=? where id=?",(time.time(),rid))
        token=obs.run_id.set(rid)
        try:
            async with self.gate:
                async for event in self.replay(uid,sid,payload.get('mode','advanced')):
                    if event.get('type')=='error' and event.get('retryable'):raise Retryable('model provider still unavailable')
                    self.append(rid,uid,event)
        finally:obs.run_id.reset(token)
        return {'run_id':rid}
    def retry_waiting(self,job,delay,error):
        p=job['payload'];self.append(p['rid'],p['uid'],{'type':'retry_queued','message':RETRY_MESSAGE,'retry_in_seconds':round(delay)})
    def retry_dead(self,job,error):
        p=job['payload'];self.mem.save_turn(p['uid'],p['sid'],'assistant',GAVE_UP);self.append(p['rid'],p['uid'],{'type':'error','message':GAVE_UP})

    def get(self,uid,rid,after=0):
        with self.mem.conn() as c:
            run=c.execute('select * from assistant_runs where id=? and user_id=?',(rid,uid)).fetchone()
            if not run:raise KeyError('Run not found')
            rows=c.execute('select seq,payload from assistant_run_events where run_id=? and user_id=? and seq>? order by seq limit 100',(rid,uid,max(0,after))).fetchall()
        return {'run_id':rid,'status':run['status'],'events':[{'seq':r['seq'],'event':json.loads(r['payload'])} for r in rows]}

    async def cancel_user(self,uid):
        with self.mem.conn() as c:ids=[r[0] for r in c.execute('select id from assistant_runs where user_id=?',(uid,))]
        tasks=[self.tasks[rid] for rid in ids if rid in self.tasks]
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
