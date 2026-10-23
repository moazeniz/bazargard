"""An honest, source-separated buying card. No generated customer experience."""
import time,re
from comparison import reviews, FIELDS, cell
from catalog import normalize


def build_review(product, research, catalogue, profile):
    from product_data import resolve
    resolved=resolve(product)
    specs=product.get('specs') or []
    facts=[dict(f) for f in resolved['facts'].values()]
    missing=[resolved['fields'][k][0] for k in resolved['schema'] if k not in resolved['facts']]
    conflicts=[f['label'] for f in facts if f['status']=='conflict']
    sources = research.get('sources', [])
    readable = [s for s in sources if s.get('excerpts')]
    specialist = [s for s in readable if s['kind'] == 'specialist']
    community = [s for s in readable if s['kind'] == 'community']
    manufacturer = [s for s in readable if s['kind'] == 'manufacturer']
    mapped={normalize(e['key']) for f in resolved['facts'].values() for e in f['raw']}
    raw_groups={}
    for entry in specs:
        key=str(entry.get('key',''))
        if normalize(key) in mapped or any(x in normalize(key) for x in ('review','نظرات','امتیاز')):continue
        raw_groups.setdefault(key,[]).append(entry)
    for label,entries in list(raw_groups.items())[:40]:
        value=cell(entries)
        facts.append(dict(value,label=label))
        if value['status']=='conflict':conflicts.append(label)
    if product.get('brand_name') and product['brand_name']!='null' and not any(f['label']=='برند' for f in facts):
        facts.insert(0,dict(cell([{'key':'برند','value':product['brand_name']}]),label='برند'))
    external=[f for source in readable for f in source.get('external_facts',[])]
    external_conflicts=[]
    for f in external:
        label={'screen':'اندازه نمایشگر','refresh':'نرخ نوسازی نمایشگر'}.get(f['field'])
        recorded=next((v for v in facts if v['label']==label and v['numeric'] is not None),None)
        if recorded and len(f['values'])==1 and float(f['values'][0])!=recorded['numeric']:
            external_conflicts.append({'label':label,'recorded':recorded['value'],'external':f['values'][0]+' '+f['unit'],'url':f['url'],'notice':'تعارض منبع یا تفاوت پیکربندی؛ مشخصات دیتاست تغییر نکرده است.'})
    condition_evidence=[]
    listings=[{'offer_id':None,'persian_title':product['persian_title']}]+catalogue.get('offers',[])
    for listing in listings:
        text=' '.join(str(listing.get(k) or '') for k in ('persian_title','seller_title','description'))
        found=re.search(r'استوک|کارکرده|دست دوم|بازسازی[‌ ]?شده|refurbished|\bused\b|open box|جعبه باز',text,re.I)
        if found and not any(normalize(e['wording'])==normalize(found[0]) for e in condition_evidence):condition_evidence.append({'offer_id':listing.get('offer_id'),'wording':found[0],'source':'catalogue_listing','notice':'عبارت آگهی است؛ وضعیت واقعی دستگاه و سلامت باتری تأیید نشده است.'})
    questions = ['مدل و پیکربندی روی جعبه و دستگاه دقیقاً با این عنوان یکی است؟',
                 'قیمت نهایی امروز، موجودی همان پیکربندی و گارانتی کتبی چیست؟',
                 'مهلت تست، امکان مرجوعی و لوازم همراه دقیقاً چیست؟',
                 'کالا نو، کارکرده، استوک یا بازسازی‌شده است؟ سابقه تعمیر و وضعیت باتری را چگونه بررسی کنیم؟']
    electronics=any(word in normalize(product.get('category_name','')+' '+product.get('persian_title','')) for word in ('گوشی','موبایل','لپ تاپ','هدفون','تبلت','دوربین'))
    if condition_evidence and electronics:questions.extend(['سلامت باتری، سابقه تعمیر/تعویض قطعه و مهلت تست کالای استوک یا کارکرده را حضوری بررسی کنید.','شماره سریال، مدل و لوازم را با دستگاه، جعبه و فاکتور تطبیق دهید؛ ادعای اصالت آگهی تأیید مستقل نیست.'])
    if condition_evidence and not electronics:questions.append('فرسودگی، سابقه تعمیر/بازسازی، اندازه و عیوب کالا را حضوری بررسی کنید و مهلت تست را بپرسید.')
    if conflicts: questions.append('تعارض مشخصات ' + '، '.join(conflicts) + ' را روی دستگاه بررسی کنید.')
    if profile.get('installment_only') or profile.get('payment_provider'):
        questions.append('اعتبار برای خرید حضوری همین کالا و همین قیمت قابل استفاده است؟ مبلغ پیش‌پرداخت و کل بازپرداخت چیست؟')
    analysis = ['این بخش تحلیل دستیار است؛ نتیجه آزمایش یا تجربه خریدار نیست.',
                'ظرفیت رم و باتری یا نام پردازنده به تنهایی عملکرد و دوام واقعی را ثابت نمی‌کند.']
    if condition_evidence:analysis.append('در متن محصول یا آفر، وضعیت استوک/کارکرده/بازسازی‌شده ذکر شده است؛ نقد نمونه نویسنده، سلامت یا دوام همین واحد فروشی را ثابت نمی‌کند.')
    if profile.get('needs'): analysis.append('کاربرد ثبت‌شده: ' + str(profile['needs'])[:500] + '؛ سازگاری با این کاربرد نیاز به شواهد مرتبط دارد.')
    if not catalogue.get('offers'): analysis.append('برای این مدل، آفر واجد شرایط فعلی شما پیدا نشد؛ بودجه و محدوده تغییر نکرده‌اند.')
    return {'product_id': product['product_id'], 'title': product['persian_title'], 'created': time.time(),
            'model': research.get('model'),'condition_evidence':condition_evidence, 'recorded_facts': facts, 'missing_facts': missing, 'conflicts': conflicts,
            'basic_spec_evidence':[dict(f,url=s['url'],retrieved_at=s['retrieved_at']) for s in sources for f in s.get('basic_specs',[])],
            'assistant_analysis': analysis,'external_facts':external,'external_conflicts':external_conflicts, 'specialist_reviews': specialist, 'manufacturer_sources': manufacturer,
            'community_evidence': community, 'other_sources': [s for s in readable if s['kind'] == 'public_page'],
            'unavailable_sources': [s for s in sources if not s.get('excerpts')],
            'customer_notice': 'متن انجمن، ادعای خرید تأییدشده نیست. تعداد نظر یا امتیاز تجمیعی، متن تجربه خریدار نیست.',
            'aggregate_reviews': reviews(product), 'seller_questions': questions,
            'research': research, 'catalogue': catalogue,
            'notice': 'واقعیت ثبت‌شده، تحلیل دستیار، بررسی تخصصی و متن انجمن جدا هستند. تطابق پیکربندی آزمایش‌شده برای هر منبع جداگانه بررسی می‌شود؛ عدد بنچمارک یا دوام اندازه‌گیری‌شده بدون تطابق به این کالا نسبت داده نمی‌شود.'}
