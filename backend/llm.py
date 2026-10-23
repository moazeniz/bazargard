"""Model provider layer: Anthropic (through our relay) or BoxAPI, switchable.

LLM_PROVIDER=anthropic|boxapi selects the provider (default anthropic);
LLM_FALLBACK_PROVIDER (default boxapi, '' disables) takes over on 429/5xx/
credit/timeout failures. Anthropic bodies pass through RELAY_URL/v1/messages
with Bearer RELAY_TOKEN. For BoxAPI, call sites keep Anthropic-shaped bodies (system, messages with text/image/
tool_use/tool_result blocks, tools with input_schema, max_tokens); chat()
translates them to the Responses API and the answer back into Anthropic
content blocks, so the agent loop and its tests stay provider-neutral.
(Chat Completions rejects function tools for reasoning models on BoxAPI, so
the Responses API is used everywhere.) Keys (BOXAPI_KEYS, comma separated)
are used round-robin; 401/402/403/429/5xx/timeouts put a key in cooldown and
the next key is tried. Key values never reach logs, errors or stats.
"""
import asyncio,json,os,re,time
from collections import deque
import httpx
import obs,resilience,devtrace

DEFAULT_MODEL='openai/gpt-6.1-sol';DEFAULT_BASE='https://ai.boxapi.ir/ai/v1';DEFAULT_FALLBACKS='openai/gpt-6-sol,openai/gpt-6-luna'
COOLDOWN={401:900,402:900,403:900,429:30,'timeout':20,'network':10}
FAILOVER={401,402,403,408,409,425,429}

class LLMError(Exception):
 def __init__(self,message,status=None,kind='error'):super().__init__(redact(message));self.status=status;self.kind=kind

# Tiers: "main" (agent, extraction, search) and "light" (guard judge, cheap helpers).
# gpt-6.1-sol <-> claude-sonnet-5-5 ; gpt-6-luna <-> claude-haiku-5-5.
LIGHT_PURPOSES={'guard'}
def box_model(tier='main'):return (os.getenv('LLM_LIGHT_MODEL') or 'openai/gpt-6-luna') if tier=='light' else (os.getenv('LLM_MODEL') or DEFAULT_MODEL)
def anthropic_model(tier='main'):return (os.getenv('ANTHROPIC_LIGHT_MODEL') or 'claude-haiku-5-5') if tier=='light' else (os.getenv('ANTHROPIC_MODEL') or 'claude-sonnet-5-5')
def relay_url():return (os.getenv('RELAY_URL') or '').rstrip('/')
def provider():return (os.getenv('LLM_PROVIDER') or 'anthropic').strip().lower()
def fallback_provider():
 # Default: the other provider (boxapi while anthropic is primary); '' disables.
 p=os.getenv('LLM_FALLBACK_PROVIDER')
 p=('boxapi' if provider()=='anthropic' else 'anthropic') if p is None else p.strip().lower()
 return p if p in ('anthropic','boxapi') else None
def provider_configured(p):return bool(relay_url() and os.getenv('RELAY_TOKEN')) if p=='anthropic' else bool(keys())
def chain(skip_open=False):
 """Configured providers in order of use; with skip_open a provider whose circuit
 breaker is open moves behind the others (it is still the last resort)."""
 out=[]
 for p in (provider(),fallback_provider()):
  if p in ('anthropic','boxapi') and p not in out and provider_configured(p):out.append(p)
 if skip_open and len(out)>1:out.sort(key=lambda p:resilience.breaker('llm:'+p,5,60).state=='open')
 return out
