"""Developer console (internal testing tool): per-run traces of the AI operations.

DEV_CONSOLE=1 is the server-wide master switch: unset, every /api/dev route is a
plain 404 and nothing is stored. Traces are assembled only from the structured
obs events (llm.call, tool.call, external.call, job.*, guard.*) that carry a
run_id; obs has already redacted them and they are scrubbed again here, so keys,
the gate token, the system prompt and contact details never reach the table.
A browser reads only runs of its own chats (its user cookie); rows expire after
DEV_TRACE_HOURS (default 24).
"""
import json,os,sqlite3,threading,time
import obs

PREFIXES=('llm.','tool.','external.','job.','guard.','chat.','circuit.','run.')
DROP={'app','env','release','trace_id','span_id','session','ts','stack','system','prompt','instructions','messages','input','tools','user_id','uid'}
ACTIVE=('queued','running','retry_queued')
# Anthropic list prices, USD per million tokens (input, output); cache read 0.1x, cache write 1.25x input.
PRICES={'claude-sonnet-5-5':(2.0,10.0),'claude-haiku-5-5':(0.10,0.50)}
JOBS={}  # chat_retry job id -> run id, so queue events join their run

def enabled():return os.getenv('DEV_CONSOLE','').strip()=='1'
def hours():
    try:return max(1.0,float(os.getenv('DEV_TRACE_HOURS','24')))
    except ValueError:return 24.0

# ---------------------------------------------------------------- capture
def scrub(v,depth=0):
    if depth>6:return '…'
    if isinstance(v,dict):return {k:scrub(x,depth+1) for k,x in v.items() if k not in DROP}
    if isinstance(v,(list,tuple)):return [scrub(x,depth+1) for x in list(v)[:30]]
    if isinstance(v,str):return obs.scrub(v)[:600]
    return v

def summarise(result):
    """What the console shows for a tool result: query, filters, hits, top items, corrections."""
    if not isinstance(result,dict):return {'type':type(result).__name__}
    out={k:result[k] for k in ('error','query','purpose','matching_offers','relaxed_terms','corrected_terms','inferred_categories','mostly_accessories','candidate_limit_reached','candidate_scope','curated','skipped','message','sampling_stats') if result.get(k) not in (None,[],{},'')}
    if isinstance(result.get('filters'),dict):out['filters']={k:v for k,v in result['filters'].items() if v not in (None,[],{},'',False)}
    offers=result.get('offers')
    if isinstance(offers,list):
        out['hits']=len(offers)
        out['top']=[{k:o.get(k) for k in ('product_id','offer_id','persian_title','shop_name','effective_price_toman','distance_km','match_group') if o.get(k) is not None} for o in offers[:5] if isinstance(o,dict)]
    for k in ('products','items','tasks','results','queries','sources','citations'):
        if isinstance(result.get(k),list):out[k+'_count']=len(result[k])
    out['keys']=sorted(map(str,result))[:30]
    return out

def tool_fields(name,args,result,label=None):
    """Extra tool.call fields, only while the console is enabled (production logs stay unchanged)."""
    if not enabled():return {}
    return {'label':label,'args':args if isinstance(args,dict) else {'value':args},'result':summarise(result)}

def stop_reason(data):
    if not isinstance(data,dict):return None
    if data.get('stop_reason'):return data['stop_reason']
    if (data.get('incomplete_details') or {}).get('reason'):return data['incomplete_details']['reason']
    if any(isinstance(o,dict) and o.get('type')=='function_call' for o in data.get('output') or []):return 'tool_use'
    return 'end_turn' if data.get('status')=='completed' else data.get('status')

def price(model):
    try:custom={k:tuple(v) for k,v in json.loads(os.getenv('DEV_PRICES','{}')).items()}
    except (ValueError,TypeError):custom={}
    table={**PRICES,**custom}
    return next((v for k,v in sorted(table.items(),key=lambda kv:-len(kv[0])) if model and str(model).startswith(k)),None)

def estimate(e):
    p=price(e.get('model'))
    if not p or e.get('provider')!='anthropic':return None
    n=lambda k:e.get(k) or 0
    return round((n('tokens_in')*p[0]+n('tokens_out')*p[1]+n('tokens_cache_read')*p[0]*0.1+n('tokens_cache_write')*p[0]*1.25)/1e6,6)

def link_job(job_id,run_id):
    if not job_id:return
    JOBS[job_id]=run_id
    while len(JOBS)>500:JOBS.pop(next(iter(JOBS)))


