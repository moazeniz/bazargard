import os
import sys
from pathlib import Path

import httpx
import pytest
from llm_fake import use_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from agent import Agent
from catalog import Catalog
from catalogue_first import CatalogueFirst
from memory import Memory


@pytest.fixture
def setup(tmp_path):
    path = os.getenv('TEST_CATALOG')
    if not path:
        pytest.skip('TEST_CATALOG required')
    mem = Memory(tmp_path / 'memory.db')
    uid = mem.user()
    sid = mem.session(uid)['id']
    return Agent(Catalog(path), mem), uid, sid


def test_retains_eligible_catalogue_after_empty_exploration(setup):
    a, u, s = setup
    ledger = CatalogueFirst(a, u, s)
    first = a.cat.search('هدفون', {'budget_max_toman': 3000000})
    ledger.record(first)
    result = ledger.retain({'offers': [], 'query': 'missing-xyz'})
    assert result['offers'] and result['catalogue_evidence']['retained_after_empty_search']
    # Every retained offer is rehydrated against today's session constraints.
    a.mem.update(u, [{'key': 'budget_max_toman', 'value': 1}], sid=s)
    assert not ledger.retain({'offers': []})['offers']
    a.mem.update(u, [{'key': 'budget_max_toman', 'value': None}], sid=s)
    chosen = {'offers': first['offers'][:1], 'curated': True}
    assert len(ledger.retain(chosen)['offers']) == 1
    ledger.reset()
    assert not ledger.retain({'offers': []})['offers']


@pytest.mark.asyncio
async def test_research_prerequisite_hard_constraints_and_isolation(setup):
    a, u, s = setup
    product = a.cat.search('هدفون')['offers'][0]['product_id']
    ledger = CatalogueFirst(a, u, s)
    result = await ledger.for_product(product)
    assert result['offers'] and all(o['product_id'] == product for o in result['offers'])
    a.mem.update(u, [{'key': 'budget_max_toman', 'value': 1}], sid=s)
    assert not (await ledger.for_product(product))['offers']
    other = a.mem.session(u)['id']
    assert (await CatalogueFirst(a, u, other).for_product(product))['offers']
    with pytest.raises(ValueError):
        await ledger.for_product('not-a-product')


@pytest.mark.asyncio
async def test_response_restores_cards_and_immutable_reply_snapshot(setup, monkeypatch):
    a, u, s = setup
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, headers, json):
            calls.append(json)
            if len(calls) <= 2:
                content = [{'type': 'tool_use', 'id': str(len(calls)), 'name': 'search_products',
                            'input': {'query': 'هدفون' if len(calls) == 1 else 'missing-xyz'}}]
            else:
                content = [{'type': 'text', 'text': 'نتیجه بررسی.'}]
            return httpx.Response(200, json={'content': content})
    use_client(monkeypatch, Client)
    events = [e async for e in a.response(u, s, 'هدفون می‌خواهم')]
    done = events[-1]
    assert done['type'] == 'done' and done['results']['offers']
    assert done['references']['catalogue_evidence']['retained_after_empty_search']
    assert done['references']['offers']
    a.mem.results(u, s, {'offers': []})
    persisted = Memory(a.mem.path).turns(u, s)[-1]['references']
    assert persisted == done['references']


@pytest.mark.asyncio
async def test_model_cannot_skip_catalogue_for_concrete_buying_request(setup, monkeypatch):
    a, u, s = setup
    payloads = []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, headers, json):
            payloads.append(json)
            return httpx.Response(200, json={'content': [{'type': 'text', 'text': 'پیشنهاد از حافظه مدل'}]})
    use_client(monkeypatch, Client)
    events = [e async for e in a.response(u, s, 'هدفون می‌خواهم', 'simple')]
    assert len(payloads) == 2
    assert events[-1]['results']['offers']
    assert events[-1]['references']['catalogue_evidence']['checks'][0]['purpose'] == 'enforced_before_answer'
    assert CatalogueFirst.explicit_query('سلام', {}) == ''
    assert CatalogueFirst.explicit_query('گوشی A55 تا ۳ میلیون', {}) == 'گوشی a55'


def test_old_cards_do_not_satisfy_changed_intent():
    old={'offers':[{'persian_title':'هدفون سونی WH-1000XM4'}]}
    assert CatalogueFirst.needs_lookup('گوشی A55 می‌خواهم',{},old)
    assert CatalogueFirst.needs_lookup('لپ تاپ می‌خواهم',{},old)
    assert not CatalogueFirst.needs_lookup('همه رو مقایسه کن',{'product_query':'هدفون'},old)
    assert not CatalogueFirst.needs_lookup('این هدفون WH1000XM4 خوب است؟',{},old)
