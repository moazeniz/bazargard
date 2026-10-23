"""Shared deterministic initial five-product price diversity after eligibility."""
import copy,re
from catalog import normalize

POLICY='initial-price-spread-v1'
def initial(query,message='',previous=None,sort='balanced',profile=None,requested_count=False):
 p=profile or {};q=normalize(query);text=normalize(message or query);previous=previous or {}
 if requested_count or sort!='balanced' or p.get('priority') in ('price','distance') or not q:return False
 if re.search(r'ارزان ترین|ارزون ترین|کمترین قیمت|نزدیک ترین|cheapest|closest|مقایسه|همین|قبلی|همون|بین این',text):return False
 if re.search(r'\b[a-z]{1,14}[ -]?\d[a-z0-9-]*\b',q,re.I):return False
 if re.search(r'(?:\d+|یک|دو|سه|چهار|پنج|شش|ده)\s*(?:تا|محصول|مدل|گزینه)\b',text):return False
 return True

def sample_sql(cte,base):
 """Read a bounded price-stratified pool, rather than a cheap top-N prefix.

 SQL filters price/location/category/reports first. Final typed/credit/opening
 eligibility is checked on all sampled offers before bands/representatives.
 """
 prefix=cte.rstrip()+', ' if cte else 'WITH '
 return prefix+'''price_base AS MATERIALIZED ('''+base+'''),
 representatives AS (SELECT *,COUNT(*) OVER(PARTITION BY product_id) seller_pool_size,ROW_NUMBER() OVER(PARTITION BY product_id ORDER BY effective_price,relevance,offer_id) seller_rank FROM price_base),
 product_pool AS (SELECT *,COUNT(*) OVER() pool_products,NTILE(12) OVER(ORDER BY effective_price,product_id) price_stratum FROM representatives WHERE seller_rank=1),
 sampled_products AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY price_stratum ORDER BY relevance,product_id) stratum_rank FROM product_pool),
 selected_pool AS (SELECT product_id,pool_products FROM sampled_products WHERE stratum_rank<=50)
 SELECT r.*,s.pool_products FROM representatives r JOIN selected_pool s ON s.product_id=r.product_id WHERE r.seller_rank<=4 ORDER BY r.product_id,r.seller_rank'''

def select(offers,profile=None):
 p=profile or {};by_product={}
 for o in offers:
  price=o.get('effective_price_toman',o.get('price_toman'))
  if not isinstance(price,(int,float)) or price<=0:continue
  by_product.setdefault(o['product_id'],[]).append(o)
 representatives=[]
 for pid,sellers in by_product.items():
  sellers.sort(key=lambda o:(o['effective_price_toman'],-o.get('score',0),o['offer_id']));representatives.append(sellers[0])
 prices=sorted({o['effective_price_toman'] for o in representatives})
 if not prices:return [],{'policy':POLICY,'eligible_sample_products':0,'sampling':'SQL price-stratified sample; no eligible products','distribution':{},'notice':'محصول واجد شرایط با قیمت معتبر پیدا نشد.'}
 low=prices[(len(prices)-1)//3];mid=prices[2*(len(prices)-1)//3]
 def band(price):return 'lower' if price<=low else 'middle' if price<=mid else 'upper'
 groups={b:[] for b in ('lower','middle','upper')}
 for o in representatives:groups[band(o['effective_price_toman'])].append(o)
 for values in groups.values():values.sort(key=lambda o:(-o.get('score',0),o.get('relevance',0),o['effective_price_toman'],o['product_id']))
 picked=[]
 for b,n in [('lower',1),('middle',2),('upper',2)]:picked.extend(groups[b][:n])
 seen={o['product_id'] for o in picked}
 for o in sorted(representatives,key=lambda o:(-o.get('score',0),o['effective_price_toman'],o['product_id'])):
  if len(picked)>=5:break
  if o['product_id'] not in seen:picked.append(o);seen.add(o['product_id'])
 labels={'lower':'بازه اقتصادی فهرست','middle':'بازه میانی فهرست','upper':'بازه بالاتر در بودجه شما' if p.get('budget_max_toman') is not None else 'بازه بالاتر فهرست واجد شرایط'}
 result=[]
 for o in picked:
  o=copy.deepcopy(o);tier=band(o['effective_price_toman'])
  o['price_band']={'policy':POLICY,'tier':tier,'label':labels[tier],'price_toman':o['effective_price_toman'],'source':o.get('price_basis','dataset'),'boundaries':{'lower_max':low,'middle_max':mid,'eligible_min':prices[0],'eligible_max':prices[-1]},'population':'eligible_products_in_stratified_catalogue_sample','eligible_sample_products':len(representatives),'notice':'بازه قیمت است؛ کیفیت یا عملکرد از قیمت نتیجه نشده و پوشش کل بازار نیست.'}
  o['seller_alternatives']=[{'offer_id':x['offer_id'],'shop_id':x['shop_id'],'price_toman':x['effective_price_toman'],'price_basis':x.get('price_basis','dataset')} for x in by_product[o['product_id']][1:5]]
  o.setdefault('why',[]).append(labels[tier]+'؛ صرفاً بر پایه قیمت واجد شرایط');result.append(o)
 notice='پنج مدل با هدف یک اقتصادی، دو میانی و دو بالاتر؛ بازه‌ها نسبی و پس از قیود سخت‌اند.'
 if len(prices)<3 or len(result)<5 or any(len(groups[b])<n for b,n in [('lower',1),('middle',2),('upper',2)]):notice='تنوع قیمت یا تعداد مدل‌های واجد شرایط برای نسبت کامل کافی نیست؛ فقط گزینه‌های موجود و بدون تغییر قیود نمایش داده شدند.'
 return result,{'policy':POLICY,'eligible_sample_products':len(representatives),'distinct_prices':len(prices),'boundaries':{'lower_max':low,'middle_max':mid},'distribution':{b:sum(o['price_band']['tier']==b for o in result) for b in groups},'sampling':'SQL price-stratified sample, up to600 matching products and4seller candidates each; final constraints applied before bands','notice':notice}
