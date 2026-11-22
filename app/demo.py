"""Entirely fictional investment-contract fixtures. Never use as legal advice."""
from .documents import export_document

TITLE = "虚构投资协议演示样例"

_PREFIX = """虚构投资协议演示样例
演示数据  全部主体、金额与事项均为虚构，不构成真实交易或可签署的合同。

甲方：示例投资合伙企业（虚构）
乙方：示例科技有限公司（虚构）
管理人：示例管理公司（虚构）
判断时点：2026年11月21日  法域：中国大陆

## 第一条 投资安排
第1.1条 甲方拟以人民币100万元认购乙方新增注册资本。本协议仅用于演示合同版本、问题承接和人工批准流程。

## 第四条 交割先决条件
第4.1条 乙方应在交割前向甲方提供附件一所列董事会批准决议。甲方完成核验后，方可书面确认交割条件满足。

## 第五条 付款安排
"""
_INITIAL_PAYMENT = "第5.2条 甲方应在全部交割先决条件满足且收到乙方书面付款通知之日起10个工作日内支付投资款。"
_COUNTERPARTY_PAYMENT = "第5.2条 甲方应在全部交割先决条件满足且收到乙方书面付款通知之日起5个工作日内支付投资款。"
_SECTION_8 = "\n\n## 第八条 通知与解除\n"
_INITIAL_NOTICE = "第8.2条 任何一方依法主张解除本协议，应向合同对方发出书面解除通知；乙方向甲方发出的解除通知应送达甲方，即示例投资合伙企业（虚构）。"
_COUNTERPARTY_NOTICE = "第8.2条 任何一方依法主张解除本协议，应发出书面解除通知；乙方发出的解除通知应送达管理人，即示例管理公司（虚构）。"
_TAIL = """

## 第九条 其他
第9.1条 附件为本协议的组成部分。双方应逐项核验版本、条款与附件；示例文本不得用于实际签署。
"""
_APPROVAL = """

## 附件一 董事会批准决议
演示数据：示例科技有限公司（虚构）董事会一致同意本协议所述投资安排，并授权指定人员办理交割。此段仅为虚构审批附件，用于验证新版遗漏时问题仍需保留。
"""

SAMPLE_TEXTS = {
    1: _PREFIX + _INITIAL_PAYMENT + _SECTION_8 + _INITIAL_NOTICE + _TAIL + _APPROVAL,
    2: _PREFIX + _COUNTERPARTY_PAYMENT + _SECTION_8 + _COUNTERPARTY_NOTICE + _TAIL,
    3: _PREFIX + _INITIAL_PAYMENT + _SECTION_8 + _COUNTERPARTY_NOTICE + _TAIL,
}


def generate_sample(stage: int, format: str = "docx") -> bytes:
    if stage not in SAMPLE_TEXTS:
        raise ValueError("演示合同版本只能为 1、2、3。")
    return export_document(SAMPLE_TEXTS[stage], format, title=TITLE)
