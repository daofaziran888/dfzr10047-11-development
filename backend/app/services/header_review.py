"""表头复核领域服务：受理受托复核员提交的表头判定并绑定样本与起始行，产出表头复核记录数据集。

受理前先由委托服务核验权限与采样范围；拒绝的提交只留工作记录，
不得改写待解析范围。已受理判定保留原样本与原起始行，后续覆盖只改变当前受理资格。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.services.delegated_review import DelegatedReviewService, batch_channel
from app.store import store


HEADER_TRUE_WORDS = {"有表头", "是", "有", "true", "1", "yes", "header"}
HEADER_FALSE_WORDS = {"无表头", "否", "无", "false", "0", "no", "no-header"}


def normalize_header(value: Any) -> bool | None:
    """把页面/接口传入的表头判定归一为布尔值，无法识别时返回 None。"""
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().casefold()
    if text in HEADER_TRUE_WORDS:
        return True
    if text in HEADER_FALSE_WORDS:
        return False
    return None


def _to_int(value: Any) -> int | None:
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    try:
        number = int(text)
    except ValueError:
        return None
    return number if number > 0 else None


class HeaderReviewService(BaseModuleService):
    MODULE = "header_review"
    ENTITY = "表头复核记录"
    OPERATION_TERM = "受理"
    LIST_FIELDS = ["复核编号", "关联批次", "来源渠道", "样本编号", "委托编号",
                   "受托人标识", "表头判定", "起始行", "受理结论", "回执编号"]
    REQUIRED_FIELDS = ["复核编号", "关联批次", "样本编号", "委托编号"]
    STATUS_ORDER = ["待受理", "已受理", "已拒绝", "已失效"]
    ACTION_RULES = {"标记待受理": "待受理", "作废复核": "已失效"}
    NEGATIVE_ACTIONS = ["作废复核"]

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def find(self, review_no: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("复核编号") == review_no:
                return row
        return None

    def records_of_sample(self, batch_id: str, sample_no: str) -> list[dict[str, Any]]:
        """样本工作记录：包含被拒提交，按提交顺序返回。"""
        return [
            dict(row) for row in store.rows(self.MODULE)
            if row.get("关联批次") == batch_id and row.get("样本编号") == sample_no
        ]

    def accepted_of_batch(self, batch_id: str) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(self.MODULE)
            if row.get("关联批次") == batch_id and row.get("status") == "已受理"
        ]

    def current_accepted(self, batch_id: str) -> dict[str, Any] | None:
        """批次当前具备受理资格的判定：每批最多一份。"""
        for row in self.accepted_of_batch(batch_id):
            if row.get("当前受理资格") == "是":
                return row
        return None

    def conflict_of_batch(self, batch_id: str) -> dict[str, Any] | None:
        """同批次多份已受理样本起始行不一致时输出冲突快照。"""
        accepted = self.accepted_of_batch(batch_id)
        starts = {
            str(row.get("样本编号")): str(row.get("起始行"))
            for row in accepted
        }
        if len({value for value in starts.values()}) <= 1:
            return None
        return {
            "关联批次": batch_id,
            "起始行冲突": "是",
            "样本起始行": "；".join(f"{k}={v}" for k, v in starts.items()),
            "冲突说明": "同一批次两份表头样本给出不同起始行，历史判定保持原样，正式解析以当前受理样本为准",
        }

    def working_record(self, batch_id: str, sample_no: str = "") -> dict[str, Any]:
        """字段解析页使用的工作记录：当前样本、授权归属与全部样本工作痕迹。"""
        current = self.current_accepted(batch_id)
        samples = sorted({
            str(row.get("样本编号"))
            for row in store.rows(self.MODULE)
            if row.get("关联批次") == batch_id and row.get("样本编号")
        })
        target = sample_no or (str(current.get("样本编号")) if current else (
            samples[0] if samples else ""))
        return {
            "关联批次": batch_id,
            "当前样本": target,
            "当前受理复核编号": current.get("复核编号") if current else "",
            "当前起始行": int(str(current.get("起始行") or 1)) if current else None,
            "样本清单": samples,
            "工作记录": self.records_of_sample(batch_id, target) if target else [],
            "起始行冲突": self.conflict_of_batch(batch_id),
        }

    # ------------------------------------------------------------------
    # 受理 / 拒绝
    # ------------------------------------------------------------------
    def _next_review_no(self, batch_id: str, sample_no: str) -> str:
        seq = len([
            row for row in store.rows(self.MODULE)
            if row.get("关联批次") == batch_id
        ]) + 1
        return f"HR-{batch_id}-{sample_no}-{seq:02d}"

    def _append(self, values: dict[str, Any], *, status: str,
                pending: bool, abnormal: bool) -> dict[str, Any]:
        rows = store.rows(self.MODULE)
        entry = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "status": status, "pending": pending, "abnormal": abnormal,
        }
        entry.update(values)
        rows.append(entry)
        return entry

    def submit_judgment(
        self, batch_id: str, sample_no: str, submitter: str,
        header_present: Any, start_row: Any = None,
        delegation_no: str = "", source: str = "", credential: str = "",
        submitted_at: str = "", remark: str = "",
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """受托人提交表头判定：先核验委托权限与采样范围，再决定受理或拒绝。

        拒绝时只写一条属于原样本的工作记录，结构嗅探与批次台账都不动，
        因此待解析范围保持不变。
        """
        batch_id = str(batch_id or "").strip()
        sample_no = str(sample_no or "").strip()
        submitter = str(submitter or "").strip()
        if not batch_id or not sample_no:
            return False, "关联批次与样本编号不能为空", None

        submitted_at = submitted_at or datetime.now().isoformat(timespec="seconds")

        header = normalize_header(header_present)
        if header is None:
            return False, f"无法识别的表头判定 {header_present!r}，只能填写有表头或无表头", None
        start = _to_int(start_row)
        if start_row not in (None, "") and start is None:
            return False, f"起始行 {start_row!r} 不是正整数", None
        if start is None:
            start = 2 if header else 1

        resolved_source = str(source or "").strip() or batch_channel(batch_id)
        delegations = DelegatedReviewService()
        resolved_no = str(delegation_no or "").strip()
        if not resolved_no:
            candidate = delegations.latest_for_batch(batch_id)
            resolved_no = str(candidate.get("委托编号")) if candidate else ""

        allowed, reason, delegation = delegations.validate_submission(
            resolved_no, batch_id, resolved_source, sample_no, submitter, credential)

        review_no = self._next_review_no(batch_id, sample_no)
        if not allowed:
            entry = self._append({
                "复核编号": review_no,
                "关联批次": batch_id,
                "来源渠道": resolved_source,
                "样本编号": sample_no,
                "委托编号": resolved_no,
                "受托人标识": submitter,
                "表头判定": "有表头" if header else "无表头",
                "起始行": str(start),
                "受理结论": "已拒绝",
                "回执编号": "",
                "拒绝原因": reason,
                "当前受理资格": "否",
                "资格失效原因": "",
                "提交时间": submitted_at,
                "备注": remark,
            }, status="已拒绝", pending=False, abnormal=True)
            return False, (
                f"判定未获受理：{reason}；提交保留在样本 {sample_no} 的工作记录中，"
                f"待解析范围未改变"
            ), entry

        # 重复批准：同一委托、同一样本、同一起始行只保留一份受理结果，不重复写首行。
        for row in self.accepted_of_batch(batch_id):
            if (row.get("样本编号") == sample_no
                    and row.get("委托编号") == resolved_no
                    and str(row.get("起始行")) == str(start)):
                return True, (
                    f"同一样本 {sample_no} 的起始行 {start} 已于复核 "
                    f"{row.get('复核编号')} 受理，重复批准不重复写入第一条数据"
                ), dict(row)

        entry = self._append({
            "复核编号": review_no,
            "关联批次": batch_id,
            "来源渠道": resolved_source,
            "样本编号": sample_no,
            "委托编号": resolved_no,
            "受托人标识": submitter,
            "表头判定": "有表头" if header else "无表头",
            "起始行": str(start),
            "受理结论": "已受理",
            "回执编号": "",
            "拒绝原因": "",
            "当前受理资格": "是",
            "资格失效原因": "",
            "提交时间": submitted_at,
            "备注": remark,
        }, status="已受理", pending=False, abnormal=False)

        self._shift_entitlement(batch_id, keep=review_no, changed_at=submitted_at)

        from app.services.review_receipt import ReviewReceiptService

        receipt = ReviewReceiptService().create_receipt(
            entry, delegation, submitted_at)
        entry["回执编号"] = receipt["回执编号"]

        conflict = self._flag_conflict(batch_id)
        self._sync_sniff_and_ledger(entry, conflict, submitted_at)
        return True, (
            f"判定已受理：样本 {sample_no} 以第 {start} 行为第一条数据，"
            f"回执 {entry['回执编号']}"
            + (f"；{conflict['冲突说明']}" if conflict else "")
        ), dict(entry)

    def _shift_entitlement(self, batch_id: str, keep: str, changed_at: str) -> None:
        """只转移当前受理资格，不改写历史受理结论、样本与起始行。"""
        for row in self.accepted_of_batch(batch_id):
            if row.get("复核编号") == keep:
                continue
            if row.get("当前受理资格") == "是":
                row["当前受理资格"] = "否"
                row["资格失效原因"] = f"被复核 {keep} 的判定覆盖，历史样本与起始行保留"
                row["资格失效时间"] = changed_at

    def _flag_conflict(self, batch_id: str) -> dict[str, Any] | None:
        conflict = self.conflict_of_batch(batch_id)
        for row in self.accepted_of_batch(batch_id):
            row["冲突标记"] = "起始行冲突" if conflict else ""
        return conflict

    def _sync_sniff_and_ledger(self, entry: dict[str, Any],
                               conflict: dict[str, Any] | None,
                               confirmed_at: str) -> None:
        """把起始行确认结果同步给结构嗅探与批次台账。"""
        from app.services.parse_batch import ParseBatchService
        from app.services.schema_sniff import SchemaSniffService

        SchemaSniffService().apply_header_review(
            str(entry.get("关联批次")),
            str(entry.get("样本编号")),
            str(entry.get("表头判定")) == "有表头",
            int(str(entry.get("起始行") or 1)),
            str(entry.get("复核编号")),
            conflict,
        )
        ParseBatchService().record_start_row(
            str(entry.get("关联批次")),
            int(str(entry.get("起始行") or 1)),
            str(entry.get("样本编号")),
            str(entry.get("复核编号")),
            confirmed_at,
        )

    def reject_count_of_batch(self, batch_id: str) -> int:
        return sum(
            1 for row in store.rows(self.MODULE)
            if row.get("关联批次") == batch_id and row.get("status") == "已拒绝"
        )
