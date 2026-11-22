"""V0.3 retrieval regression cases: authored, not executed in this iteration.

All provider responses below are simulated fixtures, not live search evidence.
Tests default to disabled public search and forbid an unmocked HTTP transport.
Run only after the user resumes validation; this file is not a passing result.
"""
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.main import LEGAL_SOURCE, create_app
from app.public_search import get_public_search_config, search_public


QUESTION = "合同解除通知应送达何种主体？"
AS_OF = "2026-11-21"
FAKE_KEY = "fixture-only-not-a-real-api-key"


class _OfflineRetrievalCase(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            "FWB_PUBLIC_SEARCH_MODE": "disabled", "FWB_TAVILY_API_KEY": "",
        })
        environment.start()
        self.addCleanup(environment.stop)
        network = patch("app.public_search.httpx.HTTPTransport", side_effect=AssertionError(
            "An external transport must be explicitly replaced with a fixture"))
        self.forbidden_transport = network.start()
        self.addCleanup(network.stop)


class RetrievalWorkflowTests(_OfflineRetrievalCase):
    def setUp(self):
        super().setUp()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app = create_app(temp.name, integration_tokens={})
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        response = self.client.post("/api/demo")
        self.assertEqual(response.status_code, 201, response.text)
        seed = response.json()
        self.pid, self.cid, self.v1 = (seed[key] for key in ("project_id", "contract_id", "version_id"))
        self.base = f"/api/projects/{self.pid}"

    def detail(self):
        response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def stage(self, number):
        response = self.client.post(f"{self.base}/contracts/{self.cid}/demo-version", json={"stage": number})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def analyze(self, version):
        response = self.client.post(f"{self.base}/contracts/{self.cid}/analyze", json={
            "version_id": version["id"], "provider": "demo",
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def public_payload(self, **changes):
        payload = {"question": QUESTION, "jurisdiction": "CN", "as_of": AS_OF,
                   "purpose": "current_rules", "deidentified_confirmed": True, "mode": "prepare"}
        payload.update(changes)
        return payload

    def public_run(self, **changes):
        response = self.client.post(f"{self.base}/research/public", json=self.public_payload(**changes))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def local_run(self, query, include_history=False):
        response = self.client.post(f"{self.base}/research/local", json={
            "query": query, "include_history": include_history,
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def outcome_fixture(self, candidate_id="fixture-candidate"):
        return {"provider": "tavily", "status": "completed", "sent_externally": True,
                "results": [{"id": candidate_id, "title": "模拟来源候选（演示数据）",
                    "kind": "public_candidate", "locator": "搜索摘要",
                    "excerpt": "模拟摘要，尚未核验原文或效力。", "url": LEGAL_SOURCE["url"],
                    "reported_published_at": "2026-11-20", "evidence_status": "unverified_search_excerpt"}],
                "warnings": ["仅为模拟候选，不是已核验法源。"], "coverage": "模拟响应；没有真实联网。"}

    def adoption_payload(self, **changes):
        payload = {key: LEGAL_SOURCE[key] for key in (
            "title", "category", "url", "citation", "published_at", "effective_at", "effect_status")}
        payload.update(candidate_id=None, title="民法典第565条（测试新增快照）", is_demo=True,
                       verification_note="测试夹具中的人工记录说明；用例本身未访问公开原文。",
                       human_confirmed=True, original_checked=True, as_of_checked=True)
        payload.update(changes)
        return payload

    def adoption_url(self, run):
        return f"{self.base}/research/runs/{run['id']}/adopt-source"

    def test_local_results_honor_project_current_and_history_scope_without_external_calls(self):
        v2 = self.stage(2)
        self.analyze(v2)
        v3 = self.stage(3)
        other_response = self.client.post("/api/demo")
        self.assertEqual(other_response.status_code, 201, other_response.text)
        other = other_response.json()
        uploaded = self.client.post(
            f"/api/projects/{other['project_id']}/contracts/{other['contract_id']}/versions",
            files={"file": ("isolated.md", "# 虚构检索样例\n第1.1条 他项目专有线索。".encode(), "text/markdown")},
            data={"kind": "revision"},
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        with patch("app.retrieval.search_public") as provider:
            current = self.local_run("通知")
            history = self.local_run("通知", include_history=True)
            absent = self.local_run("他项目专有线索", include_history=True)
            provider.assert_not_called()
        self.forbidden_transport.assert_not_called()
        self.assertFalse(current["sent_externally"])
        self.assertEqual(current["provider"], "current_project")
        self.assertEqual({r["version_id"] for r in current["results"] if r["kind"] == "contract_version"}, {v3["id"]})
        self.assertEqual({r["version_id"] for r in current["results"] if r["kind"] == "review_issue"}, {v3["id"]})
        self.assertEqual({r["version_id"] for r in history["results"] if r["kind"] == "contract_version"}, {self.v1, v2["id"], v3["id"]})
        self.assertEqual({r["version_id"] for r in history["results"] if r["kind"] == "review_issue"}, {v2["id"], v3["id"]})
        source_ids = {source["id"] for source in self.detail()["sources"]}
        self.assertTrue({r["record_id"] for r in history["results"] if r["kind"] == "recorded_source"}.issubset(source_ids))
        self.assertEqual(absent["status"], "no_results")
        self.assertEqual(absent["results"], [])
        self.assertFalse(absent["sent_externally"])
        other_access = self.client.get(f"/api/projects/{other['project_id']}/research/runs/{current['id']}")
        self.assertEqual(other_access.status_code, 404)

    def test_local_issue_search_matches_clause_number_and_exact_basis_quote(self):
        v2 = self.stage(2)
        source_id = next(source["id"] for source in self.detail()["sources"]
                         if source["title"] == LEGAL_SOURCE["title"])
        basis_quote = "当事人一方依法主张解除合同的，应当通知对方。"
        response = self.client.post(f"{self.base}/issues", json={
            "version_id": v2["id"], "clause": "8.2", "quote": "应送达管理人",
            "risk": "收件授权待复核", "suggestion": "请核对授权文件",
            "basis_source_id": source_id, "basis_quote": basis_quote,
        })
        self.assertEqual(response.status_code, 201, response.text)
        issue_id = response.json()["id"]
        for query in ("8.2", basis_quote):
            with self.subTest(query=query):
                hits = [hit for hit in self.local_run(query)["results"] if hit["kind"] == "review_issue"]
                self.assertEqual([(hit["record_id"], hit["version_id"]) for hit in hits], [(issue_id, v2["id"])])
                self.assertIn(query, hits[0]["excerpt"])

    def test_public_input_rejects_missing_confirmation_extra_text_and_known_identifiers(self):
        detail = self.detail()
        name = detail["contracts"][0]["counterparty"].replace("（虚构）", "")
        cases = [
            {"deidentified_confirmed": False},
            {"contract_text": "禁止外发的模拟合同正文"},
            {"question": "请判断 user@example.invalid 的解除通知是否有效？"},
            {"question": name + "发出的解除通知是否有效？"},
            {"question": detail["project"]["name"] + "适用什么解除通知规则？"},
        ]
        with patch("app.retrieval.search_public") as provider:
            for changes in cases:
                with self.subTest(changes=changes):
                    response = self.client.post(f"{self.base}/research/public", json=self.public_payload(mode="search", **changes))
                    self.assertEqual(response.status_code, 422, response.text)
            provider.assert_not_called()
        self.assertEqual(self.client.get(f"{self.base}/research/runs").json(), [])
        self.forbidden_transport.assert_not_called()

    def test_prepare_records_question_and_portals_without_calling_a_provider(self):
        with patch("app.retrieval.search_public") as provider:
            run = self.public_run()
            provider.assert_not_called()
        self.assertEqual(run["status"], "prepared")
        self.assertEqual(run["provider"], "manual")
        self.assertEqual(run["query"], QUESTION)
        self.assertFalse(run["sent_externally"])
        self.assertEqual(run["results"], [])
        self.assertTrue(run["official_portals"])
        recorded = self.client.get(f"{self.base}/research/runs/{run['id']}").json()
        self.assertEqual(recorded["parameters"], {
            "jurisdiction": "CN", "as_of": AS_OF, "purpose": "current_rules", "deidentified_confirmed": True, "mode": "prepare"})
        self.assertEqual(recorded["results"], [])
        self.forbidden_transport.assert_not_called()

    def test_search_passes_only_public_parameters_and_candidates_do_not_change_legal_decisions(self):
        self.analyze(self.stage(2))
        before = self.detail()
        fixture = self.outcome_fixture()
        with patch("app.retrieval.search_public", return_value=fixture) as provider:
            run = self.public_run(mode="search")
            provider.assert_called_once_with(QUESTION, "CN", AS_OF, "current_rules")
        self.assertEqual(run["results"], fixture["results"])
        self.assertEqual(run["results"][0]["evidence_status"], "unverified_search_excerpt")
        after = self.detail()
        self.assertEqual(after["sources"], before["sources"])
        self.assertEqual(after["issues"], before["issues"])
        self.assertEqual(after["project"]["review_revision"], before["project"]["review_revision"])
        self.assertEqual(run["adoptions"], [])
        self.forbidden_transport.assert_not_called()

    def test_disabled_route_records_not_configured_without_attempting_transport(self):
        run = self.public_run(mode="search")
        self.assertEqual(run["status"], "not_configured")
        self.assertFalse(run["sent_externally"])
        self.assertEqual(run["results"], [])
        capabilities = self.client.get("/api/retrieval/capabilities").json()
        self.assertFalse(capabilities["public_search"]["available"])
        self.assertEqual(capabilities["public_search"]["mode"], "disabled")
        self.forbidden_transport.assert_not_called()

    def test_source_recording_requires_three_confirmations_and_valid_dates(self):
        run = self.public_run()
        before_sources = self.detail()["sources"]
        cases = [
            {"human_confirmed": False}, {"original_checked": False}, {"as_of_checked": False},
            {"verification_note": ""}, {"published_at": "2026-02-30"}, {"effective_at": "not-a-date"},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                response = self.client.post(self.adoption_url(run), json=self.adoption_payload(**changes))
                self.assertEqual(response.status_code, 422, response.text)
        invalid_time = self.client.post(f"{self.base}/research/public", json=self.public_payload(as_of="2026-02-30"))
        self.assertEqual(invalid_time.status_code, 422)
        self.assertEqual(self.detail()["sources"], before_sources)
        self.assertEqual(self.client.get(f"{self.base}/research/runs/{run['id']}").json()["adoptions"], [])

    def test_source_recording_rejects_foreign_candidates_projects_and_internal_hits(self):
        with patch("app.retrieval.search_public", side_effect=[self.outcome_fixture("candidate-a"), self.outcome_fixture("candidate-b")]):
            run_a = self.public_run(mode="search")
            run_b = self.public_run(mode="search")
        self.assertNotEqual(run_a["id"], run_b["id"])
        foreign_candidate = self.client.post(self.adoption_url(run_a), json=self.adoption_payload(candidate_id="candidate-b"))
        self.assertEqual(foreign_candidate.status_code, 422, foreign_candidate.text)
        other_response = self.client.post("/api/demo")
        self.assertEqual(other_response.status_code, 201, other_response.text)
        other = other_response.json()
        foreign_project = self.client.post(
            f"/api/projects/{other['project_id']}/research/runs/{run_a['id']}/adopt-source",
            json=self.adoption_payload(candidate_id="candidate-a"),
        )
        self.assertEqual(foreign_project.status_code, 404, foreign_project.text)
        local = self.local_run("通知")
        wrong_scope = self.client.post(self.adoption_url(local), json=self.adoption_payload())
        self.assertEqual(wrong_scope.status_code, 422, wrong_scope.text)
        accepted = self.client.post(self.adoption_url(run_a), json=self.adoption_payload(candidate_id="candidate-a"))
        self.assertEqual(accepted.status_code, 201, accepted.text)
        self.assertEqual(accepted.json()["source"]["project_id"], self.pid)
        self.assertEqual(accepted.json()["source"]["as_of"], AS_OF)
        self.assertEqual(accepted.json()["adoption"]["candidate_id"], "candidate-a")

    def test_manual_source_replay_is_idempotent_and_changed_attestation_cannot_overwrite_it(self):
        run = self.public_run()
        before = self.detail()["sources"]
        payload = self.adoption_payload()
        first = self.client.post(self.adoption_url(run), json=payload)
        self.assertEqual(first.status_code, 201, first.text)
        adopted = first.json()
        revision = self.detail()["project"]["review_revision"]
        replay = self.client.post(self.adoption_url(run), json=payload)
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertEqual(replay.json(), adopted)
        self.assertEqual(self.detail()["project"]["review_revision"], revision)
        changed = self.client.post(self.adoption_url(run), json=payload | {"verification_note": "试图覆盖旧核验说明。"})
        self.assertEqual(changed.status_code, 409, changed.text)
        sources = self.detail()["sources"]
        self.assertEqual(len(sources), len(before) + 1)
        self.assertEqual(next(s for s in sources if s["id"] == adopted["source"]["id"]), adopted["source"])
        self.assertTrue(adopted["adoption"]["candidate_id"].startswith("manual:"))
        recorded = self.client.get(f"{self.base}/research/runs/{run['id']}").json()
        self.assertEqual(len(recorded["adoptions"]), 1)

    def test_research_request_outcome_and_adoption_rows_cannot_be_modified_or_deleted(self):
        run = self.public_run()
        response = self.client.post(self.adoption_url(run), json=self.adoption_payload())
        self.assertEqual(response.status_code, 201, response.text)
        adoption = response.json()["adoption"]
        with closing(sqlite3.connect(self.app.state.db_path)) as conn:
            outcome_id = conn.execute("SELECT id FROM research_outcomes WHERE run_id=?", (run["id"],)).fetchone()[0]
            for table, column, row_id in (
                ("research_runs", "query", run["id"]),
                ("research_outcomes", "status", outcome_id),
                ("research_adoptions", "note", adoption["id"]),
            ):
                with self.subTest(table=table):
                    self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE id=?", (row_id,)).fetchone()[0], 1)
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(f"UPDATE {table} SET {column}=? WHERE id=?", ("changed", row_id))
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))
                    self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE id=?", (row_id,)).fetchone()[0], 1)

    def test_interrupted_run_keeps_transmission_unknown_and_is_not_automatically_retried(self):
        run_id = "fixture-interrupted-public-run"
        parameters = self.public_payload(mode="search")
        parameters.pop("question")
        with closing(sqlite3.connect(self.app.state.db_path)) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("""INSERT INTO research_runs(id,project_id,scope,query,parameters,started_at)
                VALUES(?,?,?,?,?,?)""", (run_id, self.pid, "public", QUESTION, json.dumps(parameters), "2026-11-21T00:00:00+00:00"))
            conn.commit()
        with patch("app.retrieval.search_public") as provider:
            response = self.client.get(f"{self.base}/research/runs/{run_id}")
            again = self.client.get(f"{self.base}/research/runs/{run_id}")
            provider.assert_not_called()
        self.assertEqual(response.status_code, 200, response.text)
        run = response.json()
        self.assertEqual(run["status"], "unknown")
        self.assertIsNone(run["sent_externally"])
        self.assertEqual(run["transmission_status"], "unknown")
        self.assertIsNone(run["completed_at"])
        self.assertEqual(run["results"], [])
        self.assertTrue(run["warnings"])
        self.assertEqual(again.json(), run)
        with closing(sqlite3.connect(self.app.state.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM research_outcomes WHERE run_id=?", (run_id,)).fetchone()[0], 0)


class PublicSearchAdapterTests(_OfflineRetrievalCase):
    def simulated_search(self, *, mode="keyless", purpose="current_rules", status=200,
                         payload=None, raw_body=None, response_headers=None):
        requests = []
        if raw_body is None:
            raw_body = json.dumps({"results": []} if payload is None else payload).encode()

        def handler(request):
            requests.append(request)
            headers = {"Content-Type": "application/json"} | (response_headers or {})
            # A stream preserves the adapter's actual bounded streaming path.
            return httpx.Response(status, headers=headers, stream=httpx.ByteStream(raw_body))

        transport = httpx.MockTransport(handler)
        with patch.dict(os.environ, {"FWB_PUBLIC_SEARCH_MODE": mode, "FWB_TAVILY_API_KEY": FAKE_KEY}):
            with patch("app.public_search.httpx.HTTPTransport", return_value=transport) as factory:
                config = get_public_search_config()
                outcome = search_public(QUESTION, "CN", AS_OF, purpose)
                constructor = factory.call_args.kwargs
        return outcome, requests, constructor, config

    def test_disabled_missing_key_and_invalid_modes_never_construct_a_transport(self):
        for mode in ("disabled", "api_key", "invalid-mode"):
            with self.subTest(mode=mode), patch.dict(os.environ, {
                "FWB_PUBLIC_SEARCH_MODE": mode, "FWB_TAVILY_API_KEY": "",
            }):
                config = get_public_search_config()
                result = search_public(QUESTION, "CN", AS_OF, "current_rules")
                self.assertFalse(config["available"])
                self.assertEqual(result["status"], "not_configured")
                self.assertFalse(result["sent_externally"])
                self.assertEqual(result["results"], [])
                self.assertEqual(set(config), {"provider", "mode", "available", "status", "notice"})
        self.forbidden_transport.assert_not_called()

    def test_keyless_and_api_key_requests_remain_scoped_and_do_not_filter_out_old_rules(self):
        for mode, purpose, expected_time in (
            ("keyless", "recent_updates", "month"),
            ("api_key", "as_of", None),
            ("api_key", "current_rules", None),
        ):
            with self.subTest(mode=mode, purpose=purpose):
                result, requests, constructor, config = self.simulated_search(mode=mode, purpose=purpose)
                self.assertEqual(len(requests), 1)
                request = requests[0]
                self.assertEqual(str(request.url), "https://api.tavily.com/search")
                self.assertEqual(request.method, "POST")
                payload = json.loads(request.content)
                self.assertIn(QUESTION, payload["query"])
                self.assertEqual(payload.get("time_range"), expected_time)
                self.assertEqual(payload["include_domains_mode"], "restrict")
                self.assertEqual(set(payload["include_domains"]), {"npc.gov.cn", "csrc.gov.cn", "amac.org.cn", "court.gov.cn", "gov.cn"})
                self.assertFalse(payload["include_answer"])
                self.assertFalse(payload["include_raw_content"])
                self.assertFalse(payload["auto_parameters"])
                self.assertLessEqual(request.extensions["timeout"]["connect"], 15)
                self.assertLessEqual(request.extensions["timeout"]["read"], 15)
                self.assertEqual(constructor["retries"], 0)
                self.assertFalse(constructor["trust_env"])
                if mode == "keyless":
                    self.assertEqual(request.headers["X-Tavily-Access-Mode"], "keyless")
                    self.assertNotIn("Authorization", request.headers)
                else:
                    self.assertEqual(request.headers["Authorization"], "Bearer " + FAKE_KEY)
                    self.assertNotIn("X-Tavily-Access-Mode", request.headers)
                self.assertEqual(config["status"], "configured_unverified")
                self.assertNotIn(FAKE_KEY, json.dumps(config))
                self.assertNotIn(FAKE_KEY, json.dumps(result))

    def test_provider_failure_statuses_are_preserved_without_retry_or_raw_error_echo(self):
        for code, expected in (
            (401, "authentication_failed"), (403, "authentication_failed"),
            (429, "rate_limited"), (432, "rate_limited"), (433, "rate_limited"), (434, "rate_limited"),
            (302, "error"), (503, "error"),
        ):
            with self.subTest(code=code):
                result, requests, _, _ = self.simulated_search(
                    status=code, payload={"error": "provider-raw-error-marker " + FAKE_KEY},
                    response_headers={"Location": "https://example.invalid/should-not-follow"},
                )
                self.assertEqual(result["status"], expected)
                self.assertTrue(result["sent_externally"])
                self.assertEqual(result["results"], [])
                self.assertEqual(len(requests), 1)
                self.assertNotIn("provider-raw-error-marker", json.dumps(result))
                self.assertNotIn(FAKE_KEY, json.dumps(result))
        malformed, _, _, _ = self.simulated_search(payload={"unexpected": "provider-raw-error-marker"})
        self.assertEqual(malformed["status"], "error")
        empty, _, _, _ = self.simulated_search(payload={"results": []})
        self.assertEqual(empty["status"], "no_results")
        self.assertIn("不等于", "".join(empty["warnings"]))

    def test_only_safe_official_https_candidates_survive_and_remain_unverified(self):
        allowed = ["https://flk.npc.gov.cn/fixture?id=123", "https://www.csrc.gov.cn/fixture"]
        refused = [
            "http://www.csrc.gov.cn/fixture", "https://npc.gov.cn.evil.invalid/fixture",
            "https://127.0.0.1/fixture", "https://user:fixture@npc.gov.cn/fixture",
            "https://www.amac.org.cn:8443/fixture", "https://www.court.gov.cn/fixture?access_token=fixture-secret",
            "https://www.gov.cn/fixture?%61pi%5Fkey=fixture-secret", "https://www.gov.cn/fixture#token=fixture-secret",
        ]
        rows = [{"title": "模拟检索候选", "url": url, "content": "模拟摘要，待查原文。", "published_date": "2026-11-20"}
                for url in allowed + refused + [allowed[0]]]
        result, requests, _, _ = self.simulated_search(payload={"results": rows, "answer": "provider-generated-answer-marker"})
        self.assertEqual(result["status"], "completed")
        self.assertEqual({row["url"] for row in result["results"]}, set(allowed))
        self.assertEqual(len(requests), 1, "Result URLs must not be fetched by the discovery adapter")
        for row in result["results"]:
            self.assertEqual(row["kind"], "public_candidate")
            self.assertEqual(row["evidence_status"], "unverified_search_excerpt")
            self.assertEqual(row["reported_published_at"], "2026-11-20")
            self.assertNotIn("effective_at", row)
        self.assertNotIn("fixture-secret", json.dumps(result))
        self.assertNotIn("provider-generated-answer-marker", json.dumps(result))

    def test_streamed_response_above_one_mebibyte_is_rejected_without_partial_results(self):
        result, requests, _, _ = self.simulated_search(raw_body=b"x" * (1024 * 1024 + 1))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["results"], [])
        self.assertEqual(len(requests), 1)
        self.assertIn("1 MiB", "".join(result["warnings"]))


if __name__ == "__main__":
    unittest.main()