def active():return (chain() or [provider()])[0]
def model(tier='main'):return anthropic_model(tier) if active()=='anthropic' else box_model(tier)
def tier_for(tier,purpose):return tier or ('light' if purpose in LIGHT_PURPOSES else 'main')
PROVIDER_FALLBACK={'rate_limited','exhausted','credit','unavailable'}
# BoxAPI token-rate limits are per model and shared by all keys (gpt-6.1-sol: 250k
# tokens/min; gpt-6-sol and gpt-6-luna are separate deployments). When every key
# fails with 429/5xx/timeouts the same request moves down this chain.
def fallback_models():return [m.strip() for m in os.getenv('LLM_FALLBACK_MODELS',DEFAULT_FALLBACKS).split(',') if m.strip()]
def base_url():return (os.getenv('BOXAPI_BASE_URL') or DEFAULT_BASE).rstrip('/')
def keys():return tuple(k.strip() for k in os.getenv('BOXAPI_KEYS','').split(',') if k.strip())
def configured():return bool(chain())
def redact(value):
 s=str(value)
 for k in keys()+((os.getenv('RELAY_TOKEN'),) if os.getenv('RELAY_TOKEN') else ()):s=s.replace(k,'[key]')
 return re.sub(r'(?i)(bearer\s+|sk-|api[_-]?key["\'=:\s]+)[A-Za-z0-9._\-]{6,}',r'\1[redacted]',s)

# ---- key pool and usage accounting (indexes only, never key values) ----
_POOLS={};STATS={'started':time.time(),'calls':0,'errors':0,'failovers':0,'model_fallbacks':0,'provider_fallbacks':0,'by_model':{},'input_tokens':0,'output_tokens':0,'by_key':{},'recent':deque(maxlen=60)}
def _pool():
 ks=keys()
 return _POOLS.setdefault(ks,{'next':0,'cool':{}}),len(ks)
def _pick(tried,ignore_cooling=False):
 pool,n=_pool();now=time.monotonic()
 for step in range(n):
  i=(pool['next']+step)%n
  if i not in tried and (ignore_cooling or pool['cool'].get(i,0)<=now):pool['next']=(i+1)%n;return i
 # Every key is cooling: one last try on the key that recovers first.
 rest=[i for i in range(n) if i not in tried]
 return min(rest,key=lambda i:pool['cool'].get(i,0)) if rest else None
def _cool(i,reason):
 pool,_=_pool();pool['cool'][i]=time.monotonic()+COOLDOWN.get(reason,15)
def _record(i,purpose,model_name,status,seconds,usage=None,endpoint='/responses',stop=None):
 usage=usage or {};k=STATS['by_key'].setdefault(i,{'calls':0,'errors':0,'input_tokens':0,'output_tokens':0})
 ok=status==200;inp=usage.get('input_tokens',0) or 0;out=usage.get('output_tokens',0) or 0
 for d in (STATS,k):d['calls']+=1;d['errors']+=0 if ok else 1;d['input_tokens']+=inp;d['output_tokens']+=out
 if status==200:STATS['by_model'][model_name]=STATS['by_model'].get(model_name,0)+1
 STATS['recent'].append({'at':round(time.time()),'purpose':purpose,'model':model_name,'endpoint':endpoint,'key_index':i,'status':status,'seconds':round(seconds,2),'input_tokens':inp,'output_tokens':out,'reasoning_tokens':(usage.get('output_tokens_details') or {}).get('reasoning_tokens',0)})
 obs.log('llm.call','info' if ok else 'warn',purpose,provider='anthropic' if i=='relay' else 'boxapi',model=model_name,key_index=i,purpose=purpose,status=status,
         duration_ms=round(seconds*1000),tokens_in=inp,tokens_out=out,tokens_cache_read=usage.get('cache_read_input_tokens') or (usage.get('input_tokens_details') or {}).get('cached_tokens'),tokens_cache_write=usage.get('cache_creation_input_tokens'),reasoning_tokens=(usage.get('output_tokens_details') or {}).get('reasoning_tokens'),cost=usage.get('cost'),stop=stop)
def stats():
 pool,n=_pool();now=time.monotonic()
 return {'provider':active(),'chain':chain(),'model':model(),'keys':n,'cooling':{i:round(t-now) for i,t in pool['cool'].items() if t>now},**{k:v for k,v in STATS.items() if k not in ('recent','by_key')},
         'by_key':{str(i):dict(v) for i,v in STATS['by_key'].items()},'recent':list(STATS['recent'])[-20:]}

def session(timeout=110):
 """Shared keep-alive client for a multi-round conversation."""
 return httpx.AsyncClient(timeout=httpx.Timeout(timeout,connect=8))

