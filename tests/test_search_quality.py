"""Search quality rules surfaced by bench/ (general, category-agnostic)."""
import os,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))

@pytest.fixture(scope='module')
def cat():
    path=os.getenv('TEST_CATALOG')
    if not path:pytest.skip('TEST_CATALOG required')
    from catalog import Catalog
    return Catalog(path)

def test_rare_misspelling_is_corrected_from_catalogue_vocabulary(cat):
    r=cat.search('هدفن بلوتوث',{},10)
    assert {'from':'هدفن','to':'هدفون'} in r['corrected_terms']
    assert sum(o['category_name']=='هدفون، هدست و هندزفری' for o in r['offers'][:5])>=4
    assert cat.search('هدفون',{},5)['corrected_terms']==[]  # known words are never changed

def test_persian_suffixes_match_by_prefix(cat):
    r=cat.search('اسپیکر بلوتوث',{'budget_max_toman':2_000_000},10)
    assert sum(o['category_name']=='اسپیکر و سیستم صوتی' for o in r['offers'][:5])>=4

def test_category_named_by_query_is_the_product_not_an_accessory(cat):
    r=cat.search('پاوربانک',{'budget_max_toman':1_500_000},10)
    assert all(o['category_name']=='پاوربانک و شارژر همراه' for o in r['offers'][:5])

def test_accessory_categories_sink_unless_requested(cat):
    from price_policy import initial
    p={};spread=initial('جارو برقی','جارو برقی',{},'balanced',p)
    top=cat.search('جارو برقی',p,12,discovery=spread)['offers'][:5]
    assert not any(o['category_name'].startswith(('پاکت','لوازم یدکی')) for o in top)
    asked=cat.search('پاکت جارو برقی',{},5)['offers']
    assert any(o['category_name']=='پاکت جارو برقی' for o in asked)  # when asked for, accessories are returned

def test_categories_naming_more_query_words_rank_first(cat):
    r=cat.search('لامپ LED',{},10)
    assert all(o['category_name']=='لامپ LED و کم مصرف' for o in r['offers'][:5])
