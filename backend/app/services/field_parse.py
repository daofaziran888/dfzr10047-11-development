"""字段解析领域服务：按命中模板逐字段抽取取值并记录解析质量，产出解析字段数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class FieldParseService(BaseModuleService):
    MODULE = "field_parse"
    ENTITY = "解析字段"
    OPERATION_TERM = "解析"
    LIST_FIELDS = ["解析编号", "关联批次", "命中模板", "字段名称", "原始取值", "解析取值", "解析耗时", "解析行号"]
    REQUIRED_FIELDS = ["解析编号", "关联批次", "命中模板"]
    STATUS_ORDER = ["待解析", "解析中", "解析成功", "部分失败"]
    ACTION_RULES = {"启动解析": "解析中", "确认成功": "解析成功", "登记部分失败": "部分失败"}
    NEGATIVE_ACTIONS = ["登记部分失败"]

    def record_parsed_field(self, parse_no: str, intake_batch: str, template_no: str,
                            field_name: str, raw_value: str, parsed_value: str,
                            elapsed_ms: int, row_num: int,
                            success: bool, sample_no: str = "",
                            review_no: str = "") -> dict[str, Any]:
        """登记单字段解析结果，解析失败的字段连同行号可供坏行隔离消费。

        带上生效的样本编号与复核编号，产出的字段才能追到实际样本。
        """
        rows = store.rows(self.MODULE)
        entry_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        entry = {
            "id": entry_id,
            "status": "解析成功" if success else "部分失败",
            "pending": not success,
            "abnormal": not success,
            "解析编号": parse_no,
            "关联批次": intake_batch,
            "命中模板": template_no,
            "字段名称": field_name,
            "原始取值": raw_value,
            "解析取值": parsed_value,
            "解析耗时": str(elapsed_ms),
            "解析行号": str(row_num),
            "来源样本": sample_no,
            "表头复核编号": review_no,
        }
        rows.append(entry)
        return entry

    def claim_formal_parse(self, intake_batch: str, parse_no: str,
                           template_no: str, claimant: str,
                           claimed_at: str) -> tuple[bool, str, dict[str, Any] | None]:
        """领取正式字段解析：先核对结构嗅探与表头复核的当前受理资格。

        没有当前有效判定（委托撤销、被替换或从未受理）时拒绝领取，
        并保证已受理的历史判定与起始行原样保留。
        """
        from app.services.schema_sniff import SchemaSniffService
        from app.services.review_receipt import ReviewReceiptService

        entitlement = SchemaSniffService().current_entitlement(intake_batch)
        if not entitlement.get("可受理"):
            return False, (
                f"批次 {intake_batch} 不满足正式解析受理条件："
                f"{entitlement.get('阻断原因')}"
            ), None

        existing = next(
            (r for r in store.rows(self.MODULE)
             if r.get("关联批次") == intake_batch and r.get("领取人标识")),
            None)
        if existing is not None:
            return False, (
                f"批次 {intake_batch} 已由 {existing.get('领取人标识')} 于 "
                f"{existing.get('领取时间')} 领取正式解析，不得重复领取"
            ), dict(existing)

        rows = store.rows(self.MODULE)
        entry = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "status": "解析中", "pending": True, "abnormal": False,
            "解析编号": parse_no,
            "关联批次": intake_batch,
            "命中模板": template_no,
            "字段名称": "",
            "原始取值": "",
            "解析取值": "",
            "解析耗时": "0",
            "解析行号": str(entitlement.get("起始行") or 1),
            "来源样本": entitlement.get("样本编号"),
            "表头复核编号": entitlement.get("复核编号"),
            "领取人标识": claimant,
            "领取时间": claimed_at,
        }
        rows.append(entry)

        receipt_no = str(entitlement.get("回执编号") or "")
        if receipt_no:
            ReviewReceiptService().mark_actual_start(
                receipt_no, int(entitlement.get("起始行") or 1), 0)
        from app.services.parse_batch import ParseBatchService

        ParseBatchService().record_start_row(
            intake_batch,
            int(entitlement.get("起始行") or 1),
            str(entitlement.get("样本编号") or ""),
            str(entitlement.get("复核编号") or ""),
            claimed_at,
        )
        return True, (
            f"批次 {intake_batch} 正式解析已由 {claimant} 领取："
            f"按样本 {entitlement.get('样本编号')} 的判定从第 "
            f"{entitlement.get('起始行')} 行开始，不重复写入第一条数据"
        ), dict(entry)

    def failed_fields_of_batch(self, intake_batch: str) -> list[dict[str, Any]]:
        """输出解析失败字段清单（含行号），坏行隔离据此建隔离行。"""
        return [
            {
                "解析编号": r.get("解析编号"),
                "字段名称": r.get("字段名称"),
                "原始取值": r.get("原始取值"),
                "解析行号": r.get("解析行号"),
            }
            for r in store.rows(self.MODULE)
            if r.get("关联批次") == intake_batch and r.get("status") == "部分失败"
        ]

    def parsed_rows_of_batch(self, intake_batch: str) -> dict[str, dict[str, Any]]:
        """按行号聚合解析成功的取值，供抽样复核与输出契约取数。"""
        grouped: dict[str, dict[str, Any]] = {}
        for r in store.rows(self.MODULE):
            if r.get("关联批次") != intake_batch:
                continue
            line = str(r.get("解析行号") or "")
            if not line:
                continue
            bucket = grouped.setdefault(line, {})
            bucket[str(r.get("字段名称"))] = r.get("解析取值")
            bucket["__status"] = (
                "部分失败" if r.get("status") == "部分失败"
                else bucket.get("__status", "解析成功")
            )
        return grouped

    def success_rate(self, intake_batch: str) -> float:
        """批次字段级解析成功率，供解析指标交叉核对。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("关联批次") == intake_batch]
        if not rows:
            return 0.0
        return sum(1 for r in rows if r.get("status") == "解析成功") / len(rows)

    def template_hit_stats(self) -> dict[str, int]:
        """按命中模板统计解析字段数，供模板版本过渡期命中分布核对。"""
        stats: dict[str, int] = {}
        for r in store.rows(self.MODULE):
            key = str(r.get("命中模板") or "")
            if key:
                stats[key] = stats.get(key, 0) + 1
        return stats
