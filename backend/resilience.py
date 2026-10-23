"""Shared retry policy for every external call: exponential backoff with full
jitter, Retry-After, transient-only retries, timeouts and per-upstream circuit
breakers. Every attempt is logged as external.call."""
import asyncio,random,sqlite3,threading,time
import httpx
import obs

class Retryable(Exception):
 """Transient failure: safe to try again later."""
 def __init__(self,message='',retry_after=None):super().__init__(message);self.retry_after=retry_after
class NonRetryable(Exception):"""Permanent failure: retrying cannot help."""
class CircuitOpen(Retryable):"""The upstream's breaker is open; fail fast."""

TRANSIENT_STATUS={408,425,429,500,502,503,504,520,521,522,524,529}
LLM_TRANSIENT={'rate_limited','exhausted','unavailable','credit'}

def retryable(exc):
 if isinstance(exc,NonRetryable):return False
 if isinstance(exc,(Retryable,httpx.TimeoutException,httpx.TransportError,asyncio.TimeoutError,TimeoutError,ConnectionError)):return True
 if isinstance(exc,httpx.HTTPStatusError):return exc.response.status_code in TRANSIENT_STATUS
 if isinstance(exc,sqlite3.OperationalError):return 'locked' in str(exc) or 'busy' in str(exc)
 kind=getattr(exc,'kind',None)  # llm.LLMError
 return kind in LLM_TRANSIENT if kind else False

def retry_after(value):
 """Seconds from a Retry-After / retry-after-ms header value (or None)."""
 if value is None:return None
 try:return max(0.0,float(value))
 except (TypeError,ValueError):return None
def response_retry_after(r):
 ms=retry_after(r.headers.get('retry-after-ms'))
 return ms/1000 if ms is not None else retry_after(r.headers.get('retry-after'))

def backoff(attempt,base=0.5,cap=20.0,after=None,rng=random.random):
 """Full jitter: uniform(0, min(cap, base*2^(attempt-1))); Retry-After wins (capped)."""
 if after is not None:return min(cap,after)
 return rng()*min(cap,base*2**max(0,attempt-1))

class Breaker:
 """closed -> open after `threshold` consecutive failures -> half_open after
 `cooldown` seconds (one probe) -> closed on success / open on failure."""
 def __init__(self,name,threshold=5,cooldown=30.0,clock=time.monotonic):
  self.name=name;self.threshold=threshold;self.cooldown=cooldown;self.clock=clock
  self.failures=0;self.opened=None;self.probing=False;self.lock=threading.Lock()
 @property
 def state(self):
  if self.opened is None:return 'closed'
  return 'half_open' if self.clock()-self.opened>=self.cooldown else 'open'
 def allow(self):
  with self.lock:
   state=self.state
   if state=='closed':return True
   if state=='half_open' and not self.probing:self.probing=True;return True
   return False
 def success(self):
  with self.lock:
   if self.opened is not None:obs.log('circuit.close','info',self.name,upstream=self.name,circuit='closed')
   self.failures=0;self.opened=None;self.probing=False
 def failure(self):
  with self.lock:
   self.failures+=1;was=self.opened;self.probing=False
   if self.failures>=self.threshold or was is not None:
    self.opened=self.clock()
    if was is None:obs.log('circuit.open','warn',self.name,upstream=self.name,circuit='open',attempt=self.failures)

BREAKERS={}
def breaker(upstream,threshold=5,cooldown=30.0):
 b=BREAKERS.get(upstream)
 if b is None:b=BREAKERS[upstream]=Breaker(upstream,threshold,cooldown)
 return b
def circuits():return {k:{'state':b.state,'failures':b.failures} for k,b in BREAKERS.items()}

def _status(result):return getattr(result,'status_code',None)

