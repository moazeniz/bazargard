"""Literal advertised specifications; independent of measured review-unit claims.

Fail closed on family/configuration pages. This layer never edits catalogue data
or participates in eligibility. Only missing comparison cells may be completed.
"""
import copy
import re
from catalog import normalize
from spec_schema import comparable
from product_data import semantic
from comparison import FIELDS, cell, scalar, UNITS

SUPPORTED={k for k,_,_,_ in FIELDS}-{'condition','os','accessories','brand','country','model_label'}
LABELS={k:(label,unit) for k,label,unit,_ in FIELDS}
PATTERNS={
 'screen':r'(?:screen size|display size|size \(main display\)|screen diagonal|اندازه نمایشگر|اندازه صفحه نمایش)\s*[:：-]?\s*(\d{1,2}(?:\.\d+)?\s*(?:inches|inch|in|اینچ|["″]))',
 'ram':r'(?:ram(?: size| capacity| \(gb\))?|memory(?: size)?|حافظه رم)\s*[:：-]?\s*(\d+(?:\.\d+)?\s*(?:GB|TB|گیگابایت))',
 'storage':r'(?:internal storage|storage(?: size| capacity| \(gb\))?|حافظه داخلی)\s*[:：-]?\s*(\d+(?:\.\d+)?\s*(?:GB|TB|گیگابایت))',
 'ssd':r'(?:ssd(?: capacity)?\s*[:：-]?\s*(\d+(?:\.\d+)?\s*(?:GB|TB))|(\d+(?:\.\d+)?\s*(?:GB|TB))\s+SSD)',
 'hdd':r'(?:hdd(?: capacity)?\s*[:：-]?\s*(\d+(?:\.\d+)?\s*(?:GB|TB))|(\d+(?:\.\d+)?\s*(?:GB|TB))\s+HDD)',
 'weight':r'(?:device weight|net weight|weight(?: \((?:g|kg)\))?|وزن دستگاه)\s*[:：-]?\s*(\d+(?:\.\d+)?\s*(?:kg|g|گرم|کیلوگرم))\b',
 'refresh':r'(?:refresh rate|نرخ نوسازی)\s*[:：-]?\s*(\d+(?:\.\d+)?\s*(?:Hz|هرتز))',
 'battery':r'(?:battery(?: capacity| energy)?|ظرفیت باتری)\s*[:：-]?\s*(\d+(?:\.\d+)?\s*(?:Wh|Whr|وات ساعت))\b',
 'cpu':r'(?:CPU(?: model| name| type)?|processor(?: model| name| type)?)\s*[:：-]?\s*((?:Intel\s+(?:Core\s+)?(?:Ultra\s+)?(?:i[3579][ -]\d{4,5}[a-z]{0,3}|[3579]\s+\d{3}[a-z]{0,3})|AMD\s+Ryzen\s+[3579]\s+\d{4}[a-z]{0,3}|(?:Apple\s+)?M[1-9](?:\s+(?:Pro|Max|Ultra))?|(?:Qualcomm\s+)?Snapdragon\s+(?:[468]\s+Gen\s+\d|\d{3}[a-z+]*|X\s+(?:Elite|Plus))|(?:Samsung\s+)?Exynos\s+\d{3,5}))(?![a-z0-9])',
 'gpu':r'(?:GPU(?: model)?|graphics(?: model)?)\s*[:：-]?\s*((?:NVIDIA\s+)?(?:GeForce\s+)?RTX\s+\d{4}(?:\s+Ti)?|Intel\s+(?:Iris Xe|UHD)|AMD\s+Radeon\s+[\w ]{2,20})',
}
AMBIGUOUS=r'\b(?:up to|starting (?:at|from)|depending on|varies|optional|configurations|choose|available in|or)\b|حداکثر|بسته به|یا'
BAD_WEIGHT=r'shipping|package|packaging|gross|with (?:stand|packaging|box)|بسته بندی|با بسته|ناخالص'

