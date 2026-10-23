"""Accepted, explicitly labelled seller scenarios; never outbound contact."""
import math
from datetime import datetime,timedelta
from catalog import TEHRAN

SCENARIOS={'available','unavailable','no_answer','closed','price_changed','variant_mismatch','credit_declined'}


def simulate(agent,uid,sid,shop_id,product_id,scenario,price_toman=None):
    if scenario not in SCENARIOS:raise ValueError('سناریو معتبر نیست')
    contact=agent.contact(shop_id,product_id)
    product=agent.cat.product(product_id)
    with agent.cat.conn() as db:
        original=db.execute('select min(price_toman) from offers where shop_id=? and product_id=?',(shop_id,product_id)).fetchone()[0]
    if price_toman is not None and (not isinstance(price_toman,(int,float)) or not math.isfinite(price_toman) or not 0<price_toman<=1e13):raise ValueError('قیمت آزمایشی معتبر نیست')
    price=price_toman if price_toman is not None else round(original*1.25) if scenario=='price_changed' else original
    details={'scenario':scenario,'valid_until':(datetime.now(TEHRAN)+timedelta(hours=1)).isoformat(),'independently_verified':False}
    state='reported';available=True
    answers={
        'available':'در این سناریوی ساختگی، همین مدل با قیمت ثبت‌شده آماده است؛ این تأیید واقعی نیست.',
        'unavailable':'در این سناریوی ساختگی، همین مدل موجود نیست.',
        'no_answer':'در این سناریوی ساختگی، پاسخی دریافت نشد؛ موجودی نامشخص است.',
        'closed':'در این سناریوی ساختگی، فروشگاه بسته است؛ مراجعه را تغییر دهید.',
        'price_changed':f'در این سناریوی ساختگی، قیمت نهایی {price:,.0f} تومان است؛ قیمت واقعی بازار نیست.',
        'variant_mismatch':'در این سناریوی ساختگی، فقط نسخه دیگری پیشنهاد شد؛ مدل و پیکربندی درخواستی تأیید نشد.',
        'credit_declined':'در این سناریوی ساختگی، پرداخت اعتباری برای همین خرید حضوری پذیرفته نشد؛ نقدی بودن مجوز حذف قید کاربر نیست.'}
    if scenario in ('unavailable','variant_mismatch'):state='unavailable';available=False
    elif scenario in ('no_answer','closed'):state=scenario;available=None
    if scenario=='variant_mismatch':details['variant_match']=False
    if scenario=='credit_declined':details['credit_available']=False
    questions=['همین مدل و پیکربندی دقیق؟','قیمت نهایی، موجودی و گارانتی؟','ساعت و طبقه/واحد مراجعه؟']
    for turn in reversed(agent.mem.turns(uid,sid,12)):
        refs=turn.get('references') or {}
        cards=refs.get('buying_reviews',[])+(refs.get('comparison') or {}).get('buying_reviews',[])
        card=next((r for r in cards if r['product_id']==product_id),None)
        if card:
            questions=list(dict.fromkeys(questions+card.get('seller_questions',[])))[:8];break
    details['dialogue']=[{'role':'assistant_draft','text':contact['draft']},
                         {'role':'assistant_questions','text':'\n'.join(questions)},
                         {'role':'simulated_seller','text':answers[scenario]}]
    agent.mem.event(uid,sid,{'shop_id':shop_id,'product_id':product_id,'status':state,'available':available,
                           'price':price if scenario in ('available','price_changed') else None,'source':'simulated',
                           'note':answers[scenario],'details':details})
    result=agent.alternatives(uid,sid,product_id)
    result['simulation_notice']='سناریوی شبیه‌سازی فعال است؛ قیمت، موجودی و پاسخ‌ها ساختگی‌اند و هیچ تماس واقعی انجام نشده است.'
    still_eligible=any(o['shop_id']==shop_id and o['product_id']==product_id for o in result.get('offers',[]))
    invalidated=[] if still_eligible else [shop_id]
    agent.mem.results(uid,sid,result)
    return {'events':agent.mem.events(uid,sid),'results':result,'source':'simulated','scenario':scenario,
            'visit_update':{'invalidated_shop_ids':invalidated,'replacement_shop_ids':list(dict.fromkeys(o['shop_id'] for o in result.get('offers',[]) if o['shop_id']!=shop_id))[:4]},
            'dialogue':details['dialogue'],'seller_questions':questions,'notice':result['simulation_notice']}
