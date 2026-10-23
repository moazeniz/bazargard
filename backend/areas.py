"""Area-level leads after catalogue failure, distinct from product availability."""
import json,time
from functools import lru_cache
from urllib.parse import urlencode
from catalog import normalize,haversine

TOPICS=[
 ('لپ تاپ',['لپ تاپ','لپتاپ','لپ تاب','لپتاب','laptop','notebook','macbook','مک بوک'],['لپ تاپ']),
 ('گوشی موبایل',['گوشی','موبایل','smartphone','iphone','آیفون','ایفون'],['گوشی موبایل']),
 ('هدفون',['هدفون','هندزفری','هدست','earbuds','headphones'],['هدفون','هندزفری','هدست']),
 ('پاوربانک',['پاوربانک','پاور بانک','powerbank'],['پاوربانک']),
 ('کامپیوتر',['کامپیوتر','قطعات کامپیوتر','computer','pc'],['مادربرد','پردازنده','کارت گرافیک','رم کامپیوتر']),
 ('کفش',['کفش','کتانی','کتونی','sneakers'],['کفش']),
 ('دوربین',['دوربین','camera'],['دوربین']),
]
AREA_NOTICE='این‌ها سرنخ ناحیه‌ای برای ادامه جست‌وجوی حضوری‌اند، نه آفر همان مدل. موجودی، قیمت، باز بودن و شرایط قسطی تأیید نشده‌اند. نبود نتیجه در نمونه مسابقه، نبود کالا در بازار را ثابت نمی‌کند.'

def topic_for(text):
    q=normalize(text)
    for topic,aliases,categories in TOPICS:
        if any((' '+normalize(a)+' ') in (' '+q+' ') for a in aliases):return topic,[normalize(c) for c in categories]
    return None,[]

@lru_cache(maxsize=2)
def shop_index(path):
    from catalog import Catalog
    with Catalog(path).conn() as c:rows=c.execute('select shop_id,address,lat,lon,location_label,category_names from shops').fetchall()
    return [dict(r)|{'categories':json.loads(r['category_names'] or '[]')} for r in rows]

def suggestions(catalog,query,profile=None):
    profile=profile or {};topic,terms=topic_for(query)
    if not topic:
        # A precise catalogue category also works for categories outside the aliases.
        matches=catalog.categories(query[:100])
        if matches and normalize(query) and len(normalize(query).split())<=3:
            topic=query[:100];terms=[normalize(x['category_name']) for x in matches]
    if not topic:return {'query':query,'topic':None,'areas':[],'notice':AREA_NOTICE,'needs_category':True,'created':time.time()}
    shops=shop_index(catalog.path);groups={}
    for shop in shops:
        lat,lon=shop['lat'],shop['lon']
        if lat is None or lon is None or not (35<=lat<=36 and 50.8<=lon<=52):continue
        cats=[c for c in shop['categories'] if any(t in normalize(c) for t in terms)]
        if not cats:continue
        key=(shop['location_label'] or 'تهران',round(lat,2),round(lon,2))
        groups.setdefault(key,[]).append((shop,cats))
    areas=[]
    for (label,_,_),items in groups.items():
        lat=sum(x[0]['lat'] for x in items)/len(items);lon=sum(x[0]['lon'] for x in items)/len(items)
        areas.append({'id':'catalogue-'+str(items[0][0]['shop_id']),'name':'محدوده '+label,'lat':lat,'lon':lon,
          'coordinate_basis':'میانگین مختصات فروشگاه‌های مرتبط در نمونه؛ ورودی مشخص نیست',
          'address':'تهران، محدوده '+label,'evidence_kind':'catalogue_cluster','shop_count':len(items),
          'categories':list(dict.fromkeys(c for _,cats in items for c in cats))[:6],
          'sources':[{'title':'دسته فعالیت و مختصات فروشگاه‌ها در کاتالوگ مسابقه','kind':'catalogue','url':None,'checked_at':None}],
          'evidence':'در نمونه کاتالوگ، '+str(len(items))+' فروشگاه این محدوده دسته مرتبط دارند؛ ثبت دسته به معنی موجودی مدل درخواستی نیست.'})
    # A bounded, editorially checked source beyond the supplied catalogue. Its
    # coordinates come from the mall's catalogue addresses, not an invented pin.
    if topic in {'لپ تاپ','هدفون','پاوربانک'}:
        mall=[s for s in shops if 'چارسو' in s['address'] and s['lat'] is not None and s['lon'] is not None and 35.69<s['lat']<35.70 and 51.40<s['lon']<51.42]
        if mall:
            laptop=topic=='لپ تاپ'
            areas.insert(0,{'id':'charsou-public-source','name':'بازار چارسو؛ اطراف تقاطع حافظ و جمهوری',
              'lat':sum(s['lat'] for s in mall)/len(mall),'lon':sum(s['lon'] for s in mall)/len(mall),
              'address':'تهران، تقاطع خیابان حافظ و جمهوری، بازار چارسو','coordinate_basis':'نقطه تقریبی مرکز خرید از آدرس‌ها و مختصات کاتالوگ؛ ورودی دقیق نیست',
              'evidence_kind':'public_merchant_source','shop_count':None,'categories':[topic],
              'sources':[{'title':'معرفی و آدرس پارس هوشمند' if laptop else 'معرفی و آدرس فوریو موبایل',
                          'url':'https://ai-pars.com/' if laptop else 'https://foryoumobile.com/for-you-mobile/',
                          'kind':'merchant_website','checked_at':'2026-10-21'}],
              'evidence':'وب‌سایت یک فروشنده، فعالیت مرتبط و حضور در چارسو را ذکر می‌کند؛ این شاهدی برای ادامه جست‌وجوست و تخصص همه واحدها یا موجودی مدل شما را ثابت نمی‌کند.'})
    for area in areas:
        distance=haversine(profile.get('lat'),profile.get('lon'),area['lat'],area['lon'])
        area['distance_km']=round(distance,2) if distance is not None else None
        area['outside_radius']=bool(distance is not None and profile.get('radius_km') is not None and distance>profile['radius_km'])
        area['outside_neighborhood']=bool(profile.get('neighborhood') and profile['neighborhood'] not in area['address'])
        point=f"{area['lat']},{area['lon']}"
        area['maps_url']='https://www.google.com/maps/search/?'+urlencode({'api':1,'query':point})
        area['directions_url']='https://www.google.com/maps/dir/?'+urlencode({'api':1,'destination':point,'travelmode':profile.get('travel_mode','driving')})
    areas.sort(key=lambda a:(a['outside_radius'] or a['outside_neighborhood'],0 if a['evidence_kind']=='public_merchant_source' else 1,-(a['shop_count'] or 0),a['distance_km'] or 0))
    distinct=[];names=set()
    for area in areas:
        if area['name'] in names:continue
        names.add(area['name']);distinct.append(area)
    return {'query':query,'topic':topic,'areas':distinct[:3],'created':time.time(),'notice':AREA_NOTICE,
            'needs_category':False,'filters':profile,'coverage':'sample_plus_curated_public_sources','changes_preferences':False}