class Traces:
    def __init__(self,path):
        self.path=path;self.lock=threading.Lock();self.pruned=0.0
        with self.conn() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS dev_trace_events(run_id TEXT,at REAL,event TEXT,data TEXT);
            CREATE INDEX IF NOT EXISTS dev_trace_run ON dev_trace_events(run_id);
            CREATE INDEX IF NOT EXISTS dev_trace_at ON dev_trace_events(at);''')
        self.prune()
    def conn(self):
        from catalog import ClosingConnection
        c=sqlite3.connect(self.path,timeout=2,factory=ClosingConnection);c.row_factory=sqlite3.Row;return c
    def prune(self):
        self.pruned=time.time()
        try:
            with self.conn() as c:c.execute('delete from dev_trace_events where at<?',(time.time()-hours()*3600,))
        except sqlite3.Error:pass

    def capture(self,record):
        """obs sink: keep run-scoped AI events. Never logs (it runs inside obs.log)."""
        if not enabled():return
        event=str(record.get('event',''))
        rid=record.get('run_id') or JOBS.get(record.get('job_id'))
        if not rid or not event.startswith(PREFIXES):return
        self.add(rid,event,record)
    def add(self,rid,event,data):
        if not enabled():return
        row=json.dumps(scrub(data),ensure_ascii=False,default=str)
        with self.lock:
            try:
                with self.conn() as c:c.execute('insert into dev_trace_events values(?,?,?,?)',(rid,time.time(),event,row))
            except sqlite3.Error:return
        if time.time()-self.pruned>600:self.prune()
    def note_start(self,rid,message,mode):
        """The run's own user message (console only; it is never written to stdout logs)."""
        self.add(rid,'run.start',{'event':'run.start','message':obs.short(message,300),'mode':mode,'chars':len(message or '')})

    # ------------------------------------------------------------ read
    def run(self,user,rid):
        with self.conn() as c:
            row=c.execute('select * from assistant_runs where id=? and user_id=?',(rid,user)).fetchone()
            if not row or row['created']<time.time()-hours()*3600:raise KeyError(rid)
            rows=c.execute('select at,event,data from dev_trace_events where run_id=? order by rowid',(rid,)).fetchall()
        return assemble(dict(row),rows)
    def session(self,user,sid,limit=15):
        with self.conn() as c:
            runs=[dict(r) for r in c.execute('select * from assistant_runs where user_id=? and session_id=? and created>=? order by created desc limit ?',(user,sid,time.time()-hours()*3600,limit))]
            rows={r['id']:c.execute('select at,event,data from dev_trace_events where run_id=? order by rowid',(r['id'],)).fetchall() for r in runs}
        out=[assemble(r,rows[r['id']]) for r in runs]
        return {'session_id':sid,'runs':out,'totals':totals(out),'retention_hours':hours(),'generated_at':time.time()}


def kind_of(event):
    if event.startswith('llm.'):return 'model'
    if event.startswith('tool.'):return 'tool'
    if event.startswith('guard.'):return 'guard'
    if event.startswith(('job.','chat.retry')):return 'queue'
    if event.startswith(('external.','circuit.')):return 'external'
    return 'run'

def assemble(run,rows):
    """One run: ordered events plus model/tool/other time and token/cost totals."""
    t0=run['created'];events=[]
    for r in rows:
        try:d=json.loads(r['data'])
        except (ValueError,TypeError):continue
        d.update(event=r['event'],kind=kind_of(r['event']),t_ms=round((r['at']-t0)*1000))
        d['start_ms']=max(0,d['t_ms']-(d.get('duration_ms') or 0)) if isinstance(d.get('duration_ms'),(int,float)) else d['t_ms']  # events are logged when they finish
        if r['event']=='llm.call':d['cost_estimate']=estimate(d) if d.get('cost') is None else None
        events.append(d)
    events.sort(key=lambda e:(e['event']!='run.start',e['start_ms']))
    tools=[e for e in events if e['event']=='tool.call']
    # An LLM call that finished inside a tool's window (web search, spec reading) is tool time.
    def nested(e):return any(t['t_ms']-(t.get('duration_ms') or 0)-5<=e['t_ms']-(e.get('duration_ms') or 0) and e['t_ms']<=t['t_ms']+5 for t in tools)
    calls=[e for e in events if e['event']=='llm.call']
    for e in calls:e['nested']=nested(e)
    model_ms=sum(e.get('duration_ms') or 0 for e in calls if not e['nested'])
    tool_ms=sum(t.get('duration_ms') or 0 for t in tools)
    live=run['status'] in ACTIVE
    wall_ms=max(0,round(((time.time() if live else run['updated'])-t0)*1000))
    n=lambda k:sum(e.get(k) or 0 for e in calls)
    totals={'model_calls':len(calls),'model_errors':sum(1 for e in calls if e.get('status')!=200),'tool_calls':len(tools),'tool_errors':sum(1 for t in tools if t.get('error')),
            'fallbacks':sum(1 for e in events if e['event']=='llm.fallback'),'guard_blocks':sum(1 for e in events if e['event']=='guard.block'),
            'tokens_in':n('tokens_in'),'tokens_out':n('tokens_out'),'tokens_cache_read':n('tokens_cache_read'),'tokens_cache_write':n('tokens_cache_write'),'reasoning_tokens':n('reasoning_tokens'),
            'cost_reported':round(n('cost'),6),'cost_estimate':round(sum(e.get('cost_estimate') or 0 for e in calls),6),
            'wall_ms':wall_ms,'model_ms':model_ms,'tool_ms':tool_ms,'nested_model_ms':sum(e.get('duration_ms') or 0 for e in calls if e['nested']),'other_ms':max(0,wall_ms-model_ms-tool_ms)}
    start=next((e for e in events if e['event']=='run.start'),{})
    return {'run_id':run['id'],'status':run['status'],'kind':run.get('kind'),'created':t0,'updated':run['updated'],'live':live,'message':start.get('message'),'mode':start.get('mode'),'totals':totals,'events':events}

def totals(runs):
    keys=('model_calls','model_errors','tool_calls','tool_errors','fallbacks','guard_blocks','tokens_in','tokens_out','tokens_cache_read','tokens_cache_write','reasoning_tokens','cost_reported','cost_estimate','wall_ms','model_ms','tool_ms','other_ms')
    out={k:sum(r['totals'][k] for r in runs) for k in keys}
    out['cost_reported']=round(out['cost_reported'],6);out['cost_estimate']=round(out['cost_estimate'],6);out['runs']=len(runs)
    return out
