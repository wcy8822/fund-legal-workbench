"""Offline A/B evaluation preparation, anonymization and human score calculation.

No model calls, credentials, internet lookup or automatic legal grading. Run:
  python -m scripts.model_eval prepare --out data/evaluation/input
  python -m scripts.model_eval blind --input data/evaluation/input/input.json \
      --candidates one.json two.json --out data/evaluation/round-1
  python -m scripts.model_eval score \
      --review data/evaluation/round-1/review-template.json \
      --out data/evaluation/round-1/scores.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
from typing import Any

from app.demo import SAMPLE_TEXTS


ROOT = Path(__file__).resolve().parents[1]
COUNT_FIELDS = (
    "citations_verified", "citations_total", "critical_issues_missed",
    "critical_issues_total", "false_positives", "risk_suggestions_total",
)
PROMPT = """你正在审查同一虚构投资项目的三份投资协议。
比较初稿、对方版及修订稿，逐条记录变化与仍待确认事项。问题必须绑定具体版本、条款和准确原文。
只使用提供的法源，区分合同约定、事实待核验和法律判断；不能把未提供的材料推定存在或不存在。
对每条建议给出风险、修改建议、待确认事实、依据及可核验引用。旧问题不因新版本上传而自动关闭。
不得联网，不得改写源文件，不得批准对外交付，不得透露提供商或模型身份。
输出一个 JSON 对象，包含 findings 数组和各版本差异摘要；引用需包含来源URL、条号和引文。
"""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _write(path: Path, value: Any, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never silently replace a human's completed review or a prior mapping.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600 if private else 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write("\n")


def _read(path: Path) -> Any:
    if path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("评估 JSON 超过 5 MB 限制。")
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("JSON 不接受非有限数值。")))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 格式错误：{path.name}") from exc


def prepare(output_dir: Path) -> dict:
    law_sources = json.loads((ROOT / "docs/sources.json").read_text(encoding="utf-8"))
    # Research verification notes can contain evaluation-answer hints. Supply
    # only the published legal text, metadata and link, never the answer table.
    legal_keys = {"title", "category", "jurisdiction", "as_of", "url", "citation", "published_at", "effective_at", "effect_status", "verified_at"}
    material = {
        "schema_version": "1.0",
        "case_id": "synthetic-investment-001",
        "is_fictional": True,
        "jurisdiction": "CN",
        "as_of": "2026-11-21",
        "prompt_version": "v0.1",
        "prompt": PROMPT,
        "documents": [
            {"stage": stage, "filename": f"investment-v{stage}.md", "sha256": hashlib.sha256(text.encode()).hexdigest(), "text": text}
            for stage, text in sorted(SAMPLE_TEXTS.items())
        ],
        "legal_sources": [{key: value for key, value in source.items() if key in legal_keys} for source in law_sources],
    }
    material["input_sha256"] = _digest(material)
    _write(output_dir / "input.json", material)
    for number in (1, 2):
        _write(output_dir / f"candidate-{number}-template.json", {
            "provider": "",
            "model_version": "",
            "run_at": None,
            "input_sha256": material["input_sha256"],
            "status": "not_run",
            "output": None,
            "error": None,
            "run_parameters": {"temperature": None, "context_budget": None, "output_budget": None, "latency_seconds": None, "cost": None},
        })
    return {"status": "prepared", "input_sha256": material["input_sha256"], "model_calls": 0}


def _anonymize(value: Any, identifiers: list[str]) -> Any:
    if isinstance(value, str):
        for token in identifiers:
            value = re.sub(re.escape(token), "[候选身份已隐藏]", value, flags=re.IGNORECASE)
        return value
    if isinstance(value, list):
        return [_anonymize(item, identifiers) for item in value]
    if isinstance(value, dict):
        return {key: _anonymize(item, identifiers) for key, item in value.items() if key not in {"provider", "model", "model_version", "model_id", "run_parameters"}}
    return value


def blind(input_path: Path, candidate_paths: list[Path], output_dir: Path) -> dict:
    if len(candidate_paths) != 2:
        raise ValueError("每轮需要两个候选输出文件。")
    material = _read(input_path)
    recorded_hash = material.get("input_sha256")
    if not recorded_hash or _digest({key: value for key, value in material.items() if key != "input_sha256"}) != recorded_hash:
        raise ValueError("输入材料已改变，需重新 prepare 并让两个候选使用相同输入。")
    candidates = []
    for path in candidate_paths:
        candidate = _read(path)
        if not isinstance(candidate, dict) or candidate.get("input_sha256") != recorded_hash:
            raise ValueError("两个候选必须记录同一份输入材料哈希。")
        status = candidate.get("status")
        if status not in {"not_run", "completed", "failed"}:
            raise ValueError("候选 status 仅支持 not_run、completed、failed。")
        if status == "completed":
            if not all(isinstance(candidate.get(key), str) and candidate[key].strip() for key in ("provider", "model_version", "run_at")):
                raise ValueError("已运行候选必须记录提供商、精确模型版本与运行时间。")
            if not isinstance(candidate.get("output"), (dict, list, str)) or not candidate["output"]:
                raise ValueError("已运行候选必须提供实际输出。")
        elif candidate.get("output") is not None:
            raise ValueError("未运行或失败的候选不能夹带完成输出。")
        candidates.append((path, candidate))
    random.SystemRandom().shuffle(candidates)
    identifiers = sorted({str(candidate.get(key, "")).strip() for _, candidate in candidates for key in ("provider", "model_version") if str(candidate.get(key, "")).strip()}, key=len, reverse=True)
    mappings, review_rows, anonymous_outputs = [], [], []
    for label, (path, candidate) in zip(("A", "B"), candidates):
        mappings.append({
            "anonymous_provider": label,
            "provider": candidate.get("provider"),
            "model_version": candidate.get("model_version"),
            "run_at": candidate.get("run_at"),
            "source_file": str(path.resolve()),
            "response_sha256": _digest(candidate),
            "run_parameters": candidate.get("run_parameters", {}),
        })
        anonymous_outputs.append({"anonymous_provider": label, "execution_status": candidate["status"], "output": _anonymize(candidate.get("output"), identifiers), "error": _anonymize(candidate.get("error"), identifiers)})
        review_rows.append({
            "anonymous_provider": label,
            "execution_status": candidate["status"],
            "status": "not_run",
            **{key: None for key in COUNT_FIELDS},
            "human_review_minutes": None,
            "reviewer_note": "",
        })
    # Mapping is deliberately separate and mode 0600. Only give reviewers input,
    # anonymous-outputs and review-template, not the candidate or mapping files.
    _write(output_dir / "private-mapping.json", {"input_sha256": recorded_hash, "mappings": mappings}, private=True)
    _write(output_dir / "anonymous-outputs.json", {
        "input_sha256": recorded_hash,
        "blindness_note": "提供商元数据已移除；已替换已知身份字符串。文字风格或未列出的别名仍可能暴露身份，发给评分者前由非评分者检查。",
        "candidates": anonymous_outputs,
    })
    _write(output_dir / "review-template.json", {
        "schema_version": "1.0", "case_id": material["case_id"], "input_sha256": recorded_hash,
        "instruction": "由法务逐项核验后填入计数及实际复核分钟，将已完成评分的 status 改为 completed。未测保留 null 和 not_run。不要打开 private-mapping.json。",
        "reviews": review_rows,
    })
    return {"status": "blinded", "candidates": ["A", "B"], "model_calls": 0}


def _ratio(numerator: int, denominator: int) -> dict:
    if denominator == 0:
        return {"status": "not_applicable", "value": None, "percent": None, "numerator": numerator, "denominator": denominator}
    return {"status": "measured", "value": numerator / denominator, "percent": round(numerator * 100 / denominator, 2), "numerator": numerator, "denominator": denominator}


def calculate_scores(review: dict) -> dict:
    rows = review.get("reviews")
    if not isinstance(rows, list) or len(rows) != 2 or {row.get("anonymous_provider") for row in rows} != {"A", "B"}:
        raise ValueError("评分表必须各含一条候选 A 和 B。")
    scores = []
    for row in rows:
        state = row.get("status")
        metric_names = ("citation_accuracy", "critical_omission_rate", "false_positive_rate", "human_review_minutes")
        if state == "not_run":
            if any(row.get(key) is not None for key in (*COUNT_FIELDS, "human_review_minutes")):
                raise ValueError("not_run 的指标须为 null；已评分请明确标 completed。")
            metrics = {key: {"status": "not_run", "value": None} for key in metric_names}
        elif state == "completed":
            if row.get("execution_status") != "completed":
                raise ValueError("未运行或失败的候选不能填入完成效果评分。")
            for key in COUNT_FIELDS:
                if isinstance(row.get(key), bool) or not isinstance(row.get(key), int) or row[key] < 0:
                    raise ValueError(f"{key} 必须是人工核验后的非负整数。")
            for numerator, denominator in (("citations_verified", "citations_total"), ("critical_issues_missed", "critical_issues_total"), ("false_positives", "risk_suggestions_total")):
                if row[numerator] > row[denominator]:
                    raise ValueError(f"{numerator} 不能超过 {denominator}。")
            minutes = row.get("human_review_minutes")
            if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not math.isfinite(minutes) or minutes < 0:
                raise ValueError("人工复核分钟必须为有限非负数。")
            if not isinstance(row.get("reviewer_note"), str) or not row["reviewer_note"].strip():
                raise ValueError("请记录人工复核说明，不能将自动计算冒充法律评分。")
            metrics = {
                "citation_accuracy": _ratio(row["citations_verified"], row["citations_total"]),
                "critical_omission_rate": _ratio(row["critical_issues_missed"], row["critical_issues_total"]),
                "false_positive_rate": _ratio(row["false_positives"], row["risk_suggestions_total"]),
                "human_review_minutes": {"status": "measured", "value": minutes, "unit": "minute"},
            }
        else:
            raise ValueError("评分 status 仅支持 not_run 或 completed。")
        scores.append({"anonymous_provider": row["anonymous_provider"], "status": state, "metrics": metrics, "reviewer_note": row.get("reviewer_note", "")})
    return {"schema_version": "1.0", "case_id": review.get("case_id"), "input_sha256": review.get("input_sha256"), "scoring_method": "人工标注计数后的算术计算；不是自动法律判断或模型实测声明。", "scores": scores}


def score(review_path: Path, output_path: Path) -> dict:
    result = calculate_scores(_read(review_path))
    _write(output_path, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="同题离线盲测，不调用任何外部模型")
    actions = parser.add_subparsers(dest="command", required=True)
    init = actions.add_parser("prepare", help="写出相同材料与两个未运行候选模板")
    init.add_argument("--out", type=Path, required=True)
    anonymize = actions.add_parser("blind", help="导入两个离线候选并匿名化")
    anonymize.add_argument("--input", type=Path, required=True)
    anonymize.add_argument("--candidates", nargs=2, type=Path, required=True)
    anonymize.add_argument("--out", type=Path, required=True)
    grades = actions.add_parser("score", help="计算人工评分表中的四项指标")
    grades.add_argument("--review", type=Path, required=True)
    grades.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.out)
        elif args.command == "blind":
            result = blind(args.input, args.candidates, args.out)
        else:
            result = score(args.review, args.out)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
