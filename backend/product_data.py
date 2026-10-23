"""Versioned typed product projection. Raw catalogue and seller records stay immutable."""
import math,re,json,hashlib,sqlite3,functools,zlib,threading,copy
from pathlib import Path
from collections import OrderedDict
from catalog import normalize
from spec_schema import EXTRA_FIELDS,EXTENSIONS,schema,entries as normalized_entries,comparable,mapped_key
VERSION='canonical-5-'+hashlib.sha256(Path(__file__).read_bytes()+Path(__file__).with_name('spec_schema.py').read_bytes()).hexdigest()[:10]
MISSING='در داده موجود نیست'
FIELDS=[
 ('brand','برند',None,['برند','نام برند','brand','brand name','brand_name']),
 ('cpu','مدل پردازنده',None,['مدل پردازنده','مدل cpu','cpu model','processor model']),
 ('gpu','مدل گرافیک',None,['مدل گرافیک','مدل پردازنده گرافیکی','gpu model']),
 ('ram','حافظه رم','GB',['مقدار حافظه رم','ظرفیت حافظه رم','ظرفیت رم','حافظه رم','ram','ram capacity']),
 ('ssd','ظرفیت SSD','GB',['ظرفیت SSD','ظرفیت حافظه SSD','ssd capacity']),
 ('hdd','ظرفیت HDD','GB',['ظرفیت HDD','ظرفیت حافظه HDD','hdd capacity']),
 ('storage','حافظه داخلی','GB',['ظرفیت حافظه داخلی','internal storage']),
 ('weight','وزن دستگاه','kg',['وزن','وزن (کیلوگرم)','وزن کیلوگرم','وزن (گرم)','weight','weight (kg)','weight (g)']),
 ('screen','اندازه نمایشگر','inch',['سایز صفحه نمایش (اینچ)','اندازه صفحه نمایش','اندازه نمایشگر','screen size','display size']),
 ('refresh','نرخ نوسازی نمایشگر','Hz',['نرخ بروزرسانی تصویر','نرخ به روز رسانی تصویر','نرخ نوسازی تصویر','نرخ نوسازی','refresh rate']),
 ('battery','ظرفیت انرژی باتری','Wh',['ظرفیت باتری (وات ساعت)','battery capacity (wh)']),
]
FIELDS += EXTRA_FIELDS
FIELDS=[(k,label,u,aliases+EXTENSIONS.get(k,[])) for k,label,u,aliases in FIELDS]
ALIASES={normalize(alias):(key,label,unit) for key,label,unit,aliases in FIELDS for alias in aliases}
UNITS={'GB':'گیگابایت','kg':'کیلوگرم','inch':'اینچ','Hz':'هرتز','Wh':'وات‌ساعت','toman':'تومان','mAh':'میلی‌آمپرساعت','mm':'میلی‌متر','ohm':'اهم','W':'وات','L':'لیتر','bar':'بار','V':'ولت','A':'آمپر','m':'متر','mm2':'میلی‌متر مربع'}

