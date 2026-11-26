"""委托复核回执领域服务：把已受理判定固化成可由结构嗅探与字段解析共同核对的回执，产出委托复核回执数据集。

回执只读：撤销或替换只把它标成只读凭据，原样本、原起始行和实际解析起始行都不改写。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


def _int(value: Any, default: int = 0) -> int:
    text = str(value if value is not None else "").strip()
    try:
        return int(text)
    except ValueError:
        return default


class ReviewReceiptService(BaseModuleService):
    MODULE = "review_receipt"
    ENTITY = "委托复核回执"
    OPERATION_TERM = "核对"
    LIST_FIELDS = ["回执编号", "复核编号", "关联批次", "来源渠道", "样本编号",
                   "委托编号", "起始行", "实际解析起始行", "回执状态"]
    REQUIRED_FIELDS = ["回执编号", "复核编号", "关联批次", "样本编号"]
    STATUS_ORDER = ["有效", "只读", "已作废"]
    ACTION_RULES = {"标记只读": "只读", "作废回执": "已作废"}
    NEGATIVE_ACTIONS = ["作废回执"]

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def find(self, receipt_no: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("回执编号") == receipt_no:
                return row
        return None

    def find_by_review(self, review_no: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("复核编号") == review_no:
                return row
        return None

    def of_batch(self, batch_id: str) -> list[dict[str, Any]]:
        return [
            dict(row) for row in store.rows(self.MODULE)
            if row.get("关联批次") == batch_id
        ]

    # ------------------------------------------------------------------
    # 生成 / 只读
    # ------------------------------------------------------------------
    def create_receipt(self, review: dict[str, Any], delegation: dict[str, Any] | None,
                       issued_at: str) -> dict[str, Any]:
        """按受理判定生成回执，把权限依据和样本范围一并固化。"""
        batch_id = str(review.get("关联批次") or "")
        sample_no = str(review.get("样本编号") or "")
        start_row = _int(review.get("起始行"), 1)
        rows = store.rows(self.MODULE)
        receipt_no = f"RR-{batch_id}-{sample_no}-{len(rows) + 1:02d}"
        delegation_no = str(review.get("委托编号") or "")
        if delegation is None and delegation_no:
            delegation = next(
                (r for r in store.rows("delegated_review")
                 if r.get("委托编号") == delegation_no),
                None)
        delegation = delegation or {}

        receipt = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "status": "有效", "pending": False, "abnormal": False,
            "回执编号": receipt_no,
            "复核编号": review.get("复核编号"),
            "关联批次": batch_id,
            "来源渠道": review.get("来源渠道"),
            "样本编号": sample_no,
            "委托编号": delegation_no,
            "委托人标识": delegation.get("委托人标识"),
            "受托人标识": delegation.get("受托人标识"),
            "授权凭据": delegation.get("授权凭据"),
            "样本范围": delegation.get("样本范围"),
            "表头判定": review.get("表头判定"),
            "起始行": str(start_row),
            "实际解析起始行": str(start_row),
            "已解析行数": "0",
            "回执状态": "有效",
            "签发时间": issued_at,
            "只读原因": "",
            "核对结论": "待结构嗅探与字段解析共同核对",
        }
        rows.append(receipt)
        return dict(receipt)

    def mark_read_only(self, receipt_no: str, reason: str) -> tuple[bool, str]:
        """撤销/替换后回执转只读：保留实际解析起始行，仍可用于追溯。"""
        receipt = self.find(receipt_no)
        if receipt is None:
            return False, f"回执 {receipt_no} 不存在"
        if receipt.get("status") == "已作废":
            return False, f"回执 {receipt_no} 已作废，不能转只读"
        receipt["status"] = "只读"
        receipt["回执状态"] = "只读"
        receipt["只读原因"] = reason
        receipt["pending"] = False
        receipt["abnormal"] = False
        return True, f"回执 {receipt_no} 已转为只读凭据：{reason}"

    def mark_actual_start(self, receipt_no: str, actual_start: int,
                          parsed_rows: int) -> tuple[bool, str, dict[str, Any] | None]:
        """记录正式解析实际使用的起始行与行数，不覆盖回执里固化的判定起始行。"""
        receipt = self.find(receipt_no)
        if receipt is None:
            return False, f"回执 {receipt_no} 不存在", None
        receipt["实际解析起始行"] = str(actual_start)
        receipt["已解析行数"] = str(parsed_rows)
        declared = _int(receipt.get("起始行"), 1)
        receipt["核对结论"] = (
            "一致：实际解析起始行与受理判定相同"
            if declared == actual_start
            else f"不一致：受理起始行 {declared}，实际解析起始行 {actual_start}"
        )
        return True, receipt["核对结论"], dict(receipt)

    def verify(self, receipt_no: str) -> dict[str, Any] | None:
        """结构嗅探与字段解析联动核对：返回回执、批次受理资格与起始行结论。"""
        from app.services.schema_sniff import SchemaSniffService

        receipt = self.find(receipt_no)
        if receipt is None:
            return None
        batch_id = str(receipt.get("关联批次") or "")
        review = next(
            (r for r in store.rows("header_review")
             if r.get("复核编号") == receipt.get("复核编号")),
            None)
        sniff = SchemaSniffService()
        entitlement = sniff.current_entitlement(batch_id)
        sniff_start = sniff.parse_start_row(
            str(sniff.entitlement_sniff_no(batch_id) or ""))
        declared = _int(receipt.get("起始行"), 1)
        actual = _int(receipt.get("实际解析起始行"), declared)

        checks = {
            "回执状态": receipt.get("status"),
            "判定起始行": declared,
            "实际解析起始行": actual,
            "结构嗅探起始行": sniff_start,
            "当前受理资格": "是" if entitlement.get("可受理") else "否",
            "起始行与嗅探一致": declared == sniff_start,
            "受理判定仍为当前": bool(review is not None
                                   and review.get("当前受理资格") == "是"),
        }
        if receipt.get("status") == "有效" and checks["受理判定仍为当前"]:
            checks["核对结论"] = "照常受理：可据此回执继续正式字段解析"
        elif receipt.get("status") == "只读" or not checks["受理判定仍为当前"]:
            checks["核对结论"] = (
                "只读凭据：原判定已失效，可从只读回执继续追溯，"
                "或选择批次当前样本重新提交判定"
            )
        else:
            checks["核对结论"] = "回执已作废，只能查看历史"
        return {
            "回执编号": receipt_no,
            "关联批次": batch_id,
            "核对": checks,
            "回执": dict(receipt),
        }