async def post(path,body,timeout=90,purpose='chat',client_factory=None,client=None):
 """POST with round-robin keys and fail-over; returns the JSON body."""
 if not keys():raise LLMError('BoxAPI keys are not configured',kind='not_configured')
 if client is not None:return await _fallback(client,path,body,timeout,purpose)
 async with (client_factory or httpx.AsyncClient)(timeout=httpx.Timeout(timeout,connect=8)) as client:return await _fallback(client,path,body,timeout,purpose)

async def _fallback(client,path,body,timeout,purpose):
 chain=[body.get('model')]+[m for m in fallback_models() if m!=body.get('model')]
 for n,name in enumerate(chain):
  try:return await _send(client,path,dict(body,model=name),timeout if n==0 else min(timeout,60),purpose)
  except LLMError as e:
   # Auth (401/403) and request errors are not capacity problems.
   if e.kind not in ('rate_limited','exhausted') or n==len(chain)-1:raise
   STATS['model_fallbacks']+=1;obs.log('llm.fallback','warn',purpose,provider='boxapi',model=name,fallback_model=chain[n+1],error_type=e.kind)

async def _send(client,path,body,timeout,purpose):
 deadline=time.monotonic()+timeout+25;tried=set();last='no attempt';retried=False;limited=True;auth=True;wait=1.0;seen=set()
 while True:
  i=_pick(tried,retried);left=deadline-time.monotonic()
  if i is None or left<3:break
  if tried:STATS['failovers']+=1
  tried.add(i);started=time.monotonic()
  try:r=await asyncio.wait_for(client.post(base_url()+path,headers={'Authorization':'Bearer '+keys()[i]},json=body),min(timeout,left)+2)
  except (httpx.TimeoutException,asyncio.TimeoutError):limited=auth=False;_cool(i,'timeout');_record(i,purpose,body.get('model'),'timeout',time.monotonic()-started);last='timeout';continue
  except httpx.HTTPError as e:limited=auth=False;_cool(i,'network');_record(i,purpose,body.get('model'),'network',time.monotonic()-started);last=type(e).__name__;continue
  if r.status_code==200:
   try:data=r.json()
   except ValueError:limited=auth=False;_cool(i,'network');_record(i,purpose,body.get('model'),'bad_json',time.monotonic()-started);last='invalid JSON from provider';continue
   _record(i,purpose,body.get('model'),200,time.monotonic()-started,data.get('usage'),stop=devtrace.stop_reason(data));return data
  try:message=(r.json().get('error') or {}).get('message','')
  except (ValueError,AttributeError):message=r.text[:200]
  _record(i,purpose,body.get('model'),r.status_code,time.monotonic()-started);obs.log('llm.error','warn',purpose,provider='boxapi',model=body.get('model'),key_index=i,status=r.status_code,error=redact(message)[:160])
  if r.status_code==429:
   try:wait=max(wait,min(8.0,float(r.headers.get('retry-after-ms') or 1000*float(r.headers.get('retry-after') or 1))/1000))
   except ValueError:pass
   # The limit is usually model-wide: cool the key only for the advertised wait.
   pool,_=_pool();pool['cool'][i]=time.monotonic()+min(COOLDOWN[429],wait)
   if len(tried)==len(keys()) and not retried and deadline-time.monotonic()>15:
    # Every key is rate limited: one short pause, then one more pass.
    retried=True;tried=set();last='HTTP 429';await asyncio.sleep(min(wait,4));continue
   last='HTTP 429 %s'%message[:160];continue
  limited=False;auth=auth and r.status_code in (401,402,403);seen.add(str(r.status_code))
  if r.status_code in FAILOVER or r.status_code>=500:
   _cool(i,r.status_code if r.status_code in COOLDOWN else 'network');last='HTTP %d %s'%(r.status_code,message[:160]);continue
  # Request errors (400/404/413/422) are not key problems: no fail-over.
  raise LLMError('BoxAPI rejected the request: HTTP %d %s'%(r.status_code,message[:300]),r.status_code,'request')
 raise LLMError('All BoxAPI keys failed ('+str(len(tried))+' tried); last: '+last,kind='rate_limited' if limited and last.startswith('HTTP 429') else ('credit' if '402' in seen else 'auth') if auth and last[5:8] in ('401','402','403') else 'exhausted')

