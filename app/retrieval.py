"""Project-scoped search, public discovery and explicit manual source records.

Public query preparation never copies internal evidence. Optional web search
returns unverified candidates. No browser control, company connector or law MCP
is silently enabled by this module. A provider failure is not an empty result.
"""
import hashlib
import json
import re
import uuid
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .db import transaction
from .public_search import get_public_search_config, search_public


class RetrievalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LocalQuery(RetrievalInput):
    query: str = Field(min_length=2, max_length=200)
    include_history: bool = False


class PublicQuery(RetrievalInput):
    question: str = Field(min_length=4, max_length=600)
    jurisdiction: Literal["CN"] = "CN"
    as_of: date
    purpose: Literal["current_rules", "recent_updates", "as_of"] = "current_rules"
    deidentified_confirmed: bool = False
    mode: Literal["prepare", "search"] = "prepare"


class AdoptSource(RetrievalInput):
    candidate_id: str | None = Field(default=None, max_length=120)
    title: str = Field(min_length=1, max_length=300)
    category: Literal["law", "judicial_interpretation", "regulatory_rule", "self_regulatory_rule", "guiding_case", "reference_case", "judgment", "other"]
    url: str = Field(min_length=8, max_length=2000)
    citation: str = Field(min_length=1, max_length=10000)
    published_at: str = Field(default="", max_length=10)
    effective_at: str = Field(default="", max_length=10)
    effect_status: str = Field(min_length=1, max_length=160)
    verification_note: str = Field(min_length=1, max_length=1800)
    is_demo: bool = False
    human_confirmed: bool = False
    original_checked: bool = False
    as_of_checked: bool = False

    @field_validator("published_at", "effective_at")
    @classmethod
    def valid_date_or_unknown(cls, value):
        if value:
            date.fromisoformat(value)
        return value


CONNECTORS = [
    {"id": "pkulaw", "title": "北大法宝法律 MCP", "transport": "MCP", "status": "candidate_not_connected",
     "url": "https://mcp.pkulaw.com/", "scope": "法规、案例、沿革及引文核验的只读工具",
     "notice": "官方文档候选；账户授权、配额、更新机制与留存待确认。反馈提交及真实客户信息查询未开放。"},
    {"id": "yuandian", "title": "元典法律 MCP", "transport": "MCP", "status": "candidate_not_connected",
     "url": "https://open.chineselaw.com/mcp-config/", "scope": "法规、案例、证券规则的只读检索",
     "notice": "官方配置已纳入设计，尚未连接；企业信息及按客户名称检索不在当前范围。"},
    {"id": "sharepoint", "title": "公司文档库 / SharePoint", "transport": "authorized_connector", "status": "not_authorized",
     "url": "https://learn.microsoft.com/en-us/graph/api/resources/search-api-overview", "scope": "将来由公司授权的项目位置和用户身份决定",
     "notice": "尚未指定公司系统或资料范围；没有查询个人账号、公司网盘或审批系统。"},
    {"id": "filesystem", "title": "指定资料目录", "transport": "read_only_MCP", "status": "not_authorized",
     "url": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem", "scope": "未来明确选定的只读目录",
     "notice": "未开放整台电脑或主目录；当前只搜索已导入本项目的记录。"},
    {"id": "browser", "title": "浏览器查询", "transport": "browser", "status": "manual_only",
     "url": "https://github.com/microsoft/playwright-mcp", "scope": "官方跳转与人工留证",
     "notice": "未运行自动浏览器。未来按站点和身份单独授权，不绕过登录、验证码或访问限制。"},
]


def _now():
    return datetime.now(timezone.utc).isoformat()


def _id():
    return uuid.uuid4().hex


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _insert(conn, table, values):
    conn.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))


def _public_guard(conn, pid, data):
    if not data.deidentified_confirmed:
        raise HTTPException(422, "请先确认问题已去标识化，仅含抽象身份、法律关系和所需判断")
    question = data.question
    # A narrow local precheck, not a guarantee of full DLP. No model receives
    # the original text to redact it, and internal fields cannot enter the API.
    if re.search(r"https?://|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)\d{17}[\dXx](?!\d)", question):
        raise HTTPException(422, "问题包含网址、联系方式或身份标识，请改成去标识化的抽象法律问题")
    if re.search(r"<w:|BEGIN (?:RSA |EC )?PRIVATE KEY|API[_ -]?KEY\s*[:=]", question, re.I) or len(re.findall(r"(?m)^\s*(?:第[一二三四五六七八九十百]+条|\d+\.\d+)\s*", question)) >= 3:
        raise HTTPException(422, "请勿提交合同全文、凭据或其他敏感资料，只保留法律问题")
    names = [x[0] for x in conn.execute("SELECT name FROM projects WHERE id=?", (pid,))]
    names.extend(x[0] for x in conn.execute("SELECT counterparty FROM contracts WHERE project_id=?", (pid,)))
    for name in names:
        normalized = name.strip()
        variants = {normalized, re.sub(r"[（(](?:虚构|演示数据)[）)]", "", normalized).strip()}
        if any(len(v) >= 3 and v.casefold() in question.casefold() for v in variants):
            raise HTTPException(422, "问题中含本项目或交易方名称，请用投资人、目标公司等抽象身份替换")


