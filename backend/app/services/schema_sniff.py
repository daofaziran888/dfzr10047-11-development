"""结构嗅探领域服务：对无声明结构的文件抽样推断列数、列名与列类型，产出结构推断数据集。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class SchemaSniffService(BaseModuleService):
    MODULE = "schema_sniff"
    ENTITY = "结构推断"
    OPERATION_TERM = "解析"
    LIST_FIELDS = ["嗅探编号", "关联批次", "样本行数", "推断列数", "列名清单", "列类型推断", "首行是否表头", "嗅探置信度"]
    REQUIRED_FIELDS = ["嗅探编号", "关联批次", "样本行数"]
    STATUS_ORDER = ["待嗅探", "已推断", "低置信", "推断冲突"]
    ACTION_RULES = {"执行嗅探": "已推断", "标记低置信": "低置信", "登记冲突": "推断冲突"}
    NEGATIVE_ACTIONS = ["登记冲突"]

    LOW_CONFIDENCE = 0.60

    @staticmethod
    def _looks_numeric(value: str) -> bool:
        text = value.strip()
        if not text:
            return False
        try:
            float(text)
            return True
        except ValueError:
            return False

    def detect_header(self, sample_rows: list[list[str]]) -> tuple[bool, float]:
        """判定首行是否表头，返回 (是否表头, 判定置信度)。

        依据：首行全为非数值文本、且后续数据行同列类型稳定时判为表头。
        """
        if len(sample_rows) < 2:
            return False, 0.30
        first = sample_rows[0]
        rest = sample_rows[1:]
        first_alpha = all(
            cell.strip() and not self._looks_numeric(cell)
            for cell in first if cell.strip()
        )
        column_count = len(first)
        stable_columns = 0
        for index in range(column_count):
            column_values = [r[index] for r in rest if index < len(r)]
            numeric = sum(1 for v in column_values if self._looks_numeric(v))
            if column_values and (numeric == len(column_values) or numeric == 0):
                stable_columns += 1
        stability = stable_columns / column_count if column_count else 0.0
        is_header = bool(first_alpha and stability >= 0.75)
        confidence = round(min(0.99, 0.5 + stability * 0.4 + (0.09 if is_header else 0.0)), 2)
        return is_header, confidence

    def sniff_structure(self, sniff_no: str, intake_batch: str,
                        sample_rows: list[list[str]],
                        delimiter_rule_no: str) -> tuple[bool, str, dict[str, Any] | None]:
        """执行结构嗅探并写入推断记录，产出解析起始行结论。"""
        rows = store.rows(self.MODULE)
        if not sample_rows:
            return False, f"批次 {intake_batch} 样本行为空，无法嗅探结构", None

        is_header, confidence = self.detect_header(sample_rows)
        column_count = len(sample_rows[0])
        inconsistent = [
            i for i, r in enumerate(sample_rows) if len(r) != column_count
        ]
        data_rows = sample_rows[1:] if is_header else sample_rows
        column_names = (
            [c.strip() for c in sample_rows[0]] if is_header
            else [f"col-{i}" for i in range(column_count)]
        )
        column_types: list[str] = []
        for index in range(column_count):
            values = [r[index] for r in data_rows if index < len(r)]
            numeric = sum(1 for v in values if self._looks_numeric(v))
            if values and numeric == len(values):
                has_dot = any("." in v for v in values)
                column_types.append("float" if has_dot else "int")
            else:
                column_types.append("str")

        if inconsistent:
            status = "推断冲突"
            conclusion = (
                f"样本中有 {len(inconsistent)} 行列数与推断列数 {column_count} 不一致"
                f"（样本行 {inconsistent[:5]}），登记推断冲突"
            )
        elif confidence < self.LOW_CONFIDENCE:
            status = "低置信"
            conclusion = f"嗅探置信度 {confidence:.2f} 低于 {self.LOW_CONFIDENCE:.2f}，标记低置信"
        else:
            status = "已推断"
            conclusion = (
                f"推断 {column_count} 列，首行{'是' if is_header else '不是'}表头，"
                f"解析起始行为 {2 if is_header else 1}"
            )

        target = next((r for r in rows if r.get("嗅探编号") == sniff_no), None)
        if target is None:
            entry_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
            target = {"id": entry_id, "嗅探编号": sniff_no}
            rows.append(target)
        target.update({
            "关联批次": intake_batch,
            "样本行数": str(len(sample_rows)),
            "推断列数": str(column_count),
            "列名清单": ",".join(column_names),
            "列类型推断": ";".join(column_types),
            "首行是否表头": "是" if is_header else "否",
            "嗅探置信度": f"{confidence:.2f}",
            "命中分隔规则": delimiter_rule_no,
            "status": status,
            "pending": status != "已推断",
            "abnormal": status == "推断冲突",
        })
        return status == "已推断", conclusion, dict(target)

    def parse_start_row(self, sniff_no: str) -> int:
        """输出字段解析消费的起始行：表头复核确认的起始行优先于嗅探推断。"""
        for row in store.rows(self.MODULE):
            if row.get("嗅探编号") != sniff_no:
                continue
            if row.get("status") not in ("已推断",):
                return 1
            confirmed = str(row.get("确认起始行") or "").strip()
            if row.get("表头复核编号") and confirmed.isdigit():
                return int(confirmed)
            return 2 if row.get("首行是否表头") == "是" else 1
        return 1

    # ------------------------------------------------------------------
    # 表头复核联动：起始行以复核记录为准
    # ------------------------------------------------------------------
    def apply_header_review(self, batch_id: str, sample_no: str,
                            header_present: bool, start_row: int,
                            review_no: str,
                            conflict: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """把已受理的表头判定写进结构嗅探记录，起始位置改由表头复核记录管理。"""
        target = self._sniff_of_batch(batch_id)
        if target is None:
            rows = store.rows(self.MODULE)
            target = {
                "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
                "嗅探编号": f"SS-{batch_id}",
            }
            rows.append(target)
        target.update({
            "关联批次": batch_id,
            "首行是否表头": "是" if header_present else "否",
            "确认起始行": str(start_row),
            "表头复核编号": review_no,
            "起始行确认样本": sample_no,
            "起始行确认结果": (
                f"样本 {sample_no} 判定{'有表头' if header_present else '无表头'}，"
                f"解析起始行 {start_row}"
            ),
            "起始行冲突": (
                conflict.get("样本起始行") if conflict else ""
            ),
            "status": "已推断",
            "pending": False,
            "abnormal": bool(conflict),
        })
        return dict(target)

    def _sniff_of_batch(self, batch_id: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("关联批次") == batch_id:
                return row
        return None

    def entitlement_sniff_no(self, batch_id: str) -> str:
        """返回批次当前用于判定受理资格的结构嗅探编号。"""
        target = self._sniff_of_batch(batch_id)
        return str(target.get("嗅探编号")) if target else ""

    def current_entitlement(self, batch_id: str) -> dict[str, Any]:
        """字段解析领取前核对的当前受理资格：只看表头复核记录的当前有效判定。"""
        from app.services.header_review import HeaderReviewService

        review_svc = HeaderReviewService()
        current = review_svc.current_accepted(batch_id)
        conflict = review_svc.conflict_of_batch(batch_id)
        if current is None:
            accepted = review_svc.accepted_of_batch(batch_id)
            reason = (
                "批次没有当前有效的表头复核判定，正式字段解析不得领取"
                if not accepted else
                "批次已受理的表头判定均已失效（委托撤销或被替换），需重新确认样本"
            )
            return {
                "关联批次": batch_id,
                "可受理": False,
                "阻断原因": reason,
                "复核编号": "",
                "样本编号": "",
                "起始行": None,
                "起始行冲突": conflict,
            }
        return {
            "关联批次": batch_id,
            "可受理": True,
            "阻断原因": "",
            "复核编号": current.get("复核编号"),
            "样本编号": current.get("样本编号"),
            "起始行": int(str(current.get("起始行") or 1)),
            "委托编号": current.get("委托编号"),
            "受托人标识": current.get("受托人标识"),
            "回执编号": current.get("回执编号"),
            "起始行冲突": conflict,
        }

    def blocks_field_parse(self, intake_batch: str) -> tuple[bool, str]:
        """推断冲突或低置信时不得启动字段解析。"""
        for row in store.rows(self.MODULE):
            if row.get("关联批次") != intake_batch:
                continue
            if row.get("status") == "推断冲突":
                return True, (
                    f"批次 {intake_batch} 结构推断冲突（列数不一致），"
                    f"字段解析不得启动"
                )
            if row.get("status") == "低置信":
                return True, (
                    f"批次 {intake_batch} 嗅探置信度 {row.get('嗅探置信度')} 过低，"
                    f"字段解析需人工确认结构后再启动"
                )
        return False, f"批次 {intake_batch} 结构推断可用"

    def inferred_columns(self, sniff_no: str) -> list[str]:
        """输出推断列名清单，供输出契约列集合比对。"""
        for row in store.rows(self.MODULE):
            if row.get("嗅探编号") == sniff_no:
                return [c.strip() for c in str(row.get("列名清单") or "").split(",") if c.strip()]
        return []
