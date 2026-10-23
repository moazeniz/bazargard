import asyncio,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import user_reviews as U
from llm_fake import output,is_web_search

PRODUCT={'product_id':'p1','persian_title':'مایکروویو شیائومی مدل MWB010 ظرفیت ۲۰ لیتر','model':'','brand_name':'شیائومی'}

class Resp:
    def __init__(self,data,code=200):self.status_code=code;self.data=data
    def json(self):return self.data
class Http:
    async def get(self,url,params=None):
        if url.endswith('/search/'):
            return Resp({'data':{'products':[{'id':9,'title_fa':'توستر شیائومی','title_en':''},{'id':7,'title_fa':'مایکروویو 20 لیتر شیائومی مدل MWB010-2A','title_en':''}]}})
        if '/rate-review/products/7/' in url:
            return Resp({'data':{'pager':{'total_pages':1,'total_items':3},'comments':[
                {'id':1,'body':'گرمایش سریع و صداش کمه','rate':'5','review_user_type':'buyer','reactions':{'likes':4,'dislikes':0},'advantages':['کم‌صدا'],'disadvantages':[]},
                {'id':2,'body':'ارسال فروشنده خیلی دیر بود','rate':'2','review_user_type':'buyer','reactions':{'likes':1,'dislikes':0}},
                {'id':3,'body':'','rate':'4'}]}})
        return Resp({},404)
class Client:
    def __init__(self,**kw):pass
    async def __aenter__(self):return self
    async def __aexit__(self,*a):return False
    async def post(self,url,headers=None,json=None):
        if is_web_search(json):
            reviews=[{'url':'https://www.amazon.com/review/1','quote':'Heats evenly and the dial is simple, very happy after two months.','rating':5,'summary_fa':'راضی'},
                     {'url':'https://www.example.com/spec','quote':'Capacity 20L, power 700W, mechanical control, white colour body.','summary_fa':'مشخصات'},
                     {'url':'https://www.example.com/fake','quote':'Best microwave ever made, ten stars from me!!','summary_fa':'جعلی'},
                     {'url':'https://forum.example.org/t/1','quote':'Door broke after a week, very disappointed.','summary_fa':'در پس از یک هفته شکست.'},
                     {'url':'https://uncited.example.org/x','quote':'Lovely little oven for the price.','summary_fa':'ارزان و خوب'}]
            return Resp(output(__import__('json').dumps({'reviews':reviews}),citations=['https://www.amazon.com/review/1','https://forum.example.org/t/1']))
        payload=__import__('json').loads(json['input'][0]['content'])
        assert {r['id'] for r in payload['digikala']}=={'d1','d2'}
        assert {w['id'] for w in payload['web']}=={'w1','w2'}
        return Resp(output(__import__('json').dumps({'summary':'خریداران از سرعت گرمایش راضی‌اند.',
            'pros':[{'text':'گرمایش سریع','review_ids':['d1','w1']},{'text':'ادعای بی‌پشتوانه','review_ids':['zz']}],
            'cons':[],'seller_issues':[{'text':'تأخیر ارسال فروشنده','review_ids':['d2']}],
            'web':[{'id':'w1','kind':'owner_review','sentiment':'positive','fa':'یکنواخت گرم می‌کند و پیچش ساده است.'},{'id':'w2','kind':'editorial','fa':'مشخصات'}]},ensure_ascii=False)))

PAGES={'https://www.amazon.com/review/1':'Customer reviews\nHeats evenly and the dial is simple, very happy after two months.\n5 stars',
       'https://www.example.com/spec':'Spec sheet: Capacity 20L, power 700W, mechanical control, white colour body.',
       'https://www.example.com/fake':'This page has other reviews only.'}
def fetcher(url,deadline):
    return {'url':url,'status':'readable','text':PAGES[url],'title':'Page'} if url in PAGES else {'url':url,'status':'blocked'}

def run(tmp_path,monkeypatch):
    monkeypatch.setenv('BOXAPI_KEYS','t')
    return asyncio.run(U.UserReviews(tmp_path/'c.db',client_factory=Client,http=Http(),fetcher=fetcher).collect(PRODUCT))

