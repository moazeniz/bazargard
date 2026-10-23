import copy,importlib,json,os,sys
from pathlib import Path
import httpx,pytest
from llm_fake import use_client
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from comparison import build_comparison,scalar
from areas import suggestions,topic_for
from catalog import Catalog
from memory import Memory
from agent import Agent
from features import Features

def product(pid,specs):
 return {'product_id':pid,'persian_title':'مدل '+pid,'brand_name':None,'specs':[{'key':k,'value':v,'section':'مشخصات'} for k,v in specs]}

def test_numeric_comparison_converts_units_but_never_ranges_or_benchmarks():
 assert scalar('۱٬۲۵۰ گرم','وزن','kg')==1.25
 assert scalar('1 TB','ظرفیت SSD','GB')==1024
 assert scalar('1024 GB','ظرفیت SSD','GB')==1024
 assert scalar('2.4','وزن (کیلوگرم)','kg')==2.4
 for value,key,unit in [('1 الی 3 ساعت','شارژدهی','Wh'),('8/16 GB','حافظه رم','GB'),('2.4','وزن','kg'),('16','حافظه رم','GB'),('RTX 3050','مدل گرافیک',None),('NaN','ظرفیت SSD','GB')]:
  assert scalar(value,key,unit) is None

def test_comparison_joins_synonyms_preserves_missing_conflicts_and_price_sources():
 products={'a':product('a',[('ظرفیت رم','16 GB'),('وزن (کیلوگرم)','1.25'),('ظرفیت SSD','1 TB'),('ظرفیت SSD','1024 GB')]),
           'b':product('b',[('مقدار حافظه رم','8 GB'),('وزن','1250 گرم'),('ظرفیت SSD','512 GB'),('ظرفیت SSD','256 GB')]),
           'c':product('c',[('مدل پردازنده','i7-1355U')])}
 cat=type('Cat',(),{'product':lambda self,pid:copy.deepcopy(products.get(pid))})()
 offers=[{'product_id':'a','offer_id':'1','price_toman':100,'effective_price_toman':80,'price_basis':'user_reported'},
         {'product_id':'a','offer_id':'2','price_toman':90},{'product_id':'b','offer_id':'3','price_toman':0}]
 d=build_comparison(cat,offers,['a','b','c','a']);assert d['product_ids']==['a','b','c']
 rows={r['id']:r for r in d['rows']}
 assert rows['price']['cells'][0]['numeric']==80 and rows['price']['cells'][0]['source']=='user_reported'
 assert rows['price']['cells'][1]['numeric']==0 and rows['price']['cells'][2]['status']=='missing'
 assert [c['numeric'] for c in rows['weight']['cells']]==[1.25,1.25,None]
 assert rows['storage']['cells'][0]['numeric']==1024 and rows['storage']['cells'][1]['status']=='conflict'
 assert rows['ram']['cells'][2]['numeric'] is None
 assert {c['id'] for c in d['charts']}=={'price','ram','weight'} and not d['web_enriched']
 for ids in [[],['a'],['a','a'],['a','unknown'],list(map(str,range(13)))]:
  with pytest.raises(ValueError):build_comparison(cat,offers,ids)

def test_review_metadata_never_becomes_review_text_or_unsourced_sentiment():
 p=product('a',[('Customer Reviews','4.0 out of 5 stars 203 ratings')]);q=product('b',[])
 cat=type('Cat',(),{'product':lambda self,pid:p if pid=='a' else q})()
 d=build_comparison(cat,[],['a','b'])
 assert d['reviews'][0]['status']=='aggregate_only' and d['reviews'][0]['review_text_count']==0
 assert d['reviews'][0]['original_source'] is None and d['reviews'][1]['status']=='missing'
 assert not d['charts']

@pytest.fixture
def env(tmp_path):
 path=os.getenv('TEST_CATALOG')
 if not path:pytest.skip('TEST_CATALOG required')
 cat=Catalog(path);mem=Memory(tmp_path/'memory.db');a=Agent(cat,mem);a.features=Features(cat,mem,a)
 u=mem.user();s=mem.session(u)['id'];result=cat.search('لپ تاپ',limit=20)
 unique={}
 for o in result['offers']:unique.setdefault(o['product_id'],o)
 offers=list(unique.values())[:5];assert len(offers)==5
 mem.results(u,s,{'offers':offers,'query':'لپ تاپ','curated':True})
 return cat,mem,a,u,s