def scalar(value,key,unit):
    """Accept a single explicit quantity; reject ranges, dimensions, and claims."""
    if not unit:return None
    raw=str(value).replace('”','"').replace('“','"').replace('Ω','ohm').translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫٬','01234567890123456789.,')).strip().lower()
    raw=re.sub(r'^[\"″]\s*(\d+(?:\.\d+)?)$',r'\1 inch',raw)
    raw=raw.replace('²','2')
    raw=raw.replace('\u200c',' ').replace('،',',')
    raw=re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))','',raw)
    match=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*([a-zآ-ی2\s\"″]*)\s*',raw)
    if not match:return None
    n=float(match[1]);suffix=normalize(match[2]);k=normalize(key)
    factors={
      'GB':{'gb':1,'گیگابایت':1,'گیگا بایت':1,'tb':1024,'ترابایت':1024,'ترا بایت':1024,'mb':1/1024},
      'kg':{'kg':1,'کیلوگرم':1,'کیلو گرم':1,'g':.001,'grams':.001,'kilograms':1,'gr':.001,'گرم':.001},
      'inch':{'inch':1,'inches':1,'in':1,'اینچ':1,'':1},
      'Hz':{'hz':1,'هرتز':1,'':1},
      'V':{'v':1,'ولت':1},'A':{'a':1,'آمپر':1,'امپر':1},
      'mAh':{'mah':1,'میلی آمپر ساعت':1,'میلی امپر ساعت':1},
      'm':{'m':1,'متر':1,'cm':.01,'mm':.001},'mm2':{'mm2':1,'میلی متر مربع':1,'cm2':100},
      'mm':{'m':1000,'متر':1000,'mm':1,'میلی متر':1,'cm':10,'سانتی متر':10},
      'ohm':{'ohm':1,'ohms':1,'اهم':1},'W':{'w':1,'وات':1,'kw':1000,'کیلووات':1000},'L':{'l':1,'لیتر':1,'ml':.001},'bar':{'bar':1,'بار':1},
      'Wh':{'wh':1,'whr':1,'وات ساعت':1,'':1},
    }
    if not suffix:
        if unit=='kg':suffix='kg' if ('کیلوگرم' in k or 'kg' in k) else 'g' if ('گرم' in k or k.endswith(' g')) else '?'
        if unit=='GB':suffix='gb' if ('gb' in k or 'گیگابایت' in k) else '?'
    if unit=='inch' and not suffix and not any(x in k for x in ('اینچ','inch')) and not any(x in raw for x in ('"','″')):return None
    if not suffix:
        for name in sorted(factors.get(unit,{}),key=len,reverse=True):
            if name and re.search(r'(?<![a-z])'+re.escape(name)+r'(?![a-z])',k):suffix=name;break
    factor=factors.get(unit,{}).get(suffix)
    if factor is None or not math.isfinite(n) or n<=0:return None
    return round(n*factor,6)

def cell(entries,unit=None):
    if not entries:return {'value':MISSING,'status':'missing','numeric':None,'source':'catalogue','raw':[]}
    groups=OrderedDict()
    for e in entries:
        number=scalar(e['value'],e['key'],unit)
        token=('number',number) if number is not None else ('text',semantic(e.get('field') or ALIASES.get(normalize(e['key']),(None,None,None))[0],e['value']))
        groups.setdefault(token,[]).append(e)
    # Distinct USB revisions describe complementary ports, not contradictory counts.
    if entries[0].get('field') in ('usb_a','usb_c','ports') and len({e['key'] for e in entries})==len(entries) and len(entries)>1:
        return {'value':'؛ '.join(e['key']+': '+e['value'] for e in entries),'status':'recorded','numeric':None,'source':'catalogue','raw':entries}
    conflict=len(groups)>1
    first=entries[0];numeric=scalar(first['value'],first['key'],unit) if not conflict else None
    return {'value':' / '.join(dict.fromkeys(e['value'] for e in entries)) if conflict else first['value'],'status':'conflict' if conflict else 'recorded',
            'numeric':numeric,'source':'listing_title' if all(e.get('origin')=='listing_title' for e in entries) else 'catalogue','raw':entries}

FIELD_META={k:(label,unit) for k,label,unit,_ in FIELDS}

def semantic(field,value):
    if field=='resolution':
        m=re.fullmatch(r'(\d+)x(\d+)',comparable(value))
        if m:return 'x'.join(str(n) for n in sorted((int(m[1]),int(m[2])),reverse=True))
    if field=='cpu':
        m=re.search(r'(?<![a-z0-9])(\d{4,5}[uhkpgx]{1,3})(?![a-z0-9])',str(value),re.I)
        if m:return m[1].lower()
    if field=='storage_type' and re.search(r'\bSSD\b',str(value),re.I):return 'ssd'
    return comparable(value)

def field_id(key):
    return key if key in FIELD_META else mapped_key(key,FIELDS)

def fingerprint(product):
    return hashlib.sha256(json.dumps([VERSION,product.get('persian_title'),product.get('brand_name'),product.get('category_name'),product.get('specs')],ensure_ascii=False,sort_keys=True).encode()).hexdigest()

@functools.lru_cache(maxsize=4096)
def _resolve(encoded):
    product=json.loads(encoded);groups={};unknown=[]
    for e in normalized_entries(product,FIELDS):
        if e['field']:groups.setdefault(e['field'],[]).append(e)
        else:unknown.append(e)
    # Drive role and presence stay independent of installed total capacity.
    # Synthesize a total only from explicit positive drive capacities; absence
    # is retained in its own drive cell and never treated as zero/conflict.
    drives=[]
    for role in ('ssd','hdd'):
        raw=groups.get(role,[])
        if raw and cell(raw,'GB')['status']!='conflict':
            n=cell(raw,'GB')['numeric']
            if n is not None:drives.append((n,raw))
    if any(cell(groups.get(role,[]),'GB')['status']=='conflict' for role in ('ssd','hdd')):
        groups.setdefault('storage',[]).extend(dict(e,field='storage') for role in ('ssd','hdd') for e in groups.get(role,[]) if scalar(e['value'],e['key'],'GB') is not None)
    elif drives:
        total=sum(n for n,_ in drives)
        evidence=[e for _,raw in drives for e in raw]
        derived=dict(evidence[0],field='storage',value=str(total)+' GB',derivation='sum_explicit_drive_capacities',components=evidence)
        groups.setdefault('storage',[]).append(derived)
    if 'storage_type' not in groups and groups.get('ssd') and cell(groups['ssd'],'GB')['numeric'] is not None:
        e=dict(groups['ssd'][0],value='SSD',field='storage_type',quote=groups['ssd'][0]['key']);groups['storage_type']=[e]
    fields={}
    for key,raw in groups.items():
        value=cell(raw,FIELD_META[key][1]);value.update(field=key,unit=FIELD_META[key][1],label=FIELD_META[key][0],rule=VERSION,scope='product_listing')
        value['typed_status']='conflict' if value['status']=='conflict' else 'unsupported_unit' if value['unit'] and value['numeric'] is None else 'resolved'
        if key=='hdd' and value['numeric'] is not None and any(re.search(r'\bSSD\b',e['value'],re.I) and not re.search(r'\bHDD\b',e['value'],re.I) for e in groups.get('storage_type',[])):
            value.update(status='conflict',numeric=None,typed_status='conflict',context_notice='برچسب HDD با نوع صریح SSD ناسازگار است؛ ظرفیت نصب‌شده جدا حفظ شده است.')
        if not value['unit'] and (not comparable(value['value']) or (comparable(value['value']) in ('yes','no') and key not in ('bluetooth_present','touch','anc','microphone','night_vision','ethernet','hdmi'))):value['typed_status']='unverified_enum'
        if key in ('ssd','hdd') and comparable(value['value'])=='no':value.update(typed_status='absent',presence=False)
        fields[key]=value
    return {'version':VERSION,'product_id':product.get('product_id'),'facts':fields,'unmapped':unknown,'schema':schema(product),'fields':FIELD_META,
            'quality':{'mapped_assertions':sum(len(v) for v in groups.values()),'unmapped_assertions':len(unknown),'conflicting_fields':sum(f['status']=='conflict' for f in fields.values()),'unsupported_units':sum(f['typed_status']=='unsupported_unit' for f in fields.values())}}

def resolve(product):
    # Public product fields only. Seller text/web facts never enter this cache.
    selected={k:product.get(k) for k in ('product_id','persian_title','brand_name','category_name','specs')}
    return _resolve(json.dumps(selected,ensure_ascii=False,sort_keys=True))

def projection(product):
    data=resolve(product)
    return {'version':VERSION,'facts':data['facts'],'quality':data['quality'],'schema':data['schema'],'unmapped':data['unmapped']}

def offer_claims(offer):
    text=' '.join(str(offer.get(k) or '') for k in ('seller_title','description'))[:8000]
    claims=[]
    patterns={'ram':r'(?<![a-zآ-ی])(?:رم|RAM)(?:\s*(?:upgraded to|ارتقا یافته به|به))?\s*[:：]?\s*(\d+)\s*(GB|گیگابایت)', 'storage':r'(?:SSD|حافظه داخلی)\s*[:：]?\s*(\d+)\s*(GB|TB|گیگابایت|ترابایت)'}
    for field,pattern in patterns.items():
        for m in re.finditer(pattern,text,re.I):
            claims.append({'field':field,'value':m[1]+' '+m[2],'numeric':scalar(m[1]+m[2],field,'GB'),'unit':'GB','quote':m[0], 'scope':'seller_listing','offer_id':offer.get('offer_id'),'source':'catalogue_offer','rule':VERSION})
    for m in re.finditer(r'(\d+)\s*(GB|گیگابایت)\s*RAM(?![a-z])',text,re.I):
        claims.append({'field':'ram','value':m[1]+' '+m[2],'numeric':scalar(m[1]+m[2],'ram','GB'),'unit':'GB','quote':m[0],'scope':'seller_listing','offer_id':offer.get('offer_id'),'source':'catalogue_offer','rule':VERSION})
    return claims

def matches(product,key,value):
    field=field_id(key)
    if field:
        fact=(product.get('canonical') or {}).get('facts',{}).get(field)
        if fact is None:fact=resolve(product)['facts'].get(field)
        if not fact or fact['status']=='conflict' or fact.get('typed_status') not in (None,'resolved'):return False
        unit=FIELD_META[field][1]
        wanted=scalar(value,key,unit)
        if unit:
            if wanted is None or fact['numeric'] is None or wanted!=fact['numeric']:return False
        else:
            if semantic(field,fact['value'])!=semantic(field,value):return False
        # Conflicting seller-specific upgrades cannot silently inherit product
        # eligibility. They remain separate scoped claims in each offer.
        for claim in product.get('offer_spec_claims',offer_claims(product)):
            if claim['field']==field and unit and claim['numeric']!=wanted:return False
        return True
    values=[s.get('value') for s in product.get('specs',[]) if normalize(s.get('key'))==normalize(key)]
    normalized={comparable(v) for v in values}
    return bool(values) and len(normalized)==1 and comparable(value) in normalized

class ProjectionIndex:
    """Optional offline derived index; requests use bounded row/LRU resolution.

    The projection is rebuildable and never trusted when its fingerprint/version
    differs. Lazy per-product fallback also serves newly imported databases.
    """
    def __init__(self,path):self.path=Path(path);self.local=threading.local()
    def read(self,product):
        if not self.path.exists():return projection(product)
        try:
            signature=self.path.stat().st_mtime_ns
            if getattr(self.local,'signature',None)!=signature:
                if getattr(self.local,'db',None):self.local.db.close()
                self.local.db=sqlite3.connect('file:'+str(self.path)+'?mode=ro',uri=True)
                self.local.signature=signature
            row=self.local.db.execute('select fingerprint,payload from product_facts where product_id=?',(product['product_id'],)).fetchone()
            if row and row[0]==fingerprint(product):return json.loads(zlib.decompress(row[1]))
        except sqlite3.Error:pass
        return projection(product)