# ---- Anthropic <-> Responses translation ----
def _text(content):
 if isinstance(content,str):return content
 return '\n'.join(b.get('text','') if isinstance(b,dict) else str(b) for b in content or [] if not isinstance(b,dict) or b.get('type')=='text')

def to_responses(body,json_mode=False,effort=None):
 """Anthropic Messages body -> Responses API body."""
 out={'model':body.get('model') or box_model(),'input':[],'store':False}
 system=body.get('system')
 if system:out['instructions']=_text(system)
 for m in body.get('messages',[]):
  role=m['role'];content=m['content']
  if isinstance(content,str):out['input'].append({'role':role,'content':content});continue
  parts=[]
  def flush():
   if not parts:return
   if role=='assistant':out['input'].append({'role':'assistant','content':'\n'.join(p['text'] for p in parts)})
   else:out['input'].append({'role':role,'content':list(parts)})
   parts.clear()
  for b in content:
   kind=b.get('type')
   if kind=='text':parts.append({'type':'input_text','text':b['text']})
   elif kind=='image' and role=='user':
    src=b.get('source') or {}
    parts.append({'type':'input_image','image_url':'data:%s;base64,%s'%(src.get('media_type'),src.get('data')) if src.get('type')=='base64' else src.get('url')})
   elif kind=='tool_use':flush();out['input'].append({'type':'function_call','call_id':b['id'],'name':b['name'],'arguments':json.dumps(b.get('input') or {},ensure_ascii=False)})
   elif kind=='tool_result':
    flush();text=_text(b.get('content'))
    out['input'].append({'type':'function_call_output','call_id':b['tool_use_id'],'output':('ERROR: '+text) if b.get('is_error') else text})
  flush()
 tools=[]
 for t in body.get('tools') or []:
  if 'input_schema' not in t:raise ValueError('Server tools are not supported; use llm.web_search')
  tools.append({'type':'function','name':t['name'],'description':t.get('description',''),'parameters':t['input_schema'],'strict':False})
 if tools:out['tools']=tools
 choice=(body.get('tool_choice') or {}).get('type')
 if tools and choice:out['tool_choice']={'none':'none','auto':'auto','any':'required'}.get(choice) or {'type':'function','name':body['tool_choice'].get('name')}
 # Reasoning tokens count against the output budget of GPT reasoning models.
 effort=effort or os.getenv('LLM_REASONING_EFFORT') or None
 out['max_output_tokens']=max(400,int(body.get('max_tokens') or 1600))+(1600 if effort in ('medium','high') else 700)
 if effort:out['reasoning']={'effort':effort}
 if json_mode:
  out['text']={'format':{'type':'json_object'}}
  # The provider requires the word JSON inside the input messages themselves.
  if 'json' not in json.dumps(out['input'],ensure_ascii=False).lower():out['input'].append({'role':'user','content':'Answer with a single JSON object.'})
 return out

def from_responses(data):
 """Responses API result -> Anthropic-shaped message."""
 content=[]
 for item in data.get('output') or []:
  if item.get('type')=='message':
   for c in item.get('content') or []:
    if c.get('type')=='output_text' and c.get('text'):content.append({'type':'text','text':c['text']})
    elif c.get('type')=='refusal':content.append({'type':'text','text':c.get('refusal','')})
  elif item.get('type')=='function_call':
   try:args=json.loads(item.get('arguments') or '{}');args=args if isinstance(args,dict) else {}
   except ValueError:args={}
   content.append({'type':'tool_use','id':item.get('call_id') or item.get('id'),'name':item.get('name'),'input':args})
 usage=data.get('usage') or {};reason=(data.get('incomplete_details') or {}).get('reason')
 stop='tool_use' if any(b['type']=='tool_use' for b in content) else 'max_tokens' if reason=='max_output_tokens' else 'refusal' if reason=='content_filter' else 'end_turn'
 return {'id':data.get('id'),'type':'message','role':'assistant','model':data.get('model'),'content':content,'stop_reason':stop,
         'usage':{'input_tokens':usage.get('input_tokens',0),'output_tokens':usage.get('output_tokens',0)}}

