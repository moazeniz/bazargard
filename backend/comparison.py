"""Deterministic, source-labelled product comparison; no generated specifications."""
import math,re,time
from collections import OrderedDict
from catalog import normalize
from spec_schema import EXTRA_FIELDS,EXTENSIONS,schema,entries as normalized_entries,comparable

MAX_PRODUCTS=12
MISSING='در داده موجود نیست'
NOTICE='مشخصات از کاتالوگ مسابقه است. قیمت، کمترین آفر دیده‌شده در همین فهرست است؛ قیمت زنده یا کمترین قیمت بازار نیست. داده وب در این مقایسه دریافت نشده است.'

# Only explicit equivalences are merged. Generic capacity/weight fields are never
# interpreted as RAM, SSD, or bare-device weight without the corresponding key/unit.
from product_data import FIELDS,ALIASES,UNITS,scalar,cell,resolve

def reviews(product):
    entries=[s for s in product.get('specs',[]) if re.search(r'\breviews?\b|تعداد نظرات|امتیاز کاربران|امتیاز مشتریان',s.get('key',''),re.I)]
    return {'product_id':product['product_id'],'status':'aggregate_only' if entries else 'missing',
            'entries':[{'key':str(s['key'])[:200],'value':str(s.get('value',''))[:1200]} for s in entries],
            'source':'catalogue','review_text_count':0,'original_source':None,'observed_at':None,
            'notice':'فقط اطلاعات تجمیعی واردشده؛ منبع اصلی و تاریخ مشخص نیست. متن نظر خریداران در اختیار نیست.' if entries else 'متن نظر و امتیاز قابل اتکایی در این کاتالوگ موجود نیست.'}

def build_comparison(catalog,offers,product_ids=None):
    ids=list(dict.fromkeys(product_ids if product_ids is not None else [o['product_id'] for o in offers]))
    if len(ids)<2:raise ValueError('برای مقایسه دست‌کم دو محصول متفاوت انتخاب کنید؛ چند فروشنده یک مدل، یک محصول حساب می‌شوند.')
    if len(ids)>MAX_PRODUCTS:raise ValueError('در هر مقایسه حداکثر ۱۲ محصول جا می‌گیرد؛ چند گزینه را انتخاب کنید.')
    products=[catalog.product(pid) for pid in ids]
    if any(p is None for p in products):raise ValueError('یکی از محصول‌های مقایسه در کاتالوگ پیدا نشد')
    rows=OrderedDict();metadata={}
    for p in products:
        resolved=resolve(p)
        for rid,value in resolved['facts'].items():
            if value['status']=='missing':continue
            if rid in ('ssd','hdd') and resolved['facts'].get('storage',{}).get('status')!='conflict':continue
            label,unit=resolved['fields'][rid]
            rows.setdefault(rid,{pid:[] for pid in ids})[p['product_id']].extend(value['raw'])
            metadata[rid]=(label,unit)
        for spec in resolved['unmapped']:
            rid='raw:'+normalize(spec['section'])+':'+normalize(spec['key'])
            rows.setdefault(rid,{pid:[] for pid in ids})[p['product_id']].append(spec)
            metadata[rid]=(spec['key'],None)
        for rid in resolved['schema']:
            if rid not in metadata:
                metadata[rid]=resolved['fields'][rid];rows.setdefault(rid,{pid:[] for pid in ids})
    output=[]
    prices=[]
    for pid in ids:
        seen=[o for o in offers if o['product_id']==pid and isinstance(o.get('effective_price_toman',o.get('price_toman')),(int,float))]
        seen=[o for o in seen if math.isfinite(o.get('effective_price_toman',o.get('price_toman'))) and o.get('effective_price_toman',o.get('price_toman'))>=0]
        best=min(seen,key=lambda o:o.get('effective_price_toman',o.get('price_toman'))) if seen else None
        prices.append({'value':str(best.get('effective_price_toman',best.get('price_toman'))) if best else MISSING,'numeric':best.get('effective_price_toman',best.get('price_toman')) if best else None,
                       'status':'recorded' if best else 'missing','source':best.get('price_basis','dataset') if best else 'unavailable',
                       'offer_id':best.get('offer_id') if best else None,'shop_name':best.get('shop_name') if best else None,
                       'price_band':best.get('price_band') if best else None,'observed_at':best.get('last_price_changed_at') if best else None,'raw':[]})
    output.append({'id':'price','label':'کمترین قیمت دیده‌شده','unit':'toman','cells':prices})
    order=[f[0] for f in FIELDS]
    ordered=sorted(rows,key=lambda rid:order.index(rid) if rid in order else len(order))
    for rid in ordered:
        label,unit=metadata[rid]
        output.append({'id':rid,'label':label,'unit':unit,'cells':[cell(rows[rid][pid],unit) for pid in ids]})
    charts=[]
    for row in output:
        tokens=[(c['status'],c['numeric'] if c['numeric'] is not None else normalize(c['value'])) for c in row['cells']]
        row['different']=len(set(tokens))>1
        if row['unit'] and sum(c['numeric'] is not None for c in row['cells'])>=2:
            charts.append({'id':row['id'],'label':row['label'],'unit':row['unit'],'unit_label':UNITS.get(row['unit'],row['unit']),
                           'values':[{'product_id':pid,'value':c['numeric'],'status':c['status']} for pid,c in zip(ids,row['cells'])],
                           'note':'مبدأ نمودار صفر است. مقدار بزرگ‌تر لزوماً انتخاب بهتر نیست؛ داده نامشخص صفر محسوب نمی‌شود.'})
    return {'version':2,'rules_version':__import__('product_data').VERSION,'created':time.time(),'product_ids':ids,'products':[{k:p.get(k) for k in ['product_id','persian_title','brand_name','category_name','model']} for p in products],
            'rows':[r for r in output if not r['id'].startswith('raw:')], 'detailed_rows':[r for r in output if r['id'].startswith('raw:')], 'category_schemas':{p['product_id']:schema(p) for p in products},'charts':charts,'reviews':[reviews(p) for p in products],
            'notice':NOTICE,'source':'catalogue','web_enriched':False,'price_scope':'current_visible_offers'}
