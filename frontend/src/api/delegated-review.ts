import { request } from './client'
import type { ActionResult } from '@/data/types'

/** 委托复核表单：授权归属、当前样本、起始行冲突与可选操作。 */
export type DelegationAuth = {
  委托编号: string
  关联批次: string
  来源渠道: string
  委托人标识: string
  受托人标识: string
  授权状态: string
  授权凭据: string
  样本范围: string
  可提交: boolean
  阻断原因: string
}

export type HeaderRecord = {
  id: number
  status: string
  复核编号: string
  关联批次: string
  来源渠道: string
  样本编号: string
  委托编号: string
  受托人标识: string
  表头判定: string
  起始行: string
  受理结论: string
  回执编号: string
  拒绝原因?: string
  当前受理资格: string
  资格失效原因?: string
  冲突标记?: string
  提交时间: string
  备注?: string
}

export type Receipt = {
  id: number
  status: string
  回执编号: string
  复核编号: string
  关联批次: string
  来源渠道: string
  样本编号: string
  委托编号: string
  委托人标识: string
  受托人标识: string
  授权凭据: string
  样本范围: string
  表头判定: string
  起始行: string
  实际解析起始行: string
  已解析行数: string
  回执状态: string
  只读原因: string
  核对结论: string
}

export type Entitlement = {
  关联批次: string
  可受理: boolean
  阻断原因: string
  复核编号: string
  样本编号: string
  起始行: number | null
  委托编号?: string
  受托人标识?: string
  回执编号?: string
  起始行冲突: Conflict | null
}

export type Conflict = {
  关联批次: string
  起始行冲突: string
  样本起始行: string
  冲突说明: string
}

export type ReviewOption = {
  操作: string
  说明: string
  回执编号?: string
  实际解析起始行?: string
  备选样本?: string[]
  当前受理样本?: string
  当前受理资格?: string
}

export type ReviewForm = {
  关联批次: string
  授权归属: DelegationAuth
  当前样本: string
  当前受理复核编号: string
  当前起始行: number | null
  样本清单: string[]
  起始行冲突: Conflict | null
  工作记录: HeaderRecord[]
  委托清单: {
    委托编号: string
    委托人标识: string
    受托人标识: string
    样本范围: string
    授权状态: string
  }[]
  回执清单: Receipt[]
  当前受理资格: Entitlement
  批次台账: Record<string, string | null>
  操作选项: ReviewOption[]
}

export type SubmitResult = ActionResult & {
  entry?: {
    entry: HeaderRecord | null
    working_record: HeaderRecord[]
    receipt?: ReceiptVerify
    form: ReviewForm
  }
}

export type ReceiptVerify = {
  回执编号: string
  关联批次: string
  核对: Record<string, string | number | boolean>
  回执: Receipt
}

async function postJson<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  const payload = (await response.json()) as T & { detail?: string }
  if (!response.ok) {
    throw new Error(payload?.detail ?? `接口返回 ${response.status}，数据未更新`)
  }
  return payload
}

export async function loadReviewForm(
  batchId: string,
  sampleNo = '',
): Promise<ReviewForm> {
  const query = new URLSearchParams({ batch_id: batchId })
  if (sampleNo) query.set('sample_no', sampleNo)
  const response = await request(`/api/delegated-review/form?${query}`)
  if (!response.ok) throw new Error('委托复核表单读取失败')
  return (await response.json()) as ReviewForm
}

export async function submitJudgment(
  payload: Record<string, unknown>,
): Promise<SubmitResult> {
  return await postJson<SubmitResult>('/api/delegated-review/submit', payload)
}

export async function revokeDelegation(
  delegationNo: string,
  payload: Record<string, unknown>,
): Promise<SubmitResult> {
  return await postJson<SubmitResult>(
    `/api/delegated-review/${encodeURIComponent(delegationNo)}/revoke`,
    payload,
  )
}

export async function replaceDelegation(
  payload: Record<string, unknown>,
): Promise<SubmitResult> {
  return await postJson<SubmitResult>('/api/delegated-review/replace', payload)
}

export async function claimFormalParse(
  payload: Record<string, unknown>,
): Promise<ActionResult & { entry?: Record<string, unknown> }> {
  return await postJson('/api/delegated-review/claim', payload)
}

export async function loadReceipt(receiptNo: string): Promise<ReceiptVerify> {
  const response = await request(`/api/delegated-review/receipts/${encodeURIComponent(receiptNo)}`)
  if (!response.ok) throw new Error(`回执 ${receiptNo} 读取失败`)
  return (await response.json()) as ReceiptVerify
}
