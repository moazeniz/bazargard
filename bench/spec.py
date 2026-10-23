"""Labelled Persian shopping requests for the benchmark.

Each item: the natural request (for the agent), the concise catalogue phrase and hard
constraints a correct parse yields (for the retrieval benchmark), and expectations.
Expected categories are given as catalogue category names; build.py checks every
label against the catalogue and records the ground-truth counts.
"""
SAT_11='2026-10-24T11:00'   # Saturday 11:00 Tehran (a normal working hour)
FRI_20='2026-10-23T20:00'   # Friday 20:00 Tehran (many shops closed)
H='هدفون، هدست و هندزفری';PH='گوشی موبایل';LT='لپ تاپ و نوت بوک';TV='تلویزیون';PB='پاوربانک و شارژر همراه'
SW='ساعت و مچ بند هوشمند';SP='اسپیکر و سیستم صوتی';SH='ماشین اصلاح و ریش‌ تراش';WA='ساعت مچی عقربه‌ ای و دیجیتالی'
DR='دریل و پیچ گوشتی برقی و شارژی';CC='دوربین مدار بسته';NC='کابل شبکه';CH='شارژر گوشی';LED='لامپ LED و کم مصرف';BAG=['کیف و کوله','کیف و کوله ورزشی','کیف و کوله کوهنوردی']
HD='هارد دیسک اینترنال';PS5='کنسول بازی  PS5';MW='مایکروویو';VAC=['جارو برقی','جارو برقی صنعتی','جارو شارژی','جارو رباتیک'];TAB='تبلت';CAM='دوربین عکاسی و فیلمبرداری'
PR=['پرینتر لیزری و چند کاره','جت پرینتر','پرینتر جوهر افشان','پرینتر حرارتی و لیبل زن','پرینتر سوزنی','پرینتر چاپ عکس','پرینتر، اسکنر، فکس و چند کاره']

def q(id,kind,request,search,cats,**kw):
    d={'id':id,'kind':kind,'request':request,'search':search,'categories':cats if isinstance(cats,list) else [cats]}
    d.update(kw);return d

