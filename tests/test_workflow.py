"""Behavioral acceptance tests: preserve evidence, carry review, control release."""
import hashlib
import io
import json
import secrets
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.demo import SAMPLE_TEXTS, generate_sample
from app.documents import inspect_export, parse_document
from app.main import LEGAL_SOURCE, create_app
from app.providers import clause_text


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tokens = {}
        self.app = create_app(self.temp.name, integration_tokens=self.tokens)
        self.client = TestClient(self.app)
        seed = self.client.post('/api/demo')
        self.assertEqual(seed.status_code, 201, seed.text)
        self.seed = seed.json()
        self.pid, self.cid, self.v1 = (self.seed[k] for k in ('project_id', 'contract_id', 'version_id'))
        self.base = f'/api/projects/{self.pid}'

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def stage(self, n):
        response = self.client.post(f'{self.base}/contracts/{self.cid}/demo-version', json={'stage': n})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def analyze(self, v):
        response = self.client.post(f'{self.base}/contracts/{self.cid}/analyze', json={'version_id': v['id'], 'provider': 'demo'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def detail(self):
        return self.client.get(self.base).json()

    def deliver(self, v, kind='external', fmt='docx'):
        response = self.client.post(f'{self.base}/deliverables', json={'version_id': v['id'], 'kind': kind, 'format': fmt})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def authorize(self, pid=None, scopes=None):
        token = secrets.token_urlsafe(24)
        self.tokens[token] = {'projects': [pid or self.pid], 'scopes': scopes or ['contracts:read', 'versions:write', 'deliverables:read']}
        return {'Authorization': 'Bearer ' + token}

    def test_originals_preserved_after_multiple_versions(self):
        original = self.client.get(f'{self.base}/versions/{self.v1}/original').content
        self.stage(2); self.stage(3)
        again = self.client.get(f'{self.base}/versions/{self.v1}/original').content
        self.assertEqual(original, again)
        v = self.detail()['contracts'][0]['versions']
        self.assertEqual([x['number'] for x in v], [1, 2, 3])
        self.assertEqual(hashlib.sha256(original).hexdigest(), v[0]['sha256'])
        self.assertEqual(len(set(x['sha256'] for x in v)), 3)

    def test_diff_shows_all_three_material_changes(self):
        v2 = self.stage(2)
        response = self.client.get(f'{self.base}/diff', params={'left': self.v1, 'right': v2['id']})
        self.assertEqual(response.status_code, 200)
        diffs = response.json()['lines']
        added = '\n'.join(x['text'] for x in diffs if x['type'] == 'add')
        removed = '\n'.join(x['text'] for x in diffs if x['type'] == 'remove')
        self.assertIn('5个工作日', added)
        self.assertIn('管理公司', added)
        self.assertIn('董事会一致同意', removed)

    def test_new_version_carries_three_issues_without_closure(self):
        v2 = self.stage(2)
        first = self.analyze(v2)
        self.assertEqual(len(first['saved_issues']), 3)
        v3 = self.stage(3)
        all_issues = self.detail()['issues']
        carried = [i for i in all_issues if i['version_id'] == v3['id']]
        self.assertEqual(len(carried), 3)
        self.assertTrue(all(i['status'] == 'open' for i in carried))
        self.assertTrue(all(i['carried_from'] and not i['human_confirmed'] for i in carried))
        payment = next(i for i in carried if i['clause'] == '5.2')
        self.assertEqual(payment['anchor_status'], 'changed')
        self.assertIn('5个工作日', payment['quote'])
        self.assertIn('10个工作日', payment['current_quote'])
        result = self.analyze(v3)
        self.assertIn('通知对象', ''.join(result['summary']))
        self.assertEqual(len(self.detail()['issues']), 6)
        self.assertEqual({i['clause'] for i in result['saved_issues'] if i['status'] == 'open'}, {'4.1', '5.2', '8.2'})

    def test_precise_legal_citation_and_contractual_basis(self):
        out = self.analyze(self.stage(2))
        issues = out['saved_issues']
        legal = next(i for i in issues if i['clause'] == '8.2')
        source = next(s for s in self.detail()['sources'] if s['id'] == legal['basis_source_id'])
        self.assertEqual(legal['basis_quote'], LEGAL_SOURCE['citation'])
        self.assertIn('gov.cn', source['url'])
        self.assertEqual(source['effective_at'], '2021-01-01')
        for item in issues:
            if item['clause'] != '8.2':
                self.assertIsNone(item['basis_source_id'])
                self.assertTrue(item['basis_note'])
            self.assertNotIn('##', item['quote'], 'A quote must stop before the next section heading')

    def test_wrong_clause_or_fabricated_citation_rejected(self):
        v = self.stage(2)
        sid = self.detail()['sources'][0]['id']
        payload = {'version_id': v['id'], 'clause': '8.2', 'quote': '不存在的条款引文', 'risk': '风险', 'suggestion': '核对', 'basis_source_id': sid, 'basis_quote': LEGAL_SOURCE['citation']}
        self.assertEqual(self.client.post(f'{self.base}/issues', json=payload).status_code, 422)
        payload['quote'] = clause_text(v['text'], '8.2')
        payload['basis_quote'] = '第五百六十五条要求所有通知必须提前十日。'
        self.assertEqual(self.client.post(f'{self.base}/issues', json=payload).status_code, 422)

    def test_only_human_can_close_and_old_review_is_retained(self):
        issues = self.analyze(self.stage(2))['saved_issues']
        iid = issues[0]['id']
        endpoint = f'{self.base}/issues/{iid}'
        self.assertEqual(self.client.patch(endpoint, json={'status': 'resolved'}).status_code, 422)
        good = self.client.patch(endpoint, json={'status': 'resolved', 'human_confirmed': True, 'resolution_note': '演示：法务逐条核对后确认期限安排。'})
        self.assertEqual(good.status_code, 200)
        self.assertEqual(self.client.delete(endpoint).status_code, 405)
        self.assertTrue(any(x['action'] == 'issue.human_reviewed' for x in self.detail()['audit']))

    def test_unapproved_delivery_is_blocked_locally_and_integration(self):
        d = self.deliver(self.stage(2))
        self.assertEqual(self.client.get(f'{self.base}/deliverables/{d["id"]}/download').status_code, 403)
        headers = self.authorize()
        endpoint = f'/api/integration/projects/{self.pid}/deliverables/{d["id"]}/download'
        self.assertEqual(self.client.get(endpoint, headers=headers).status_code, 403)
        self.assertEqual(self.client.get('/.data/objects/' + d['sha256']).status_code, 404)
        preview = self.client.get(f'{self.base}/deliverables/{d["id"]}/preview')
        self.assertEqual(preview.status_code, 200)
        self.assertIn('并非已批准', preview.json()['notice'])

    def test_approval_requires_manual_note_and_explicit_open_issue_acceptance(self):
        v = self.stage(2); self.analyze(v)
        d = self.deliver(v)
        endpoint = f'{self.base}/deliverables/{d["id"]}/approve'
        self.assertEqual(self.client.post(endpoint, json={}).status_code, 422)
        self.assertEqual(self.client.post(endpoint, json={'human_confirmed': True, 'review_note': '已核对'}).status_code, 409)
        accepted = self.client.post(endpoint, json={'human_confirmed': True, 'review_note': '仅批准虚构对外谈判稿，明确保留三项未决问题。', 'accept_open_issues': True})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(self.client.get(f'{self.base}/deliverables/{d["id"]}/download').status_code, 200)
        self.assertEqual(self.client.get(f'/api/integration/projects/{self.pid}/deliverables/{d["id"]}/download', headers=self.authorize()).status_code, 200)

    def test_new_version_invalidates_old_approval(self):
        v = self.stage(2); d = self.deliver(v)
        self.client.post(f'{self.base}/deliverables/{d["id"]}/approve', json={'human_confirmed': True, 'review_note': '已逐条核对虚构稿'})
        self.stage(3)
        self.assertEqual(self.client.get(f'{self.base}/deliverables/{d["id"]}/download').status_code, 409)
        self.assertFalse(self.detail()['deliverables'][0]['approval_valid'])

    def test_review_change_invalidates_approval(self):
        v = self.stage(2); issues = self.analyze(v)['saved_issues']; d = self.deliver(v)
        self.client.post(f'{self.base}/deliverables/{d["id"]}/approve', json={'human_confirmed': True, 'review_note': '已复核未决事项', 'accept_open_issues': True})
        self.client.patch(f'{self.base}/issues/{issues[0]["id"]}', json={'status': 'in_review', 'human_confirmed': True, 'resolution_note': '出现新的谈判事实，重新审核'})
        self.assertEqual(self.client.get(f'{self.base}/deliverables/{d["id"]}/download').status_code, 409)

    def test_internal_checklist_and_clean_formats(self):
        v2 = self.stage(2); self.analyze(v2); v3 = self.stage(3)
        for fmt in ('docx', 'pdf', 'md'):
            with self.subTest(format=fmt):
                internal = self.deliver(v3, 'internal', fmt)
                response = self.client.get(f'{self.base}/deliverables/{internal["id"]}/download')
                self.assertEqual(response.status_code, 200)
                parsed = parse_document(internal['filename'], response.content)
                self.assertIn('内部审核清单', parsed['text'])
                self.assertIn('通知对象', parsed['text'])
                external = self.deliver(v3, 'external', fmt)
                content = self.app.state.store.get(external['sha256'])
                self.assertTrue(inspect_export(content, fmt)['passed'])
                self.assertNotIn('风险：', parse_document(external['filename'], content)['text'])

    def test_internal_document_never_exposed_by_external_interface(self):
        d = self.deliver(self.stage(2), 'internal', 'md')
        self.assertEqual(self.client.get(f'/api/integration/projects/{self.pid}/deliverables/{d["id"]}/download', headers=self.authorize()).status_code, 404)

    def test_internal_review_package_preserves_exact_draft_without_approval(self):
        d = self.deliver(self.stage(2))
        response = self.client.get(f'{self.base}/deliverables/{d["id"]}/review-copy')
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as package:
            name = next(n for n in package.namelist() if n.endswith('.docx'))
            self.assertTrue(name.startswith('INTERNAL-REVIEW-UNAPPROVED'))
            self.assertEqual(hashlib.sha256(package.read(name)).hexdigest(), d['sha256'])
            self.assertIn('不改变批准状态', package.read('READ-ME-NOT-A-RELEASE.txt').decode())
        self.assertEqual(self.client.get(f'/api/integration/projects/{self.pid}/deliverables/{d["id"]}/download', headers=self.authorize()).status_code, 403)

    def test_project_boundary_scope_and_disabled_integration(self):
        endpoint = f'/api/integration/projects/{self.pid}/contracts'
        self.assertEqual(self.client.get(endpoint).status_code, 503)
        wrong = self.authorize(pid='other-project')
        self.assertEqual(self.client.get(endpoint, headers=wrong).status_code, 403)
        no_scope = self.authorize(scopes=['deliverables:read'])
        self.assertEqual(self.client.get(endpoint, headers=no_scope).status_code, 403)
        permitted = self.client.get(endpoint, headers=self.authorize())
        self.assertEqual(permitted.status_code, 200)
        self.assertNotIn('objects/', permitted.text)
        other = self.client.post('/api/demo').json()['project_id']
        self.assertEqual(self.client.get(f'/api/projects/{other}/versions/{self.v1}/original').status_code, 404)

    def test_import_idempotency_replay_and_conflict(self):
        url = f'/api/integration/projects/{self.pid}/contracts/{self.cid}/versions'
        headers = self.authorize() | {'Idempotency-Key': 'fictional-import-001'}
        file = ('counterparty.md', SAMPLE_TEXTS[2].encode(), 'text/markdown')
        first = self.client.post(url, headers=headers, files={'file': file}, data={'kind': 'counterparty'})
        second = self.client.post(url, headers=headers, files={'file': file}, data={'kind': 'counterparty'})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()['id'], second.json()['id'])
        self.assertEqual(len(self.detail()['contracts'][0]['versions']), 2)
        conflict = self.client.post(url, headers=headers, files={'file': ('counterparty.md', SAMPLE_TEXTS[3].encode(), 'text/markdown')}, data={'kind': 'counterparty'})
        self.assertEqual(conflict.status_code, 409)

    def test_archive_restore_signed_original_and_no_hard_delete(self):
        response = self.client.post(f'{self.base}/contracts/{self.cid}/versions', files={'file': ('signed.pdf', generate_sample(1, 'pdf'), 'application/pdf')}, data={'kind': 'signed'})
        self.assertEqual(response.status_code, 201)
        signed = response.json()
        self.assertEqual(self.client.post(f'{self.base}/deliverables', json={'version_id': signed['id'], 'kind': 'external', 'format': 'docx'}).status_code, 422)
        self.assertEqual(self.client.patch(f'{self.base}/contracts/{self.cid}', json={'archived': True}).status_code, 200)
        self.assertEqual(self.client.get(f'{self.base}/versions/{signed["id"]}/original').status_code, 200)
        self.assertEqual(self.client.delete(f'{self.base}/versions/{signed["id"]}').status_code, 404)
        self.assertEqual(self.client.patch(f'{self.base}/contracts/{self.cid}', json={'archived': False}).status_code, 200)
        self.assertFalse(self.detail()['contracts'][0]['archived'])

    def test_database_prevents_original_rewrite_and_audit_delete(self):
        with sqlite3.connect(self.app.state.db_path) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('UPDATE versions SET text=? WHERE id=?', ('changed', self.v1))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('DELETE FROM audit')

    def test_foreign_contract_comparison_rejected(self):
        other = self.client.post(f'{self.base}/contracts', json={'title': '股东协议（虚构）'}).json()
        other_v = self.client.post(f'{self.base}/contracts/{other["id"]}/versions', files={'file': ('agreement.md', SAMPLE_TEXTS[1].encode(), 'text/markdown')}, data={'kind': 'initial'}).json()
        self.assertEqual(self.client.get(f'{self.base}/diff', params={'left': self.v1, 'right': other_v['id']}).status_code, 422)

    def test_scan_is_archived_but_requires_ocr_before_diff_or_clean_export(self):
        from io import BytesIO
        from pypdf import PdfWriter
        stream = BytesIO(); writer = PdfWriter(); writer.add_blank_page(width=612, height=792); writer.write(stream)
        response = self.client.post(f'{self.base}/contracts/{self.cid}/versions', files={'file': ('scan.pdf', stream.getvalue(), 'application/pdf')}, data={'kind': 'revision'})
        self.assertEqual(response.status_code, 201)
        v = response.json(); self.assertEqual(v['parse_status'], 'needs_ocr')
        self.assertEqual(self.client.get(f'{self.base}/diff', params={'left': self.v1, 'right': v['id']}).status_code, 422)
        self.assertEqual(self.client.post(f'{self.base}/deliverables', json={'version_id': v['id'], 'kind': 'external', 'format': 'md'}).status_code, 422)

    def test_same_origin_and_host_guards(self):
        response = self.client.post('/api/demo', headers={'Origin': 'https://malicious.invalid'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get('/api/health', headers={'Host': 'rebind.invalid'}).status_code, 400)

    def test_external_model_is_not_fabricated_and_search_does_not_send(self):
        v = self.stage(2)
        response = self.client.post(f'{self.base}/contracts/{self.cid}/analyze', json={'version_id': v['id'], 'provider': 'gemini'})
        self.assertEqual(response.status_code, 422)
        response = self.client.post('/api/legal-search/prepare', json={'legal_question': '解除通知送达经授权管理人是否有效？', 'deidentified_confirmed': True})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['sent'])
        self.assertEqual(self.client.post('/api/legal-search/prepare', json={'legal_question': '甲公司全文', 'contract_text': 'secret', 'deidentified_confirmed': True}).status_code, 422)

    def test_openapi_describes_three_scoped_company_operations(self):
        spec = self.client.get('/openapi.json').json()
        integration = {p: ops for p, ops in spec['paths'].items() if p.startswith('/api/integration/')}
        self.assertEqual(len(integration), 3)
        for operations in integration.values():
            for operation in operations.values():
                self.assertEqual(operation['security'], [{'ProjectBearer': []}])

    def test_current_rollback_does_not_drop_open_review_roots(self):
        v2 = self.stage(2); self.analyze(v2)
        self.client.post(f'{self.base}/contracts/{self.cid}/current', json={'version_id': self.v1})
        v3 = self.stage(3)
        self.assertEqual(len([i for i in self.detail()['issues'] if i['version_id'] == v3['id']]), 3)
        d = self.deliver(v3)
        self.assertEqual(self.client.post(f'{self.base}/deliverables/{d["id"]}/approve', json={'human_confirmed': True, 'review_note': '仍有未结问题'}).status_code, 409)

    def test_disabling_carry_cannot_bypass_open_issue_approval_gate(self):
        self.analyze(self.stage(2))
        imported = self.client.post(f'{self.base}/contracts/{self.cid}/versions', files={'file': ('revision.md', SAMPLE_TEXTS[3].encode(), 'text/markdown')}, data={'kind': 'revision', 'carry_issues': 'false'})
        self.assertEqual(imported.status_code, 201)
        v = imported.json()
        self.assertEqual(self.detail()['contracts'][0]['open_issue_count'], 3)
        d = self.deliver(v)
        self.assertEqual(self.client.post(f'{self.base}/deliverables/{d["id"]}/approve', json={'human_confirmed': True, 'review_note': '仍有未重定位问题'}).status_code, 409)
        internal = self.deliver(v, 'internal', 'md')
        text = self.client.get(f'{self.base}/deliverables/{internal["id"]}/download').text
        self.assertIn('未带入本版', text)

    def test_demo_uses_actual_deadline_in_modified_fictional_sample(self):
        altered = SAMPLE_TEXTS[2].replace('5个工作日', '7个工作日')
        v = self.client.post(f'{self.base}/contracts/{self.cid}/versions', files={'file': ('seven.md', altered.encode(), 'text/markdown')}, data={'kind': 'counterparty'}).json()
        output = self.analyze(v)
        payment = next(x for x in output['findings'] if x['clause'] == '5.2')
        self.assertIn('7个工作日', payment['risk'])
        self.assertNotIn('5个工作日', payment['risk'])


if __name__ == '__main__':
    unittest.main()