async def _anthropic(body,timeout,purpose,client_factory=None,client=None,tier='main'):
 """Anthropic Messages through the relay (no temperature; relay caps max_tokens at 5000)."""
 url,token=relay_url(),os.getenv('RELAY_TOKEN','')
 if not (url and token):raise LLMError('Anthropic relay is not configured',kind='not_configured')
 payload=dict(body,model=anthropic_model(tier));payload['max_tokens']=min(int(payload.get('max_tokens') or 1600),5000);payload.pop('temperature',None)
 msgs=payload.get('messages') or []
 if any('input_schema' in t for t in payload.get('tools') or []) and msgs and os.getenv('LLM_PROMPT_CACHE','1')!='0':
  # Incremental prompt caching for tool loops: each round reuses the previous prefix.
  last=msgs[-1];content=[{'type':'text','text':last['content']}] if isinstance(last['content'],str) else [dict(b) for b in last['content']]
  if content and content[-1].get('type') in ('text','tool_result','image'):content[-1]=dict(content[-1],cache_control={'type':'ephemeral'})
  payload['messages']=msgs[:-1]+[dict(last,content=content)]
 async def go(c):
  deadline=time.monotonic()+timeout+25;last='no attempt'
  for attempt in range(3):
   left=deadline-time.monotonic()
   if left<3:break
   started=time.monotonic()
   try:r=await asyncio.wait_for(c.post(url+'/v1/messages',headers={'Authorization':'Bearer '+token},json=payload),min(timeout,left)+2)
   except (httpx.TimeoutException,asyncio.TimeoutError):_record('relay',purpose,payload['model'],'timeout',time.monotonic()-started);last='timeout';continue
   except httpx.HTTPError as e:_record('relay',purpose,payload['model'],'network',time.monotonic()-started);last=type(e).__name__;await asyncio.sleep(max(0.5,resilience.backoff(attempt+1,1.0,8.0)));continue
   if r.status_code==200:
    try:data=r.json()
    except ValueError:last='invalid JSON';continue
    _record('relay',purpose,payload['model'],200,time.monotonic()-started,data.get('usage'),stop=devtrace.stop_reason(data));return data
   try:message=str((r.json().get('error') or {}).get('message',''))
   except (ValueError,AttributeError):message=r.text[:200]
   _record('relay',purpose,payload['model'],r.status_code,time.monotonic()-started);obs.log('llm.error','warn',purpose,provider='anthropic',model=payload['model'],status=r.status_code,error=redact(message)[:160])
   credit=r.status_code==402 or 'credit' in message.lower() or 'billing' in message.lower()
   # The Arvan LLM gate answers 401/403 (token or time window) and 503 (kill switch): provider unavailable.
   if r.status_code in (429,500,502,504,529) and attempt<2 and not credit:await asyncio.sleep(max(0.5,resilience.backoff(attempt+1,1.0,8.0,resilience.response_retry_after(r))));last='HTTP %d'%r.status_code;continue
   kind='credit' if credit else 'rate_limited' if r.status_code in (429,529) else 'unavailable' if r.status_code in (401,403,503) else 'exhausted' if r.status_code>=500 else 'request'
   raise LLMError('Anthropic relay: HTTP %d %s'%(r.status_code,message[:300]),r.status_code,kind)
  raise LLMError('Anthropic relay unavailable; last: '+last,kind='exhausted')
 if client is not None:return await go(client)
 async with (client_factory or httpx.AsyncClient)(timeout=httpx.Timeout(timeout,connect=8)) as c:return await go(c)

async def chat(body,json_mode=False,timeout=90,purpose='chat',effort=None,client_factory=None,client=None,tier=None):
 """Anthropic-shaped request -> Anthropic-shaped response on the active
 provider, moving to the fallback provider on capacity/credit failures."""
 providers=chain(True)
 if not providers:raise LLMError('No model provider is configured',kind='not_configured')
 body={k:v for k,v in body.items() if k!='model'};tier=tier_for(tier,purpose)
 for n,p in enumerate(providers):
  b=resilience.breaker('llm:'+p,5,60)
  try:
   result=await (_anthropic(body,timeout,purpose,client_factory,client,tier) if p=='anthropic' else _box_chat(dict(body,model=box_model(tier)),json_mode,timeout,purpose,effort,client_factory,client))
   b.success();return result
  except LLMError as e:
   if e.kind in PROVIDER_FALLBACK:b.failure()
   if n==len(providers)-1 or e.kind not in PROVIDER_FALLBACK:raise
   STATS['provider_fallbacks']+=1;obs.log('llm.fallback','warn',purpose,provider=p,fallback_provider=providers[n+1],error_type=e.kind)