def missing_fields(product):
    from product_data import resolve
    data=resolve(product)
    return [k for k in data['schema'] if k in SUPPORTED and (k not in data['facts'] or data['facts'][k]['typed_status']=='unsupported_unit')]

def signature(value):
    tokens=re.findall(r'[a-z]+|\d+',value.lower())
    return r'(?<![a-z0-9])'+r'[\s-]*'.join(map(re.escape,tokens))+r'(?![a-z0-9])' if tokens else r'(?!)'

def extract(product,text,title,model,kind,truncated=False):
    """Retain at most 70 words of literal evidence per document.

    Manufacturer single-configuration declarations can establish static facts
    without a tested-unit paragraph. Other pages remain inspectable, unaccepted.
    """
    title_match=bool(model and re.search(signature(model),title,re.I))
    # Exact boundary above also excludes e.g. A55s. Reject explicit sibling
    # variants, family selectors and multiple configurations anywhere on page.
    sibling=False
    prefix=re.match(r'([a-z]+)[ -]*(\d+)',model,re.I)
    if prefix:
        for found in re.findall(r'\b'+re.escape(prefix[1])+r'[ -]*\d+[a-z0-9-]*(?:[ -]+(?:pro|max|ultra|plus|mini|fe))?\b',title+'\n'+text,re.I):
            if not re.fullmatch(signature(model),found,re.I):sibling=True
    family=bool(re.search(r'\b(?:configurations|available in|depending on configuration|select (?:your )?(?:model|configuration))\b',text,re.I))
    # A family page with alternative CPU/memory/storage cannot establish a
    # particular device weight (or silently choose a configuration).
    for field in ('ram','storage','ssd','hdd','cpu','gpu'):
        found=list(re.finditer(PATTERNS[field],text,re.I))
        vals={normalize(next(g for g in m.groups() if g is not None)) for m in found}
        if len(vals)>1:family=True
        for m in found:
            line=text[text.rfind('\n',0,m.start())+1:text.find('\n',m.end()) if text.find('\n',m.end())>=0 else len(text)]
            if re.search(AMBIGUOUS,line,re.I):family=True
            if field in ('ram','storage','ssd','hdd'):
                quantities=re.findall(r'\d+(?:\.\d+)?\s*(?:GB|TB)\b',line,re.I)
                if len(quantities)>1 or re.search(r'[+/]|\b[24]\s*[x×]|[x×]\s*[24]\b',line,re.I):family=True
    out=[];remaining=70
    for key in (f[0] for f in FIELDS if f[0] in PATTERNS):
        matches=list(re.finditer(PATTERNS[key],text,re.I))
        if not matches:continue
        candidates=[];ambiguous=False;quotes=[]
        for m in matches:
            value=next(g for g in m.groups() if g is not None).strip()
            # CPU names end at punctuation/newline; never synthesize a model.
            value=re.split(r'[\n;,|]',value)[0].strip()
            start=text.rfind('\n',0,m.start())+1;end=text.find('\n',m.end());end=len(text) if end<0 else end
            context=text[max(start,m.start()-50):min(end,m.end()+65)]
            if re.search(AMBIGUOUS,context,re.I) or re.search(r'[/+]|\bminimum\b|\bstarting\b',context,re.I):ambiguous=True
            if key in ('screen','refresh','battery'):
                quantity={'screen':r'\d+(?:\.\d+)?\s*(?:inch(?:es)?|[\"″])','refresh':r'\d+(?:\.\d+)?\s*Hz','battery':r'\d+(?:\.\d+)?\s*Wh'}[key]
                if len(re.findall(quantity,context,re.I))>1:ambiguous=True
            if key=='weight':
                previous=text[max(0,start-45):m.start()]
                if re.search(BAD_WEIGHT,previous+' '+context,re.I):ambiguous=True
                if len(re.findall(r'\d+(?:\.\d+)?\s*(?:kg|g)\b',context,re.I))>1:ambiguous=True
            if key=='ram' and re.search(r'gpu|graphics|video|internal|storage',text[max(0,m.start()-25):m.start()],re.I):ambiguous=True
            if re.match(r'[a-zA-Z]',text[m.end():m.end()+1]):ambiguous=True
            # All additional capacities in the field passage imply alternatives.
            if key in ('ram','storage','ssd','hdd'):
                nums={scalar(x,key,'GB') for x in re.findall(r'\d+(?:\.\d+)?\s*(?:GB|TB)\b',context,re.I)}
                if len(nums)>1:ambiguous=True
            numeric=scalar(value,key,LABELS[key][1])
            if LABELS[key][1] and numeric is None:ambiguous=True
            candidates.append((numeric if numeric is not None else normalize(value),value,numeric))
            quotes.append(text[m.start():m.start()+len(m[0])].strip())
        unique={v[0] for v in candidates}
        status='accepted'
        if not title_match or sibling:status='model_or_variant_unproven'
        elif kind!='manufacturer':status='non_manufacturer_unverified'
        elif truncated:status='partial_document'
        elif family or ambiguous or len(unique)!=1:status='ambiguous'
        # Catalogue/title configuration anchors must agree, including values
        # hidden under otherwise unsupported original aliases.
        for field in ('ram','storage','ssd','hdd','cpu','gpu'):
            _,_,unit,aliases=next(f for f in FIELDS if f[0]==field)
            known=cell([s for s in product.get('specs',[]) if normalize(s.get('key')) in {normalize(a) for a in aliases}],unit)
            observed=list(re.finditer(PATTERNS[field],text,re.I))
            if known['status']=='conflict':status='configuration_unproven'
            if known['status']=='recorded' and not observed and key in ('ram','storage','ssd','hdd','cpu','gpu','weight'):status='configuration_unproven'
            if known['status']=='recorded' and observed:
                vals=[next(g for g in x.groups() if g is not None).strip() for x in observed]
                good=all(scalar(v,field,unit)==known['numeric'] for v in vals) if known['numeric'] is not None else all(normalize(v)==normalize(known['value']) for v in vals)
                if not good:status='configuration_conflict'
        # Explicit capacities in the product title are also variant anchors.
        for field,pattern in [('ram',r'(?:ram|رم)\s*(\d+)\s*(?:GB|گیگابایت)?'),('storage',r'(?:storage|حافظه(?: داخلی)?)\s*(\d+)\s*(?:GB|گیگابایت)?')]:
            anchor=re.search(pattern,product.get('persian_title',''),re.I)
            if anchor:
                observed=list(re.finditer(PATTERNS[field],text,re.I))
                vals=[next(g for g in x.groups() if g is not None).strip() for x in observed]
                if not vals or any(scalar(v,field,'GB')!=float(anchor[1]) for v in vals):status='configuration_unproven'
        # Capacity before the label and unlabeled explicit capacities in a
        # catalogue title are constraints too, never guessed to be another SKU.
        product_title=product.get('persian_title','')
        title_capacities={scalar(v,'capacity','GB') for v in re.findall(r'\d+(?:\.\d+)?\s*(?:GB|TB|گیگابایت|ترابایت)\b',product_title,re.I)}
        source_capacities={scalar(v,'capacity','GB') for v in re.findall(r'\d+(?:\.\d+)?\s*(?:GB|TB|گیگابایت|ترابایت)\b',text,re.I)}
        if not title_capacities.issubset(source_capacities):status='configuration_unproven'
        for cpu in re.findall(r'\bi[3579][ -]\d{4,5}[a-z]{0,3}\b|\bRyzen\s+[3579]\s+\d{4}[a-z]{0,3}\b',product_title,re.I):
            if not re.search(signature(cpu),text,re.I):status='configuration_unproven'
        quote=' '.join(quotes[0].split()[:min(remaining,16)]);remaining-=len(quote.split())
        if not quote:continue
        _,value,numeric=candidates[0]
        out.append({'field':key,'value':value,'numeric':numeric,'unit':LABELS[key][1], 'status':status,'quote':quote,
                    'model_match':'exact_title' if title_match else 'unproven','variant_match':'single_configuration' if status=='accepted' else status,
                    'model':model,'source_title':title[:300],
                    'configuration_checks':{'exact_title':title_match,'sibling_detected':sibling,'family_or_multiple_configurations':family},
                    'alternatives':list(dict.fromkeys(c[1] for c in candidates))[:5]})
    return out


