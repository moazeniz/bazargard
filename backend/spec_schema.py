"""Category schemas and source-preserving catalogue/title normalization."""
import re,html,functools
from catalog import normalize,DIGITS

EXTRA_FIELDS=[
 ('cpu_arch','معماری پردازنده',None,['نوع پردازنده مرکزی (CPU)']),('os_family','خانواده سیستم عامل',None,['سیستم عامل']),('os_launch','سیستم عامل زمان عرضه',None,['نسخه سیستم عامل اولیه','سیستم عامل در زمان عرضه']),('os_upgrade','امکان ارتقای سیستم عامل',None,['قابلیت ارتقا سیستم عامل','قابلیت به روزرسانی سیستم عامل','توضیحات سیستم عامل']),
 ('camera_resolution','وضوح تصویربرداری',None,['کیفیت تصویر','حداکثر رزولوشن','camera resolution']),('lens','لنز',None,['لنز','نوع لنز','lens']),('sensor','حسگر تصویر',None,['سنسور تصویر','image sensor']),('night_vision','دید در شب',None,['دید در شب','نوع دید در شب']),('view_angle','زاویه دید',None,['زاویه دید']),
 ('conductor','جنس هادی',None,['جنس هادی','جنس مغزی']),('insulation','جنس عایق',None,['جنس عایق','جنس روکش']),('cores','تعداد رشته',None,['تعداد رشته','تعداد رشته سیم']),('cross_section','سطح مقطع هادی','mm2',['سطح مقطع','سطح مقطع هادی']),('gauge','گیج / سایز کابل',None,['wire gauge','سایز سیم','سایز کابل']),('standard','استاندارد',None,['استاندارد','standard']),
 ('fuel','نوع سوخت',None,['نوع سوخت','سوخت مصرفی']),('engine','نوع موتور',None,['نوع موتور']),('phase','تعداد فاز',None,['تعداد فاز']),('fuel_capacity','ظرفیت مخزن سوخت','L',['ظرفیت مخزن سوخت','ظرفیت سوخت']),('max_head','حداکثر ارتفاع پمپاژ','m',['حداکثر ارتفاع','حداکثر هد']),('flow','دبی نامی',None,['دبی']),('max_flow','حداکثر دبی',None,['حداکثر آبدهی']),('impeller','جنس پروانه',None,['جنس پروانه']),
 ('clothing_size','اندازه پوشاک',None,['سایز','سایزبندی']),('gender','گروه جنسیت',None,['جنسیت']),('codecs','کدک‌های صوتی',None,['کدک های صوتی','کدک صوتی','audio codecs']),

 ('color','رنگ',None,['رنگ','رنگ بدنه','color']),('country','کشور سازنده',None,['کشور سازنده','ساخت کشور','کشور تولید کننده']),('brand_origin','کشور مبدأ برند',None,['کشور مبدا برند']),('model_label','مدل ثبت‌شده',None,['مدل','نام مدل']),
 ('voltage','ولتاژ نامی','V',['ولتاژ','ولتاژ کاری','rated voltage']),('current','جریان نامی','A',['جریان','جریان نامی','آمپر']),('ip_rating','درجه حفاظت',None,['درجه حفاظت','IP rating']),('frequency','فرکانس نامی','Hz',['فرکانس','frequency']),('length','طول','mm',['طول کابل','طول']),
 ('resolution','وضوح نمایشگر',None,['رزولیشن صفحه نمایش','رزولوشن صفحه نمایش','کیفیت تصویر نمایشگر','کیفیت صفحه نمایش','رزولوشن','screen resolution','resolution','رزولوشن صفحه نمایش','تعداد رنگ/ رزولوشن']),
 ('panel','فناوری نمایشگر',None,['نوع پنل','نوع نمایشگر','فناوری صفحه نمایش','display type']),
 ('screen_finish','روکش نمایشگر',None,['نوع صفحه نمایش']),
 ('touch','نمایشگر لمسی',None,['صفحه نمایش لمسی','صفحه لمسی','touchscreen','touch']),
 ('ram_type','نوع رم',None,['نوع حافظه رم','نوع رم','ram type','memory type']),
 ('ram_max','حداکثر رم قابل پشتیبانی','GB',['حداکثر حافظه رم','maximum ram']),
 ('storage_type','نوع حافظه داخلی',None,['نوع حافظه داخلی','نوع هارد داخلی','نوع هارد','storage type']),
 ('storage_max','حداکثر حافظه قابل پشتیبانی','GB',['حداکثر ظرفیت هاردها']),
 ('cpu_family','خانواده پردازنده',None,['سری پردازنده','مشخصات پردازنده']),
 ('cpu_generation','نسل پردازنده',None,['نسل پردازنده','cpu generation']),
 ('igpu','گرافیک مجتمع',None,['مدل پردازنده گرافیکی آنبرد','integrated graphics']),
 ('dgpu','گرافیک مجزا',None,['مدل پردازنده گرافیکی مجزا','مدل کارت گرافیک','discrete graphics']),
 ('vram','حافظه گرافیک مجزا','GB',['رم کارت گرافیک مجزا','کارت گرافیک اختصاصی','قدرت گرافیک']),
 ('dimensions','ابعاد دستگاه',None,['ابعاد','dimensions']),
 ('ports','درگاه‌ها',None,['درگاه های ارتباطی','درگاه‌ها','رابط‌ها','ports']),
 ('bluetooth_present','پشتیبانی بلوتوث',None,[]),
 ('bluetooth','نسخه بلوتوث',None,['نسخه بلوتوث','bluetooth version','بلوتوث','بلوتوث/ ورژن و ویژگی']),
 ('wifi','وای‌فای',None,['نسخه وای فای','wi-fi','wifi','استاندارد wi fi','شبکه Wi-Fi (وایرلس)/ ورژن','شبکه وایرلس Wi-Fi']),
 ('webcam','دوربین / وب‌کم',None,['رزولیشن وب کم','وب کم','webcam']),
 ('battery_mah','ظرفیت شارژ باتری','mAh',['ظرفیت باتری','battery capacity (mah)']),
 ('connection','نوع اتصال',None,['نوع اتصال','connectivity']),
 ('driver','قطر درایور','mm',['قطر درایور','driver size']),
 ('anc','حذف نویز فعال',None,['قابلیت نویز کنسلینگ','نویز کنسلینگ','active noise cancellation']),
 ('impedance','امپدانس','ohm',['امپدانس','impedance']),
 ('microphone','میکروفن',None,['میکروفن','microphone']),
 ('camera','دوربین اصلی',None,['رزولوشن دوربین اصلی','دوربین اصلی','main camera']),
 ('material','جنس بدنه',None,['جنس بدنه','جنس','body material']),
 ('condition','وضعیت آگهی',None,['وضعیت','وضعیت محصول']),
 ('os','سیستم عامل آگهی',None,['سیستم عامل']),
 ('accessories','اقلام همراه آگهی',None,['اقلام همراه']),
 ('power','توان نامی','W',['توان مصرفی','توان','power','توان خروجی']),
 ('capacity','ظرفیت مخزن','L',['ظرفیت مخزن','گنجایش مخزن','ظرفیت کتری','water tank capacity']),
 ('pressure','فشار پمپ','bar',['فشار بخار','فشار پمپ','pump pressure']),
 ('width','عرض دستگاه','mm',['عرض ( به میلیمتر )']),('depth','عمق دستگاه','mm',['عمق ( به میلیمتر )']),('height','ارتفاع دستگاه','mm',['ارتفاع ( به میلیمتر )']),
 ('usb_c','درگاه USB-C',None,['type-c']),('usb_a','درگاه USB-A',None,['usb 3']),('hdmi','درگاه HDMI',None,['hdmi-out']),('ethernet','درگاه شبکه',None,['پورت شبکه (RJ-45)']),
]
EXTENSIONS={'ram':['حافظه RAM','ظرفیت حافظه RAM','memory size'],'storage':['حافظه داخلی','حافظه داخلی (Storage)'],'ssd':['ظرفیت هارد داخلی (SSD)'],'hdd':['حافظه HDD'], 'cpu':['پردازنده','تراشه','chipset','نوع پردازنده - CPU','مدل تراشه','نوع تراشه','تراشه پردازنده'],'gpu':['پردازنده گرافیکی'],'screen':['ابعاد نمایشگر','صفحه نمایش','سایز صفحه نمایش','اندازه صفحه','سایز نمایشگر','قطر صفحه نمایش'],'refresh':['نرخ تازه سازی'],'gpu':['پردازنده گرافیکی','پردازنده گرافیکی - GPU','پردازنده گرافیکی (GPU)'],'weight':['وزن ( کیلوگرم )','وزن محصول','وزن دستگاه']}
COMMON=['brand','condition','weight','dimensions','material','color','country']
SCHEMAS={
 'laptop':['screen','resolution','panel','touch','ram','ram_type','storage','storage_type','cpu','cpu_family','cpu_generation','igpu','dgpu','vram','ports','bluetooth','wifi','webcam','battery','usb_c','usb_a','hdmi','ethernet'],
 'phone':['screen','resolution','panel','refresh','ram','storage','cpu','camera','battery_mah','bluetooth','wifi','connection','os_family','os_launch'],
 'headphone':['connection','bluetooth','driver','anc','impedance','microphone','battery_mah','codecs'],
 'appliance':['power','capacity','pressure'],
 'camera':['camera_resolution','lens','sensor','connection','wifi','night_vision','view_angle','power','voltage','ip_rating'],
 'cable':['conductor','insulation','cores','cross_section','gauge','length','voltage','current','standard'],
 'industrial':['power','voltage','current','frequency','ip_rating','engine','fuel','fuel_capacity','phase','max_head','flow','max_flow','impeller'],
 'clothing':['clothing_size','gender'],
 'generic':[],
}