def test_digikala_matches_exact_model_and_reads_reviews(tmp_path,monkeypatch):
    r=run(tmp_path,monkeypatch)
    assert r['digikala']['product_id']==7 and r['digikala']['average']==3.5
    assert [x['id'] for x in r['reviews']]==['d1','d2']  # empty review dropped

def test_only_cited_owner_reviews_kept_and_translated(tmp_path,monkeypatch):
    r=run(tmp_path,monkeypatch)
    assert len(r['web_reviews'])==1
    w=r['web_reviews'][0]
    assert w['url'].startswith('https://www.amazon.com') and w['fa'].startswith('یکنواخت') and 'Heats evenly' in w['text']

def test_unverifiable_quotes_are_never_buyer_quotes(tmp_path,monkeypatch):
    r=run(tmp_path,monkeypatch)
    texts=' '.join(w['text'] for w in r['web_reviews'])
    # Quote missing from its readable page: dropped. Unreadable page: only a labelled summary.
    assert 'ten stars' not in texts and 'Door broke' not in texts and 'Lovely' not in texts
    assert [s['url'] for s in r['web_summaries']]==['https://forum.example.org/t/1']
    s=r['web_summaries'][0];assert s['kind']=='search_summary' and s['text']=='در پس از یک هفته شکست.' and 'نقل‌قول' in s['label']
    assert r['web_reviews'][0]['verified']=='page_quote'

def test_summary_points_need_real_review_ids(tmp_path,monkeypatch):
    r=run(tmp_path,monkeypatch)
    assert [p['text'] for p in r['pros']]==['گرمایش سریع'] and r['pros'][0]['count']==2
    assert r['seller_issues'][0]['review_ids']==['d2']

def test_no_model_code_skips_digikala(tmp_path,monkeypatch):
    monkeypatch.delenv('BOXAPI_KEYS',raising=False)
    r=asyncio.run(U.UserReviews(tmp_path/'c.db',http=Http()).collect({'product_id':'p2','persian_title':'لامپ ماکروفر','model':''}))
    assert r['status']=='none' and r['digikala']['status']=='no_model_code'


def test_anthropic_reviews_use_verbatim_citations_only(tmp_path,monkeypatch):
    import httpx as hx
    monkeypatch.delenv('BOXAPI_KEYS',raising=False);monkeypatch.setenv('LLM_PROVIDER','anthropic')
    monkeypatch.setenv('RELAY_URL','http://gate.test/meow-llm');monkeypatch.setenv('RELAY_TOKEN','gate-token')
    class Anthropic:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):return False
        async def post(self,url,headers=None,json=None):
            assert url.endswith('/v1/messages')
            if json.get('tools'):
                assert json['tools'][0]['type']=='web_search_20250305'
                return hx.Response(200,json={'content':[{'type':'text','text':'Owners say it heats well.','citations':[
                    {'url':'https://www.amazon.com/review/1','title':'Amazon','cited_text':'Heats evenly and the dial is simple, very happy after two months.'},
                    {'url':'https://www.example.com/spec','title':'Spec','cited_text':'Capacity 20L, power 700W, mechanical control, white colour body.'},
                    {'url':'https://www.example.com/short','title':'S','cited_text':'Too short'}]}]})
            payload=__import__('json').loads(json['messages'][0]['content'])
            assert {w['id'] for w in payload['web']}=={'w1','w2'}
            return hx.Response(200,json={'content':[{'type':'text','text':__import__('json').dumps({'summary':'s','pros':[{'text':'گرمایش یکنواخت','review_ids':['w1']}],'cons':[],'seller_issues':[],
                'web':[{'id':'w1','kind':'owner_review','sentiment':'positive','fa':'یکنواخت گرم می‌کند.'},{'id':'w2','kind':'editorial','fa':'مشخصات'}]},ensure_ascii=False)}]})
    import llm;llm.reset()
    r=asyncio.run(U.UserReviews(tmp_path/'a.db',client_factory=Anthropic,http=Http()).collect(PRODUCT))
    assert [w['url'] for w in r['web_reviews']]==['https://www.amazon.com/review/1'] and r['web_reviews'][0]['verified']=='provider_citation'
    assert r['web_reviews'][0]['text'].startswith('Heats evenly') and r['web_summaries']==[] and r['pros'][0]['review_ids']==['w1']