async def call(upstream,fn,attempts=3,base=0.5,cap=20.0,timeout=None,breaker_threshold=5,cooldown=30.0,sleep=asyncio.sleep,ok_status=None):
 """Run `await fn()` with the shared policy. fn may return an httpx.Response:
 429/5xx responses are retried (honouring Retry-After) and the last one is
 returned; other responses are returned as-is. Exceptions that are not
 transient are raised immediately. Raises CircuitOpen when the breaker is open."""
 b=breaker(upstream,breaker_threshold,cooldown);result=None
 for attempt in range(1,attempts+1):
  if not b.allow():
   obs.log('external.call','warn','circuit open',upstream=upstream,attempt=attempt,max_attempts=attempts,circuit=b.state)
   raise CircuitOpen(upstream+' circuit open')
  started=time.monotonic()
  try:
   result=await (asyncio.wait_for(fn(),timeout) if timeout else fn())
  except Exception as exc:
   duration=round((time.monotonic()-started)*1000);transient=retryable(exc)
   if transient:b.failure()
   wait=backoff(attempt,base,cap,getattr(exc,'retry_after',None)) if transient and attempt<attempts else None
   obs.log('external.call','warn' if transient else 'error',type(exc).__name__,upstream=upstream,attempt=attempt,max_attempts=attempts,duration_ms=duration,
           error_type=type(exc).__name__,error=str(exc),backoff_ms=round(wait*1000) if wait is not None else None,circuit=b.state)
   if not transient or attempt==attempts:raise
   await sleep(wait);continue
  duration=round((time.monotonic()-started)*1000);status=_status(result)
  if status is not None and status in TRANSIENT_STATUS:
   b.failure();wait=backoff(attempt,base,cap,response_retry_after(result)) if attempt<attempts else None
   obs.log('external.call','warn','HTTP %d'%status,upstream=upstream,attempt=attempt,max_attempts=attempts,status=status,duration_ms=duration,
           backoff_ms=round(wait*1000) if wait is not None else None,circuit=b.state)
   if attempt==attempts:return result
   await sleep(wait);continue
  b.success()
  obs.log('external.call','info','ok' if status is None or status<400 else 'HTTP %d'%status,upstream=upstream,attempt=attempt,max_attempts=attempts,status=status,duration_ms=duration,circuit='closed')
  return result
 return result

def call_sync(upstream,fn,attempts=2,base=0.5,cap=10.0,breaker_threshold=5,cooldown=30.0,sleep=time.sleep,ok=None):
 """Blocking twin of call() for thread-pool workers (page fetchers, catalogue seed).
 `ok(result)` may return False to count a returned value as a transient failure."""
 b=breaker(upstream,breaker_threshold,cooldown);result=None
 for attempt in range(1,attempts+1):
  if not b.allow():
   obs.log('external.call','warn','circuit open',upstream=upstream,attempt=attempt,max_attempts=attempts,circuit=b.state)
   raise CircuitOpen(upstream+' circuit open')
  started=time.monotonic()
  try:result=fn()
  except Exception as exc:
   duration=round((time.monotonic()-started)*1000);transient=retryable(exc)
   if transient:b.failure()
   wait=backoff(attempt,base,cap) if transient and attempt<attempts else None
   obs.log('external.call','warn' if transient else 'error',type(exc).__name__,upstream=upstream,attempt=attempt,max_attempts=attempts,duration_ms=duration,
           error_type=type(exc).__name__,error=str(exc),backoff_ms=round(wait*1000) if wait is not None else None,circuit=b.state)
   if not transient or attempt==attempts:raise
   sleep(wait);continue
  duration=round((time.monotonic()-started)*1000)
  good=ok(result) if ok else True
  if not good:
   b.failure();wait=backoff(attempt,base,cap) if attempt<attempts else None
   obs.log('external.call','warn','unavailable',upstream=upstream,attempt=attempt,max_attempts=attempts,duration_ms=duration,backoff_ms=round(wait*1000) if wait is not None else None,circuit=b.state)
   if attempt==attempts:return result
   sleep(wait);continue
  b.success();obs.log('external.call','info','ok',upstream=upstream,attempt=attempt,max_attempts=attempts,duration_ms=duration,circuit='closed')
  return result
 return result
