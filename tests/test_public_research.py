import sys
import time
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import public_research as web

PRODUCT = {'product_id': 'p1', 'brand_name': 'Sony', 'model': 'WH-1000XM4', 'specs': []}

@pytest.fixture(autouse=True)
def no_curated_leads(monkeypatch):
    monkeypatch.setattr(web, 'REVIEW_LEADS', {})


@pytest.mark.parametrize('url', ['http://example.com', 'https://localhost/', 'https://user:pass@example.com', 'https://example.com:8506', 'file:///etc/passwd'])
def test_rejects_unsafe_url(url):
    with pytest.raises(ValueError): web.public_target(url)


@pytest.mark.parametrize('address', ['127.0.0.1', '10.1.1.1', '169.254.169.254', '::1', '::ffff:127.0.0.1', '100.64.0.1'])
def test_rejects_private_and_mixed_dns(monkeypatch, address):
    monkeypatch.setattr(web, 'resolve', lambda *a: [(None, None, None, None, ('8.8.8.8', 443)), (None, None, None, None, (address, 443))])
    with pytest.raises(ValueError): web.public_target('https://example.com')


def test_model_match_is_not_configuration_or_customer_experience():
    assert web.identity(PRODUCT, 'Sony WH-1000XM5 review')['status'] == 'mismatch_or_unproven'
    matched = web.identity(PRODUCT, 'Sony WH-1000XM4 review')
    assert matched['status'] == 'model_tokens_match' and not matched['allow_measured_claims']
    assert web.identity(dict(PRODUCT, model='Sony headphones'), 'Sony headphones review')['status'] == 'mismatch_or_unproven'
    assert web.source_kind('https://www.soundguys.com/review') == 'specialist'
    assert web.source_kind('https://soundguys.com.evil.example/review') == 'public_page'


def test_bounds_caching_provenance_missing_and_mismatch(tmp_path):
    calls = []
    def fetch(url, deadline):
        calls.append(url)
        assert deadline > time.monotonic()
        if 'duckduckgo.com' in url:
            return {'status': 'readable', 'url': url, 'links': ['https://www.soundguys.com/review', 'https://www.reddit.com/comments/id', 'https://www.notebookcheck.net/review', 'https://unused.example/review']}
        if 'reddit' in url: raise TimeoutError()
        if 'notebookcheck' in url: return {'url': url, 'status': 'blocked', 'http_status': 403}
        return {'url': url, 'status': 'readable', 'title': 'Sony WH-1000XM5 review', 'text': 'A different model. ' * 30}
    research = web.PublicResearch(tmp_path / 'public.db', fetch)
    first = research.investigate(PRODUCT)
    assert len(calls) == 4 and len(first['sources']) == 3
    assert first['status'] == 'unavailable' and all(not s['excerpts'] for s in first['sources'])
    assert [s['status'] for s in first['sources']] == ['readable', 'timeout', 'blocked']
    assert all(s['published_at'] is None and s['retrieved_at'] for s in first['sources'])
    assert research.investigate(PRODUCT)['cached'] and len(calls) == 4
    with research.conn() as db: db.execute('UPDATE public_research SET expires=0')
    assert not research.investigate(PRODUCT)['cached'] and len(calls) == 8


def test_matching_text_and_cache_do_not_include_private_context(tmp_path):
    def fetch(url, deadline):
        if 'duckduckgo' in url: return {'url': url, 'status': 'readable', 'links': ['https://www.soundguys.com/review']}
        return {'url': url, 'status': 'readable', 'title': 'WH-1000XM4 review', 'text': 'Sony WH-1000XM4 ' + 'readable review ' * 300}
    result = web.PublicResearch(tmp_path / 'public.db', fetch).investigate(PRODUCT)
    source = result['sources'][0]
    assert result['status'] == 'partial' and source['kind'] == 'specialist'
    assert len(' '.join(source['excerpts']).split()) <= 120
    assert not source['match']['allow_measured_claims']
    assert 'text' not in source and 'links' not in source


