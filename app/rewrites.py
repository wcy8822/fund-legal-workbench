"""Frozen opinion-rewrite tasks with explicit, append-only human adoption.

No model or network calls occur here. The demo is a labelled fixed template for
the fictional seed only; other material can prepare a prompt and save human text.
"""
import difflib
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .db import transaction
from .providers import clause_text

ROOT = Path(__file__).resolve().parent.parent
PROMPT_VERSION = "opinion-rewrite/0.2.0"
AUDIENCES = {"internal": "内部法务", "business": "业务沟通", "counterparty": "对方批注"}
TONES = {"concise": "简洁", "gentle": "温和", "firm": "坚定"}
SEMANTIC_NOTICE = "未执行自动语义核验；措辞变化、实质立场和披露范围均须人工确认。"


class RewriteModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RewriteInput(RewriteModel):
    audience: Literal["internal", "business", "counterparty"]
    tone: Literal["concise", "gentle", "firm"] = "concise"
    mode: Literal["prepare", "demo"] = "prepare"


class RewriteDraftInput(RewriteModel):
    text: str = Field(min_length=1, max_length=12000)
    meaning_notes: str = Field(min_length=1, max_length=4000)


class RewriteAdoptInput(RewriteModel):
    draft_id: str = Field(min_length=1, max_length=100)
    human_confirmed: bool = False
    meaning_confirmed: bool = False
    external_safe_confirmed: bool = False
    note: str = Field(min_length=1, max_length=4000)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _id():
    return uuid.uuid4().hex


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _insert(conn, table, values):
    # Table and field names are application constants, never user-supplied SQL.
    conn.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))


def _issue(conn, pid, iid):
    item = conn.execute("SELECT * FROM issues WHERE project_id=? AND id=?", (pid, iid)).fetchone()
    if item is None:
        raise HTTPException(404, "审核意见不存在或不属于本项目")
    return dict(item)


def _latest_issue_id(conn, pid, root_id):
    item = conn.execute("SELECT id FROM issues WHERE project_id=? AND root_id=? ORDER BY created_at DESC,id DESC LIMIT 1", (pid, root_id)).fetchone()
    return item["id"] if item else None


def _task(conn, pid, tid):
    item = conn.execute("SELECT * FROM rewrite_tasks WHERE project_id=? AND id=?", (pid, tid)).fetchone()
    if item is None:
        raise HTTPException(404, "改写任务不存在或不属于本项目")
    data = dict(item)
    data["context_snapshot"] = json.loads(data["context_snapshot"])
    return data


def _adoption(conn, pid, tid):
    item = conn.execute("""SELECT e.*,d.source FROM issue_expressions e
        JOIN rewrite_drafts d ON d.id=e.draft_id WHERE e.project_id=? AND e.task_id=?""", (pid, tid)).fetchone()
    if item is None:
        return None
    value = dict(item)
    for field in ("human_confirmed", "meaning_confirmed", "external_safe_confirmed"):
        value[field] = bool(value[field])
    value["target_version_id"] = value["version_id"]
    return value


def _stale_reason(conn, task):
    snapshot = task["context_snapshot"]
    p = conn.execute("SELECT * FROM projects WHERE id=?", (task["project_id"],)).fetchone()
    c = conn.execute("SELECT * FROM contracts WHERE id=? AND project_id=?", (task["contract_id"], task["project_id"])).fetchone()
    if p is None or c is None:
        return "项目或合同已不可用"
    if p["archived"] or c["archived"]:
        return "项目或合同已归档，历史改写记录仅供查看"
    if c["current_version_id"] != task["target_version_id"]:
        return "合同当前版本已改变，请按新版本重新准备改写"
    issue = _issue(conn, task["project_id"], task["issue_id"])
    if _latest_issue_id(conn, task["project_id"], task["root_id"]) != task["issue_id"]:
        return "原意见已承接到新版，请使用最新意见重新准备改写"
    if issue != snapshot["issue"]:
        return "原意见或处理状态已变化，请重新准备改写"
    # Every wording adoption appends exactly one expression and increments the
    # project revision in the same transaction. Exclude those increments here so
    # internal/business/counterparty expressions can coexist. The full project
    # revision still invalidates external release approvals. All other review
    # changes retain their effect on this conservative evidence revision.
    expression_count = conn.execute("SELECT COUNT(*) FROM issue_expressions WHERE project_id=?", (task["project_id"],)).fetchone()[0]
    # Earlier snapshots without this field use zero; their stored evidence is
    # never rewritten to infer a count after the fact.
    captured_expression_count = snapshot.get("expression_revision", 0)
    current_evidence_revision = p["review_revision"] - expression_count
    captured_evidence_revision = snapshot["project"]["review_revision"] - captured_expression_count
    if current_evidence_revision != captured_evidence_revision:
        return "项目审查资料已更新，请重新准备改写；历史资料快照保持不变"
    return ""