def category(product):
 t=normalize(product.get('category_name',''))
 if any(x in t for x in ('لوازم یدکی','کیف و کاور','لوازم جانبی','گلس','تاچ و ال سی دی')):return 'generic'
 if any(x in t for x in ('لپ تاپ','notebook')):return 'laptop'
 if t in ('گوشی موبایل','تبلت') or t.startswith('تبلت '):return 'phone'
 if any(x in t for x in ('هدفون','هدست','هندزفری')):return 'headphone'
 if any(x in t for x in ('دوربین','وب کم')):return 'camera'
 if any(x in t for x in ('سیم و کابل برق','کابل برق','سیم برق','کابل صنعتی')):return 'cable'
 if any(x in t for x in ('ژنراتور','موتور برق','پمپ خانگی','پمپ صنعتی','الکتروموتور','رله','کنتاکتور','کلید و فیوز','درایو و اینورتر','سوکت و کانکتور')):return 'industrial'
 if any(x in t for x in ('قهوه','اسپرسو','کتری','چای ساز','جارو','اتو بخار','سرخ کن','یخچال','لباسشویی')):return 'appliance'
 if any(x in t for x in ('کفش','لباس','پیراهن','شلوار','تی شرت','مانتو','پوشاک','کیف و کوله','ساعت مچی')):return 'clothing'
 return 'generic'

