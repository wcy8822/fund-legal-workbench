"""V0.2 evidence/adoption regression cases. Authored, not run in this iteration.

The user's validation deferral applies to these cases and the earlier suite.
Run only when the validation step is resumed; their presence is not a pass.
"""
import sqlite3
import secrets
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.main import LEGAL_SOURCE, create_app


class OpinionRewriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tokens = {}
        self.app = create_app(self.temp.name, integration_tokens=self.tokens)
        self.client = TestClient(self.app)
        seed = self.client.post('/api/demo').json()
        self.pid, self.cid, self.v1 = (seed[k] for k in ('project_id', 'contract_id', 'version_id'))
        self.base = f'/api/projects/{self.pid}'
        stage = self.client.post(f'{self.base}/contracts/{self.cid}/demo-version', json={'stage': 2})
        self.assertEqual(stage.status_code, 201, stage.text)
        self.v2 = stage.json()
        analyzed = self.client.post(f'{self.base}/contracts/{self.cid}/analyze', json={'version_id': self.v2['id'], 'provider': 'demo'})
        self.assertEqual(analyzed.status_code, 200, analyzed.text)
        self.issue = next(i for i in analyzed.json()['saved_issues'] if i['clause'] == '8.2')
        self.issue_path = f'{self.base}/issues/{self.issue["id"]}/rewrites'

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def create_task(self, mode='demo', audience='internal'):
        response = self.client.post(self.issue_path, json={'audience': audience, 'tone': 'concise', 'mode': mode})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def task_path(self, task):
        return f'{self.base}/rewrite-tasks/{task["id"]}'

    def adoption_payload(self, task, **changes):
        payload = {'draft_id': task['latest_draft']['id'], 'human_confirmed': True,
                   'meaning_confirmed': True, 'external_safe_confirmed': True,
                   'note': '已核对虚构条款、保留条件及表达用途，原问题继续保留。'}
        payload.update(changes)
        return payload

    def save_draft(self, task, text='请补充核对通知收件主体及相应授权，材料补齐前保留意见。'):
        response = self.client.post(self.task_path(task) + '/drafts', json={
            'text': text, 'meaning_notes': '保留授权待核实和问题未关闭的条件，未增加承诺。'})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_prepare_pins_evidence_without_claiming_a_model_result(self):
        task = self.create_task(mode='prepare')
        self.assertEqual(task['status'], 'prepared')
        self.assertIsNone(task['latest_draft'])
        self.assertEqual(task['drafts'], [])
        snapshot = task['context_snapshot']
        self.assertEqual(snapshot['issue']['id'], self.issue['id'])
        self.assertEqual(snapshot['target_version']['id'], self.v2['id'])
        self.assertEqual(snapshot['target_version']['sha256'], self.v2['sha256'])
        self.assertIn(self.issue['quote'], snapshot['target_version']['clause_text'])
        self.assertIn(self.issue['basis_quote'], snapshot['source']['citation'])
        self.assertIn(self.issue['pending'], task['prompt_text'])
        self.assertNotIn('text', snapshot['target_version'], 'Only relevant clauses, not the whole file, belong here')
        self.assertFalse(self.client.get('/api/health').json()['external_models_enabled'])

    def test_general_opinion_uses_manual_flow_and_cannot_masquerade_as_demo(self):
        original = self.issue
        response = self.client.post(f'{self.base}/issues', json={
            'version_id': self.v2['id'], 'clause': original['clause'], 'quote': original['quote'],
            'risk': '另行手工记录的待核事项', 'suggestion': '请核对项目约定。', 'pending': '事实待确认',
            'basis_note': '仅依据当前虚构合同。'})
        self.assertEqual(response.status_code, 201, response.text)
        self.issue_path = f'{self.base}/issues/{response.json()["id"]}/rewrites'
        self.assertFalse(self.client.get(self.issue_path).json()['demo_available'])
        refused = self.client.post(self.issue_path, json={'audience': 'business', 'tone': 'gentle', 'mode': 'demo'})
        self.assertEqual(refused.status_code, 422, refused.text)
        task = self.save_draft(self.create_task(mode='prepare', audience='business'))
        self.assertEqual(task['status'], 'draft')
        self.assertEqual(task['latest_draft']['source'], 'manual_input')
        forbidden = self.client.post(self.issue_path, json={'audience': 'business', 'tone': 'gentle', 'mode': 'gemini'})
        self.assertEqual(forbidden.status_code, 422)

    def test_adoption_keeps_original_evidence_status_and_original_file(self):
        raw = self.client.get(f'{self.base}/versions/{self.v1}/original').content
        task = self.create_task()
        draft = task['latest_draft']
        result = self.client.post(self.task_path(task) + '/adopt', json=self.adoption_payload(task))
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['status'], 'adopted')
        detail = self.client.get(self.base).json()
        preserved = next(i for i in detail['issues'] if i['id'] == self.issue['id'])
        self.assertEqual(preserved, self.issue, 'Expression adoption must not rewrite the underlying review decision')
        expression = next(i for i in detail['expression_revisions'] if i['task_id'] == task['id'])
        self.assertEqual(expression['text'], draft['text'])
        self.assertEqual(self.client.get(f'{self.base}/versions/{self.v1}/original').content, raw)

    def test_adoption_requires_separate_human_meaning_and_disclosure_confirmations(self):
        task = self.create_task(audience='counterparty')
        for change in ({'human_confirmed': False}, {'meaning_confirmed': False},
                       {'external_safe_confirmed': False}, {'note': ''}):
            with self.subTest(change=change):
                response = self.client.post(self.task_path(task) + '/adopt', json=self.adoption_payload(task, **change))
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get(self.issue_path).json()['expressions'], [])
        self.assertEqual(self.client.get(self.task_path(task)).json()['status'], 'draft')

    def test_drafts_are_retained_and_an_old_draft_cannot_replace_the_latest(self):
        first = self.create_task()
        original = first['latest_draft']
        second = self.save_draft(first)
        self.assertEqual(len(second['drafts']), 2)
        self.assertEqual(next(d for d in second['drafts'] if d['id'] == original['id'])['text'], original['text'])
        response = self.client.post(self.task_path(first) + '/adopt', json=self.adoption_payload(second, draft_id=original['id']))
        self.assertEqual(response.status_code, 409, response.text)
        response = self.client.post(self.task_path(first) + '/adopt', json=self.adoption_payload(second))
        self.assertEqual(response.status_code, 200, response.text)

    def test_new_contract_version_makes_old_task_stale_without_losing_its_snapshot(self):
        task = self.create_task()
        response = self.client.post(f'{self.base}/contracts/{self.cid}/demo-version', json={'stage': 3})
        self.assertEqual(response.status_code, 201, response.text)
        historical = self.client.get(self.task_path(task)).json()
        self.assertTrue(historical['stale'])
        self.assertEqual(historical['context_snapshot'], task['context_snapshot'])
        self.assertFalse(historical['can_adopt'])
        response = self.client.post(self.task_path(task) + '/adopt', json=self.adoption_payload(task))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertFalse(self.client.get(self.issue_path).json()['can_create'])

    def test_new_source_or_issue_status_invalidates_prior_task(self):
        task = self.create_task()
        response = self.client.post(f'{self.base}/sources', json=LEGAL_SOURCE)
        self.assertEqual(response.status_code, 201, response.text)
        response = self.client.post(self.task_path(task) + '/adopt', json=self.adoption_payload(task))
        self.assertEqual(response.status_code, 409, response.text)
        task = self.create_task()
        response = self.client.patch(f'{self.base}/issues/{self.issue["id"]}', json={
            'status': 'resolved', 'human_confirmed': True, 'resolution_note': '仅为状态变更边界样例。'})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(self.task_path(task) + '/adopt', json=self.adoption_payload(task))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertFalse(self.client.get(self.issue_path).json()['can_create'])

    def test_cross_project_read_save_and_adopt_are_rejected(self):
        task = self.create_task()
        other = self.client.post('/api/projects', json={'name': '第二个虚构项目'}).json()['id']
        foreign = f'/api/projects/{other}/rewrite-tasks/{task["id"]}'
        self.assertEqual(self.client.get(foreign).status_code, 404)
        self.assertEqual(self.client.get(f'/api/projects/{other}/issues/{self.issue["id"]}/rewrites').status_code, 404)
        self.assertEqual(self.client.post(foreign + '/drafts', json={'text': '越界草稿', 'meaning_notes': '越界'}).status_code, 404)
        self.assertEqual(self.client.post(foreign + '/adopt', json=self.adoption_payload(task)).status_code, 404)

    def test_archive_blocks_draft_and_adoption_but_retains_history(self):
        task = self.create_task()
        self.assertEqual(self.client.patch(f'{self.base}/contracts/{self.cid}', json={'archived': True}).status_code, 200)
        historical = self.client.get(self.task_path(task))
        self.assertEqual(historical.status_code, 200, historical.text)
        self.assertTrue(historical.json()['stale'])
        response = self.client.post(self.task_path(task) + '/drafts', json={'text': '不应写入', 'meaning_notes': '归档'} )
        self.assertEqual(response.status_code, 409, response.text)
        response = self.client.post(self.task_path(task) + '/adopt', json=self.adoption_payload(task))
        self.assertEqual(response.status_code, 409, response.text)

    def test_adoption_invalidates_prior_release_without_approving_new_artifacts(self):
        token = secrets.token_urlsafe(24)
        self.tokens[token] = {'projects': [self.pid], 'scopes': ['deliverables:read']}
        headers = {'Authorization': 'Bearer ' + token}
        response = self.client.post(f'{self.base}/deliverables', json={'version_id': self.v2['id'], 'kind': 'external', 'format': 'md'})
        self.assertEqual(response.status_code, 201, response.text)
        did = response.json()['id']
        integration = f'/api/integration/projects/{self.pid}/deliverables/{did}/download'
        self.assertEqual(self.client.get(integration, headers=headers).status_code, 403)
        response = self.client.post(f'{self.base}/deliverables/{did}/approve', json={
            'human_confirmed': True, 'review_note': '虚构谈判稿，明确保留未决问题。', 'accept_open_issues': True})
        self.assertEqual(response.status_code, 200, response.text)
        task = self.create_task()
        self.assertEqual(self.client.get(integration, headers=headers).status_code, 200,
                         'Preparing a private draft must not alter review decisions')
        response = self.client.post(self.task_path(task) + '/adopt', json=self.adoption_payload(task))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(integration, headers=headers).status_code, 409)
        self.assertFalse(self.client.get(self.base).json()['deliverables'][0]['approval_valid'])

    def test_different_audiences_coexist_until_evidence_changes(self):
        internal = self.create_task(audience='internal')
        counterparty = self.create_task(audience='counterparty')
        first = self.client.post(self.task_path(internal) + '/adopt', json=self.adoption_payload(internal))
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post(self.task_path(counterparty) + '/adopt', json=self.adoption_payload(counterparty))
        self.assertEqual(second.status_code, 200, second.text)
        expressions = self.client.get(self.base).json()['expression_revisions']
        self.assertEqual({e['audience'] for e in expressions if not e['stale']}, {'internal', 'counterparty'})
        response = self.client.post(f'{self.base}/sources', json=LEGAL_SOURCE)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(all(e['stale'] for e in self.client.get(self.base).json()['expression_revisions']))

    def test_adopted_internal_wording_is_not_added_to_external_contract(self):
        marker = '仅用于内部表达的虚构标记，请保留授权待核实条件。'
        task = self.save_draft(self.create_task(mode='prepare'), marker)
        response = self.client.post(self.task_path(task) + '/adopt', json=self.adoption_payload(task))
        self.assertEqual(response.status_code, 200, response.text)
        for kind in ('internal', 'external'):
            response = self.client.post(f'{self.base}/deliverables', json={
                'version_id': self.v2['id'], 'kind': kind, 'format': 'md'})
            self.assertEqual(response.status_code, 201, response.text)
            did = response.json()['id']
            preview = self.client.get(f'{self.base}/deliverables/{did}/preview')
            self.assertEqual(preview.status_code, 200, preview.text)
            if kind == 'internal':
                self.assertIn(marker, preview.json()['text'])
                self.assertIn(self.issue['suggestion'], preview.json()['text'])
            else:
                self.assertNotIn(marker, preview.json()['text'])
                self.assertEqual(self.client.get(f'{self.base}/deliverables/{did}/download').status_code, 403)

    def test_task_draft_and_adoption_records_are_append_only(self):
        task = self.create_task()
        response = self.client.post(self.task_path(task) + '/adopt', json=self.adoption_payload(task))
        self.assertEqual(response.status_code, 200, response.text)
        for table in ('rewrite_tasks', 'rewrite_drafts', 'issue_expressions'):
            for statement in (f'UPDATE {table} SET id=id', f'DELETE FROM {table}'):
                with self.subTest(table=table, statement=statement):
                    with sqlite3.connect(self.app.state.db_path) as conn:
                        with self.assertRaises(sqlite3.IntegrityError):
                            conn.execute(statement)

    def test_duplicate_adoption_cannot_create_a_second_revision(self):
        task = self.create_task()
        first = self.client.post(self.task_path(task) + '/adopt', json=self.adoption_payload(task))
        self.assertEqual(first.status_code, 200, first.text)
        again = self.client.post(self.task_path(task) + '/adopt', json=self.adoption_payload(task))
        self.assertIn(again.status_code, (200, 409), again.text)
        expressions = self.client.get(self.issue_path).json()['expressions']
        self.assertEqual(len([i for i in expressions if i['task_id'] == task['id']]), 1)


if __name__ == '__main__':
    unittest.main()
