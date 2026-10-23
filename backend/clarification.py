"""Progressive, trip-scoped clarification, driven by retrieved candidates."""
import json,math,re,time
from collections import Counter,defaultdict
from catalog import normalize,spec_value

PURPOSES={'هدفون':['مکالمه و جلسه','موسیقی','ورزش و پیاده‌روی','بازی'], 'لپ تاپ':['کار اداری و دانشگاه','برنامه‌نویسی','بازی','طراحی و تدوین']}
PURPOSE_WORDS=['مکالمه','تماس','جلسه','موسیقی','موزیک','ورزش','پیاده','دویدن','بازی','گیم','دانشگاه','اداری','تدوین','طراحی','برنامه نویسی','programming','gaming','calls','music']

class Clarification:
    def __init__(self,cat,mem,agent):
        self.cat=cat;self.mem=mem;self.agent=agent
        with mem.conn() as c:c.execute('CREATE TABLE IF NOT EXISTS clarification_state(user_id TEXT,session_id TEXT,key TEXT,value TEXT,created REAL,PRIMARY KEY(user_id,session_id,key))')
    def state(self,u,s):
        self.mem.session(u,s)
        with self.mem.conn() as c:return {r['key']:json.loads(r['value']) for r in c.execute('select key,value from clarification_state where user_id=? and session_id=?',(u,s))}
    def remember(self,u,s,key,value):
        self.mem.session(u,s)
        with self.mem.conn() as c:c.execute('insert or replace into clarification_state values(?,?,?,?,?)',(u,s,key,json.dumps(value,ensure_ascii=False),time.time()))
    def question(self,u,s):
        session=self.mem.session(u,s)
        if session.get('chat_mode','simple')!='advanced':return {'question':None,'mode':'simple','notice':'در حالت سریع، پرسش تکمیلی خودکار نمایش داده نمی‌شود.'}
        result=self.agent.overlay(u,s,self.mem.results(u,s));offers=result.get('offers',[]);p=self.mem.profile(u,s);answers=self.state(u,s)
        unique={o['product_id']:o for o in offers}
        if len(unique)<2:return {'question':None,'notice':'برای یک پرسش مقایسه‌ای مفید، فعلاً تنوع کافی در نتایج نداریم.'}
        category=' '.join(o['category_name'] for o in unique.values());needs=normalize(p.get('needs',''))
        group='هدفون' if 'هدفون' in category else 'لپ تاپ' if 'لپ تاپ' in normalize(category) else None
        known=any(normalize(w) in needs for w in PURPOSE_WORDS)
        if group and not known and not answers.get('purpose'):
            return {'question':'بیشتر برای چه کاری می‌خواهید؟','kind':'purpose','key':'purpose','constraint_type':'preference','options':[{'label':v,'value':v} for v in PURPOSES[group]],'reason':'چند مدل پیدا شد. کاربری شما مشخص می‌کند کدام مشخصات را در دور بعد مقایسه کنیم؛ پاسخ، ترجیح همین سفر است و سقف بودجه را تغییر نمی‌دهد.','retrieved_models':len(unique)}
        prices=[o.get('effective_price_toman',o['price_toman']) for o in unique.values()]
        if p.get('budget_max_toman') is None and not answers.get('budget') and max(prices)>min(prices)*1.5:
            return {'question':'سقف قطعی بودجه این سفر چقدر است؟','kind':'budget','key':'budget','constraint_type':'hard','options':[],'reason':'قیمت گزینه‌های پیدا‌شده متفاوت است؛ با سقف شما، آفرهای گران‌تر حذف می‌شوند. اگر هنوز نمی‌دانید، می‌توانید رد کنید.','retrieved_models':len(unique)}
        from product_data import resolve,FIELD_META,field_id
        facets=defaultdict(dict)
        useful={'connection','touch','ram','storage','screen','material','panel','anc','color'}
        filters={field_id(k) or k for k in (p.get('spec_filters') or {})}
        for pid,o in unique.items():
            data=o.get('canonical') or resolve(o)
            for key,fact in data['facts'].items():
                label=FIELD_META[key][0]
                if key in useful and key not in filters and not answers.get('spec:'+label) and fact['status']!='conflict' and fact.get('typed_status')=='resolved' and len(fact['value'])<90:
                    from spec_schema import comparable
                    value=(f"{fact['numeric']:g} {fact['unit']}" if fact['numeric'] is not None else comparable(fact['value']) if key=='connection' else fact['value'])
                    facets[label][pid]=value
        candidates=[]
        for key,values in facets.items():
            counts=Counter(values.values())
            if 2<=len(counts)<=4 and len(values)>=max(2,len(unique)*.5):
                entropy=-sum((n/len(values))*math.log2(n/len(values)) for n in counts.values());candidates.append((entropy,key,counts))
        if not candidates:return {'question':None,'notice':'پرسش دیگری که از نتایج فعلی تفاوت معنادار بسازد پیدا نشد.'}
        _,key,counts=max(candidates,key=lambda x:x[0])
        return {'question':f'برای انتخاب بین این گزینه‌ها، «{key}» را چطور می‌خواهید؟','kind':'spec','key':'spec:'+key,'spec_key':key,'constraint_type':'selectable','options':[{'label':v,'value':v,'count':n} for v,n in counts.most_common()], 'reason':'این ویژگی محصولات فعلی را جدا می‌کند. حالت الزامی، گزینه‌های فاقد این مشخصه را حذف می‌کند؛ حالت ترجیحی برای بررسی بعدی ثبت می‌شود.','retrieved_models':len(unique)}
    def answer(self,u,s,key,value=None,skip=False,strict=True):
        q=self.question(u,s)
        if q.get('key')!=key:raise ValueError('این پرسش دیگر مربوط به نتایج فعلی نیست؛ دوباره بررسی کنید')
        if skip:
            self.remember(u,s,key,{'skipped':True,'value':None});return {'facts':self.mem.facts(u,s),'question':self.question(u,s),'followup':None}
        profile=self.mem.profile(u,s);followup=None
        if key=='budget':
            if isinstance(value,bool):raise ValueError('بودجه معتبر نیست')
            try:amount=float(value)
            except (ValueError,TypeError):raise ValueError('سقف بودجه را به تومان وارد کنید')
            self.mem.update(u,[{'key':'budget_max_toman','value':amount,'quote':'سقف قطعی انتخاب‌شده توسط کاربر'}],sid=s)
        else:
            if value not in [o['value'] for o in q.get('options',[])]:raise ValueError('پاسخ باید از گزینه‌های همین پرسش باشد')
            if key=='purpose':
                needs='کاربری ترجیحی: '+value
                if profile.get('needs'):needs=profile['needs']+'؛ '+needs
                self.mem.update(u,[{'key':'needs','value':needs,'quote':value}],sid=s)
                followup=f'در این سفر بیشتر برای «{value}» می‌خواهم. با حفظ همه قیود قطعی، مشخصات ثبت‌شده گزینه‌ها را برای این کاربری بررسی کن و پیشنهادها را بازبینی کن.'
            elif key.startswith('spec:'):
                field=key[5:];filters=dict(profile.get('spec_filters') or {})
                if strict:filters[field]=value
                else:filters.pop(field,None)
                updates=[{'key':'spec_filters','value':filters,'quote':field+' = '+value}]
                if not strict:
                    updates.append({'key':'needs','value':(profile.get('needs','')+'؛ ترجیح غیرالزامی '+field+': '+value).strip('؛ '),'quote':field+' = '+value})
                    followup=f'برای این سفر «{field}: {value}» را ترجیح می‌دهم، ولی الزامی نیست. با حفظ بودجه و بقیه قیود قطعی، گزینه‌ها و تفاوت‌ها را بر اساس داده بررسی کن.'
                self.mem.update(u,updates,sid=s)
        self.remember(u,s,key,{'skipped':False,'value':value,'strict':True if key=='budget' else False if key=='purpose' else strict})
        return {'facts':self.mem.facts(u,s),'question':None,'followup':followup,'scope':'trip'}
