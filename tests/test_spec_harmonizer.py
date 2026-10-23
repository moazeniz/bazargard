import asyncio,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import spec_harmonizer as H
from llm_fake import output,is_web_search,user_text
UNREADABLE='https://specs.example.net/apple-iphone-16e-refresh'

PHONE={'product_id':'p1','persian_title':'گوشی اپل iPhone 16e ظرفیت ۱۲۸ گیگابایت','brand_name':'اپل','category_name':'گوشی موبایل',
       'specs':[{'section':'حافظه','key':'حافظه RAM','value':'8 گیگابایت'},{'section':'بدنه','key':'ابعاد/ وزن','value':'146.7x71.5x7.8 میلی‌متر، وزن 167 گرم'},
                {'section':'باتری','key':'ظرفیت باتری','value':'3961 mAh'},{'section':'سایر','key':'کد محصول','value':'MD1Q4'}]}
LAPTOP={'product_id':'p2','persian_title':'لپ تاپ اچ‌پی OMEN WF0020NIA پردازنده i7 13700HX رم ۱۶ گیگابایت SSD ۵۱۲ گیگابایت','brand_name':'اچ‌پی','category_name':'لپ تاپ و الترابوک',
        'specs':[{'section':'نمایشگر','key':'صفحه نمایش','value':'16.1 اینچ IPS 165Hz'},{'section':'حافظه','key':'حافظه HDD','value':'ندارد'},
                 {'section':'حافظه','key':'ظرفیت هارد داخلی (SSD)','value':'512GB'},{'section':'باتری','key':'باتری','value':'6 سلولی 70 وات ساعت'}]}
ROWS=[{'id':'screen','label':'اندازه نمایشگر','en':'screen size','unit':'inch','core':True,'web':True},
      {'id':'ram','label':'حافظه رم','en':'RAM','unit':'GB','core':True,'web':True},
      {'id':'storage','label':'حافظه داخلی','en':'storage','unit':'GB','core':True,'web':True},
      {'id':'weight','label':'وزن','en':'weight','unit':'kg','core':True,'web':True},
      {'id':'battery','label':'باتری','en':'battery','unit':'Wh','core':True,'web':True},
      {'id':'refresh','label':'نرخ نوسازی','en':'refresh rate','unit':'Hz','core':False,'web':True},
      {'id':'color','label':'رنگ','en':'color','unit':None,'core':False,'web':False}]
EXTRACT={
 'p1':{'model_name':'Apple iPhone 16e','facts':[
   {'row':'ram','v':'۸ گیگابایت','n':8,'src':[2],'q':'8 گیگابایت'},
   {'row':'weight','v':'۱۶۷ گرم','n':0.167,'src':[3],'q':'وزن 167 گرم'},
   {'row':'storage','v':'۱۲۸ گیگابایت','n':128,'src':[0],'q':'ظرفیت ۱۲۸ گیگابایت'},
   {'row':'screen','v':'۶٫۱ اینچ','n':6.1,'src':[2],'q':'6.1 inch'}]},  # fabricated quote -> rejected
 'p2':{'model_name':'HP OMEN 16-wf0020nia','facts':[
   {'row':'screen','v':'۱۶٫۱ اینچ','n':16.1,'src':[2],'q':'16.1 اینچ'},
   {'row':'refresh','v':'۱۶۵ هرتز','n':165,'src':[2],'q':'165Hz'},
   {'row':'ram','v':'۱۶ گیگابایت','n':16,'src':[0],'q':'رم ۱۶ گیگابایت'},
   {'row':'storage','v':'۵۱۲ گیگابایت SSD','n':512,'src':[4],'q':'512GB'},
   {'row':'battery','v':'۷۰ وات‌ساعت','n':70,'src':[5],'q':'70 وات ساعت'},
   {'row':'weight','v':'۲٫۴ کیلوگرم','n':2.4,'src':[5],'q':'6 سلولی'}]},  # number not in quote -> rejected
}
PAGE={'url':'https://www.gsmarena.com/apple_iphone_16e-13395.php','status':'readable','title':'Apple iPhone 16e - Full phone specifications',
      'text':'Apple iPhone 16e\nDisplay Size 6.1 inches, 91.7 cm2\nBattery Li-Ion 3961 mAh\nWeight 167 g','links':[]}