def _excerpt(text, terms, limit=650):
    lowered = text.casefold()
    if not all(term in lowered for term in terms):
        return None
    positions = [lowered.find(term) for term in terms]
    start = max(0, min(positions) - 100)
    return text[start:start + limit]


def _local_results(conn, pid, query, include_history):
    terms = query.casefold().split()
    matches = []
    skipped = 0
    scanned = 0
    # Every table query is project-scoped. No arbitrary paths, sessions or
    # cross-project fallback are consulted when a query has no result.
    sql = """SELECT v.*,c.title AS contract_title,c.current_version_id FROM versions v
        JOIN contracts c ON c.id=v.contract_id
        WHERE v.project_id=? AND c.project_id=? AND c.archived=0"""
    args = [pid, pid]
    if not include_history:
        sql += " AND v.id=c.current_version_id"
    sql += " ORDER BY v.created_at DESC"
    for record in conn.execute(sql, args):
        item = dict(record)
        scanned += 1
        if item["parse_status"] in ("needs_ocr", "unsupported") or not item["text"].strip():
            skipped += 1
            continue
        text = item["contract_title"] + "\n" + item["text"]
        excerpt = _excerpt(text, terms)
        if excerpt is not None:
            matches.append({"id": "version:" + item["id"], "title": item["contract_title"],
                "kind": "contract_version", "record_id": item["id"], "contract_id": item["contract_id"],
                "version_id": item["id"], "version_number": item["number"], "sha256": item["sha256"],
                "locator": f"V{item['number']} · " + ("当前版" if item["id"] == item["current_version_id"] else "历史版"),
                "excerpt": excerpt, "entry": "contracts", "evidence_status": "project_record",
                "notice": "提取文字片段；不证明全文、修订、签章或附件已完整覆盖。"})
    for record in conn.execute("""SELECT i.*,c.title AS contract_title,v.number AS version_number,
            v.sha256 AS version_sha256 FROM issues i
            JOIN contracts c ON c.id=i.contract_id JOIN versions v ON v.id=i.version_id
            WHERE i.project_id=? AND c.archived=0 ORDER BY i.created_at DESC,i.id DESC""", (pid,)):
        item = dict(record)
        if not include_history:
            newer = conn.execute("SELECT id FROM issues WHERE project_id=? AND root_id=? ORDER BY created_at DESC,id DESC LIMIT 1", (pid, item["root_id"])).fetchone()
            if newer["id"] != item["id"]:
                continue
        text = "\n".join(item[k] for k in (
            "clause", "risk", "suggestion", "pending", "quote", "basis_quote", "basis_note", "resolution_note",
        ))
        excerpt = _excerpt(text, terms)
        if excerpt is not None:
            matches.append({"id": "issue:" + item["id"], "title": item["risk"], "kind": "review_issue",
                "record_id": item["id"], "contract_id": item["contract_id"], "version_id": item["version_id"],
                "version_number": item["version_number"], "sha256": item["version_sha256"],
                "locator": f"{item['contract_title']} · {item['clause']} · {item['status']}",
                "excerpt": excerpt, "entry": "issues", "evidence_status": "project_record",
                "notice": "项目意见记录，不等同于法律规定、公司通用政策或已确认结论。"})
    for record in conn.execute("SELECT * FROM sources WHERE project_id=? ORDER BY created_at DESC", (pid,)):
        item = dict(record)
        excerpt = _excerpt(item["title"] + "\n" + item["citation"] + "\n" + item["verification_note"], terms)
        if excerpt is not None:
            matches.append({"id": "source:" + item["id"], "title": item["title"], "kind": "recorded_source",
                "record_id": item["id"], "locator": item["category"] + " · 台账判断时点 " + item["as_of"],
                "excerpt": excerpt, "url": item["url"], "entry": "sources", "evidence_status": "recorded_source_snapshot",
                "notice": "已录入来源快照；搜索命中不代表本次重新核验或确认当前有效。"})
    seen = set()
    for record in conn.execute("""SELECT e.*,c.title AS contract_title,v.number AS version_number
        FROM issue_expressions e JOIN contracts c ON c.id=e.contract_id
        JOIN versions v ON v.id=e.version_id
        WHERE e.project_id=? AND c.archived=0 ORDER BY e.created_at DESC,e.id DESC""", (pid,)):
        item = dict(record)
        key = (item["root_id"], item["audience"])
        if not include_history and key in seen:
            continue
        seen.add(key)
        excerpt = _excerpt(item["text"] + "\n" + item["meaning_notes"], terms)
        if excerpt is not None:
            matches.append({"id": "expression:" + item["id"], "title": item["contract_title"] + " · 已采纳表达",
                "kind": "adopted_expression", "record_id": item["id"], "version_id": item["version_id"],
                "version_number": item["version_number"], "locator": item["audience"] + " · " + item["created_at"],
                "excerpt": excerpt, "entry": "issues", "evidence_status": "historical_adoption_record",
                "notice": "历史人工采用记录；复用前到意见页核对当前资料状态，不是对外批准。"})
    warnings = ["仅检索本项目已录入资料；不是公司全库、电脑文件或审批系统搜索。", "当前范围未命中不表示文件、事实或法律依据不存在。"]
    if skipped:
        warnings.append(f"有{skipped}个选定范围内的版本待OCR或缺少可用文字，未完成全文检索。")
    if len(matches) > 40:
        warnings.append("命中超过40条，仅返回前40条；请缩小关键词或分别检索。")
    return {"provider": "current_project", "status": "completed" if matches else "no_results",
            "sent_externally": False, "results": matches[:40], "warnings": warnings,
            "coverage": f"当前项目；{'包含历史版本和历史意见' if include_history else '当前合同版本、最新根问题及已录来源/表达'}；查看了{scanned}个合同版本。"}


