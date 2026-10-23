"""Bounded multi-round catalogue investigation with a visible evidence trail.

Plans contain short user-facing questions, never private chain-of-thought. Query
expansion only substitutes equivalent category terms; every branch uses the same
current hard filters. A missing match never authorizes widening constraints.
"""
import asyncio,copy,hashlib,json,re
from catalog import normalize

EQUIVALENTS=[('هدفون','هندزفری','هدست','earbuds','headphones'),('لپ تاپ','laptop'),('گوشی','موبایل','smartphone'),('کتانی','کتونی','sneakers'),('پاوربانک','پاور بانک','powerbank')]

def expansions(query):
    normalized=normalize(query);out=[]
    for group in EQUIVALENTS:
        for word in group:
            pattern=r'(?<!\w)'+re.escape(normalize(word))+r'(?!\w)'
            if re.search(pattern,normalized):
                for equivalent in group:
                    candidate=re.sub(pattern,normalize(equivalent),normalized,count=1)
                    if candidate!=normalized and candidate not in out:out.append(candidate)
                return out[:2]
    return []

class Research:
    def __init__(self,agent,uid,sid):self.agent=agent;self.uid=uid;self.sid=sid;self.trace=[];self.plan=[];self.cache={};self.query_count=0;self.initial_message=None
    def planning(self,tasks):
        if not isinstance(tasks,list) or not 1<=len(tasks)<=6:raise ValueError('Use 1–6 short investigation questions')
        ids=set();plan=[]
        for i,t in enumerate(tasks):
            tid=str(t.get('id') or str(i+1))[:30];deps=t.get('depends_on',[])
            if tid in ids or any(x not in ids for x in deps):raise ValueError('Dependencies must refer to earlier task IDs')
            ids.add(tid);plan.append({'id':tid,'question':str(t['question'])[:180],'depends_on':deps,'evidence_needed':str(t.get('evidence_needed',''))[:180]})
        self.plan=plan;return {'tasks':plan,'notice':'پرسش‌های بررسی؛ پاسخ نهایی فقط پس از جمع‌آوری شواهد. برنامه هیچ تغییری در بودجه و فیلترها نمی‌دهد.'}
    async def query(self,q,purpose,sort='balanced',product_ids=None):
        a=self.agent;p=a.mem.profile(self.uid,self.sid);q=q[:160].strip();ids=(product_ids or [])[:4]
        signature=hashlib.sha256(json.dumps([normalize(q),p,sort,ids,a.mem.events(self.uid,self.sid)],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if signature in self.cache:return copy.deepcopy(self.cache[signature]),True
        if self.query_count>=18:raise ValueError('Query budget reached; synthesize current evidence or ask one useful question')
        from price_policy import initial
        spread=bool(self.initial_message and self.query_count==0 and not ids and initial(q,self.initial_message,a.mem.results(self.uid,self.sid),sort,p))
        self.query_count+=1
        result=await asyncio.to_thread(a.cat.search,q,p,8,a.exclusions(self.uid,self.sid),product_ids=ids or None,sort=sort,exclude_pairs=a.unavailable_pairs(self.uid,self.sid),seller_events=a.mem.events(self.uid,self.sid),discovery=spread)
        result=a.overlay(self.uid,self.sid,result)
        self.trace.append({'query':q,'purpose':purpose[:200],'matching_offers':result['matching_offers'],'returned_offers':len(result['offers']),'product_ids':ids,'filters':p,'candidate_limit_reached':result.get('candidate_limit_reached',False)})
        self.cache[signature]=copy.deepcopy(result);return result,False
    async def search(self,queries,purpose,expand=False,sort='balanced'):
        queries=list(dict.fromkeys(q[:160].strip() for q in queries if q.strip()))[:4]
        if not queries:raise ValueError('At least one concise query is required')
        # Bound the total before scheduling concurrent reads.
        if self.query_count+len(queries)>18:raise ValueError('Remaining query budget is too small; synthesize current evidence')
        first=await asyncio.gather(*(self.query(q,purpose,sort) for q in queries));parts=[];merged={}
        for q,(result,cached) in zip(queries,first):
            parts.append({'query':q,'matches':result['matching_offers'],'cached':cached,'expanded_from':None})
            for o in result['offers']:merged.setdefault(o['offer_id'],o)
            if expand and not result['offers']:
                for variant in expansions(q):
                    if self.query_count>=18:break
                    retry,cached=await self.query(variant,purpose,sort)
                    parts.append({'query':variant,'matches':retry['matching_offers'],'cached':cached,'expanded_from':q})
                    for o in retry['offers']:merged.setdefault(o['offer_id'],o)
        spread_result=next((r for r,_ in first if r.get('price_diversity')),None)
        if spread_result:
            return dict(spread_result,query_round=parts,queries_used=self.query_count,investigated_offers=len(merged))
        offers=list(merged.values());self.agent.cat.rank(offers,self.agent.mem.profile(self.uid,self.sid),sort)
        return {'offers':offers[:24],'query':' / '.join(queries),'query_round':parts,'filters':self.agent.mem.profile(self.uid,self.sid),'matching_offers':len(merged),'candidate_count_is_partial':True,'queries_used':self.query_count,'query_budget':18,'price_status':'dataset_not_live','notice':'نتایج چند جست‌وجوی محدود ترکیب شدند؛ این شمارش کل بازار یا کل دیتاست نیست. قیود سخت در همه شاخه‌ها حفظ شدند.'}
    async def sellers(self,product_ids,purpose):
        ids=list(dict.fromkeys(product_ids))[:4]
        if self.query_count+len(ids)>18:raise ValueError('Query budget reached')
        parts=await asyncio.gather(*(self.query('',purpose,'balanced',[pid]) for pid in ids));offers=[];products=[]
        for pid,(result,cached) in zip(ids,parts):
            product=self.agent.cat.product(pid)
            if product:products.append(product)
            offers.extend(result['offers'][:6])
        return {'products':products,'offers':offers,'filters':self.agent.mem.profile(self.uid,self.sid),'matching_offers':len(offers),'query':'فروشنده‌های مدل‌های بررسی‌شده','queries_used':self.query_count,'notice':'آفرهای صریح همین مدل‌ها؛ موجودی زنده از ثبت آفر نتیجه نمی‌شود.'}
    def verify(self,offers):
        a=self.agent;now=a.overlay(self.uid,self.sid,{'offers':offers,'curated':True})['offers'];valid={o['offer_id']:o for o in now}
        return {'checked':[{'offer_id':o['offer_id'],'eligible_now':o['offer_id'] in valid,'effective_price_toman':valid[o['offer_id']].get('effective_price_toman') if o['offer_id'] in valid else None,'product_id':o['product_id'],'shop_id':o['shop_id'],'specs':a.cat.product(o['product_id']).get('specs',[]),'canonical':a.cat.product(o['product_id']).get('canonical'),'availability':valid.get(o['offer_id'],{}).get('availability'),'opening':valid.get(o['offer_id'],{}).get('opening'),'inspection':valid.get(o['offer_id'],{}).get('inspection')} for o in offers],
                'limits':['ثبت آفر، تأیید موجودی و اصالت نیست.','ویژگی‌های غایب در مشخصات باید نامشخص اعلام شوند.','برای زمان سفر باید ابزار زمان‌بندی جداگانه اجرا شود.'],'all_eligible':len(now)==len(offers)}
