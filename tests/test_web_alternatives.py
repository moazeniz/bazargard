import asyncio,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import web_alternatives as W
from llm_fake import output,is_web_search,user_text
SUMMARY_URL='https://shop-b.ir/tv-55'

SHOP={'url':'https://tvshop.ir/samsung-55','status':'readable','title':'خرید تلویزیون سامسونگ ۵۵ اینچ',
      'text':'تلویزیون سامسونگ 55 اینچ 4K مدل CU7000\nقیمت: 32,500,000 تومان\nفروشگاه نورا\nآدرس: تهران، خیابان جمهوری، پاساژ امجد، پلاک 12\nخرید حضوری در فروشگاه','links':[]}
ONLINE={'url':'https://example.com/tv','status':'readable','title':'LG 55 inch','text':'LG 55UR7550 55 inch 4K Smart TV price 30,000,000','links':[]}

class Response:
    def __init__(self,data,raw=None):self.status_code=200;self.data=data;self.raw=raw
    def json(self):return self.raw or output(json.dumps(self.data,ensure_ascii=False))
class Client:
    def __init__(self,**kw):pass
    async def __aenter__(self):return self
    async def __aexit__(self,*a):return False
    async def post(self,url,headers=None,json=None):
        if is_web_search(json):
            if 'URLs only' in user_text(json):return Response(None,output('https://tvshop.ir/samsung-55\nhttps://www.youtube.com/x\nhttps://example.com/tv\nhttps://torob.com/p/1'))
            items=[{'url':SUMMARY_URL,'title':'تلویزیون ایکس‌ویژن 55 اینچ 4K','price':'25,000,000 تومان','store':'فروشگاه ب','address':'تهران، خیابان ولیعصر','match':'exact','why':'۵۵ اینچ'},
                   {'url':'https://example.com/tv','title':'invented TV not on page','match':'close'}]
            return Response(None,output(__import__('json').dumps({'items':items},ensure_ascii=False),citations=[SUMMARY_URL]))
        payload=__import__('json').loads(json['input'][0]['content'])
        if json['instructions']==W.PLAN_PROMPT:
            return Response({'product':'تلویزیون ۵۵ اینچ 4K','requirements':['۵۵ اینچ','4K'],'city':'تهران','queries':['خرید تلویزیون 55 اینچ 4K تهران']})
        if payload['page']['url']==SHOP['url']:
            return Response({'store':{'name':'فروشگاه نورا','address':'تهران، خیابان جمهوری، پاساژ امجد، پلاک 12','city':'تهران','phone':'','in_person':'yes',
                              'quotes':{'name':'فروشگاه نورا','address':'تهران، خیابان جمهوری، پاساژ امجد، پلاک 12','in_person':'خرید حضوری در فروشگاه'}},
                             'listings':[{'title':'تلویزیون سامسونگ 55 اینچ 4K مدل CU7000','price':'32,500,000 تومان','match':'exact','why':'۵۵ اینچ و 4K',
                                          'quotes':{'title':'تلویزیون سامسونگ 55 اینچ 4K مدل CU7000','price':'32,500,000 تومان'}},
                                         {'title':'تلویزیون ال‌جی 65 اینچ','price':'','match':'close','why':'x','quotes':{'title':'invented title'}}]})
        return Response({'store':{'name':'LG','address':'Tehran fake street','in_person':'yes','quotes':{'name':'LG','address':'not on page','in_person':'not on page'}},
                         'listings':[{'title':'LG 55UR7550 55 inch 4K Smart TV','price':'30,000,000','match':'close','why':'online','quotes':{'title':'LG 55UR7550 55 inch 4K Smart TV','price':'30,000,000'}}]})

class Geo:
    async def locate(self,address,city):return {'lat':35.69,'lon':51.42,'precision':'address','matched':address}

def fetcher(url,deadline):
    return {SHOP['url']:SHOP,ONLINE['url']:ONLINE}.get(url,{'url':url,'status':'unavailable'})

def run(monkeypatch,updates):
    monkeypatch.setenv('BOXAPI_KEYS','t');monkeypatch.delenv('WEB_GATEWAY_URL',raising=False)
    w=W.WebAlternatives(':memory:',client_factory=Client,fetcher=fetcher,geocoder=Geo())
    return asyncio.run(w.run('تلویزیون ۵۵ اینچ 4K',{'lat':35.70,'lon':51.41},on_update=updates.append))

def test_listings_are_quote_verified_located_and_ranked(monkeypatch):
    updates=[];result=run(monkeypatch,updates)
    titles=[i['title'] for i in result['items']]
    assert titles[0]=='تلویزیون سامسونگ 55 اینچ 4K مدل CU7000'
    assert 'تلویزیون ال‌جی 65 اینچ' not in titles  # unquoted title dropped
    top=result['items'][0]
    assert top['store']['in_person']=='yes' and top['lat']==35.69 and top['distance_km'] is not None and top['maps_url']
    assert top['price']=='32,500,000 تومان'
    online=next(i for i in result['items'] if 'LG' in i['title'])
    # Address and in-person claims not present on the page are discarded.
    assert online['store']['address']=='' and online['store']['in_person']=='unknown' and online['lat'] is None

def test_unreadable_pages_only_add_labelled_search_summaries(monkeypatch):
    updates=[];result=run(monkeypatch,updates)
    summary=[i for i in result['items'] if i.get('verification')=='search_summary']
    assert [i['url'] for i in summary]==[SUMMARY_URL] and summary[0]['label'] and summary[0]['lat'] is None
    assert result['items'][-1]['url']==SUMMARY_URL  # verified page listings rank first
    assert not any(i['title']=='invented TV not on page' for i in result['items'])

def test_blocked_sources_skipped_and_progress_streamed(monkeypatch):
    updates=[];result=run(monkeypatch,updates)
    urls={s['url'] for s in result['sources']}
    assert not any('youtube' in u or 'torob.com' in u for u in urls)
    stages=[u['stage'] for u in updates]
    assert stages[0]=='plan' and 'read' in stages and stages[-1]=='done'
    assert any(u['items'] for u in updates[:-1])  # items visible before the end
    assert result['status']=='completed' and 'ترب' in result['notice']

def test_no_model_returns_unavailable(monkeypatch):
    monkeypatch.delenv('BOXAPI_KEYS',raising=False)
    w=W.WebAlternatives(':memory:',client_factory=Client,fetcher=fetcher,geocoder=Geo())
    assert asyncio.run(w.run('کفش کوهنوردی سایز ۴۲',{}))['status']=='unavailable'
