"""委托复核领域服务：登记带样本范围的表头判定委托、撤销与替换，产出委托复核单数据集。

委托是受托人提交表头判定的唯一权限来源：来源渠道、批次和采样范围三者缺一不可。
撤销或替换只让后续提交失去资格，不会改写已经受理的历史判定与回执。
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


def split_scope(scope: Any) -> list[str]:
    """把样本范围拆成样本编号清单，支持逗号、顿号、竖线和分号分隔。"""
    text = str(scope or "").replace("，", ",").replace("、", ",")
    for separator in ("|", ";", "；"):
        text = text.replace(separator, ",")
    return [item.strip() for item in text.split(",") if item.strip()]


def batch_channel(batch_id: str) -> str:
    """从投递批次（或批次记录）反查来源渠道；查不到时返回空串。"""
    for row in store.rows("file_intake"):
        if row.get("批次编号") == batch_id:
            return str(row.get("来源渠道") or "")
    for row in store.rows("parse_batch"):
        if (row.get("关联投递批次") == batch_id
                or row.get("批次记录编号") == batch_id):
            return str(row.get("来源渠道") or "")
    return ""


class DelegatedReviewService(BaseModuleService):
    MODULE = "delegated_review"
    ENTITY = "委托复核单"
    OPERATION_TERM = "授权"
    LIST_FIELDS = ["委托编号", "关联批次", "来源渠道", "样本范围", "委托人标识",
                   "受托人标识", "授权凭据", "创建时间"]
    REQUIRED_FIELDS = ["委托编号", "关联批次", "来源渠道", "样本范围",
                       "委托人标识", "受托人标识"]
    STATUS_ORDER = ["有效", "已撤销", "已替换", "已过期"]
    ACTION_RULES = {"撤销委托": "已撤销", "登记过期": "已过期"}
    NEGATIVE_ACTIONS = ["撤销委托"]

    # ------------------------------------------------------------------
    # 查询与凭据
    # ------------------------------------------------------------------
    def find(self, delegation_no: str) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if row.get("委托编号") == delegation_no:
                return row
        return None

    def scope_samples(self, delegation_no: str) -> list[str]:
        delegation = self.find(delegation_no)
        return split_scope(delegation.get("样本范围")) if delegation else []

    def delegations_of_batch(self, batch_id: str) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(self.MODULE)
            if row.get("关联批次") == batch_id
        ]

    def latest_for_batch(self, batch_id: str) -> dict[str, Any] | None:
        rows = self.delegations_of_batch(batch_id)
        return max(rows, key=lambda row: int(row.get("id", 0))) if rows else None

    @staticmethod
    def _credential(delegation_no: str, batch_id: str, samples: list[str]) -> str:
        seed = f"{delegation_no}|{batch_id}|{','.join(samples)}"
        return "DRC-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]

    # ------------------------------------------------------------------
    # 登记 / 撤销 / 替换
    # ------------------------------------------------------------------
    def create_delegation(self, delegation_no: str, batch_id: str, source: str,
                          sample_scope: Any, delegator: str, delegatee: str,
                          created_at: str, credential: str = "",
                          remark: str = "") -> tuple[bool, str, dict[str, Any] | None]:
        """登记委托：来源、批次与采样范围必须齐全且互相对应。"""
        delegation_no = str(delegation_no or "").strip()
        batch_id = str(batch_id or "").strip()
        source = str(source or "").strip()
        delegator = str(delegator or "").strip()
        delegatee = str(delegatee or "").strip()
        samples = split_scope(sample_scope)
        missing = [
            label for label, value in (
                ("委托编号", delegation_no), ("关联批次", batch_id),
                ("来源渠道", source), ("样本范围", samples),
                ("委托人标识", delegator), ("受托人标识", delegatee),
            ) if not value
        ]
        if missing:
            return False, f"缺少必填字段：{'、'.join(missing)}", None
        if self.find(delegation_no) is not None:
            return False, f"委托编号 {delegation_no} 已存在", None
        channel = batch_channel(batch_id)
        if channel and channel != source:
            return False, (
                f"批次 {batch_id} 的来源渠道为 {channel}，"
                f"与委托来源 {source} 不一致，无法登记委托"
            ), None

        created_at = created_at or datetime.now().isoformat(timespec="seconds")
        row = {
            "id": max((int(r.get("id", 0)) for r in store.rows(self.MODULE)), default=0) + 1,
            "status": "有效",
            "pending": False,
            "abnormal": False,
            "委托编号": delegation_no,
            "关联批次": batch_id,
            "来源渠道": source,
            "样本范围": ",".join(samples),
            "委托人标识": delegator,
            "受托人标识": delegatee,
            "授权凭据": credential or self._credential(delegation_no, batch_id, samples),
            "创建时间": created_at,
            "撤销时间": "",
            "撤销原因": "",
            "备注": remark,
        }
        store.rows(self.MODULE).append(row)
        return True, (
            f"委托 {delegation_no} 已登记：{delegator} 授权 {delegatee} "
            f"复核 {batch_id} 的样本 {','.join(samples)}"
        ), dict(row)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """通用登记入口：把页面字段映射到带范围校验的委托登记。"""
        ok, message, row = self.create_delegation(
            delegation_no=str(values.get("委托编号") or values.get("delegation_no") or ""),
            batch_id=str(values.get("关联批次") or values.get("batch_id") or ""),
            source=str(values.get("来源渠道") or values.get("source") or ""),
            sample_scope=values.get("样本范围") or values.get("sample_scope") or "",
            delegator=str(values.get("委托人标识") or values.get("delegator") or ""),
            delegatee=str(values.get("受托人标识") or values.get("delegatee") or ""),
            created_at=str(values.get("创建时间") or values.get("created_at") or ""),
            credential=str(values.get("授权凭据") or values.get("credential") or ""),
            remark=str(values.get("备注") or values.get("remark") or ""),
        )
        if not ok:
            return None, [message]
        return row, []

    def create_failure_message(self, reasons: list[str]) -> str:
        """委托登记失败时原样给出领域校验原因，不再套「缺少必填字段」。"""
        return reasons[0] if reasons else "委托复核单登记失败"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """通用动作入口：撤销必须走权限与回执失效处理，不能只改状态。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action == "撤销委托":
            ok, message, changed = self.revoke_delegation(
                str(entry.get("委托编号") or ""),
                str(entry.get("委托人标识") or ""),
                str(entry.get("撤销时间") or ""),
                "页面撤销",
            )
            return (changed if ok else None), message
        return super().run_action(entry_id, action)

    def _invalidate_reviews_of(self, delegation_no: str, reason: str,
                               changed_at: str) -> list[str]:
        """让委托关联的已受理判定失去当前资格，但保留原样本、起始行与回执。"""
        from app.services.review_receipt import ReviewReceiptService

        invalidated: list[str] = []
        receipts = ReviewReceiptService()
        for row in store.rows("header_review"):
            if row.get("委托编号") != delegation_no:
                continue
            if row.get("status") != "已受理":
                continue
            row["当前受理资格"] = "否"
            row["资格失效原因"] = reason
            row["资格失效时间"] = changed_at
            receipt_no = str(row.get("回执编号") or "")
            if receipt_no:
                receipts.mark_read_only(receipt_no, reason)
            invalidated.append(str(row.get("复核编号") or ""))
        return invalidated

    def revoke_delegation(self, delegation_no: str, actor: str,
                          revoked_at: str,
                          reason: str = "") -> tuple[bool, str, dict[str, Any] | None]:
        """撤销委托：仅委托人本人可撤销；已受理判定改为只读，历史起始行不变。"""
        delegation = self.find(delegation_no)
        if delegation is None:
            return False, f"委托 {delegation_no} 不存在", None
        if str(delegation.get("委托人标识") or "") != str(actor or ""):
            return False, (
                f"{actor or '未署名提交人'} 不是委托 {delegation_no} 的委托人，"
                f"无权撤销"
            ), None
        if delegation.get("status") != "有效":
            return False, (
                f"委托 {delegation_no} 当前状态为 {delegation.get('status')}，"
                f"不能重复撤销"
            ), dict(delegation)

        revoked_at = revoked_at or datetime.now().isoformat(timespec="seconds")

        delegation["status"] = "已撤销"
        delegation["pending"] = False
        delegation["abnormal"] = False
        delegation["撤销时间"] = revoked_at
        delegation["撤销原因"] = reason or "委托人撤销"
        invalidated = self._invalidate_reviews_of(
            delegation_no, f"委托 {delegation_no} 已撤销", revoked_at)
        return True, (
            f"委托 {delegation_no} 已撤销，"
            f"{len(invalidated)} 份已受理判定转为只读；"
            f"原样本与起始行保留，需委托人确认样本后才能领取正式解析"
        ), dict(delegation)

    def replace_delegation(self, old_no: str, new_no: str, sample_scope: Any,
                           actor: str, replaced_at: str,
                           delegatee: str = "", source: str = "",
                           batch_id: str = "") -> tuple[bool, str, dict[str, Any] | None]:
        """用新样本范围替换旧委托：旧委托及其回执只读，历史判定不改写。"""
        old = self.find(old_no)
        if old is None:
            return False, f"委托 {old_no} 不存在", None
        if str(old.get("委托人标识") or "") != str(actor or ""):
            return False, (
                f"{actor or '未署名提交人'} 不是委托 {old_no} 的委托人，无权替换"
            ), None
        if old.get("status") != "有效":
            return False, (
                f"委托 {old_no} 当前状态为 {old.get('status')}，不能替换"
            ), dict(old)

        replaced_at = replaced_at or datetime.now().isoformat(timespec="seconds")

        ok, message, created = self.create_delegation(
            new_no,
            batch_id or str(old.get("关联批次") or ""),
            source or str(old.get("来源渠道") or ""),
            sample_scope,
            str(old.get("委托人标识") or ""),
            delegatee or str(old.get("受托人标识") or ""),
            replaced_at,
        )
        if not ok:
            return False, message, None

        old["status"] = "已替换"
        old["pending"] = False
        old["abnormal"] = False
        old["撤销时间"] = replaced_at
        old["撤销原因"] = f"被委托 {new_no} 替换"
        invalidated = self._invalidate_reviews_of(
            old_no, f"委托 {old_no} 已被 {new_no} 替换", replaced_at)
        return True, (
            f"委托 {old_no} 已由 {new_no} 替换，"
            f"{len(invalidated)} 份历史判定只读保留"
        ), created

    # ------------------------------------------------------------------
    # 权限校验
    # ------------------------------------------------------------------
    def validate_submission(self, delegation_no: str, batch_id: str, source: str,
                            sample_no: str, submitter: str,
                            credential: str = "") -> tuple[bool, str, dict[str, Any] | None]:
        """在采纳判定前核验权限：委托有效、受托人相符、来源与样本都在范围内。

        任何一项不满足都按越权处理，调用方只能登记拒绝记录，不得变更待解析范围。
        """
        delegation = self.find(delegation_no)
        if delegation is None:
            label = f" {delegation_no}" if delegation_no else ""
            return False, (
                f"没有找到委托{label}："
                f"缺少对应来源与采样范围的授权，属于越权提交"
            ), None
        status = str(delegation.get("status") or "")
        if status != "有效":
            return False, (
                f"委托 {delegation_no} 当前状态为 {status}，"
                f"不能再提交人工判定"
            ), delegation
        if str(delegation.get("受托人标识") or "") != str(submitter or ""):
            return False, (
                f"{submitter or '未署名提交人'} 不是委托 {delegation_no} 的受托人，"
                f"属于越权提交"
            ), delegation
        if str(delegation.get("关联批次") or "") != str(batch_id or ""):
            return False, (
                f"委托 {delegation_no} 只覆盖批次 {delegation.get('关联批次')}，"
                f"与提交批次 {batch_id} 不符，属于越权提交"
            ), delegation
        delegation_source = str(delegation.get("来源渠道") or "")
        if delegation_source != str(source or ""):
            return False, (
                f"委托 {delegation_no} 的来源为 {delegation_source}，"
                f"与提交来源 {source or '（未提供）'} 不符，属于越权提交"
            ), delegation
        if credential and credential != str(delegation.get("授权凭据") or ""):
            return False, f"授权凭据与委托 {delegation_no} 不匹配，属于越权提交", delegation
        if str(sample_no or "") not in self.scope_samples(delegation_no):
            return False, (
                f"样本 {sample_no} 不在委托 {delegation_no} 的采样范围 "
                f"{delegation.get('样本范围')} 内，属于越权提交"
            ), delegation
        return True, f"委托 {delegation_no} 权限核验通过", delegation

    def authorization_of_batch(self, batch_id: str) -> dict[str, Any]:
        """输出批次当前授权归属快照，供委托复核表单展示。"""
        latest = self.latest_for_batch(batch_id)
        if latest is None:
            return {
                "委托编号": "",
                "关联批次": batch_id,
                "来源渠道": "",
                "委托人标识": "",
                "受托人标识": "",
                "授权状态": "未授权",
                "授权凭据": "",
                "样本范围": "",
                "可提交": False,
                "阻断原因": "该批次没有对应来源和采样范围的委托，提交判定属于越权",
            }
        return {
            "委托编号": latest.get("委托编号"),
            "关联批次": latest.get("关联批次"),
            "来源渠道": latest.get("来源渠道"),
            "委托人标识": latest.get("委托人标识"),
            "受托人标识": latest.get("受托人标识"),
            "授权状态": latest.get("status"),
            "授权凭据": latest.get("授权凭据"),
            "样本范围": latest.get("样本范围"),
            "创建时间": latest.get("创建时间"),
            "撤销时间": latest.get("撤销时间"),
            "可提交": latest.get("status") == "有效",
            "阻断原因": "" if latest.get("status") == "有效" else (
                f"委托 {latest.get('委托编号')} 已{latest.get('status')}，"
                f"不能再以受托人身份提交判定"
            ),
        }