def expression_summaries(conn, pid):
    """Adopted wording is still internal review material, never release approval."""
    records = conn.execute("""SELECT e.*,d.source,v.number AS version_number,
            t.context_snapshot,t.context_sha256
        FROM issue_expressions e
        JOIN rewrite_drafts d ON d.id=e.draft_id
        JOIN rewrite_tasks t ON t.id=e.task_id
        JOIN versions v ON v.id=e.version_id
        WHERE e.project_id=? ORDER BY e.created_at DESC,e.id DESC""", (pid,)).fetchall()
    result = []
    for record in records:
        value = dict(record)
        snapshot = json.loads(value.pop("context_snapshot"))
        task = {"project_id": pid, "contract_id": value["contract_id"], "issue_id": value["issue_id"],
                "root_id": value["root_id"], "target_version_id": value["version_id"], "context_snapshot": snapshot}
        reason = _stale_reason(conn, task)
        value.update(target_version_id=value["version_id"], stale=bool(reason), stale_reason=reason,
                     release_approved=False, semantic_review="human_confirmation_only")
        for field in ("human_confirmed", "meaning_confirmed", "external_safe_confirmed"):
            value[field] = bool(value[field])
        result.append(value)
    return result


def _demo_key(issue, target):
    key = issue["root_id"].rsplit(":", 1)[-1]
    if (issue["origin"] != "demo_rule_draft" or issue["root_id"] != issue["contract_id"] + ":demo:" + key
            or key not in ("payment", "notification", "approval")
            or "虚构投资协议演示样例" not in target["text"]):
        return None
    block = clause_text(target["text"], issue["clause"])
    if key == "payment" and not re.search(r"\d+\s*个工作日", block):
        return None
    if key == "notification" and "管理公司" not in block:
        return None
    if key == "approval" and re.search(r"(?m)^\s*(?:#{1,6}\s*)?附件一[\s：:、—-]+", target["text"]):
        return None
    return key


def _demo_draft(key, snapshot, audience, tone):
    """Fixed presentation examples; not a model or a legal/semantic conclusion."""
    if key == "payment":
        days = re.search(r"(\d+)\s*个工作日", snapshot["target_version"]["clause_text"])[1]
        messages = {
            "internal": f"本版付款期限为{days}个工作日。请结合原意见引文核对期限变化、付款起算条件、资金安排及已确认的谈判口径；本次仅整理意见表达，期限事项仍待法务复核。",
            "business": f"请确认本版约定的{days}个工作日是否满足资金安排，并核对从何时起算。确认结果请反馈法务；其他通知与附件问题仍需分别处理。",
            "counterparty": f"请确认本条{days}个工作日的起算条件及付款安排，并与相关交割条件保持一致；本轮保留对上述安排的核对。",
        }
    elif key == "notification":
        messages = {
            "internal": "本版解除通知涉及管理公司收件。保留原意见的修改建议：约定向甲方送达并可抄送管理公司；同时核对合同相对方与管理公司的关系、代理及收件授权。相关事实尚待核实，不能仅凭主体名称判断通知效力，本问题继续保留。",
            "business": "按原意见建议，请与对方沟通约定解除通知向甲方送达，并可抄送管理公司；同时确认调整收件主体的原因，协助核对管理公司的代理及收件授权。上述事项核实前，继续保留意见，尚不作通知效力结论。",
            "counterparty": "建议约定解除通知向甲方送达，并可抄送管理公司；同时请明确甲方与管理公司的关系及代理、收件授权依据。在相关安排核实前，保留本项意见，不据此判断现有通知安排的效力。",
        }
    else:
        messages = {
            "internal": "当前合同提取文字中未见第4.1条所引用的附件一。请补充董事会批准决议，并核对主体、授权范围和签署；当前材料未附不等于该文件不存在，本项尚待材料核实。",
            "business": "请协助取得本合同第4.1条引用的董事会批准决议，并确认对应的主体、批准范围与签署情况；资料补齐后交法务核对。",
            "counterparty": "请补充本合同第4.1条所引用的附件一董事会批准决议，并核对批准主体、范围及签署信息；收到相关材料前保留本项意见。",
        }
    text = messages[audience]
    if tone == "gentle":
        text = "烦请协助核对以下事项：\n" + text
    elif tone == "firm":
        text = "请明确回应以下待确认事项：\n" + text
    notes = ("这是虚构样例的固定表达示范，不是AI生成或自动语义核验结果。"
             "示范意图是保留待核对事项并调整表达对象；是否遗漏条件、改变义务或新增承诺，须逐项对照原意见确认。")
    return text, notes