def register_retrieval_routes(app, db_path, *, project, add_source, source_input, audit, official_sources):
    portals = [{"title": p["title"], "url": p["url"], "category": p["category"]} for p in official_sources]

    def full_run(conn, pid, rid):
        record = conn.execute("SELECT * FROM research_runs WHERE id=? AND project_id=?", (rid, pid)).fetchone()
        if not record:
            raise HTTPException(404, "检索记录不存在或不属于本项目")
        run = dict(record)
        run["parameters"] = json.loads(run["parameters"])
        outcome = conn.execute("SELECT * FROM research_outcomes WHERE run_id=? AND project_id=?", (rid, pid)).fetchone()
        if outcome:
            run.update(json.loads(outcome["payload"]))
            run.update(provider=outcome["provider"], status=outcome["status"],
                       sent_externally=bool(outcome["sent_externally"]), completed_at=outcome["completed_at"])
        else:
            run.update(provider="pending", status="unknown", sent_externally=None, completed_at=None,
                       results=[], warnings=["未记录到完成结果；可能仍在处理或曾中断。不能认定未发送或没有相关依据。"],
                       coverage="执行状态未知，未自动重试")
            run["transmission_status"] = "unknown"
        run["official_portals"] = portals if run["scope"] == "public" else []
        run["adoptions"] = [dict(x) for x in conn.execute("SELECT * FROM research_adoptions WHERE run_id=? AND project_id=? ORDER BY created_at", (rid, pid))]
        return run

    def begin_run(conn, pid, scope, query, parameters):
        value = {"id": _id(), "project_id": pid, "scope": scope, "query": query,
                 "parameters": _json(parameters), "started_at": _now()}
        _insert(conn, "research_runs", value)
        audit(conn, pid, "research.requested", value["id"], {"scope": scope, "query_sha256": _hash(query), "mode": parameters.get("mode", "local")})
        return value["id"]

    def finish_run(conn, pid, rid, outcome):
        payload = {k: outcome[k] for k in ("results", "warnings", "coverage")}
        _insert(conn, "research_outcomes", {"id": _id(), "run_id": rid, "project_id": pid,
            "provider": outcome["provider"], "status": outcome["status"],
            "sent_externally": int(outcome["sent_externally"]), "payload": _json(payload), "completed_at": _now()})
        audit(conn, pid, "research.finished", rid, {"provider": outcome["provider"], "status": outcome["status"],
            "candidate_count": len(outcome["results"]), "sent_externally": outcome["sent_externally"]})

    @app.get("/api/retrieval/capabilities")
    def capabilities():
        return {"public_search": get_public_search_config(),
            "internal_search": {"scope": "current_project", "available": True,
                "notice": "只查已导入本项目的合同文字、审核意见、法源台账和表达记录；未连接公司系统或电脑目录。"},
            "connectors": CONNECTORS,
            "browser": {"status": "manual_only", "notice": "仅提供官方入口和人工记录；未启动或连接自动浏览器。"}}

    @app.get("/api/projects/{pid}/research/runs")
    def list_runs(pid: str):
        with transaction(db_path) as conn:
            project(conn, pid)
            ids = conn.execute("SELECT id FROM research_runs WHERE project_id=? ORDER BY started_at DESC,id DESC LIMIT 20", (pid,)).fetchall()
            return [full_run(conn, pid, item["id"]) for item in ids]

    @app.get("/api/projects/{pid}/research/runs/{rid}")
    def get_run(pid: str, rid: str):
        with transaction(db_path) as conn:
            project(conn, pid)
            return full_run(conn, pid, rid)

    @app.post("/api/projects/{pid}/research/local", status_code=201)
    def local_search(pid: str, data: LocalQuery):
        with transaction(db_path) as conn:
            p = project(conn, pid, True)
            rid = begin_run(conn, pid, "project", data.query, {"include_history": data.include_history, "as_of": p["as_of"]})
            finish_run(conn, pid, rid, _local_results(conn, pid, data.query, data.include_history))
            return full_run(conn, pid, rid)

    @app.post("/api/projects/{pid}/research/public", status_code=201)
    def public_research(pid: str, data: PublicQuery):
        with transaction(db_path) as conn:
            project(conn, pid, True)
            _public_guard(conn, pid, data)
            parameters = data.model_dump(mode="json", exclude={"question"})
            rid = begin_run(conn, pid, "public", data.question, parameters)
        # Do not hold a SQLite write lock during an optional network operation.
        # This is the entire outbound data surface: question + public time/scope.
        if data.mode == "prepare":
            outcome = {"provider": "manual", "status": "prepared", "sent_externally": False, "results": [],
                "warnings": ["本次只准备问题和官方入口，尚未执行联网检索。", "可复制去标识化问题自行查询；人工记录不代表应用已抓取或核验原文。"],
                "coverage": "未执行检索；法域CN，判断时点" + str(data.as_of)}
        else:
            outcome = search_public(data.question, data.jurisdiction, str(data.as_of), data.purpose)
        with transaction(db_path) as conn:
            # Record the truthful outcome even if the project was archived while
            # waiting. Source adoption still checks current project state.
            project(conn, pid)
            finish_run(conn, pid, rid, outcome)
            return full_run(conn, pid, rid)

    @app.post("/api/projects/{pid}/research/runs/{rid}/adopt-source", status_code=201)
    def record_source(pid: str, rid: str, data: AdoptSource):
        if not (data.human_confirmed and data.original_checked and data.as_of_checked):
            raise HTTPException(422, "请人工核对原文引文、法域与判断时点，并确认记录说明；搜索摘要不能直接作为已核验法条")
        with transaction(db_path) as conn:
            project(conn, pid, True)
            run = full_run(conn, pid, rid)
            if run["scope"] != "public":
                raise HTTPException(422, "内部资料命中不能通过公开法源采纳入口转成外部法律依据")
            if data.candidate_id and not any(r["id"] == data.candidate_id for r in run["results"]):
                raise HTTPException(422, "候选不属于本次检索；独立查询的材料请使用人工录入方式")
            cid = data.candidate_id or "manual:" + _hash(data.url + "\n" + data.citation + "\n" + data.title)
            request_hash = _hash(_json(data.model_dump(mode="json")))
            prior = conn.execute("SELECT * FROM research_adoptions WHERE project_id=? AND run_id=? AND candidate_id=?", (pid, rid, cid)).fetchone()
            if prior:
                if prior["request_sha256"] != request_hash:
                    raise HTTPException(409, "此候选已记录，不能覆盖原核验记录；新证据请新建检索任务并追加来源")
                source = dict(conn.execute("SELECT * FROM sources WHERE id=? AND project_id=?", (prior["source_id"], pid)).fetchone())
                return {"source": source, "adoption": dict(prior)}
            note = "人工核验记录（应用未抓取原文）：" + data.verification_note + "；关联检索任务 " + rid
            values = {"title": data.title, "category": data.category, "jurisdiction": run["parameters"]["jurisdiction"],
                "as_of": run["parameters"]["as_of"], "url": data.url, "citation": data.citation,
                "published_at": data.published_at, "effective_at": data.effective_at, "effect_status": data.effect_status,
                "verified_at": _now(), "verification_note": note, "is_demo": data.is_demo}
            try:
                values = source_input(**values).model_dump(mode="json")
            except ValidationError:
                raise HTTPException(422, "法源字段不完整或链接、日期格式无效，请保留准确原文与安全的原文链接")
            source = add_source(conn, pid, values)
            adoption = {"id": _id(), "project_id": pid, "run_id": rid, "candidate_id": cid,
                "source_id": source["id"], "request_sha256": request_hash, "note": data.verification_note, "created_at": _now()}
            _insert(conn, "research_adoptions", adoption)
            audit(conn, pid, "research.source_recorded", source["id"], {"run_id": rid, "candidate_id": cid,
                "verification_method": "human_attestation", "application_fetched_original": False,
                "issue_status_changed": False, "release_approved": False})
            return {"source": source, "adoption": adoption}
