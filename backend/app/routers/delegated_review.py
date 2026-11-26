"""委托复核与表头受理接口：表单、提交、撤销、替换、回执核对和正式解析领取。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from app.schemas import ActionResult
from app.services.delegated_review import DelegatedReviewService
from app.services.field_parse import FieldParseService
from app.services.header_review import HeaderReviewService
from app.services.parse_batch import ParseBatchService
from app.services.review_receipt import ReviewReceiptService
from app.services.schema_sniff import SchemaSniffService
from app.store import store

router = APIRouter(prefix="/api/delegated-review", tags=["委托复核"])


def _delegations() -> DelegatedReviewService:
    return DelegatedReviewService()


def _reviews() -> HeaderReviewService:
    return HeaderReviewService()


def _receipts() -> ReviewReceiptService:
    return ReviewReceiptService()


def _batch_or_404(batch_id: str) -> str:
    value = str(batch_id or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="必须提供关联批次")
    return value


def _receipt_options(batch_id: str) -> list[dict[str, Any]]:
    """撤销或替换发生时给页面两条路：只读凭据继续，或改选批次当前样本。"""
    options: list[dict[str, Any]] = []
    read_only = [
        receipt for receipt in _receipts().of_batch(batch_id)
        if receipt.get("status") == "只读"
    ]
    if read_only:
        options.append({
            "操作": "从只读凭据继续",
            "说明": "原判定只读保留，可按回执打开实际解析起始行追溯，但不能据此领取正式解析",
            "回执编号": read_only[-1].get("回执编号"),
            "实际解析起始行": read_only[-1].get("实际解析起始行"),
        })
    entitlement = SchemaSniffService().current_entitlement(batch_id)
    reviews = [
        row for row in store.rows("header_review")
        if row.get("关联批次") == batch_id
    ]
    samples = sorted({str(row.get("样本编号")) for row in reviews if row.get("样本编号")})
    options.append({
        "操作": "选择其他样本",
        "说明": (
            "由委托人确认同批次另一份样本后重新提交判定"
            if not entitlement.get("可受理") else
            "当前样本仍有效，如需改用其他样本请先由委托人替换委托"
        ),
        "备选样本": samples,
        "当前受理样本": entitlement.get("样本编号", ""),
        "当前受理资格": "是" if entitlement.get("可受理") else "否",
    })
    return options


def _form_payload(batch_id: str, sample_no: str = "") -> dict[str, Any]:
    delegation = _delegations().authorization_of_batch(batch_id)
    reviews = _reviews()
    working = reviews.working_record(batch_id, sample_no)
    receipts = _receipts().of_batch(batch_id)
    return {
        "关联批次": batch_id,
        "授权归属": delegation,
        "当前样本": working["当前样本"],
        "当前受理复核编号": working["当前受理复核编号"],
        "当前起始行": working["当前起始行"],
        "样本清单": working["样本清单"],
        "起始行冲突": working["起始行冲突"],
        "工作记录": working["工作记录"],
        "委托清单": [
            {
                "委托编号": row.get("委托编号"),
                "委托人标识": row.get("委托人标识"),
                "受托人标识": row.get("受托人标识"),
                "样本范围": row.get("样本范围"),
                "授权状态": row.get("status"),
            }
            for row in _delegations().delegations_of_batch(batch_id)
        ],
        "回执清单": receipts,
        "当前受理资格": SchemaSniffService().current_entitlement(batch_id),
        "批次台账": ParseBatchService().header_ledger(batch_id),
        "操作选项": _receipt_options(batch_id),
    }


@router.get("/form", response_model=dict)
def review_form(batch_id: str = Query(...), sample_no: str = Query(default="")) -> dict[str, Any]:
    """委托复核表单：展示授权归属、当前样本、起始行冲突与可选操作。"""
    return _form_payload(_batch_or_404(batch_id), sample_no)


@router.post("/submit", response_model=ActionResult)
def submit_judgment(payload: dict[str, Any] = Body(default_factory=dict)) -> ActionResult:
    """提交表头判定：服务端先核验权限和样本，再决定受理或拒绝并签发回执。"""
    batch_id = _batch_or_404(str(payload.get("batch_id") or payload.get("关联批次") or ""))
    sample_no = str(payload.get("sample_no") or payload.get("样本编号") or "")
    if not sample_no:
        return ActionResult(ok=False, message="必须选择样本后才能提交判定")
    ok, message, entry = _reviews().submit_judgment(
        batch_id=batch_id,
        sample_no=sample_no,
        submitter=str(payload.get("submitter") or payload.get("受托人标识") or ""),
        header_present=payload.get("header_present", payload.get("表头判定")),
        start_row=payload.get("start_row", payload.get("起始行")),
        delegation_no=str(payload.get("delegation_no") or payload.get("委托编号") or ""),
        source=str(payload.get("source") or payload.get("来源渠道") or ""),
        credential=str(payload.get("credential") or payload.get("授权凭据") or ""),
        submitted_at=str(payload.get("submitted_at") or payload.get("提交时间") or ""),
        remark=str(payload.get("remark") or payload.get("备注") or ""),
    )
    result: dict[str, Any] = {
        "ok": ok,
        "message": message,
        "entry": entry,
        "working_record": _reviews().working_record(batch_id, sample_no)["工作记录"],
    }
    receipt_no = str((entry or {}).get("回执编号") or "")
    if ok and receipt_no:
        result["receipt"] = _receipts().verify(receipt_no)
    result["form"] = _form_payload(batch_id, sample_no)
    return ActionResult(ok=ok, message=message, entry=result)


@router.post("/{delegation_no}/revoke", response_model=ActionResult)
def revoke(delegation_no: str,
           payload: dict[str, Any] = Body(default_factory=dict)) -> ActionResult:
    """委托人撤销委托：后续提交失去资格，已受理判定和回执转为只读。"""
    ok, message, entry = _delegations().revoke_delegation(
        delegation_no,
        str(payload.get("actor") or payload.get("委托人标识") or ""),
        str(payload.get("revoked_at") or payload.get("撤销时间") or ""),
        str(payload.get("reason") or payload.get("撤销原因") or ""),
    )
    batch_id = str((entry or {}).get("关联批次") or payload.get("batch_id") or "")
    return ActionResult(ok=ok, message=message, entry={
        "entry": entry,
        "form": _form_payload(batch_id) if batch_id else None,
    })


@router.post("/replace", response_model=ActionResult)
def replace(payload: dict[str, Any] = Body(default_factory=dict)) -> ActionResult:
    """替换委托：旧委托只读，新样本范围另起委托，历史判定不改写。"""
    old_no = str(payload.get("old_no") or payload.get("旧委托编号") or "")
    new_no = str(payload.get("new_no") or payload.get("新委托编号") or "")
    if not old_no or not new_no:
        return ActionResult(ok=False, message="替换委托必须同时提供旧委托编号和新委托编号")
    ok, message, entry = _delegations().replace_delegation(
        old_no, new_no,
        payload.get("sample_scope") or payload.get("样本范围") or "",
        str(payload.get("actor") or payload.get("委托人标识") or ""),
        str(payload.get("replaced_at") or payload.get("替换时间") or ""),
        delegatee=str(payload.get("delegatee") or payload.get("受托人标识") or ""),
        source=str(payload.get("source") or payload.get("来源渠道") or ""),
        batch_id=str(payload.get("batch_id") or payload.get("关联批次") or ""),
    )
    batch_id = str((entry or {}).get("关联批次") or payload.get("batch_id") or "")
    return ActionResult(ok=ok, message=message, entry={
        "entry": entry,
        "form": _form_payload(batch_id) if batch_id else None,
    })


@router.get("/receipts/{receipt_no}", response_model=dict)
def receipt_detail(receipt_no: str) -> dict[str, Any]:
    """打开回执：给出实际解析起始行、权限依据和当前受理资格。"""
    detail = _receipts().verify(receipt_no)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"回执 {receipt_no} 不存在")
    return detail


@router.get("/receipts", response_model=dict)
def receipt_list(batch_id: str = Query(...)) -> dict[str, Any]:
    return {
        "关联批次": batch_id,
        "items": _receipts().of_batch(_batch_or_404(batch_id)),
    }


@router.post("/claim", response_model=ActionResult)
def claim_formal_parse(payload: dict[str, Any] = Body(default_factory=dict)) -> ActionResult:
    """从同一批次领取正式字段解析：核对当前样本、起始行与权限依据。"""
    batch_id = _batch_or_404(str(payload.get("batch_id") or payload.get("关联批次") or ""))
    ok, message, entry = FieldParseService().claim_formal_parse(
        batch_id,
        str(payload.get("parse_no") or f"FP-CLAIM-{batch_id}"),
        str(payload.get("template_id") or payload.get("命中模板") or ""),
        str(payload.get("claimant") or payload.get("领取人标识") or ""),
        str(payload.get("claimed_at") or ""),
    )
    entitlement = SchemaSniffService().current_entitlement(batch_id)
    return ActionResult(ok=ok, message=message, entry={
        "entry": entry,
        "entitlement": entitlement,
        "ledger": ParseBatchService().header_ledger(batch_id),
        "form": _form_payload(batch_id, str(entitlement.get("样本编号") or "")),
    })
