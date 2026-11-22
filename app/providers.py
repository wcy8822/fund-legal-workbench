"""Provider port. V0 makes no external model or legal-search network calls."""
import re
from typing import Protocol


def clause_text(text: str, clause: str) -> str:
    number = re.sub(r"[^0-9.]", "", clause)
    if not number:
        return ""
    lines = text.splitlines()
    pattern = re.compile(r"^\s*(?:#{1,6}\s*)?(?:第\s*)?" + re.escape(number) + r"(?:\s*条|[\s:：、])")
    for index, line in enumerate(lines):
        if pattern.search(line):
            body = [line.strip()]
            for following in lines[index + 1:]:
                if re.match(r"\s*(?:#{1,6}\s*)?(?:第\s*)?\d+[.．]\d+", following) or re.match(r"\s*(?:#{1,6}\s*)?(?:附件|第[一二三四五六七八九十百零〇]+条)", following):
                    break
                if following.strip():
                    body.append(following.strip())
            return "\n".join(body)
    return ""


class ReviewProvider(Protocol):
    name: str
    def review(self, text: str, baseline: str) -> dict: ...


class DemoProvider:
    name = "demo"

    def review(self, text: str, baseline: str) -> dict:
        if "虚构投资协议演示样例" not in text or not all(clause_text(text, x) for x in ("4.1", "5.2", "8.2")):
            return {"provider": self.name, "findings": [], "summary": ["此离线规则仅覆盖内置虚构合同。未对上传合同完成法律审查；没有提示不代表没有风险。"], "is_ai": False}
        findings = []
        payment = clause_text(text, "5.2")
        original_payment = clause_text(baseline, "5.2")
        payment_changed = payment != original_payment
        old_days = re.search(r"(\d+)\s*个工作日", original_payment)
        new_days = re.search(r"(\d+)\s*个工作日", payment)
        if payment_changed and old_days and new_days and old_days[1] != new_days[1]:
            payment_risk = f"付款期限由{old_days[1]}个工作日变为{new_days[1]}个工作日"
        elif payment_changed:
            payment_risk = "付款条款发生变化，需逐项核对起算条件和期限"
        else:
            payment_risk = "付款条款与所选初始版本一致，旧意见仍待人工确认"
        findings.append({
            "clause": "5.2", "quote": payment,
            "risk": payment_risk,
            "suggestion": "核对付款起算条件、资金安排和最新谈判口径，再决定是否关闭期限意见。",
            "pending": "期限变动待确认" if payment_changed else "期限已恢复；尚未人工结案",
            "basis_note": "合同初始版本第5.2条：" + original_payment + "；该期限是合同约定，不是法定统一天数。",
            "basis_quote": "", "basis": "contract", "demo_key": "payment",
        })
        notification = clause_text(text, "8.2")
        if "管理公司" in notification:
            findings.append({
                "clause": "8.2", "quote": notification,
                "risk": "解除通知对象由甲方改为管理公司，收件或代理授权尚待核验",
                "suggestion": "确认合同相对方与管理公司的关系、代理及收件授权；约定向甲方送达并可抄送管理公司。不得仅凭对象名称直接判定通知无效。",
                "pending": "通知对象差异和代理授权尚未解决",
                "basis_quote": "当事人一方依法主张解除合同的，应当通知对方。",
                "basis_note": "《民法典》第565条提供解除通知规则；具体通知效力仍须结合代理授权、合同约定和送达事实判断。",
                "basis": "civil_code_565", "demo_key": "notification",
            })
        has_approval_attachment = bool(re.search(r"(?m)^\s*(?:#{1,6}\s*)?附件一[\s：:、—-]+", text))
        if not has_approval_attachment:
            findings.append({
                "clause": "4.1", "quote": clause_text(text, "4.1"),
                "risk": "第4.1条要求的审批附件未随本版提供",
                "suggestion": "补齐附件一董事会批准决议，并核对主体、授权范围和签署；当前文件未附不等于外部不存在该文件。",
                "pending": "缺失的审批附件仍未解决",
                "basis_quote": "", "basis_note": "本合同第4.1条及初稿附件一为依据。不能据此推断所有私募投资均法定要求同一审批文件。",
                "basis": "contract", "demo_key": "approval",
            })
        summary = ["离线演示规则生成建议草稿；未调用 Gemini 或 Claude，不能作为模型能力结论。"]
        if not payment_changed:
            summary.append("付款条款与初始版本一致；通知对象与审批附件须分别核验，旧意见不会自动关闭。")
        return {"provider": self.name, "is_ai": False, "findings": findings, "summary": summary}


PROVIDERS = {"demo": DemoProvider()}


def get_provider(name: str) -> ReviewProvider:
    if name not in PROVIDERS:
        raise ValueError("该模型尚未接入或获准。V0仅启用离线演示规则；Gemini和Claude等待同题盲测与数据授权。")
    return PROVIDERS[name]