def schema(product):return COMMON+SCHEMAS[category(product)]

def clean(value):return html.unescape(str(value or '')).strip()

def comparable(value):
 s=normalize(clean(value));compact=s.replace(' ','')
 brands={'اچپی':'hp','hp':'hp','مایکروسافت':'microsoft','سونی':'sony','سامسونگ':'samsung','لنوو':'lenovo','اماسآی':'msi','اماسای':'msi','msi':'msi','lenovo':'lenovo'}
 if compact in brands:return brands[compact]
 # Bilingual brand repetitions are equivalent only when both tokens agree.
 bilingual=re.fullmatch(r'(.+?)\s*\(([^()]+)\)',clean(value).lower())
 if bilingual:
  a,b=(brands.get(normalize(x).replace(' ',''),normalize(x).replace(' ','')) for x in bilingual.groups())
  if a==b:return a
 chip=re.fullmatch(r'(?:apple\s+)?(a\d{1,2}(?:\s+(?:pro|bionic))?)(?:\s+\d+\s+(?:nm|نانومتر))?',s,re.I)
 if chip:return normalize(chip[1])
 resolutions={'fhd':'1920x1080','fullhd':'1920x1080','fullhd19201080':'1920x1080','4k':'3840x2160','uhd':'3840x2160'}
 if compact in resolutions:return resolutions[compact]
 m=re.search(r'(\d{3,4})\s*(?:[x×*]|در)\s*(\d{3,4})',clean(value).translate(DIGITS),re.I)
 if m:return m[1]+'x'+m[2]
 if clean(value) in ('✔️','✔','✅','✓'):return 'yes'
 if clean(value) in ('❌','✘','✖️','✖'):return 'no'
 if s in ('دارد','بله','yes','true'):return 'yes'
 if s in ('ندارد','خیر','no','false'):return 'no'
 if normalize('نوع اتصال')==s:return s
 if s.replace(' ','') in ('باسیم','سیمی','wired'):return 'با سیم'
 if s.replace(' ','') in ('بیسیم','wireless'):return 'بی سیم'
 return s

def key_signature(key):
 """Safe syntactic equivalence; preserve maximum/package/launch/drive context."""
 s=clean(key).lower().replace("\u200c"," ")
 # Category qualifiers and redundant bilingual abbreviations are syntax, not scope.
 s=re.sub(r'لپ\s*تاپ|نوت بوک','',s)
 for token,word in [('ram','رم'),('gpu','گرافیکی'),('cpu','مرکزی'),('storage','داخلی'),('پنل','پنل')]:
  s=re.sub(r'\(\s*'+token+r'\s*\)', ' '+word+' ',s,flags=re.I)
 s=re.sub(r'\((?:اینچ|inch|kg|g|gb|mm|cm|میلیمتر|میلی متر|کیلوگرم|گرم|وات ساعت)\)','',s,flags=re.I)
 s=re.sub(r'\b(رم|مرکزی|گرافیکی|داخلی)\s+\1\b',r'\1',s)
 return normalize(s)

