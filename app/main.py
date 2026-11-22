"""Single-user, loopback-only V0. No cloud model, crawler, mail or company connection."""
import difflib
import hashlib
import io
import json
import os
import secrets
import uuid
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .db import initialize, transaction
from .documents import export_document, inspect_export, parse_document
from .demo import generate_sample
from .providers import clause_text, get_provider
from .rewrites import expression_summaries, register_rewrite_routes
from .retrieval import register_retrieval_routes
from .public_search import get_public_search_config
from .storage import BlobStore, LocalBlobStore

ROOT = Path(__file__).resolve().parent.parent
MAX_FILE_BYTES = 15 * 1024 * 1024
MEDIA = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "pdf": "application/pdf", "md": "text/markdown; charset=utf-8"}
OFFICIAL_SOURCES = [
    {"title": "国家法律法规数据库", "url": "https://flk.npc.gov.cn/", "category": "法律法规、司法解释", "api_status": "官方检索入口；V0未验证可用开放API，未连接自动检索"},
    {"title": "中国证监会", "url": "https://www.csrc.gov.cn/", "category": "监管规则", "api_status": "官网跳转；接口覆盖及授权待核验"},
    {"title": "中国证券投资基金业协会", "url": "https://www.amac.org.cn/", "category": "自律规则", "api_status": "官网跳转；接口覆盖及授权待核验"},
    {"title": "人民法院案例库", "url": "https://rmfyalk.court.gov.cn/", "category": "指导性案例、参考案例（须逐条识别）", "api_status": "官方检索入口；不绕过注册或访问限制"},
    {"title": "中国裁判文书网", "url": "https://wenshu.court.gov.cn/", "category": "公开裁判文书", "api_status": "公开文书不等于公开案卷；未检出不等于不存在"},
    {"title": "北大法宝开发者服务", "url": "https://mcp.pkulaw.com/", "category": "商业授权检索", "api_status": "官方服务入口已发现；本账号授权、覆盖、限额、更新与留存待合同核验，V0未连接"},
]
LEGAL_SOURCE = {
    "title": "中华人民共和国民法典 · 第565条", "category": "law",
    "jurisdiction": "CN", "as_of": "2026-11-21",
    "url": "https://www.gjxfj.gov.cn/2020-05/28/c_139952976.htm",
    "citation": "当事人一方依法主张解除合同的，应当通知对方。",
    "published_at": "2020-05-28", "effective_at": "2021-01-01",
    "effect_status": "现行有效（以判断时点人工复核为准）", "verified_at": "2026-11-21",
    "verification_note": "官方全文第565条首句，网站注明来源国家法律法规数据库。研究核验日2026-11-21；本应用不自动追踪后续修法。合同示例虚构，法条非虚构。",
    "is_demo": False,
}


def now():
    return datetime.now(timezone.utc).isoformat()


def new_id():
    return uuid.uuid4().hex


def pack(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def row(record):
    if record is None:
        return None
    result = dict(record)
    for key in ("warnings", "safety", "inspection", "source_safety", "detail"):
        if key in result:
            result[key] = json.loads(result[key])
    for key in ("archived", "human_confirmed", "is_demo"):
        if key in result:
            result[key] = bool(result[key])
    return result


def fail(code, message):
    raise HTTPException(code, message)


def insert(conn, table, values):
    # table and keys originate exclusively from the application, never request data.
    conn.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))


def audit(conn, pid, action, entity, detail, actor="local-legal-reviewer"):
    insert(conn, "audit", {"project_id": pid, "actor": actor, "action": action, "entity_id": entity, "detail": pack(detail), "created_at": now()})


def bump(conn, pid):
    conn.execute("UPDATE projects SET review_revision=review_revision+1 WHERE id=?", (pid,))


def project(conn, pid, mutable=False):
    value = row(conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())
    if not value:
        fail(404, "项目不存在或不在授权范围内")
    if mutable and value["archived"]:
        fail(409, "项目已归档，请先恢复")
    return value


def contract(conn, pid, cid, mutable=False):
    project(conn, pid, mutable)
    value = row(conn.execute("SELECT * FROM contracts WHERE id=? AND project_id=?", (cid, pid)).fetchone())
    if not value:
        fail(404, "合同不存在或不属于本项目")
    if mutable and value["archived"]:
        fail(409, "合同已归档，请先恢复")
    return value


def version(conn, pid, vid):
    value = row(conn.execute("SELECT * FROM versions WHERE id=? AND project_id=?", (vid, pid)).fetchone())
    if not value:
        fail(404, "合同版本不存在或不属于本项目")
    return value