QUERIES=[
 # budget
 q('b1','budget','یه هدفون بی‌سیم تا ۳ میلیون می‌خوام','هدفون بی سیم',H,budget=3_000_000),
 q('b2','budget','گوشی سامسونگ زیر ۲۰ میلیون','گوشی سامسونگ',PH,budget=20_000_000,title_any=['سامسونگ','samsung']),
 q('b3','budget','لپ تاپ تا ۳۰ میلیون برای دانشگاه','لپ تاپ',LT,budget=30_000_000),
 q('b4','budget','تلویزیون حداکثر ۴۰ میلیون تومان','تلویزیون',TV,budget=40_000_000),
 q('b5','budget','یه پاوربانک ارزون زیر یک و نیم میلیون','پاوربانک',PB,budget=1_500_000),
 q('b6','budget','ساعت هوشمند تا ۵ میلیون','ساعت هوشمند',SW,budget=5_000_000),
 q('b7','budget','اسپیکر بلوتوثی زیر ۲ میلیون','اسپیکر بلوتوث',SP,budget=2_000_000),
 q('b8','budget','ماشین اصلاح تا یک میلیون','ماشین اصلاح',SH,budget=1_000_000),
 # brand
 q('r1','brand','هدفون انکر می‌خوام','هدفون انکر',H,title_any=['انکر','anker']),
 q('r2','brand','لپ تاپ لنوو','لپ تاپ لنوو',LT,title_any=['لنوو','lenovo']),
 q('r3','brand','گوشی شیائومی','گوشی شیائومی',PH,title_any=['شیائومی','xiaomi','ردمی','redmi','poco','پوکو']),
 q('r4','brand','تلویزیون ال جی','تلویزیون ال جی',TV,title_any=['ال جی','lg']),
 q('r5','brand','ساعت کاسیو','ساعت کاسیو',WA,title_any=['کاسیو','casio']),
 q('r6','brand','دریل بوش','دریل بوش',DR,title_any=['بوش','bosch']),
 # area
 q('a1','area','هدفون از مغازه‌های شیخ هادی','هدفون',H,area='شیخ هادی'),
 q('a2','area','گوشی موبایل حوالی فردوسی','گوشی موبایل',PH,area='فردوسی'),
 q('a3','area','دوربین مدار بسته در سنائی','دوربین مدار بسته',CC,area='سنائی'),
 q('a4','area','لپ تاپ نزدیک ایرانشهر','لپ تاپ',LT,area='ایرانشهر'),
 q('a5','area','ساعت مچی در ایرانشهر','ساعت مچی',WA,area='ایرانشهر'),
 q('a6','area','کابل شبکه در سنائی','کابل شبکه',NC,area='سنائی'),
 # open now (fixed visit time for reproducibility)
 q('o1','open','همین الان شارژر گوشی می‌خوام از مغازه‌ای که بازه','شارژر گوشی',CH,open_at=SAT_11),
 q('o2','open','هدفون از جایی که الان بازه','هدفون',H,open_at=SAT_11),
 q('o3','open','لامپ ال ای دی، مغازه باز','لامپ LED',LED,open_at=SAT_11),
 q('o4','open','پاوربانک تا ۳ میلیون که الان باز باشه','پاوربانک',PB,open_at=SAT_11,budget=3_000_000),
 q('o5','open','جمعه ساعت ۸ شب گوشی موبایل می‌خوام بخرم','گوشی موبایل',PH,open_at=FRI_20),
 # in-person credit / installments
 q('c1','credit','دوربین مدار بسته قسطی حضوری','دوربین مدار بسته',CC,credit=True),
 q('c2','credit','هدفون با خرید اقساطی در مغازه','هدفون',H,credit=True),
 q('c3','credit','اسپیکر قسطی','اسپیکر',SP,credit=True),
 q('c4','credit','کوله پشتی با اقساط حضوری','کوله',BAG,credit=True),
 q('c5','credit','هارد اینترنال اقساطی','هارد',HD,credit=True),
 # model codes
 q('m1','model','گوشی سامسونگ A55','گوشی a55',PH,title_all=['a55']),
 q('m2','model','هدفون کیو سی وای T13','هدفون t13',H,title_all=['t13']),
 q('m3','model','ردمی نوت ۱۳','گوشی redmi note 13',PH,title_all=['note','13']),
 q('m4','model','آیفون ۱۳','گوشی آیفون 13',PH,title_all=['13'],title_any=['آیفون','iphone','ایفون']),
 q('m5','model','هدفون P47','هدفون p47',H,title_all=['p47']),
 q('m6','model','گلکسی S24','گوشی s24',PH,title_all=['s24']),
 q('m7','model','کنسول PS5','کنسول ps5',PS5,title_any=['ps5','پلی استیشن']),
 # misspellings / Finglish / English
 q('s1','spelling','هدفن بلوتوث','هدفن بلوتوث',H),
 q('s2','spelling','لبتاپ ایسوس','لبتاپ ایسوس',LT,title_any=['ایسوس','asus']),
 q('s3','spelling','مایکروفر','مایکروفر',MW),
 q('s4','spelling','headphone bluetooth','headphone bluetooth',H),
 q('s5','spelling','goshi samsung','goshi samsung',PH),
 q('s6','spelling','مچ بند شیائومی mi band','mi band',SW),
 # not in the Torob dataset
 q('n1','not_found','تلویزیون ۵۵ اینچ لایکا','تلویزیون لایکا',TV,title_all=['لایکا'],not_found=True,missing='لایکا'),
 q('n2','not_found','کتابخوان کیندل','کیندل',['کتاب‌خوان و کاغذ دیجیتال'],title_all=['کیندل'],not_found=True,missing='کیندل'),
 q('n3','not_found','کنسول پلی استیشن ۶','کنسول پلی استیشن 6',[PS5,'سایر کنسول بازی'],title_any=['ps6','پلی استیشن 6 ','playstation 6'],not_found=True,missing='6'),
 q('n4','not_found','ماشین ظرفشویی بوش','ماشین ظرفشویی بوش',['ماشین ظرفشویی'],title_any=['بوش','bosch'],not_found=True,missing='بوش'),
 q('n5','not_found','دوچرخه برقی','دوچرخه برقی',['دوچرخه'],title_all=['برقی'],not_found=True,missing='برقی'),
 q('n6','not_found','پهپاد DJI Mini 5','پهپاد dji mini 5',['کوادکوپتر و پهپاد'],title_all=['dji'],not_found=True,missing='پهپاد'),
 q('n7','not_found','یک مایکروویو خوب زیر ۸ میلیون','مایکروویو',MW,budget=8_000_000,not_found=True,missing='budget'),
 # accessory traps: the main product, not its accessories
 q('t1','trap','گوشی آیفون','گوشی آیفون',PH,trap=True),
 q('t2','trap','جارو برقی','جارو برقی',VAC,trap=True),
 q('t3','trap','پرینتر','پرینتر',PR,trap=True),
 q('t4','trap','تبلت','تبلت',TAB,trap=True),
 q('t5','trap','ساعت هوشمند','ساعت هوشمند',SW,trap=True),
 q('t6','trap','دوربین عکاسی','دوربین عکاسی',CAM,trap=True),
 # multi-constraint
 q('x1','multi','هدفون بی‌سیم زیر ۲ میلیون در شیخ هادی که الان باز باشه','هدفون بی سیم',H,budget=2_000_000,area='شیخ هادی',open_at=SAT_11),
 q('x2','multi','دوربین مدار بسته قسطی زیر ۵ میلیون','دوربین مدار بسته',CC,budget=5_000_000,credit=True),
 q('x3','multi','گوشی شیائومی زیر ۱۵ میلیون در فردوسی','گوشی شیائومی',PH,budget=15_000_000,area='فردوسی',title_any=['شیائومی','xiaomi','ردمی','redmi','poco','پوکو']),
 q('x4','multi','لپ تاپ لنوو تا ۴۰ میلیون از جایی که الان بازه','لپ تاپ لنوو',LT,budget=40_000_000,open_at=SAT_11,title_any=['لنوو','lenovo']),
 q('x5','multi','اسپیکر بلوتوث قسطی تا ۳ میلیون','اسپیکر بلوتوث',SP,budget=3_000_000,credit=True),
]

# Agent (end-to-end) subset: one or two of each kind, kept small for cost.
E2E=['b1','b3','r2','a1','o4','c1','m1','m2','s1','s2','s5','n1','n4','n7','t2','x1','x3']