OTHER={'url':'https://www.example-shop.com/galaxy-a55','status':'readable','title':'Samsung Galaxy A55','text':'Galaxy A55 Display 6.6 inches Weight 213 g','links':[]}

class Response:
    def __init__(self,data,raw=None):self.status_code=200;self.data=data;self.raw=raw
    def json(self):return self.raw or output(json.dumps(self.data,ensure_ascii=False))
class Client:
    calls=[]
    def __init__(self,**kw):pass
    async def __aenter__(self):return self
    async def __aexit__(self,*a):return False
    async def post(self,url,headers=None,json=None):
        if is_web_search(json):
            Client.calls.append('search');prompt=user_text(json)
            if 'page URLs only' in prompt:return Response(None,output(PAGE['url']+'\nhttps://www.youtube.com/watch?v=1\n'+OTHER['url']))
            # Summary fallback: one value from a page unreadable here (labelled), one invented quote on a readable page (dropped).
            facts=[{'row':'refresh','v':'۶۰ هرتز','n':60,'url':UNREADABLE,'q':'60Hz refresh rate'},{'row':'battery','v':'x','n':99,'url':PAGE['url'],'q':'invented 99 Wh'}]
            return Response(None,output(__import__('json').dumps({'page_product':'Apple iPhone 16e','facts':facts}),citations=[UNREADABLE]))
        system=json['instructions'];payload=__import__('json').loads(json['input'][0]['content']);Client.calls.append(system[:20])
        if system==H.PLAN_PROMPT:return Response({'rows':ROWS})
        if system==H.EXTRACT_PROMPT:
            title=payload['entries'][0]
            return Response(EXTRACT['p1' if 'iPhone' in title else 'p2'])
        if system==H.WEB_PROMPT:
            if 'A55' in payload['page']['title']:return Response({'page_product':'Galaxy A55','match':'different','facts':[]})
            return Response({'page_product':'Apple iPhone 16e','match':'exact','facts':[{'row':'screen','v':'۶٫۱ اینچ','n':6.1,'q':'Display Size 6.1 inches'},{'row':'battery','v':'x','n':99,'q':'invented 99 Wh'}]})
        raise AssertionError(system)

class Catalog:
    def product(self,pid):return {'p1':PHONE,'p2':LAPTOP}[pid]

def fetcher(url,deadline):
    return {PAGE['url']:PAGE,OTHER['url']:OTHER}.get(url,{'url':url,'status':'unavailable'})

def base():
    price={'id':'price','label':'کمترین قیمت دیده‌شده','unit':'toman','cells':[{'value':'1','numeric':1,'status':'recorded','source':'dataset','raw':[]},{'value':'2','numeric':2,'status':'recorded','source':'dataset','raw':[]}]}
    return {'version':2,'created':1,'product_ids':['p1','p2'],'products':[],'rows':[price],'detailed_rows':[],'charts':[],'reviews':[]}

def run(tmp_path,monkeypatch,web=True):
    monkeypatch.setenv('BOXAPI_KEYS','test');monkeypatch.delenv('WEB_GATEWAY_URL',raising=False);Client.calls=[]
    h=H.Harmonizer(tmp_path/'cache.db',client_factory=Client,fetcher=fetcher)
    return asyncio.run(h.complete(Catalog(),base(),['p1','p2'],web=web)),h

def cell(result,row,index):return next(r for r in result['rows'] if r['id']==row)['cells'][index]

def test_differently_named_and_composite_fields_align(tmp_path,monkeypatch):
    result,_=run(tmp_path,monkeypatch,web=False)
    assert cell(result,'ram',0)['numeric']==8 and cell(result,'ram',1)['numeric']==16
    assert cell(result,'weight',0)['numeric']==0.167 and cell(result,'weight',0)['source']=='catalogue'
    assert cell(result,'storage',0)['source']=='listing_title'
    assert cell(result,'battery',1)['numeric']==70 and cell(result,'refresh',1)['numeric']==165
    # SSD capacity next to "no HDD" never becomes a conflict.
    assert cell(result,'storage',1)['status']=='recorded'