def _prompt(snapshot, audience, tone):
    # Insert untrusted document data last, so literal template markers in evidence
    # cannot recursively introduce variables or commands.
    try:
        shared = (ROOT / "prompts/shared.md").read_text(encoding="utf-8")
        task = (ROOT / "prompts/03-opinion-rewrite.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(503, "意见改写提示词文件不可用，请恢复应用中的prompts目录") from exc
    task = task.replace("{{issue_id}}", snapshot["issue"]["id"])
    task = task.replace("{{audience}}", AUDIENCES[audience]).replace("{{tone}}", TONES[tone])
    shared = shared.replace("{{context_snapshot}}", json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
    return ("# 内部意见改写资料包\n\n仅在获准处理这些资料的环境中使用；本应用未发送资料或调用外部模型。\n\n"
            + shared + "\n\n" + task)


def register_rewrite_routes(app, db_path, *, project, contract, version, audit, bump):
    def context(conn, pid, iid):
        p = project(conn, pid)
        issue = _issue(conn, pid, iid)
        c = contract(conn, pid, issue["contract_id"])
        target = version(conn, pid, c["current_version_id"]) if c["current_version_id"] else None
        reason = ""
        if p["archived"] or c["archived"]:
            reason = "项目或合同已归档，请先恢复"
        elif _latest_issue_id(conn, pid, issue["root_id"]) != iid:
            reason = "这条意见已承接到更新版本，请在最新处理记录中改写"
        elif issue["status"] == "resolved":
            reason = "该意见已关闭；如需继续处理，请先人工重新打开"
        elif not target:
            reason = "合同尚无当前版本"
        elif target["kind"] == "signed":
            reason = "当前版为签署原件；请先确定用于继续审查的合同版本"
        elif target["parse_status"] in ("needs_ocr", "unsupported") or not target["text"].strip():
            reason = "当前版本尚不能可靠提取文字，请先处理OCR或解析问题"
        elif not clause_text(target["text"], issue["clause"]):
            reason = "当前版本未定位到原条款，请先人工记录准确位置的审核意见"
        return p, c, issue, target, reason

    def full_task(conn, task):
        adoption = _adoption(conn, task["project_id"], task["id"])
        drafts = [dict(x) for x in conn.execute("SELECT * FROM rewrite_drafts WHERE task_id=? AND project_id=? ORDER BY number", (task["id"], task["project_id"]))]
        latest = drafts[-1] if drafts else None
        reason = _stale_reason(conn, task)
        status = "adopted" if adoption else "draft" if latest else "prepared"
        source_notice = "尚未接通模型；已准备本机资料与提示词，可手工录入草稿。"
        if latest:
            source_notice = "虚构样例的离线固定模板（非AI输出）。" if latest["source"] == "demo_template" else "手工录入的草稿；本应用未调用模型，不确认其外部来源。"
        before = task["context_snapshot"]["issue"]["suggestion"]
        comparison = {"original_suggestion": before, "draft_text": latest["text"] if latest else "",
                      "automated_semantic_check": "not_run", "notice": SEMANTIC_NOTICE, "lines": []}
        if latest:
            comparison["lines"] = [{"type": {"  ": "equal", "+ ": "add", "- ": "remove"}[line[:2]], "text": line[2:]}
                                   for line in difflib.ndiff(before.splitlines(), latest["text"].splitlines()) if not line.startswith("? ")]
        return dict(task, status=status, stale=bool(reason), stale_reason=reason,
                    can_adopt=bool(latest and not adoption and not reason), drafts=drafts,
                    latest_draft=latest, adoption=adoption, source_notice=source_notice,
                    comparison=comparison, external_models_enabled=False, release_approved=False)

    def summary(task):
        keys = ("id", "issue_id", "audience", "tone", "mode", "status", "created_at", "stale", "stale_reason", "can_adopt", "source_notice")
        result = {key: task[key] for key in keys}
        result["latest_draft_id"] = task["latest_draft"]["id"] if task["latest_draft"] else None
        return result

    def add_draft(conn, task, text, meaning_notes, source):
        number = conn.execute("SELECT COALESCE(MAX(number),0)+1 FROM rewrite_drafts WHERE task_id=?", (task["id"],)).fetchone()[0]
        draft = {"id": _id(), "task_id": task["id"], "project_id": task["project_id"], "number": number,
                 "text": text, "meaning_notes": meaning_notes, "source": source,
                 "sha256": _hash(_json({"text": text, "meaning_notes": meaning_notes})),
                 "actor": "offline-demo-template" if source == "demo_template" else "local-legal-reviewer", "created_at": _now()}
        _insert(conn, "rewrite_drafts", draft)
        audit(conn, task["project_id"], "rewrite.draft_saved", draft["id"], {"task_id": task["id"], "source": source, "number": number, "sha256": draft["sha256"], "external_request": False})

    @app.get("/api/projects/{pid}/issues/{iid}/rewrites")
    def list_rewrites(pid: str, iid: str):
        with transaction(db_path) as conn:
            p, c, issue, target, reason = context(conn, pid, iid)
            tasks = [_task(conn, pid, x["id"]) for x in conn.execute("SELECT id FROM rewrite_tasks WHERE project_id=? AND issue_id=? ORDER BY created_at DESC,id DESC", (pid, iid))]
            return {"issue_id": iid, "can_create": not reason, "blocked_reason": reason,
                    "demo_available": bool(not reason and target and _demo_key(issue, target)),
                    "history": [summary(full_task(conn, task)) for task in tasks],
                    "expressions": [x for x in expression_summaries(conn, pid) if x["issue_id"] == iid],
                    "external_models_enabled": False}

    @app.post("/api/projects/{pid}/issues/{iid}/rewrites", status_code=201)
    def create_rewrite(pid: str, iid: str, data: RewriteInput):
        with transaction(db_path) as conn:
            p, c, issue, target, reason = context(conn, pid, iid)
            if reason:
                raise HTTPException(409, reason)
            key = _demo_key(issue, target)
            if data.mode == "demo" and not key:
                raise HTTPException(422, "离线表达示例仅适用于内置虚构问题；此意见可以准备资料并手工录入草稿")
            origin = version(conn, pid, issue["origin_version_id"])
            source_row = conn.execute("SELECT * FROM sources WHERE project_id=? AND id=?", (pid, issue["basis_source_id"])).fetchone() if issue["basis_source_id"] else None
            unknowns = ["项目我方角色和公司审查口径尚未结构化确认，不得自行推定。", SEMANTIC_NOTICE]
            if issue["pending"]:
                unknowns.append(issue["pending"])
            if issue["version_id"] != target["id"]:
                unknowns.append("原意见来自历史版本；本次同时固定当前版本的对应条款，须确认原意见是否仍适用。")
            block = clause_text(target["text"], issue["clause"])
            if issue["quote"] not in block:
                unknowns.append("当前条款已不同于原意见引文，含义变化需人工核对。")
            snapshot = {
                "project": {k: p[k] for k in ("id", "name", "jurisdiction", "as_of", "review_revision")},
                "contract": {k: c[k] for k in ("id", "title", "current_version_id")},
                "issue": issue,
                "target_version": {**{k: target[k] for k in ("id", "number", "sha256", "kind", "parse_status", "warnings")}, "clause_text": block},
                "origin_version": {k: origin[k] for k in ("id", "number", "sha256")},
                "source": dict(source_row) if source_row else None,
                "unknowns": unknowns, "prompt_version": PROMPT_VERSION,
                "expression_revision": conn.execute("SELECT COUNT(*) FROM issue_expressions WHERE project_id=?", (pid,)).fetchone()[0],
                "data_handling": "仅本机准备内部资料；未调用模型、未对外检索或发送。",
            }
            prompt_text = _prompt(snapshot, data.audience, data.tone)
            values = {"id": _id(), "project_id": pid, "contract_id": c["id"], "issue_id": iid,
                      "root_id": issue["root_id"], "target_version_id": target["id"],
                      "audience": data.audience, "tone": data.tone, "mode": data.mode,
                      "context_snapshot": _json(snapshot), "context_sha256": _hash(_json(snapshot)),
                      "prompt_version": PROMPT_VERSION, "prompt_text": prompt_text, "created_at": _now()}
            _insert(conn, "rewrite_tasks", values)
            task = _task(conn, pid, values["id"])
            audit(conn, pid, "rewrite.prepared", task["id"], {"issue_id": iid, "target_version_id": target["id"], "context_sha256": task["context_sha256"], "mode": data.mode, "audience": data.audience, "external_request": False})
            if data.mode == "demo":
                text, notes = _demo_draft(key, snapshot, data.audience, data.tone)
                add_draft(conn, task, text, notes, "demo_template")
            return full_task(conn, task)

    @app.get("/api/projects/{pid}/rewrite-tasks/{tid}")
    def get_rewrite(pid: str, tid: str):
        with transaction(db_path) as conn:
            project(conn, pid)
            return full_task(conn, _task(conn, pid, tid))

    @app.post("/api/projects/{pid}/rewrite-tasks/{tid}/drafts", status_code=201)
    def save_rewrite_draft(pid: str, tid: str, data: RewriteDraftInput):
        with transaction(db_path) as conn:
            project(conn, pid, True)
            task = _task(conn, pid, tid)
            contract(conn, pid, task["contract_id"], True)
            if _adoption(conn, pid, tid):
                raise HTTPException(409, "该任务已采纳，后续修改请新建改写任务，历史草稿保留")
            reason = _stale_reason(conn, task)
            if reason:
                raise HTTPException(409, reason)
            add_draft(conn, task, data.text, data.meaning_notes, "manual_input")
            return full_task(conn, task)

    @app.post("/api/projects/{pid}/rewrite-tasks/{tid}/adopt")
    def adopt_rewrite(pid: str, tid: str, data: RewriteAdoptInput):
        with transaction(db_path) as conn:
            p = project(conn, pid, True)
            task = _task(conn, pid, tid)
            contract(conn, pid, task["contract_id"], True)
            existing = _adoption(conn, pid, tid)
            if existing:
                if existing["draft_id"] != data.draft_id:
                    raise HTTPException(409, "该任务已采纳其他草稿，不能覆盖历史采纳记录")
                return full_task(conn, task)
            reason = _stale_reason(conn, task)
            if reason:
                raise HTTPException(409, reason)
            draft_row = conn.execute("SELECT * FROM rewrite_drafts WHERE task_id=? AND project_id=? ORDER BY number DESC LIMIT 1", (tid, pid)).fetchone()
            if draft_row is None or draft_row["id"] != data.draft_id:
                raise HTTPException(409, "只能采纳本任务最新草稿；如需旧稿，请另存为新草稿后确认")
            if not data.human_confirmed or not data.meaning_confirmed:
                raise HTTPException(422, "需要人工核对原意见、条款及草稿，并明确确认含义变化")
            if task["audience"] == "counterparty" and not data.external_safe_confirmed:
                raise HTTPException(422, "对方批注还需人工确认未包含不应披露的内部信息；该确认不等于文件放行")
            draft = dict(draft_row)
            value = {"id": _id(), "project_id": pid, "contract_id": task["contract_id"], "issue_id": task["issue_id"],
                     "root_id": task["root_id"], "task_id": tid, "draft_id": draft["id"], "version_id": task["target_version_id"],
                     "audience": task["audience"], "tone": task["tone"], "text": draft["text"], "meaning_notes": draft["meaning_notes"],
                     "note": data.note, "human_confirmed": 1, "meaning_confirmed": 1,
                     "external_safe_confirmed": int(data.external_safe_confirmed), "adopted_review_revision": p["review_revision"] + 1,
                     "actor": "local-legal-reviewer", "created_at": _now()}
            _insert(conn, "issue_expressions", value)
            bump(conn, pid)
            audit(conn, pid, "rewrite.human_adopted", value["id"], {"task_id": tid, "draft_id": draft["id"], "issue_id": task["issue_id"], "target_version_id": task["target_version_id"], "audience": task["audience"], "source": draft["source"], "issue_status_unchanged": True, "release_approved": False})
            return full_task(conn, task)
