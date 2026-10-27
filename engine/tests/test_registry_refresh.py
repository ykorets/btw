"""Public registry contracts: completeness, version identity and blocked access."""
import copy
import json
from pathlib import Path

import httpx
import pytest
from btw_engine import watch

FIX = Path(__file__).parent / 'fixtures'

def fixture():
    return json.loads((FIX / 'tceq_portal.json').read_text())

def test_registry_status_is_versioned_without_counting_it_as_generation():
    rows = fixture()['content']
    first = watch.tceq_portal_candidates(rows)
    assert len(first) == 2
    assert first[0]['payload']['evidence_basis'] == 'registry_status_not_operating_capacity'
    changed = copy.deepcopy(rows)
    changed[0]['pspStatusCd'] = 'NEW APPLICATION'
    assert watch.tceq_portal_candidates(changed)[0]['external_id'] != first[0]['external_id']
    rows[0]['complaints'] = {'irrelevant': 1}
    assert watch.tceq_portal_candidates(rows) == first
    assert len(watch.tceq_portal_candidates(rows + rows)) == 2

def test_registry_requires_airnsr_identity():
    row = fixture()['content'][0]
    row['aiPgmCd'] = 'WATER'
    assert watch.tceq_portal_candidates([row]) == []
    row['aiPgmCd'] = 'AIRNSR'
    del row['addnId']
    with pytest.raises(ValueError, match='identity'):
        watch.tceq_portal_candidates([row])

def test_portal_replays_all_pages_and_detects_truncation(monkeypatch):
    requests = []
    def handler(req):
        if req.method == 'GET':
            return httpx.Response(200, text='ephemeral-test-token')
        assert req.headers['AppName'] == 'psp-frontend'
        assert req.headers['Authorization'] == 'Bearer ephemeral-test-token'
        payload = json.loads(req.content)
        page = payload['pageable']['pageNumber']
        requests.append(page)
        data = fixture()
        data.update(number=page, last=page == 1, totalElements=4, totalPages=2)
        return httpx.Response(200, json=data)
    original = httpx.Client
    monkeypatch.setattr(watch.httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    cfg = {'params': {'queries':['data center'], 'max_pages':2}}
    assert len(watch.run_tceq_portal(cfg)) == 2
    assert requests == [0,1]
    cfg['params']['max_pages'] = 1
    with pytest.raises(RuntimeError, match='incomplete sweep'):
        watch.run_tceq_portal(cfg)

def test_html_or_changed_schema_is_not_a_successful_empty_registry(monkeypatch):
    def handler(req):
        return httpx.Response(200, text='session') if req.method == 'GET' else httpx.Response(200, json={'content': []})
    original = httpx.Client
    monkeypatch.setattr(watch.httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(ValueError, match='pagination'):
        watch.run_tceq_portal({'params': {'queries':['data center']}})

def test_opsb_accepts_attribute_spacing_entities_and_nested_text():
    original = (FIX/'opsb_case_25-0185.html').read_text()
    modified = original.replace('href="','class="filing" href = "').replace('</a></td><td>','</a> </td>\n<td>').replace('Condition 12.', '<strong>Condition</strong> 12. &amp; details')
    rows = watch.parse_opsb_case_html(modified,'25-0185')
    assert len(rows)==3
    assert 'Condition 12. & details' in rows[0]['payload']['summary']

def test_opsb_http200_block_is_not_an_empty_success():
    with pytest.raises(RuntimeError, match='access blocked'):
        watch.parse_opsb_case_html('<html><title>Request Rejected</title></html>','25-0185')

def test_portal_rejects_last_page_with_missing_records(monkeypatch):
    def handler(req):
        if req.method == 'GET':
            return httpx.Response(200,text='session')
        data=fixture()
        data['totalElements']=3  # two rows, claiming a complete three-row result
        return httpx.Response(200,json=data)
    original=httpx.Client
    monkeypatch.setattr(watch.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    with pytest.raises(RuntimeError,match='incomplete result count'):
        watch.run_tceq_portal({'params':{'queries':['data center']}})

def test_failed_single_source_does_not_advance_success_timestamp(monkeypatch):
    monkeypatch.setattr(watch.sys,'argv',['watch','--source','opsb-dockets'])
    monkeypatch.setattr(watch,'load_sources',lambda:[{'id':'opsb-dockets','adapter':'opsb_case'}])
    monkeypatch.setattr(watch,'upsert_source',lambda cfg:None)
    calls=[]
    monkeypatch.setattr(watch,'store_candidates',lambda *args:calls.append('stored'))
    monkeypatch.setattr(watch,'touch_source',lambda *args:calls.append('success'))
    def blocked(cfg):
        raise RuntimeError('public docket access blocked')
    monkeypatch.setitem(watch.ADAPTERS,'opsb_case',blocked)
    with pytest.raises(SystemExit) as error:
        watch.main()
    assert error.value.code==1 and calls==[]