def outstanding(conn, cid):
    """Latest incarnation per review root, including roots not carried to current."""
    return [row(x) for x in conn.execute("""SELECT i.* FROM issues i
        WHERE i.contract_id=? AND i.status!='resolved' AND NOT EXISTS (
          SELECT 1 FROM issues newer WHERE newer.root_id=i.root_id AND
          (newer.created_at>i.created_at OR (newer.created_at=i.created_at AND newer.id>i.id)))
        ORDER BY i.created_at""", (cid,))]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectInput(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    jurisdiction: str = Field(default="CN", min_length=2, max_length=40)
    as_of: date = date(2026, 11, 21)


class ContractInput(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    counterparty: str = Field(default="", max_length=160)


class ContractPatch(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    counterparty: str | None = Field(default=None, max_length=160)
    archived: bool | None = None


class VersionPointer(StrictModel):
    version_id: str


class DemoStage(StrictModel):
    stage: Literal[2, 3]


class SourceInput(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    category: Literal["law", "judicial_interpretation", "regulatory_rule", "self_regulatory_rule", "guiding_case", "reference_case", "judgment", "other"] = "law"
    jurisdiction: str = Field(default="CN", min_length=2, max_length=40)
    as_of: date = date(2026, 11, 21)
    url: str = Field(min_length=8, max_length=2000)
    citation: str = Field(min_length=1, max_length=10000)
    published_at: str = Field(default="", max_length=20)
    effective_at: str = Field(default="", max_length=20)
    effect_status: str = Field(default="待核验", max_length=160)
    verified_at: str = Field(default="", max_length=40)
    verification_note: str = Field(default="用户手工录入，未自动核验", max_length=2000)
    is_demo: bool = False

    @field_validator("url")
    @classmethod
    def safe_url(cls, value):
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("只允许无账号信息的http/https原文链接")
        return value


class IssueInput(StrictModel):
    version_id: str
    clause: str = Field(min_length=1, max_length=80)
    quote: str = Field(min_length=1, max_length=20000)
    risk: str = Field(min_length=1, max_length=4000)
    suggestion: str = Field(min_length=1, max_length=6000)
    pending: str = Field(default="", max_length=4000)
    basis_source_id: str | None = None
    basis_quote: str = Field(default="", max_length=10000)
    basis_note: str = Field(default="", max_length=4000)
    status: Literal["open", "in_review"] = "open"


class IssuePatch(StrictModel):
    status: Literal["open", "in_review", "resolved"]
    human_confirmed: bool = False
    resolution_note: str = Field(default="", max_length=4000)


class AnalyzeInput(VersionPointer):
    provider: str = "demo"


class DeliverableInput(VersionPointer):
    kind: Literal["internal", "external"]
    format: Literal["docx", "pdf", "md"]


class ApproveInput(StrictModel):
    human_confirmed: bool = False
    review_note: str = Field(default="", max_length=4000)
    accept_open_issues: bool = False


class SearchDraftInput(StrictModel):
    # Deliberately no project_id, parties, document, or contract text field.
    legal_question: str = Field(min_length=4, max_length=600)
    deidentified_confirmed: bool = False


def add_source(conn, pid, values):
    data = dict(values)
    data.update(id=new_id(), project_id=pid, created_at=now())
    data["as_of"] = str(data["as_of"])
    insert(conn, "sources", data)
    bump(conn, pid)
    audit(conn, pid, "source.recorded", data["id"], {"url": data["url"], "verification_note": data["verification_note"]})
    return row(conn.execute("SELECT * FROM sources WHERE id=?", (data["id"],)).fetchone())


def add_issue(conn, pid, data, origin="human", root=None):
    v = version(conn, pid, data["version_id"])
    contract(conn, pid, v["contract_id"], True)
    block = clause_text(v["text"], data["clause"])
    if not block or data["quote"] not in block:
        fail(422, "原文引文无法在指定条款核对；请使用该版本实际提取的条款文字")
    sid = data.get("basis_source_id")
    if sid:
        source = row(conn.execute("SELECT * FROM sources WHERE id=? AND project_id=?", (sid, pid)).fetchone())
        if not source:
            fail(422, "法源不属于本项目")
        if not data.get("basis_quote") or data["basis_quote"] not in source["citation"]:
            fail(422, "法源引文与已记录原文不匹配")
    elif data.get("basis_quote"):
        fail(422, "引用法源原文时必须选择法源记录")
    iid = new_id()
    values = {
        "id": iid, "project_id": pid, "contract_id": v["contract_id"], "version_id": v["id"],
        "root_id": root or iid, "carried_from": None, "origin_version_id": v["id"],
        "clause": data["clause"], "quote": data["quote"], "current_quote": block, "anchor_status": "exact",
        "risk": data["risk"], "suggestion": data["suggestion"], "pending": data.get("pending", ""),
        "basis_source_id": sid, "basis_quote": data.get("basis_quote", ""), "basis_note": data.get("basis_note", ""),
        "status": data.get("status", "open"), "origin": origin, "human_confirmed": int(origin == "human"),
        "resolution_note": "", "created_at": now(), "updated_at": now(),
    }
    insert(conn, "issues", values)
    bump(conn, pid)
    audit(conn, pid, "issue.created", iid, {"version_id": v["id"], "clause": data["clause"], "origin": origin})
    return row(conn.execute("SELECT * FROM issues WHERE id=?", (iid,)).fetchone())


def add_version(conn, store, pid, cid, filename, data, kind, carry=True, key=None, actor="local-legal-reviewer"):
    c = contract(conn, pid, cid, True)
    if kind not in ("initial", "counterparty", "revision", "final", "signed"):
        fail(422, "未知的版本类型")
    filename = Path(filename.replace("\\", "/")).name
    if not filename or len(filename) > 180 or any(ord(ch) < 32 for ch in filename):
        fail(422, "文件名无效")
    if not data or len(data) > MAX_FILE_BYTES:
        fail(413, "文件为空或超过15MB演示上限")
    request_hash = hashlib.sha256(data + pack([filename, kind, carry]).encode()).hexdigest()
    if key:
        if len(key) > 200:
            fail(422, "幂等键过长")
        old = conn.execute("SELECT * FROM imports WHERE project_id=? AND contract_id=? AND idempotency_key=?", (pid, cid, key)).fetchone()
        if old:
            if old["request_hash"] != request_hash:
                fail(409, "同一幂等键对应不同文件或参数")
            return version(conn, pid, old["version_id"])
    try:
        parsed = parse_document(filename, data)
    except ValueError as exc:
        fail(422, str(exc))
    digest = store.put(data)
    number = conn.execute("SELECT COALESCE(MAX(number),0)+1 FROM versions WHERE contract_id=?", (cid,)).fetchone()[0]
    vid = new_id()
    values = {"id": vid, "project_id": pid, "contract_id": cid, "number": number,
              "filename": filename, "kind": kind, "sha256": digest, "size_bytes": len(data),
              "text": parsed["text"], "parse_status": parsed["parse_status"], "warnings": pack(parsed["warnings"]),
              "safety": pack(parsed["safety"]), "created_at": now()}
    insert(conn, "versions", values)
    carried = 0
    if carry:
        old_issues = outstanding(conn, cid)
        for old_row in old_issues:
            item = dict(old_row)
            current_quote = clause_text(parsed["text"], item["clause"])
            item.update(id=new_id(), version_id=vid, carried_from=item["id"], current_quote=current_quote,
                        anchor_status="exact" if item["quote"] in current_quote else ("changed" if current_quote else "missing"),
                        human_confirmed=0, created_at=now(), updated_at=now())
            # Copy status exactly, never close; keep original quote/version and the evidence trail.
            insert(conn, "issues", item)
            carried += 1
    conn.execute("UPDATE contracts SET current_version_id=? WHERE id=?", (vid, cid))
    if key:
        insert(conn, "imports", {"project_id": pid, "contract_id": cid, "idempotency_key": key, "request_hash": request_hash, "version_id": vid})
    bump(conn, pid)
    audit(conn, pid, "version.imported", vid, {"contract_id": cid, "number": number, "sha256": digest, "kind": kind, "issues_carried": carried, "previous_current": c["current_version_id"]}, actor)
    return version(conn, pid, vid)


def create_app(data_dir: Path | str | None = None, *, integration_tokens: dict | None = None, blob_store: BlobStore | None = None):
    data_dir = Path(data_dir or os.environ.get("FWB_DATA_DIR", ROOT / ".data")).resolve()
    db_path = data_dir / "workbench.sqlite3"
    initialize(db_path)
    store = blob_store or LocalBlobStore(data_dir / "objects")
    tokens = integration_tokens if integration_tokens is not None else json.loads(os.environ.get("FWB_INTEGRATION_TOKENS", "{}"))
    app = FastAPI(title="私募基金投资法务工作台 · 本地演示", version="0.3.0-dev", description="本地虚构项目、意见改写与分范围检索。可配置公开网页候选搜索，默认关闭；专业法律MCP、公司系统和自动浏览器未接入。V0.2/V0.3代码尚待运行验证。", docs_url=None, redoc_url=None, openapi_url="/openapi.json")
    app.state.db_path = db_path
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.headers.get('host')}"
            if (origin and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "拒绝跨站写入；请从本机工作台操作"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    def authorize(request, pid, scope):
        if not tokens:
            fail(503, "公司接口未配置。V0默认不连接真实公司系统")
        bearer = request.headers.get("authorization", "")
        if not bearer.startswith("Bearer "):
            fail(401, "需要项目级接口授权")
        grant = None
        for token, candidate in tokens.items():
            if secrets.compare_digest(bearer[7:], token):
                grant = candidate
        if not grant or pid not in grant.get("projects", []) or scope not in grant.get("scopes", []):
            fail(403, "项目或操作不在授权范围内")
        return "integration-client"

    def download_response(content, filename, fmt):
        from urllib.parse import quote
        return Response(content, media_type=MEDIA.get(fmt, "application/octet-stream"), headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename)})

    @app.get("/api/health")
    def health():
        return {"status": "ok", "mode": "local-fictional-demo", "external_models_enabled": False, "external_search_enabled": get_public_search_config()["available"], "integration_configured": bool(tokens)}

    @app.get("/api/providers")
    def providers():
        return [{"id": "demo", "label": "离线演示规则（非大模型）", "enabled": True}, {"id": "gemini", "label": "Gemini · 待授权与盲测", "enabled": False}, {"id": "claude", "label": "Claude · 待授权与盲测", "enabled": False}]

    @app.get("/api/projects")
    def list_projects(q: str = ""):
        with transaction(db_path) as conn:
            return [row(x) for x in conn.execute("SELECT p.*, (SELECT COUNT(*) FROM contracts c WHERE c.project_id=p.id AND c.archived=0) AS contract_count FROM projects p WHERE name LIKE ? ORDER BY created_at DESC", ("%" + q + "%",))]

    @app.post("/api/projects", status_code=201)
    def create_project(data: ProjectInput):
        with transaction(db_path) as conn:
            values = data.model_dump(mode="json") | {"id": new_id(), "created_at": now()}
            insert(conn, "projects", values)
            audit(conn, values["id"], "project.created", values["id"], {"name": values["name"]})
            return project(conn, values["id"])

    @app.get("/api/projects/{pid}")
    def get_project(pid: str):
        with transaction(db_path) as conn:
            p = project(conn, pid)
            contracts = [row(x) for x in conn.execute("SELECT * FROM contracts WHERE project_id=? ORDER BY created_at", (pid,))]
            for c in contracts:
                c["versions"] = [row(x) for x in conn.execute("SELECT * FROM versions WHERE contract_id=? ORDER BY number", (c["id"],))]
                c["outstanding_issues"] = outstanding(conn, c["id"])
                c["open_issue_count"] = len(c["outstanding_issues"])
            deliveries = [row(x) for x in conn.execute("SELECT * FROM deliverables WHERE project_id=? ORDER BY created_at DESC", (pid,))]
            current = {c["id"]: c for c in contracts}
            for d in deliveries:
                c = current[d["contract_id"]]
                d["approval_valid"] = (d["kind"] == "external" and d["status"] == "approved" and not p["archived"] and not c["archived"] and d["version_id"] == c["current_version_id"] and d["approved_revision"] == p["review_revision"])
            return {"project": p, "contracts": contracts,
                    "issues": [row(x) for x in conn.execute("SELECT * FROM issues WHERE project_id=? ORDER BY created_at", (pid,))],
                    "sources": [row(x) for x in conn.execute("SELECT * FROM sources WHERE project_id=? ORDER BY created_at", (pid,))],
                    "expression_revisions": expression_summaries(conn, pid),
                    "deliverables": deliveries,
                    "audit": [row(x) for x in conn.execute("SELECT * FROM audit WHERE project_id=? ORDER BY id DESC LIMIT 200", (pid,))]}

    @app.post("/api/projects/{pid}/contracts", status_code=201)
    def create_contract(pid: str, data: ContractInput):
        with transaction(db_path) as conn:
            project(conn, pid, True)
            cid = new_id()
            insert(conn, "contracts", data.model_dump() | {"id": cid, "project_id": pid, "created_at": now()})
            audit(conn, pid, "contract.created", cid, data.model_dump())
            return contract(conn, pid, cid)

    @app.patch("/api/projects/{pid}/contracts/{cid}")
    def update_contract(pid: str, cid: str, data: ContractPatch):
        with transaction(db_path) as conn:
            old = contract(conn, pid, cid)
            project(conn, pid, True)
            changes = data.model_dump(exclude_none=True)
            if changes:
                conn.execute("UPDATE contracts SET " + ",".join(f"{key}=?" for key in changes) + " WHERE id=?", (*changes.values(), cid))
                bump(conn, pid)
                audit(conn, pid, "contract.updated", cid, {"before": {k: old[k] for k in changes}, "after": changes})
            return contract(conn, pid, cid)

    @app.post("/api/projects/{pid}/contracts/{cid}/versions", status_code=201)
    async def upload_version(pid: str, cid: str, file: UploadFile = File(...), kind: str = Form("revision"), carry_issues: bool = Form(True), idempotency_key: str | None = Header(default=None)):
        data = await file.read(MAX_FILE_BYTES + 1)
        with transaction(db_path) as conn:
            return add_version(conn, store, pid, cid, file.filename or "", data, kind, carry_issues, idempotency_key)

    @app.post("/api/projects/{pid}/contracts/{cid}/current")
    def set_current(pid: str, cid: str, data: VersionPointer):
        with transaction(db_path) as conn:
            c = contract(conn, pid, cid, True)
            v = version(conn, pid, data.version_id)
            if v["contract_id"] != cid:
                fail(422, "版本不属于这份合同")
            conn.execute("UPDATE contracts SET current_version_id=? WHERE id=?", (v["id"], cid))
            bump(conn, pid)
            audit(conn, pid, "contract.current_changed", cid, {"before": c["current_version_id"], "after": v["id"]})
            return contract(conn, pid, cid)

    @app.get("/api/projects/{pid}/versions/{vid}/original")
    def download_original(pid: str, vid: str):
        with transaction(db_path) as conn:
            v = version(conn, pid, vid)
            audit(conn, pid, "version.original_downloaded", vid, {"sha256": v["sha256"]})
        return download_response(store.get(v["sha256"]), v["filename"], v["filename"].rsplit(".", 1)[-1].lower())

    @app.get("/api/projects/{pid}/diff")
    def diff(pid: str, left: str, right: str):
        with transaction(db_path) as conn:
            a, b = version(conn, pid, left), version(conn, pid, right)
            if a["contract_id"] != b["contract_id"]:
                fail(422, "只能比较同一合同的两个版本")
            if any(v["parse_status"] in ("needs_ocr", "unsupported") for v in (a, b)):
                fail(422, "至少一个版本尚不能可靠提取文字，请先OCR或人工核对")
            lines = []
            for line in difflib.ndiff(a["text"].splitlines(), b["text"].splitlines()):
                if not line.startswith("? "):
                    lines.append({"type": {"  ": "equal", "+ ": "add", "- ": "remove"}[line[:2]], "text": line[2:]})
            changed = [x for x in ("4.1", "5.2", "8.2") if clause_text(a["text"], x) != clause_text(b["text"], x)]
            summary = ["文本级差异；不重建Word修订痕迹，不覆盖原文件。", "变化条款：" + ("、".join(changed) if changed else "请查看逐行差异（演示规则未命中指定条款）")]
            return {"left": a, "right": b, "lines": lines, "summary": summary}

    @app.post("/api/projects/{pid}/issues", status_code=201)
    def create_issue(pid: str, data: IssueInput):
        with transaction(db_path) as conn:
            return add_issue(conn, pid, data.model_dump())

    @app.patch("/api/projects/{pid}/issues/{iid}")
    def update_issue(pid: str, iid: str, data: IssuePatch):
        with transaction(db_path) as conn:
            item = row(conn.execute("SELECT * FROM issues WHERE id=? AND project_id=?", (iid, pid)).fetchone())
            if not item:
                fail(404, "审核意见不存在或不属于本项目")
            contract(conn, pid, item["contract_id"], True)
            newer = conn.execute("SELECT id FROM issues WHERE root_id=? AND created_at>? LIMIT 1", (item["root_id"], item["created_at"])).fetchone()
            if newer:
                fail(409, "该意见已承接到更新版本，请在最新承接记录中复核；历史记录保留")
            if not data.human_confirmed or not data.resolution_note.strip():
                fail(422, "变更审核状态需要法务明确确认并填写复核说明")
            conn.execute("UPDATE issues SET status=?,human_confirmed=1,resolution_note=?,updated_at=? WHERE id=?", (data.status, data.resolution_note, now(), iid))
            bump(conn, pid)
            audit(conn, pid, "issue.human_reviewed", iid, {"before": item["status"], "after": data.status, "note": data.resolution_note})
            return row(conn.execute("SELECT * FROM issues WHERE id=?", (iid,)).fetchone())

    @app.post("/api/projects/{pid}/contracts/{cid}/analyze")
    def analyze(pid: str, cid: str, data: AnalyzeInput):
        try:
            provider = get_provider(data.provider)
        except ValueError as exc:
            fail(422, str(exc))
        with transaction(db_path) as conn:
            contract(conn, pid, cid, True)
            v = version(conn, pid, data.version_id)
            if v["contract_id"] != cid:
                fail(422, "版本不属于本合同")
            if v["parse_status"] in ("needs_ocr", "unsupported"):
                fail(422, "原件待可靠提取或OCR，不可生成审核结论")
            baseline = conn.execute("SELECT text FROM versions WHERE contract_id=? ORDER BY number LIMIT 1", (cid,)).fetchone()[0]
            result = provider.review(v["text"], baseline)
            source = conn.execute("SELECT id FROM sources WHERE project_id=? AND title=? ORDER BY created_at DESC LIMIT 1", (pid, LEGAL_SOURCE["title"])).fetchone()
            if any(f["basis"] == "civil_code_565" for f in result["findings"]) and not source:
                source = {"id": add_source(conn, pid, LEGAL_SOURCE)["id"]}
            saved = []
            for f in result["findings"]:
                root = cid + ":demo:" + f["demo_key"]
                existing = conn.execute("SELECT * FROM issues WHERE version_id=? AND root_id=?", (v["id"], root)).fetchone()
                if existing:
                    saved.append(row(existing))
                    continue
                payload = {k: f[k] for k in ("clause", "quote", "risk", "suggestion", "pending", "basis_note", "basis_quote")}
                payload.update(version_id=v["id"], basis_source_id=source["id"] if f["basis"] == "civil_code_565" else None, status="open")
                saved.append(add_issue(conn, pid, payload, origin="demo_rule_draft", root=root))
            audit(conn, pid, "review.demo_drafted", v["id"], {"provider": data.provider, "saved_count": len(saved), "external_request": False})
            result["saved_issues"] = saved
            return result

    @app.get("/api/sources/official")
    def official_sources():
        return OFFICIAL_SOURCES

    @app.post("/api/projects/{pid}/sources", status_code=201)
    def create_source(pid: str, data: SourceInput):
        with transaction(db_path) as conn:
            project(conn, pid, True)
            return add_source(conn, pid, data.model_dump(mode="json"))

    @app.post("/api/legal-search/prepare")
    def prepare_legal_search(data: SearchDraftInput):
        if not data.deidentified_confirmed:
            fail(422, "外部检索前必须人工确认已去标识化；请勿输入客户名、交易秘密或合同全文")
        # There is intentionally NO HTTP client here. V1 needs DLP + vendor authorization.
        return {"sent": False, "status": "manual_only", "legal_question": data.legal_question,
                "notice": "本版不对外发送查询。请人工确认仅保留抽象法律问题，再前往官方入口检索。没有结果不等于不存在。", "sources": OFFICIAL_SOURCES}

    @app.post("/api/projects/{pid}/deliverables", status_code=201)
    def create_deliverable(pid: str, data: DeliverableInput):
        with transaction(db_path) as conn:
            p = project(conn, pid, True)
            v = version(conn, pid, data.version_id)
            c = contract(conn, pid, v["contract_id"], True)
            if data.kind == "external" and v["id"] != c["current_version_id"]:
                fail(409, "对外稿必须基于当前版本")
            if v["parse_status"] in ("needs_ocr", "unsupported") or not v["text"].strip():
                fail(422, "尚无可靠正文，无法生成交付稿；原件仍可下载归档")
            if data.kind == "external" and v["kind"] == "signed":
                fail(422, "签署件仅归档原件；不从签署件重建清洁稿以免丢失签署证据")
            issues = [row(x) for x in conn.execute("SELECT * FROM issues WHERE version_id=? ORDER BY created_at", (v["id"],))]
            current_roots = {i["root_id"] for i in issues}
            issues.extend(i for i in outstanding(conn, c["id"]) if i["root_id"] not in current_roots)
            if data.kind == "internal":
                parts = ["内部审核清单 · 不可对外发送", p["name"], c["title"], f"版本 V{v['number']} / SHA-256 {v['sha256']}", f"法域：{p['jurisdiction']}；判断时点：{p['as_of']}", "AI/演示建议须法务复核；没有意见不代表已完成法律审查。"]
                adopted_wording = {}
                for expression in expression_summaries(conn, pid):
                    if expression["target_version_id"] == v["id"] and not expression["stale"]:
                        adopted_wording.setdefault((expression["root_id"], expression["audience"]), expression)
                audience_names = {"internal": "内部法务", "business": "业务沟通", "counterparty": "对方批注"}
                for i, issue in enumerate(issues, 1):
                    location = issue["anchor_status"] if issue["version_id"] == v["id"] else "未带入本版，仍待重定位与人工确认"
                    parts.extend([f"\n{i}. 第{issue['clause']}条 [{issue['status']}]", "锚定状态：" + location, "关联版本：" + issue["version_id"], "原文：" + issue["quote"], "本版条款：" + clause_text(v["text"], issue["clause"]), "风险：" + issue["risk"], "建议：" + issue["suggestion"], "待确认：" + issue["pending"], "依据说明：" + issue["basis_note"], "处理说明：" + issue["resolution_note"]])
                    if issue["basis_source_id"]:
                        s = row(conn.execute("SELECT * FROM sources WHERE id=?", (issue["basis_source_id"],)).fetchone())
                        parts.extend(["法源：" + s["title"], "引文：" + issue["basis_quote"], "原文链接：" + s["url"], f"公布：{s['published_at']}；生效：{s['effective_at']}；效力：{s['effect_status']}；核验：{s['verified_at']}"])
                    for audience, audience_label in audience_names.items():
                        expression = adopted_wording.get((issue["root_id"], audience))
                        if expression:
                            parts.extend([f"人工采纳的{audience_label}表达（仅作内部记录，不替代原意见，不代表放行）：",
                                          expression["text"], "含义核对说明：" + expression["meaning_notes"],
                                          "采纳说明：" + expression["note"], "表达记录：" + expression["id"]])
                if not issues:
                    parts.append("当前版本尚未录入意见。请人工审查，不得将空清单解释为无风险。")
                text = "\n".join(parts)
            else:
                text = v["text"]
            title = "内部审核清单" if data.kind == "internal" else "投资协议 · 清洁阅读稿"
            try:
                content = export_document(text, data.format, title, data.kind == "internal")
                inspection = inspect_export(content, data.format)
            except ValueError as exc:
                fail(422, str(exc))
            if not inspection.get("passed"):
                fail(422, "输出检查未通过，无法进入交付流程")
            did = new_id()
            values = {"id": did, "project_id": pid, "contract_id": c["id"], "version_id": v["id"],
                      "kind": data.kind, "format": data.format, "filename": f"{data.kind}-v{v['number']}-{did[:8]}.{data.format}",
                      "sha256": store.put(content), "status": "internal" if data.kind == "internal" else "awaiting_approval",
                      "inspection": pack(inspection), "source_safety": pack(v["safety"]), "created_at": now()}
            insert(conn, "deliverables", values)
            audit(conn, pid, "deliverable.generated", did, {"version_id": v["id"], "kind": data.kind, "format": data.format, "sha256": values["sha256"]})
            return row(conn.execute("SELECT * FROM deliverables WHERE id=?", (did,)).fetchone())

    @app.post("/api/projects/{pid}/deliverables/{did}/approve")
    def approve(pid: str, did: str, data: ApproveInput):
        with transaction(db_path) as conn:
            p = project(conn, pid, True)
            d = row(conn.execute("SELECT * FROM deliverables WHERE id=? AND project_id=?", (did, pid)).fetchone())
            if not d or d["kind"] != "external":
                fail(404, "对外交付稿不存在或不属于本项目")
            c = contract(conn, pid, d["contract_id"], True)
            if d["version_id"] != c["current_version_id"]:
                fail(409, "当前版本已变化，请重新生成交付稿")
            if not data.human_confirmed or not data.review_note.strip():
                fail(422, "需要法务人工确认内容、排版、批注、修订和隐藏元数据，并填写复核说明")
            open_count = len(outstanding(conn, d["contract_id"]))
            if open_count and not data.accept_open_issues:
                fail(409, "仍有未结意见；请先处理，或明确确认保留这些问题用于对外谈判")
            inspection = inspect_export(store.get(d["sha256"]), d["format"])
            if not inspection.get("passed"):
                fail(422, "交付文件重新检查失败")
            conn.execute("UPDATE deliverables SET status='approved',approved_at=?,approved_by=?,review_note=?,approved_revision=? WHERE id=?", (now(), "local-legal-reviewer", data.review_note, p["review_revision"], did))
            audit(conn, pid, "deliverable.human_approved", did, {"note": data.review_note, "open_issues": open_count, "accepted_open_issues": data.accept_open_issues, "sha256": d["sha256"], "review_revision": p["review_revision"]})
            return row(conn.execute("SELECT * FROM deliverables WHERE id=?", (did,)).fetchone())

    @app.get("/api/projects/{pid}/deliverables/{did}/preview")
    def preview_deliverable(pid: str, did: str):
        with transaction(db_path) as conn:
            project(conn, pid)
            d = row(conn.execute("SELECT * FROM deliverables WHERE id=? AND project_id=?", (did, pid)).fetchone())
            if not d:
                fail(404, "交付物不存在或不属于本项目")
            content = store.get(d["sha256"])
            parsed = parse_document(d["filename"], content)
            audit(conn, pid, "deliverable.internally_previewed", did, {"sha256": d["sha256"], "approved": d["status"] == "approved"})
            return {"text": parsed["text"], "kind": d["kind"], "format": d["format"], "sha256": d["sha256"],
                    "inspection": d["inspection"], "source_safety": d["source_safety"],
                    "notice": "仅供本机法务预览正文；并非已批准交付件。导出为新建纯文本阅读稿，不保留原Word排版、修订、签章或复杂构件；批准前需与原件逐条核对。"}

    @app.get("/api/projects/{pid}/deliverables/{did}/review-copy")
    def internal_review_copy(pid: str, did: str):
        """An explicitly internal review package, never a company delivery endpoint."""
        with transaction(db_path) as conn:
            project(conn, pid)
            d = row(conn.execute("SELECT * FROM deliverables WHERE id=? AND project_id=?", (did, pid)).fetchone())
            if not d:
                fail(404, "待审产物不存在或不属于本项目")
            content = store.get(d["sha256"])
            stream = io.BytesIO()
            notice = ("内部审阅包 · 不代表批准 · 不可作为可交付状态证明\n"
                      "请在Word/PDF阅读器打开文件，与原件核对条款、附件、修订提取及实际布局。\n"
                      "包内文档是拟交付产物的精确字节；下载本包不改变批准状态，不关闭审核意见。\n"
                      f"产物ID：{did}\nSHA-256：{d['sha256']}\n当前记录状态：{d['status']}\n"
                      "公司交付接口仍只允许下载有效人工批准的对外件。\n")
            with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(f"INTERNAL-REVIEW-UNAPPROVED-{d['filename']}", content)
                archive.writestr("READ-ME-NOT-A-RELEASE.txt", notice.encode("utf-8"))
            audit(conn, pid, "deliverable.internal_review_copy_downloaded", did, {"sha256": d["sha256"], "approval_unchanged": True})
            return download_response(stream.getvalue(), f"internal-review-not-approved-{did[:8]}.zip", "zip")

    def deliverable_download(pid, did, external_only=False, actor="local-legal-reviewer"):
        with transaction(db_path) as conn:
            p = project(conn, pid)
            d = row(conn.execute("SELECT * FROM deliverables WHERE id=? AND project_id=?", (did, pid)).fetchone())
            if not d or (external_only and d["kind"] != "external"):
                fail(404, "交付物不存在或不可通过此接口下载")
            c = contract(conn, pid, d["contract_id"])
            if c["archived"] or p["archived"]:
                fail(409, "归档项目或合同不提供交付下载；请恢复后重新核对")
            if d["kind"] == "external":
                if d["status"] != "approved":
                    fail(403, "未经人工批准的对外稿不可下载")
                if c["current_version_id"] != d["version_id"] or p["review_revision"] != d["approved_revision"]:
                    fail(409, "批准后合同或审查依据已变化，需要重新复核批准")
            content = store.get(d["sha256"])
            audit(conn, pid, "deliverable.downloaded", did, {"channel": "integration" if external_only else "local", "sha256": d["sha256"]}, actor)
        return download_response(content, d["filename"], d["format"])

    @app.get("/api/projects/{pid}/deliverables/{did}/download")
    def download_deliverable(pid: str, did: str):
        return deliverable_download(pid, did)

    @app.get("/api/integration/projects/{pid}/contracts", tags=["company-integration"])
    def integration_contracts(pid: str, request: Request):
        actor = authorize(request, pid, "contracts:read")
        with transaction(db_path) as conn:
            project(conn, pid)
            values = [row(x) for x in conn.execute("SELECT id,title,counterparty,current_version_id,archived FROM contracts WHERE project_id=?", (pid,))]
            for c in values:
                c["versions"] = [dict(x) for x in conn.execute("SELECT id,number,kind,filename,sha256,parse_status,created_at FROM versions WHERE contract_id=? ORDER BY number", (c["id"],))]
            audit(conn, pid, "integration.contracts_listed", pid, {"count": len(values)}, actor)
            return values

    @app.post("/api/integration/projects/{pid}/contracts/{cid}/versions", status_code=201, tags=["company-integration"])
    async def integration_import(pid: str, cid: str, request: Request, file: UploadFile = File(...), kind: str = Form("revision"), carry_issues: bool = Form(True), idempotency_key: str = Header(..., min_length=1, max_length=200)):
        actor = authorize(request, pid, "versions:write")
        data = await file.read(MAX_FILE_BYTES + 1)
        with transaction(db_path) as conn:
            return add_version(conn, store, pid, cid, file.filename or "", data, kind, carry_issues, idempotency_key, actor)

    @app.get("/api/integration/projects/{pid}/deliverables/{did}/download", tags=["company-integration"])
    def integration_download(pid: str, did: str, request: Request):
        actor = authorize(request, pid, "deliverables:read")
        return deliverable_download(pid, did, external_only=True, actor=actor)

    @app.post("/api/demo", status_code=201)
    def seed_demo():
        with transaction(db_path) as conn:
            pid, cid = new_id(), new_id()
            insert(conn, "projects", {"id": pid, "name": "青禾科技 · A轮投资（虚构）", "description": "全程仅使用虚构合同。验证期限已改、通知对象与审批附件仍待解决的审查链路。", "jurisdiction": "CN", "as_of": "2026-11-21", "created_at": now()})
            insert(conn, "contracts", {"id": cid, "project_id": pid, "title": "投资协议（演示数据）", "counterparty": "示例科技有限公司（虚构）", "created_at": now()})
            add_source(conn, pid, LEGAL_SOURCE)
            v = add_version(conn, store, pid, cid, "investment-initial.docx", generate_sample(1, "docx"), "initial")
            audit(conn, pid, "demo.created", pid, {"fictional": True})
            return {"project_id": pid, "contract_id": cid, "version_id": v["id"]}

    @app.post("/api/projects/{pid}/contracts/{cid}/demo-version", status_code=201)
    def demo_version(pid: str, cid: str, data: DemoStage):
        with transaction(db_path) as conn:
            c = contract(conn, pid, cid, True)
            p = project(conn, pid)
            if "虚构" not in p["name"] or "演示数据" not in c["title"]:
                fail(422, "样例上传按钮仅适用于内置虚构项目；其他合同请上传自己的虚构文件")
            return add_version(conn, store, pid, cid, f"investment-v{data.stage}.docx", generate_sample(data.stage, "docx"), "counterparty" if data.stage == 2 else "revision", key=f"demo-stage-{data.stage}")

    @app.get("/api/demo/samples/{stage}.{fmt}")
    def sample(stage: int, fmt: str):
        if stage not in (1, 2, 3) or fmt not in MEDIA:
            fail(404, "虚构样例不存在")
        return download_response(generate_sample(stage, fmt), f"fictional-investment-v{stage}.{fmt}", fmt)

    register_rewrite_routes(app, db_path, project=project, contract=contract,
                            version=version, audit=audit, bump=bump)
    register_retrieval_routes(app, db_path, project=project, add_source=add_source,
                              source_input=SourceInput, audit=audit, official_sources=OFFICIAL_SOURCES)

    app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(ROOT / "app" / "static" / "index.html")

    # Document the reserved project-scoped bearer boundary in the generated contract.
    original_openapi = app.openapi

    def custom_openapi():
        schema = original_openapi()
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["ProjectBearer"] = {"type": "http", "scheme": "bearer", "description": "V0默认禁用。令牌限指定project_id与scope；正式试点需SSO/RBAC替换。"}
        for path, methods in schema["paths"].items():
            if path.startswith("/api/integration/"):
                for operation in methods.values():
                    operation["security"] = [{"ProjectBearer": []}]
                    operation["responses"].update({"401": {"description": "未认证"}, "403": {"description": "项目/范围未授权或交付件未批准"}, "409": {"description": "幂等冲突或批准后内容发生变化"}, "503": {"description": "公司接口默认禁用"}})
        return schema

    app.openapi = custom_openapi
    return app