SEMANTIC_KEYS={
 'cpu':['مدل پردازنده مرکزی','سری و مدل پردازنده'],
 'cpu_family':['سری پردازنده مرکزی'],
 'ram':['ظرفیت حافظه رم'], 'ram_max':['قابلیت ارتقا رم'],
 'ram_type':['نوع حافظه رم'],
 'storage':['ظرفیت حافظه داخلی'], 'storage_type':['نوع حافظه'],
 'gpu':['مدل پردازنده گرافیکی'],
 'vram':['حجم گرافیک','ظرفیت گرافیک','ظرفیت حافظه گرافیکی','حافظه اختصاصی پردازنده گرافیکی'],
 'panel':['پنل تصویر','نوع پنل تصویر','نوع صفحه نمایش پنل'],
 'resolution':['وضوح تصویر','رزولوشن تصویر','دقت صفحه نمایش'],
 'ports':['درگاه ها و فناوری های ارتباطی'],
 'wifi':['توضیحات شبکه بی سیم Wi-Fi'],
 'hdmi':['HDMI','پورت HDMI','درگاه HDMI'],
 'ethernet':['LAN','درگاه شبکه LAN','درگاه شبکه (LAN)'],
}
@functools.lru_cache(maxsize=8)
def signatures(definition):
 result={key_signature(a):k for k,names in definition for a in names}
 result.update({key_signature(a):k for k,names in SEMANTIC_KEYS.items() for a in names})
 return result

def mapped_key(key,fields,kind=None):
 definition=tuple((k,tuple(names)) for k,_,_,names in fields)
 aliases=_aliases(definition)
 if normalize(key) in aliases:return aliases[normalize(key)]
 sig=key_signature(key)
 field=signatures(definition).get(sig)
 if field:return field
 # USB versions/count qualifiers retain the connector role, never the speed as count.
 if kind=='laptop' and re.search(r'usb|type',sig,re.I) and not re.search(r'توضیح|قابلیت|توان',sig):
  if re.search(r'type\s*[- ]?c|usb\s*[- ]?c',sig,re.I):return 'usb_c'
  if re.search(r'type\s*[- ]?a|usb\s*[- ]?a|usb [23][ .]',sig,re.I):return 'usb_a'
 return None

@functools.lru_cache(maxsize=8)
def _aliases(definition):
 return {normalize(a):k for k,names in definition for a in names}