def test_unsupported_quotes_and_numbers_are_rejected(tmp_path,monkeypatch):
    result,_=run(tmp_path,monkeypatch,web=False)
    assert cell(result,'screen',0)['status']=='missing'
    assert cell(result,'weight',1)['status']=='missing'

def test_unused_entries_stay_inspectable_and_price_kept(tmp_path,monkeypatch):
    result,_=run(tmp_path,monkeypatch,web=False)
    assert result['rows'][0]['id']=='price'
    assert any(r['label']=='کد محصول' for r in result['detailed_rows'])
    assert not any(r['label']=='حافظه RAM' for r in result['detailed_rows'])
    assert not any(r['id']=='color' for r in result['rows'])  # empty non-core row hidden

def test_web_fills_missing_with_verified_quote_and_identity(tmp_path,monkeypatch):
    result,_=run(tmp_path,monkeypatch)
    screen=cell(result,'screen',0)
    assert screen['status']=='web_completed' and screen['numeric']==6.1 and screen['source']=='public_spec'
    assert screen['research_evidence'][0]['url']==PAGE['url'] and screen['research_evidence'][0]['source_type']=='spec_reference'
    # Invented quote is dropped; the unrelated product page never contributes.
    assert cell(result,'battery',0)['status']=='missing'
    research=next(r for r in result['spec_research'] if r['product_id']=='p1')['research']
    statuses={s['url']:s['status'] for s in research['sources']}
    assert statuses[OTHER['url']] in ('model_not_found','different_product')
    assert not any('youtube' in u for u in statuses)

def test_unverifiable_web_search_values_are_labelled_summaries(tmp_path,monkeypatch):
    result,_=run(tmp_path,monkeypatch)
    refresh=cell(result,'refresh',0)
    assert refresh['status']=='web_completed' and refresh['research_status']=='search_summary' and refresh['numeric']==60
    item=refresh['research_evidence'][0]
    assert item['status']=='search_summary' and item['url']==UNREADABLE and 'خلاصه جست‌وجوی وب' in item['model_match']
    # A readable page that lacks the cited quote never yields a value.
    assert cell(result,'battery',0)['status']=='missing'
    # Catalogue values stay catalogue values.
    assert cell(result,'refresh',1)['source']=='catalogue'

def test_results_are_cached(tmp_path,monkeypatch):
    run(tmp_path,monkeypatch)
    monkeypatch.setenv('BOXAPI_KEYS','test');Client.calls=[]
    h=H.Harmonizer(tmp_path/'cache.db',client_factory=Client,fetcher=fetcher)
    asyncio.run(h.complete(Catalog(),base(),['p1','p2']))
    assert Client.calls==[]

def test_without_model_returns_none_for_fallback(tmp_path,monkeypatch):
    monkeypatch.delenv('BOXAPI_KEYS',raising=False)
    h=H.Harmonizer(tmp_path/'cache.db',client_factory=Client,fetcher=fetcher)
    assert asyncio.run(h.complete(Catalog(),base(),['p1','p2']))is None

def test_quote_and_number_helpers():
    assert H.quote_in('وزن ۱۶۷ گرم','ابعاد، وزن 167 گرم')
    assert H.number_supported(1024,'1 ترابایت') and H.number_supported(0.167,'167g') and not H.number_supported(70,'6 سلولی')

def test_previews_report_catalogue_table_then_web_progress(tmp_path,monkeypatch):
    monkeypatch.setenv('BOXAPI_KEYS','test')
    seen=[]
    h=H.Harmonizer(tmp_path/'cache.db',client_factory=Client,fetcher=fetcher)
    final=asyncio.run(h.complete(Catalog(),base(),['p1','p2'],on_preview=lambda r,st:seen.append((r,st))))
    stages=[st['stage'] for _,st in seen]
    assert stages[0]=='harmonize' and 'web' in stages
    first=next(r for r,st in seen if r is not None)
    # Catalogue values are visible before any web work; gaps are marked pending, not missing.
    assert cell(first,'ram',0)['numeric']==8
    assert cell(first,'screen',0).get('pending') and cell(first,'screen',0)['value']=='در حال جست‌وجو در وب…'
    assert not any(c.get('pending') for r in final['rows'] for c in r['cells'])
    assert seen[-1][1]['products_total']==2