def test_parser_excludes_scripts_and_discovery_unavailable(tmp_path):
    parser = web.Document()
    parser.feed('<title>Review</title><script>steal secrets</script><p>Product evidence</p>')
    assert 'steal' not in parser.text() and 'Product evidence' in parser.text()
    result = web.PublicResearch(tmp_path / 'public.db', lambda u, d: {'url': u, 'status': 'blocked'}).investigate(PRODUCT)
    assert result['status'] == 'unavailable' and result['sources'] == []


def test_delivered_import_ids_and_broad_families_do_not_drive_search():
    product = dict(PRODUCT, model='authentic-base-10969', persian_title='هدفون بی‌سیم سونی مدل WH 1000XM6')
    assert web.model_identity(product) == 'WH 1000XM6'
    assert web.identity(product, 'Sony WH-1000XM6 review')['status'] == 'model_tokens_match'
    assert web.identity(product, 'Sony WH-1000XM5 review')['status'] == 'mismatch_or_unproven'
    phone = dict(PRODUCT, model='a 55', persian_title='گوشی سامسونگ A55 5G حافظه 256 رم 8')
    assert web.identity(phone, 'Samsung Galaxy A55 review')['status'] == 'model_tokens_match'
    assert web.identity(phone, 'Samsung A555 review')['status'] == 'mismatch_or_unproven'
    laptop = dict(PRODUCT, model='loq', persian_title='لپ تاپ لنوو LOQ 15IRX10-VE 14700HX')
    assert web.model_identity(laptop) == 'LOQ 15IRX10-VE'
    assert web.identity(laptop, 'LOQ 15IRX9 review')['status'] == 'mismatch_or_unproven'


def test_dns_wait_is_bounded(monkeypatch):
    def slow(*args,**kwargs):
        time.sleep(.15)
        return [(None,None,None,None,('8.8.8.8',443))]
    monkeypatch.setattr(web.socket,'getaddrinfo',slow)
    started=time.monotonic()
    with pytest.raises(TimeoutError):web.resolve('slow.example',.01)
    assert time.monotonic()-started<.1


def test_absolute_watchdog_is_installed_before_request(monkeypatch):
    calls=[]
    class Sock:
        def shutdown(self,*args):calls.append('shutdown')
        def close(self):calls.append('socket_close')
    class Conn:
        def __init__(self,*args,**kwargs):self.sock=Sock()
        def connect(self):calls.append('connect')
        def request(self,*args,**kwargs):calls.append('request');raise TimeoutError()
        def close(self):calls.append('close')
    class Timer:
        def __init__(self,remaining,fn):assert 0<remaining<=1
        def start(self):calls.append('deadline_installed')
        def cancel(self):calls.append('cancel')
    monkeypatch.setattr(web,'public_target',lambda *a:('example.com','8.8.8.8','https://example.com/'))
    monkeypatch.setattr(web.http.client,'HTTPSConnection',Conn)
    monkeypatch.setattr(web.threading,'Timer',Timer)
    with pytest.raises(TimeoutError):web.fetch('https://example.com/',time.monotonic()+1)
    assert calls==['connect','deadline_installed','request','cancel','close']


def test_variant_words_and_hyphenated_model_prefix_are_preserved():
    headphone=dict(PRODUCT,persian_title='هدفون Sony WH-CH720N')
    assert web.model_identity(headphone)=='WH-CH720N'
    phone=dict(PRODUCT,persian_title='گوشی iPhone 13 Pro Max')
    assert web.model_identity(phone)=='iPhone 13 Pro Max'
    assert web.identity(phone,'iPhone 13 review')['status']=='mismatch_or_unproven'
    assert web.identity(phone,'Apple iPhone 13 Pro Max review')['status']=='model_tokens_match'