def entries(product,fields):
 aliases=_aliases(tuple((k,tuple(names)) for k,_,_,names in fields))
 output=[];title=clean(product.get('persian_title')).translate(DIGITS)
 for original in product.get('specs',[]):
  key=clean(original.get('key'));value=clean(original.get('value')).translate(DIGITS);kind=category(product);field=mapped_key(key,fields,kind)
  if field is None and re.search(r'\((?:kg|g|gb|mm|cm|m|w|v|a|کیلوگرم|گرم|وات|ولت|متر|گیگابایت)\)',key,re.I):field=aliases.get(normalize(re.sub(r'\([^)]*\)','',key)))
  kind=category(product)
  if kind=='camera' and field=='resolution':field='camera_resolution'
  if kind=='cable' and field=='clothing_size':field='gauge'
  if not value or normalize(value) in ('null','نامشخص','نامعلوم'):continue
  if kind=='phone' and normalize(key) in (normalize('نوع سیستم عامل'),normalize('نسخه سیستم عامل')):
   field='os_launch' if re.search(r'زمان عرضه|initial|launch',value,re.I) else 'os_family'
  if field=='os':field='os_family'
  if kind in ('phone','laptop') and field=='screen_finish' and re.search(r'OLED|AMOLED|IPS|LCD|Retina|PixelSense',value,re.I):field='panel'
  if kind=='phone' and normalize(key)==normalize('ساختار بدنه'):field='material'
  if normalize(key)==normalize('ابعاد/ وزن'):
   m=re.search(r'(?:وزن)\s*(\d+(?:\.\d+)?\s*(?:کیلوگرم|گرم|kg|g))',value,re.I)
   if m:output.append({'field':'weight','key':key,'value':m[1],'section':clean(original.get('section')),'original':dict(original),'origin':'catalogue','quote':m[0]})
   m=re.search(r'\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?\s*(?:میلی[‌ ]*متر|سانتی[‌ ]*متر|mm|cm)',value,re.I)
   if m:output.append({'field':'dimensions','key':key,'value':m[0],'section':clean(original.get('section')),'original':dict(original),'origin':'catalogue','quote':m[0]})
   continue
  if field=='bluetooth' and comparable(value) in ('yes','no'):field='bluetooth_present'
  if field=='battery_mah':
   m=re.search(r'\d+(?:\.\d+)?\s*(?:mAh|میلی[‌ ]?آمپر[‌ ]?ساعت|میلی[‌ ]?امپر[‌ ]?ساعت)',value,re.I)
   if m:value=m[0]
  if field=='cpu' and re.fullmatch(r'(?:Intel\s*)?(?:Core\s*)?i[3579]',value,re.I):field='cpu_family'
  if field=='cpu' and re.search(r'نسل|generation',value,re.I):field='cpu_generation'
  if field=='dimensions' and re.search(r'بسته|package|shipping',key+' '+value,re.I):field=None
  if field=='weight' and re.search(r'بسته|ناخالص|shipping|package',key+' '+value,re.I):field=None
  if field=='screen' and not (re.search(r'\d+(?:\.\d+)?\s*(?:اینچ|inch|["″])|^["″]\s*\d',value,re.I) or (re.fullmatch(r'\d+(?:\.\d+)?',value) and re.search(r'اینچ|inch',key,re.I))):field=None
  if field=='battery_mah' and not re.search(r'mah|میلی آمپر',value,re.I):field=None
  # Battery energy must be explicit Wh, never W or claimed runtime.
  if re.search(r'باتری|ظرفیت و نوع',key) and not re.search(r'شارژدهی|عمر',key):
   m=re.search(r'\d+(?:\.\d+)?\s*(?:whr?\b|وات[‌ ]*ساعت)',value,re.I)
   if m:field='battery';value=m[0]
  if field=='dimensions' and re.search(r'میلیمتر|میلی[‌ ]*متر|\bmm\b',key,re.I) and not re.search(r'mm|متر',value,re.I):value+=' mm'
  # A cable strand count is not a processor thread count.
  if field=='cores' and kind!='cable':field=None
  if field=='clothing_size' and kind not in ('clothing','cable'):field=None
  output.append({'field':field,'key':key,'value':value,'section':clean(original.get('section')),'original':dict(original),'origin':'catalogue'})
 if product.get('brand_name') and product['brand_name']!='null':output.insert(0,{'field':'brand','key':'برند','value':clean(product['brand_name']),'section':'مشخصات اصلی','origin':'catalogue'})
 present={x['field'] for x in output}
 def add(field,value,quote):
  if field not in present:
   output.append({'field':field,'key':'عنوان آگهی','value':value,'section':'اظهار صریح عنوان','origin':'listing_title','quote':quote});present.add(field)
 if category(product) not in ('laptop','phone'):
  return output
 m=re.search(r'\b(?:i[3579][ -])?(\d{4,5}[UHGPX]{1,3})\b',title,re.I)
 if m:add('cpu',m[1],m[0])
 m=re.search(r'(\d+(?:\.\d+)?)\s*(?:اینچ|inch(?:es)?)',title,re.I)
 if m:add('screen',m[1]+' inch',m[0])
 m=re.search(r'(?:رم|RAM)\s*[:：]?\s*(\d+)\s*(?:GB|گیگابایت)?',title,re.I)
 if not m:m=re.search(r'(\d+)\s*GB\s*RAM',title,re.I)
 if m:add('ram',m[1]+' GB',m[0])
 m=re.search(r'(?:SSD|هارد)\s*[:：]?\s*(\d+)\s*(GB|TB|گیگابایت|ترابایت)?',title,re.I)
 if m and (m[2] or 'SSD' in m[0].upper()):add('storage',m[1]+' '+(m[2] or 'GB'),m[0])
 # Explicit paired RAM-storage title convention is accepted only with GB suffix.
 m=re.search(r'\b(\d{1,3})-(\d{2,4})\s*GB\b',title,re.I)
 if m:add('ram',m[1]+' GB',m[0]);add('storage',m[2]+' GB',m[0])
 if 'storage_type' not in present and re.search(r'\bSSD\b',title,re.I):add('storage_type','SSD','SSD')
 if 'condition' not in present:
  m=re.search(r'استوک|کارکرده|بازسازی شده|\brefurbished\b|\bused\b',title,re.I)
  if m:add('condition',m[0],m[0])
 return output