def test_empty_search_suggests_sourced_areas_without_relaxing_filters(env):
 cat,mem,a,u,s=env
 profile={'neighborhood':'تجریش','lat':35.8,'lon':51.3,'radius_km':.1,'budget_max_toman':1,'installment_only':True}
 mem.update(u,[{'key':k,'value':v} for k,v in profile.items()],sid=s)
 before=mem.profile(u,s);d=a.overlay(u,s,{'offers':[],'query':'لپ تاپ ZZNOTFOUND90210'})
 areas=d['shopping_areas'];assert areas['areas'] and mem.profile(u,s)==before
 assert len({x['name'] for x in areas['areas']})==len(areas['areas'])
 assert all(x['outside_radius'] for x in areas['areas'])
 public=next(x for x in areas['areas'] if x['evidence_kind']=='public_merchant_source')
 assert public['sources'][0]['url']=='https://ai-pars.com/' and public['sources'][0]['checked_at']
 assert not areas['changes_preferences'] and areas['filters']['budget_max_toman']==1
 assert not any('price_toman' in x or 'availability' in x or 'opening' in x for x in areas['areas'])
 assert suggestions(cat,'ZZUNKNOWN90210')['needs_category']
 assert topic_for('هدفون XXMISSING90210')[0]=='هدفون'

@pytest.mark.asyncio
async def test_comparison_tool_opens_all_five_and_persists_immutable_reply(env,monkeypatch):
 cat,mem,a,u,s=env;payloads=[]
 class Client:
  def __init__(self,**kw):pass
  async def __aenter__(self):return self
  async def __aexit__(self,*args):pass
  async def post(self,url,**kw):
   payloads.append(kw['json'])
   blocks=[{'type':'tool_use','id':'cmp','name':'compare_products','input':{}}] if len(payloads)==1 else [{'type':'text','text':'مقایسه پنج محصول در جدول و نمودار آماده است.'}]
   return httpx.Response(200,json={'content':blocks})
 use_client(monkeypatch,Client)
 events=[e async for e in a.response(u,s,'همه رو مقایسه کن',mode='simple')]
 cmp=next(e['data'] for e in events if e['type']=='comparison');assert len(cmp['products'])==5
 saved=copy.deepcopy(mem.turns(u,s)[-1]['references']['comparison'])
 assert events[-1]['tools']==['compare_products'] and len(payloads)==2
 mem.results(u,s,{'offers':[],'query':'گوشی'})
 assert mem.turns(u,s)[-1]['references']['comparison']==saved
 assert all(p['system']==payloads[0]['system'] for p in payloads)

@pytest.mark.asyncio
async def test_compare_and_area_api_isolate_sessions_and_reject_invalid_requests(env,monkeypatch,tmp_path):
 cat,mem,a,u,s=env;monkeypatch.setenv('DATA_DIR',str(tmp_path))
 server=importlib.import_module('server')
 for key,value in dict(cat=cat,mem=mem,agent=a,features=a.features).items():monkeypatch.setattr(server,key,value)
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://testserver',cookies={'hamrah_user':u}) as client:
  d=await client.post('/api/compare',json={'session_id':s});assert d.status_code==200 and len(d.json()['products'])==5
  assert (await client.post('/api/compare',json={'session_id':s,'product_ids':['unknown','not-real']})).status_code==422
  assert (await client.post('/api/compare',json={'session_id':s,'product_ids':list(map(str,range(13)))})).status_code==422
  d=await client.post('/api/shopping-areas',json={'session_id':s,'query':'لپ تاپ UNKNOWN90210'});assert d.status_code==200 and d.json()['areas']
  other=mem.session(mem.user())['id']
  for endpoint in ['compare','shopping-areas']:
   assert (await client.post('/api/'+endpoint,json={'session_id':other,'query':'لپ تاپ'})).status_code==404