def overlay(base,cards):
    """Deep-copy projection; originals and catalogue remain unchanged."""
    result=copy.deepcopy(base);ids=result['product_ids'];rows={r['id']:r for r in result['rows']}
    for card in cards:
        pid=card['product_id'];index=ids.index(pid);by_field={}
        for source in card.get('research',{}).get('sources',[]):
            for fact in source.get('basic_specs',[]):
                evidence=dict(fact,url=source['url'],retrieved_at=source['retrieved_at'],source_kind=source['kind'])
                by_field.setdefault(fact['field'],[]).append(evidence)
        for key,evidence in by_field.items():
            if key not in SUPPORTED:continue
            if key not in rows:
                label,unit=LABELS[key]
                row={'id':key,'label':label,'unit':unit,'cells':[cell([],unit) for _ in ids]}
                rows[key]=row;result['rows'].append(row)
            target=rows[key]['cells'][index]
            prior=target.get('research_evidence',[])
            evidence=list({(e['url'],e['quote']):e for e in prior+evidence}.values())
            target['research_evidence']=evidence
            accepted=[e for e in evidence if e['status']=='accepted']
            values={e['numeric'] if e['numeric'] is not None else semantic(key,e['value']) for e in accepted}
            disagreement=len(values)>1
            target['research_status']='source_conflict' if disagreement else 'accepted' if accepted else 'unverified'
            if target['status']=='web_completed':
                # Re-research can add a disagreement; restore the saved missing
                # cell instead of presenting one arbitrarily chosen web value.
                original=target['original_cell'];target.update(copy.deepcopy(original))
                target['research_evidence']=evidence;target['research_status']='source_conflict' if disagreement else 'accepted' if accepted else 'unverified'
            if target['status']=='missing' and accepted and not disagreement:
                original={k:copy.deepcopy(v) for k,v in target.items() if k not in ('research_evidence','research_status','original_cell')}
                chosen=accepted[0]
                target.update(value=chosen['value'],numeric=chosen['numeric'],status='web_completed',source='public_spec',original_cell=original)
            elif target['status'] in ('recorded','conflict') and accepted:
                original=target['numeric'] if target['numeric'] is not None else semantic(key,target['value'])
                if any(v!=original for v in values):target['research_status']='catalogue_disagreement'
    result['charts']=[]
    for row in result['rows']:
        row['different']=len({(c['status'],c['numeric'] if c['numeric'] is not None else comparable(c['value'])) for c in row['cells']})>1
        if row['unit'] and sum(c['numeric'] is not None for c in row['cells'])>=2:
            result['charts'].append({'id':row['id'],'label':row['label'],'unit':row['unit'],'unit_label':UNITS.get(row['unit'],row['unit']),
                'values':[{'product_id':pid,'value':c['numeric'],'status':c['status'],'source':c['source'],'research_evidence':c.get('research_evidence',[])} for pid,c in zip(ids,row['cells'])],
                'note':'مبدأ نمودار صفر است؛ نامشخص صفر نیست. تکمیل وب با منبع مشخص است؛ مقدار بزرگ‌تر لزوماً بهتر نیست.'})
    # Keep byte-for-byte legacy rows/charts when no static evidence was found.
    if not any(s.get('basic_specs') for c in cards for s in c.get('research',{}).get('sources',[])):
        result['rows']=copy.deepcopy(base['rows']);result['charts']=copy.deepcopy(base['charts'])
    result['basic_specs_completed']=sum(c['status']=='web_completed' for r in result['rows'] for c in r['cells'])
    return result