async def _box_chat(body,json_mode,timeout,purpose,effort,client_factory,client):
 request=to_responses(body,json_mode,effort)
 result=from_responses(await post('/responses',request,timeout,purpose,client_factory,client))
 if result['stop_reason']=='max_tokens' and not result['content']:
  # Hidden reasoning used the whole budget; one retry with more room.
  request['max_output_tokens']*=2
  result=from_responses(await post('/responses',request,timeout,purpose,client_factory,client))
 return result

async def text(system,user,max_tokens=800,json_mode=False,timeout=60,purpose='text',effort=None,client_factory=None,tier=None):
 data=await chat({'system':system,'max_tokens':max_tokens,'messages':[{'role':'user','content':user}]},json_mode,timeout,purpose,effort,client_factory,tier=tier)
 return '\n'.join(b['text'] for b in data['content'] if b['type']=='text').strip()

def parse_json(raw):
 """First JSON object in a model answer (tolerates fences and prose)."""
 raw=re.sub(r'^```(?:json)?\s*|\s*```$','',(raw or '').strip())
 start=raw.find('{');end=raw.rfind('}')
 return json.loads(raw[start:end+1]) if start>=0 and end>start else None

# ---- provider web search ----
_SEARCH_GATES={}
def _search_gate():
 # Parallel web searches trip per-key rate limits; keep a few in flight per loop.
 loop=asyncio.get_running_loop()
 return _SEARCH_GATES.setdefault(id(loop),asyncio.Semaphore(int(os.getenv('LLM_SEARCH_CONCURRENCY','3'))))

async def web_search(prompt,max_tool_calls=3,context='medium',effort='low',max_tokens=3000,timeout=120,purpose='web_search',client_factory=None,instructions=None,tier=None):
 """Provider web search. prompt may be a dict {provider: prompt}. Returns
 {provider,text,citations,sources,queries,usage}. Anthropic citations carry
 verbatim page text (verbatim=True); BoxAPI citation text is a model-written
 summary (verbatim=False), so callers verify quotes against fetched pages."""
 providers=chain(True)
 if not providers:raise LLMError('No model provider is configured',kind='not_configured')
 for n,p in enumerate(providers):
  text_prompt=prompt.get(p) if isinstance(prompt,dict) else prompt
  try:
   async with _search_gate():
    if p=='anthropic':return _anthropic_search(await _anthropic({'max_tokens':max_tokens,'output_config':{'effort':'low'},'tools':[{'type':'web_search_20250305','name':'web_search','max_uses':max_tool_calls}],
                                                       'messages':[{'role':'user','content':text_prompt}],**({'system':instructions} if instructions else {})},timeout,purpose,client_factory,None,tier_for(tier,purpose)))
    return await _box_search(text_prompt,max_tool_calls,context,effort,max_tokens,timeout,purpose,client_factory,instructions,box_model(tier_for(tier,purpose)))
  except LLMError as e:
   if n==len(providers)-1 or e.kind not in PROVIDER_FALLBACK:raise
   STATS['provider_fallbacks']+=1;obs.log('llm.fallback','warn',purpose,provider=p,fallback_provider=providers[n+1],error_type=e.kind)

