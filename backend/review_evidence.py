"""Conservative configuration and external-fact checks, separate from catalogue."""
import re
from catalog import normalize
from comparison import FIELDS, cell


def configuration(product, text):
    fields={}
    for key,label,unit,aliases in FIELDS:
        entries=[s for s in product.get('specs',[]) if normalize(s.get('key')) in {normalize(a) for a in aliases}]
        if entries:fields[key]=(label,cell(entries,unit))
    category=normalize(product.get('category_name',''))
    required=['cpu','gpu','ram','ssd','screen'] if ('لپ تاپ' in category or 'notebook' in category) else ['ram','storage'] if ('گوشی' in category or 'موبایل' in category) else []
    passages=[p[:2200] for p in text.splitlines() if re.search(r'our review unit|review sample|tested configuration|configuration (?:we )?tested|our test unit',p,re.I)]
    tested=' '.join(' '.join(passages).split()[:70])
    checks=[]
    for key in required:
        label,value=fields.get(key,(key,None));matched=False
        if value and value['status']!='conflict' and tested:
            if key in ('cpu','gpu'):
                tokens=re.findall(r'[a-z]+|[0-9]+',normalize(value['value']))
                signature=r'(?<![a-z0-9])'+r'[\W_]*'.join(re.escape(t) for t in tokens)+r'(?![a-z0-9])'
                matched=sum(map(len,tokens))>=5 and bool(re.search(signature,tested,re.I))
            elif value['numeric'] is not None:
                n=f"{value['numeric']:g}"
                names={'ram':r'ram|memory|رم','ssd':r'ssd|storage','storage':r'storage|internal memory','screen':r'screen|display'}[key]
                units=r'(?:inch|inches|["″])' if key=='screen' else r'(?:gb|گیگابایت)'
                quantity=re.escape(n)+r'\s*[-]?\s*'+units
                matched=bool(re.search(r'(?:'+names+r').{0,22}?'+quantity+r'(?!\d)|(?<!\d)'+quantity+r'.{0,16}?(?:'+names+r')',tested,re.I))
        checks.append({'field':key,'label':label,'status':'matched_in_test_unit' if matched else 'conflict' if value and value['status']=='conflict' else 'unverified','recorded':value['value'] if value else None})
    exact=bool(required) and all(c['status']=='matched_in_test_unit' for c in checks)
    return {'status':'matched_test_unit' if exact else 'unverified','checks':checks,'tested_unit_quote':tested,
            'notice':'فقط پیکربندی صریح نمونه آزمایش‌شده با مشخصات ثبت‌شده سنجیده شد؛ شباهت خانواده کافی نیست.'}


FACT_PATTERNS=[
 ('screen','اندازه نمایشگر',r'(?<!\d)(\d{1,2}(?:\.\d+)?)\s*[-]?\s*(?:inch|inches|اینچ)\b','inch'),
 ('battery_mah','ظرفیت باتری',r'(?<!\d)(\d{3,5})\s*mAh\b','mAh'),
 ('refresh','نرخ نوسازی',r'(?<!\d)(\d{2,3})\s*Hz\b','Hz'),
]

def external_facts(text, product, source_url):
    """Literal declared quantities, never benchmarks or inferred runtime.

    Multiple distinct quantities on a review/comparison page remain ambiguous;
    none is selected as the product's value. These never overwrite dataset cells.
    """
    facts=[]
    for key,label,pattern,unit in FACT_PATTERNS:
        matches=list(re.finditer(pattern,text,re.I))
        values=list(dict.fromkeys(m[1] for m in matches))
        if not matches:continue
        m=matches[0];quote=' '.join(text[max(0,m.start()-55):min(len(text),m.end()+55)].split()[:20])
        facts.append({'field':key,'label':label,'values':values[:5],'unit':unit,
                      'status':'ambiguous' if len(values)>1 else 'source_statement_configuration_unverified',
                      'source':'public_source','url':source_url,'quote':quote,'measured':False})
    return facts
