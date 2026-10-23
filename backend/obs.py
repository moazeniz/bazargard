"""Structured JSON logs (one object per line on stdout) with request context.

Shared standard keys: ts, level, app, env, release, event, request_id,
trace_id, span_id, session, msg (+ typed optional keys). Secrets, tokens,
phone numbers and e-mails are redacted; free text over 200 characters is
truncated and hashed. Plain print() output and stdlib logging are converted
to JSON lines too, so stdout stays machine-readable for Liara/Negahban.
"""
import contextvars,hashlib,io,json,logging,os,re,secrets,sys,threading,time,traceback
from datetime import datetime,timezone

APP='bazargard';RELEASE='4.5.0'
LEVELS={'debug':10,'info':20,'warn':30,'error':40,'critical':50}
request_id=contextvars.ContextVar('request_id',default=None)
trace_id=contextvars.ContextVar('trace_id',default=None)
span_id=contextvars.ContextVar('span_id',default=None)
session=contextvars.ContextVar('session',default=None)
job_id=contextvars.ContextVar('job_id',default=None)
run_id=contextvars.ContextVar('run_id',default=None)  # assistant run (chat turn)
SINKS=[]  # extra consumers of finished records (the developer console); must never log
_LOCK=threading.Lock();_OUT=[None]

SECRET_KEY=re.compile(r'(?i)(token|secret|passw|api_?key|authorization|cookie|signature|credential|^key$|^keys$)')
TEXT_KEYS={'msg','message','query','text','prompt','reply','user_message','detail','error'}
PATTERNS=[(re.compile(r'(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{6,}'),r'\1 [redacted]'),
          (re.compile(r'\bsk-[A-Za-z0-9._\-]{8,}'),'sk-[redacted]'),
          (re.compile(r'(?i)(api[_-]?key|token|secret|password)(["\'=:\s]+)[A-Za-z0-9._\-]{6,}'),r'\1\2[redacted]'),
          (re.compile(r'(?<!\d)(?:\+98|0098|0)?9\d{9}(?!\d)'),'[phone]'),
          (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'),'[email]')]

def level_enabled(level):return LEVELS.get(level,20)>=LEVELS.get(os.getenv('LOG_LEVEL','info').lower(),20)
def digest(value,n=10):return hashlib.sha256(str(value).encode()).hexdigest()[:n]
def secret_values():
 out=[]
 for name in ('BOXAPI_KEYS','RELAY_TOKEN','ADMIN_TOKEN','LIARA_API_TOKEN','CATALOG_TOKEN','WEB_GATEWAY_TOKEN'):
  out+=[v.strip() for v in os.getenv(name,'').split(',') if len(v.strip())>=6]
 return out
def scrub(text):
 s=str(text)
 for v in secret_values():s=s.replace(v,'[redacted]')
 for pattern,repl in PATTERNS:s=pattern.sub(repl,s)
 return s
def short(text,limit=200):
 """Truncate long free text and add a hash so equal texts stay correlatable."""
 s=scrub(text)
 return s if len(s)<=limit else s[:limit]+'…#'+digest(s)
def redact(value,key=''):
 # Token *counts* (tokens_in, tokens_cache_read, output_tokens...) are metrics, not secrets.
 if key and SECRET_KEY.search(str(key)) and key not in ('key_index','idem_key') and not str(key).startswith('tokens_') and not str(key).endswith('_tokens'):return '[redacted]'
 if isinstance(value,dict):return {k:redact(v,k) for k,v in value.items()}
 if isinstance(value,(list,tuple)):return [redact(v,key) for v in list(value)[:50]]
 if isinstance(value,str):return short(value,2000 if key=='stack' else 200 if key in TEXT_KEYS else 500)
 return value

def now_iso():return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.')+'%03dZ'%(time.time()%1*1000)
def emit(record):
 line=json.dumps(record,ensure_ascii=False,default=str)
 out=_OUT[0] or sys.stdout
 with _LOCK:
  try:out.write(line+'\n');out.flush()
  except (ValueError,OSError):pass

def log(event,level='info',msg='',**fields):
 """One JSON line; context (request/trace/session/job) is added automatically."""
 if not level_enabled(level):return
 record={'ts':now_iso(),'level':level,'app':APP,'env':os.getenv('APP_ENV','production'),'release':RELEASE,'event':event,
         'request_id':request_id.get(),'trace_id':trace_id.get(),'span_id':span_id.get(),'session':session.get(),'msg':short(msg)}
 if job_id.get() and 'job_id' not in fields:record['job_id']=job_id.get()
 if run_id.get() and 'run_id' not in fields:record['run_id']=run_id.get()
 for k,v in fields.items():
  if v is not None:record[k]=redact(v,k)
 emit(record)
 for sink in SINKS:
  try:sink(record)
  except Exception:pass

def error(event,exc,msg='',level='error',**fields):
 log(event,level,msg or type(exc).__name__,error_type=type(exc).__name__,error=str(exc),
     stack=''.join(traceback.format_exception(type(exc),exc,exc.__traceback__))[-4000:],**fields)

# ---- request context (W3C traceparent compatible) ----
SAFE_ID=re.compile(r'^[A-Za-z0-9._:\-]{8,128}$')
TRACEPARENT=re.compile(r'^00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$')
def start_request(headers,user=None):
 rid=headers.get('x-request-id','');rid=rid if SAFE_ID.match(rid) else secrets.token_hex(16)
 m=TRACEPARENT.match(headers.get('traceparent','').strip().lower())
 tid=m.group(1) if m and m.group(1)!='0'*32 else secrets.token_hex(16);sid=secrets.token_hex(8)
 tokens=[request_id.set(rid),trace_id.set(tid),span_id.set(sid),session.set('u:'+digest(user,8) if user else None)]
 return rid,'00-%s-%s-01'%(tid,sid),tokens
def end_request(tokens):
 for var,token in zip((request_id,trace_id,span_id,session),tokens):
  try:var.reset(token)
  except ValueError:pass

# ---- stdout/stderr and stdlib logging as JSON ----
class JsonLines(io.TextIOBase):
 """Stream wrapper: JSON lines pass through; any other line becomes app.print."""
 def __init__(self,target,level):self.target=target;self.level=level;self.buffer='';self.lock=threading.Lock()
 def writable(self):return True
 def write(self,s):
  with self.lock:
   self.buffer+=s;lines=self.buffer.split('\n');self.buffer=lines.pop()
  for line in lines:
   if not line.strip():continue
   if line.startswith('{') and line.endswith('}'):
    with _LOCK:self.target.write(line+'\n');self.target.flush()
   else:
    lvl='warn' if re.search(r'(?i)fail|error|exception|traceback',line) or self.level=='warn' else 'info'
    words=line.split()
    log('app.print',lvl,line,source_event=words[0][:60] if words else None)
  return len(s)
 def flush(self):
  try:self.target.flush()
  except (ValueError,OSError):pass

class JsonHandler(logging.Handler):
 def emit(self,record):
  try:
   level={10:'debug',20:'info',30:'warn',40:'error',50:'critical'}.get(record.levelno,'info')
   fields={'logger':record.name}
   if record.exc_info:fields.update(error_type=record.exc_info[0].__name__,error=str(record.exc_info[1]),stack=''.join(traceback.format_exception(*record.exc_info))[-4000:])
   log('app.crash' if record.exc_info and level in ('error','critical') else 'app.log',level,record.getMessage(),**fields)
  except Exception:pass

def setup():
 """Idempotent: JSON stdout/stderr (not under pytest), JSON stdlib logging, no access logs."""
 if getattr(setup,'done',False):return
 setup.done=True
 if os.getenv('LOG_FORMAT','json')=='json' and 'pytest' not in sys.modules:
  _OUT[0]=sys.__stdout__;sys.stdout=JsonLines(sys.__stdout__,'info');sys.stderr=JsonLines(sys.__stdout__,'warn')
 handler=JsonHandler()
 for name in ('','uvicorn','uvicorn.error','fastapi','asyncio'):
  lg=logging.getLogger(name);lg.handlers=[handler];lg.propagate=False
  if name in ('uvicorn','uvicorn.error'):lg.setLevel(logging.INFO)
 access=logging.getLogger('uvicorn.access');access.handlers=[];access.propagate=False;access.disabled=True