def _anthropic_search(data):
 answer='';citations=[];sources=[];queries=[]
 for block in data.get('content') or []:
  kind=block.get('type')
  if kind=='text':
   answer+=block.get('text','')
   for c in block.get('citations') or []:
    if str(c.get('url','')).startswith('http'):citations.append({'url':c['url'],'title':c.get('title') or '','text':c.get('cited_text') or '','verbatim':True})
  elif kind=='server_tool_use' and isinstance(block.get('input'),dict) and block['input'].get('query'):queries.append(block['input']['query'])
  elif kind=='web_search_tool_result' and isinstance(block.get('content'),list):
   sources+=[r['url'] for r in block['content'] if isinstance(r,dict) and str(r.get('url','')).startswith('http')]
 return {'provider':'anthropic','text':answer.strip(),'citations':citations,'sources':list(dict.fromkeys(sources)),'queries':list(dict.fromkeys(queries)),'usage':data.get('usage') or {}}

async def _box_search(prompt,max_tool_calls,context,effort,max_tokens,timeout,purpose,client_factory,instructions,model_name):
 body={'model':model_name,'input':prompt,'tools':[{'type':'web_search','search_context_size':context}],'max_tool_calls':max_tool_calls,
       'include':['web_search_call.action.sources'],'max_output_tokens':max_tokens,'store':False,'reasoning':{'effort':effort}}
 if instructions:body['instructions']=instructions
 data=await post('/responses',body,timeout,purpose,client_factory)
 answer='';citations=[];sources=[];queries=[]
 for item in data.get('output') or []:
  if item.get('type')=='web_search_call':
   action=item.get('action') or {}
   queries+=action.get('queries') or ([action['query']] if action.get('query') else [])
   sources+=[s.get('url') for s in action.get('sources') or [] if isinstance(s,dict) and s.get('url')]
   if action.get('url'):sources.append(action['url'])
  elif item.get('type')=='message':
   for c in item.get('content') or []:
    if c.get('type')!='output_text':continue
    offset=len(answer);body_text=c.get('text','');answer+=body_text+'\n';previous=0
    for a in c.get('annotations') or []:
     if a.get('type')!='url_citation' or not str(a.get('url','')).startswith('http'):continue
     start=a.get('start_index',0);end=a.get('end_index',start)
     # Supporting text: the model sentence(s) just before the citation marker.
     lo=max(previous,body_text.rfind('\n',0,max(0,start-1))+1)
     citations.append({'url':a['url'],'title':a.get('title',''),'text':body_text[lo:start].strip(),'verbatim':False,'start':offset+start,'end':offset+end});previous=end
 return {'provider':'boxapi','text':answer.strip(),'citations':citations,'sources':list(dict.fromkeys(sources)),'queries':list(dict.fromkeys(queries)),'usage':data.get('usage') or {}}

_HEALTH={}
async def _reach(p,ttl):
 if not provider_configured(p):return 'not_configured'
 hit=_HEALTH.get(p)
 if hit and time.monotonic()-hit[0]<ttl:return hit[1]
 try:
  async with httpx.AsyncClient(timeout=4) as client:
   if p=='anthropic':r=await client.get(relay_url()+'/health',headers={'Authorization':'Bearer '+os.getenv('RELAY_TOKEN','')})
   else:
    pool,n=_pool();r=await client.get(base_url()+'/models',headers={'Authorization':'Bearer '+keys()[pool['next']%n]})
  value='reachable' if r.status_code==200 else 'credit_exhausted' if r.status_code==402 else 'auth_error' if r.status_code in (401,403) else 'unavailable'
 except httpx.HTTPError:value='unreachable'
 _HEALTH[p]=(time.monotonic(),value);return value
async def health(ttl=120):
 """Cached (2 min) reachability of both providers; no secrets."""
 both={p:{'configured':provider_configured(p),'status':await _reach(p,ttl),'model':anthropic_model() if p=='anthropic' else box_model(),'light_model':anthropic_model('light') if p=='anthropic' else box_model('light')} for p in ('anthropic','boxapi')}
 return {'provider':active(),'provider_status':both[active()]['status'],'model':model(),'fallback_provider':fallback_provider(),'providers':both}

def reset():
 _POOLS.clear();_HEALTH.clear()
 for name in [k for k in resilience.BREAKERS if k.startswith('llm:')]:del resilience.BREAKERS[name]
 STATS.update(calls=0,errors=0,failovers=0,model_fallbacks=0,provider_fallbacks=0,by_model={},input_tokens=0,output_tokens=0,by_key={});STATS['recent'].clear()
