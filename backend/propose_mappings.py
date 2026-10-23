"""Offline bounded mapping-review helper; suggestions NEVER change runtime rules.

Reads the full read-only field inventory; emits up to100 frequency-ranked,
string-similarity candidates for review. Similar spelling does not establish
meaning or applicability. No model/API calls and no automatic ontology rewrite.
"""
import argparse,json,difflib
from pathlib import Path
from product_data import ALIASES,VERSION
from catalog import normalize

def propose(inventory,output):
 suggestions=[];unknown_entries=0;known=0
 for line in Path(inventory).open():
  item=json.loads(line);key=item['normalized_key']
  if key in ALIASES:known+=item['entries'];continue
  unknown_entries+=item['entries']
  if len(suggestions)>=100 or item['entries']<10:continue
  if any(w in key for w in ('بسته بندی','حداکثر','قابل ارتقا','ماکزیمم','gpu','گرافیک')):continue
  candidates=difflib.get_close_matches(key,ALIASES,n=2,cutoff=.78)
  if candidates:suggestions.append({'key':key,'entries':item['entries'],'top_categories':item['top_categories'],'examples':item['examples'],'candidate_fields':[{'alias':a,'field':ALIASES[a][0]} for a in candidates],'approved':False,'state':'needs_context_and_unit_review','rule_version':VERSION})
 Path(output).write_text(json.dumps({'version':VERSION,'scope':'all inventory labels; suggestions bounded to100 frequent unknown labels, never accepted automatically','exact_known_label_entries':known,'unmapped_label_entries':unknown_entries,'suggestions':suggestions},ensure_ascii=False,indent=2))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--inventory',required=True);p.add_argument('--output',required=True);a=p.parse_args();propose(a.inventory,a.output)
