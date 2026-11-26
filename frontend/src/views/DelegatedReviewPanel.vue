<template>
  <section class="delegated-panel" data-testid="delegated-review">
    <header class="delegated-head">
      <div>
        <h3>委托复核 · 表头判定</h3>
        <p class="page-desc">
          受托复核员按委托的样本范围提交表头判定；服务端先核验权限与样本，再签发可由结构嗅探与字段解析共同核对的回执。
        </p>
      </div>
      <form class="delegated-batch" @submit.prevent="loadForm">
        <label>
          <span>关联批次</span>
          <input
            v-model="batchId"
            data-testid="dr-batch"
            list="dr-batch-options"
            placeholder="如 FI-20260902001"
          />
          <datalist id="dr-batch-options">
            <option v-for="item in batchOptions" :key="item" :value="item" />
          </datalist>
        </label>
        <button class="btn" data-testid="dr-load" type="submit">读取授权与样本</button>
      </form>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="dr-error">{{ errorMessage }}</p>
    <p v-if="notice" class="notice-text" data-testid="dr-notice">{{ notice }}</p>

    <template v-if="form">
      <div class="delegated-grid">
        <article class="delegated-card" data-testid="dr-authorization">
          <h4>授权归属</h4>
          <dl>
            <dt>委托编号</dt>
            <dd>{{ form.授权归属.委托编号 || '—' }}</dd>
            <dt>委托人 / 受托人</dt>
            <dd>{{ form.授权归属.委托人标识 || '—' }} → {{ form.授权归属.受托人标识 || '—' }}</dd>
            <dt>来源渠道</dt>
            <dd>{{ form.授权归属.来源渠道 || '—' }}</dd>
            <dt>样本范围</dt>
            <dd>{{ form.授权归属.样本范围 || '—' }}</dd>
            <dt>授权状态</dt>
            <dd data-testid="dr-auth-status">{{ form.授权归属.授权状态 }}</dd>
            <dt>授权凭据</dt>
            <dd class="mono">{{ form.授权归属.授权凭据 || '—' }}</dd>
          </dl>
          <p v-if="form.授权归属.阻断原因" class="error-text">{{ form.授权归属.阻断原因 }}</p>
        </article>

        <article class="delegated-card" data-testid="dr-current-sample">
          <h4>当前样本</h4>
          <dl>
            <dt>样本</dt>
            <dd>{{ form.当前样本 || '—' }}</dd>
            <dt>受理复核编号</dt>
            <dd>{{ form.当前受理复核编号 || '—' }}</dd>
            <dt>起始行</dt>
            <dd>{{ form.当前起始行 ?? '—' }}</dd>
            <dt>受理资格</dt>
            <dd data-testid="dr-entitlement">
              {{ form.当前受理资格.可受理 ? `可领取正式解析（样本 ${form.当前受理资格.样本编号}）` : '不可领取' }}
            </dd>
          </dl>
          <p v-if="form.当前受理资格.阻断原因" class="error-text">
            {{ form.当前受理资格.阻断原因 }}
          </p>
          <p v-if="form.起始行冲突" class="conflict-text" data-testid="dr-conflict">
            起始行冲突：{{ form.起始行冲突.样本起始行 }}
          </p>
        </article>

        <article class="delegated-card" data-testid="dr-ledger">
          <h4>批次台账</h4>
          <dl>
            <dt>批次记录</dt>
            <dd>{{ form.批次台账.批次记录编号 || '—' }}</dd>
            <dt>解析起始行</dt>
            <dd>{{ form.批次台账.解析起始行 ?? '—' }}</dd>
            <dt>确认样本</dt>
            <dd>{{ form.批次台账.起始行确认样本 || '—' }}</dd>
            <dt>复核编号</dt>
            <dd>{{ form.批次台账.表头复核编号 || '—' }}</dd>
          </dl>
        </article>
      </div>

      <div v-if="revokedOrReplaced && form.操作选项.length" class="delegated-options" data-testid="dr-options">
        <p class="conflict-text">
          {{ form.授权归属.可提交 ? '历史判定已转只读，可按回执追溯或改选样本。' : '委托已撤销或替换：后续提交不再具备资格。可以选择下面的处理方式。' }}
        </p>
        <button
          v-for="option in form.操作选项"
          :key="option.操作"
          class="btn"
          type="button"
          :data-testid="option.操作 === '从只读凭据继续' ? 'dr-option-readonly' : 'dr-option-other-sample'"
          @click="chooseOption(option)"
        >
          {{ option.操作 }}
        </button>
        <p v-if="optionHint" class="page-desc" data-testid="dr-option-hint">{{ optionHint }}</p>
      </div>

      <form class="delegated-form" data-testid="dr-form" @submit.prevent="submit">
        <h4>提交表头判定</h4>
        <label>
          <span>样本</span>
          <select v-model="draft.sample_no" data-testid="dr-sample">
            <option v-for="sample in sampleChoices" :key="sample" :value="sample">{{ sample }}</option>
          </select>
        </label>
        <label>
          <span>受托人标识</span>
          <input v-model="draft.submitter" data-testid="dr-submitter" placeholder="如 yi" />
        </label>
        <label>
          <span>来源渠道</span>
          <input v-model="draft.source" data-testid="dr-source" placeholder="如 web-upload" />
        </label>
        <label>
          <span>委托编号（留空自动识别）</span>
          <input v-model="draft.delegation_no" data-testid="dr-delegation" placeholder="如 DG-001" />
        </label>
        <label>
          <span>授权凭据（可选）</span>
          <input v-model="draft.credential" data-testid="dr-credential" :placeholder="form.授权归属.授权凭据" />
        </label>
        <label>
          <span>表头判定</span>
          <select v-model="draft.header_present" data-testid="dr-header">
            <option value="有表头">有表头</option>
            <option value="无表头">无表头</option>
          </select>
        </label>
        <label>
          <span>起始行（留空按判定推导）</span>
          <input v-model="draft.start_row" data-testid="dr-start-row" inputmode="numeric" placeholder="如 5" />
        </label>
        <div class="page-actions">
          <button class="btn primary" data-testid="dr-submit" type="submit" :disabled="submitting">
            {{ submitting ? '提交中…' : '提交判定' }}
          </button>
        </div>
      </form>

      <div class="delegated-card" data-testid="dr-working-record">
        <h4>样本 {{ workingSample || form.当前样本 || '—' }} 的工作记录</h4>
        <table class="data-table">
          <thead>
            <tr><th>复核编号</th><th>样本</th><th>判定</th><th>起始行</th><th>受理结论</th><th>当前资格</th><th>回执</th><th>说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in workingRecords" :key="row.id" :data-testid="`dr-record-${row.复核编号}`">
              <td>{{ row.复核编号 }}</td>
              <td>{{ row.样本编号 }}</td>
              <td>{{ row.表头判定 }}</td>
              <td>{{ row.起始行 }}</td>
              <td>{{ row.受理结论 }}</td>
              <td>{{ row.当前受理资格 }}</td>
              <td>
                <button
                  v-if="row.回执编号"
                  class="link"
                  type="button"
                  :data-testid="`dr-open-receipt-${row.复核编号}`"
                  @click="openReceipt(row.回执编号)"
                >
                  打开回执
                </button>
                <span v-else>—</span>
              </td>
              <td>{{ row.拒绝原因 || row.资格失效原因 || row.冲突标记 || '—' }}</td>
            </tr>
            <tr v-if="!workingRecords.length">
              <td colspan="8" class="empty-state">该样本暂无提交记录</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="delegated-card" data-testid="dr-receipts">
        <h4>回执</h4>
        <table class="data-table">
          <thead>
            <tr><th>回执编号</th><th>样本</th><th>判定起始行</th><th>实际解析起始行</th><th>状态</th><th>核对结论</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="receipt in form.回执清单" :key="receipt.id" :data-testid="`dr-receipt-${receipt.回执编号}`">
              <td>{{ receipt.回执编号 }}</td>
              <td>{{ receipt.样本编号 }}</td>
              <td>{{ receipt.起始行 }}</td>
              <td>{{ receipt.实际解析起始行 }}</td>
              <td>{{ receipt.回执状态 }}</td>
              <td>{{ receipt.核对结论 }}</td>
              <td>
                <button class="link" type="button" @click="openReceipt(receipt.回执编号)">打开回执</button>
              </td>
            </tr>
            <tr v-if="!form.回执清单.length">
              <td colspan="7" class="empty-state">暂无回执</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="receiptDetail" class="delegated-card" data-testid="dr-receipt-detail">
        <h4>回执 {{ receiptDetail.回执编号 }}</h4>
        <dl>
          <dt>判定起始行</dt>
          <dd>{{ receiptDetail.回执.起始行 }}</dd>
          <dt>实际解析起始行</dt>
          <dd data-testid="dr-actual-start">{{ receiptDetail.回执.实际解析起始行 }}</dd>
          <dt>权限依据</dt>
          <dd>{{ receiptDetail.回执.委托编号 }} / {{ receiptDetail.回执.受托人标识 }} / 样本 {{ receiptDetail.回执.样本编号 }}</dd>
          <dt>回执状态</dt>
          <dd>{{ receiptDetail.回执.回执状态 }}{{ receiptDetail.回执.只读原因 ? `（${receiptDetail.回执.只读原因}）` : '' }}</dd>
          <dt>核对结论</dt>
          <dd>{{ receiptDetail.核对.核对结论 }}</dd>
        </dl>
      </div>

      <div class="delegated-form" data-testid="dr-claim-block">
        <h4>正式字段解析</h4>
        <label>
          <span>领取人标识</span>
          <input v-model="claimant" data-testid="dr-claimant" placeholder="如 jia" />
        </label>
        <div class="page-actions">
          <button class="btn" data-testid="dr-claim" type="button" @click="claim">领取正式解析</button>
        </div>
        <p class="page-desc">
          领取时按当前受理样本的判定从第 {{ form.当前受理资格.起始行 ?? '—' }} 行开始，同一样本重复批准不会多写第一条数据。
        </p>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import {
  claimFormalParse,
  loadReceipt,
  loadReviewForm,
  submitJudgment,
  type ReceiptVerify,
  type ReviewForm,
  type ReviewOption,
} from '@/api/delegated-review'

const props = withDefaults(
  defineProps<{ initialBatch?: string; batchOptions?: string[] }>(),
  { initialBatch: '', batchOptions: () => [] },
)

const batchId = ref(props.initialBatch)
const form = ref<ReviewForm | null>(null)
const errorMessage = ref('')
const notice = ref('')
const optionHint = ref('')
const submitting = ref(false)
const claimant = ref('')
const receiptDetail = ref<ReceiptVerify | null>(null)
const workingSample = ref('')
const draft = ref({
  sample_no: '',
  submitter: '',
  source: '',
  delegation_no: '',
  credential: '',
  header_present: '有表头',
  start_row: '',
})

const revokedOrReplaced = computed(() => {
  const auth = form.value?.授权归属
  if (!auth) return false
  const hasReadOnly = (form.value?.回执清单 ?? []).some(
    (receipt) => receipt.回执状态 === '只读',
  )
  return !auth.可提交 || hasReadOnly
})

const sampleChoices = computed(() => {
  const samples = form.value?.样本清单 ?? []
  return samples.length ? samples : ['S1']
})

const activeSample = computed(
  () => workingSample.value || draft.value.sample_no || form.value?.当前样本 || '',
)

const workingRecords = computed(() => {
  if (!form.value) return []
  const sample = activeSample.value
  return form.value.工作记录.filter(
    (row) => !sample || row.样本编号 === sample,
  )
})

function hydrate(payload: ReviewForm, preferredSample = '') {
  form.value = payload
  draft.value.source = payload.授权归属.来源渠道 || draft.value.source
  draft.value.submitter = payload.授权归属.受托人标识 || draft.value.submitter
  const sample = preferredSample || payload.当前样本 || sampleChoices.value[0]
  draft.value.sample_no = sample
  draft.value.delegation_no = payload.授权归属.委托编号 || ''
  workingSample.value = preferredSample || payload.当前样本 || ''
}

async function loadForm() {
  errorMessage.value = ''
  notice.value = ''
  optionHint.value = ''
  receiptDetail.value = null
  if (!batchId.value.trim()) {
    errorMessage.value = '请先填写关联批次'
    return
  }
  try {
    hydrate(await loadReviewForm(batchId.value.trim(), workingSample.value))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '表单读取失败'
  }
}

async function refreshForm(sampleNo = '') {
  if (!form.value) return
  try {
    hydrate(await loadReviewForm(form.value.关联批次, sampleNo), sampleNo)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '表单读取失败'
  }
}

function chooseOption(option: ReviewOption) {
  if (option.操作 === '从只读凭据继续') {
    if (option.回执编号) void openReceipt(String(option.回执编号))
    optionHint.value = option.说明
    return
  }
  const alternatives = (option.备选样本 ?? []).filter(
    (sample) => sample !== form.value?.当前受理资格.样本编号,
  )
  const next = alternatives[0] ?? sampleChoices.value[0]
  workingSample.value = next
  draft.value.sample_no = next
  draft.value.start_row = ''
  optionHint.value = `${option.说明}；已切换到样本 ${next}`
  void refreshForm(next)
}

async function openReceipt(receiptNo: string) {
  errorMessage.value = ''
  try {
    receiptDetail.value = await loadReceipt(receiptNo)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回执读取失败'
  }
}

async function submit() {
  if (!form.value) return
  submitting.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await submitJudgment({
      batch_id: form.value.关联批次,
      sample_no: draft.value.sample_no,
      submitter: draft.value.submitter,
      source: draft.value.source,
      delegation_no: draft.value.delegation_no,
      credential: draft.value.credential,
      header_present: draft.value.header_present,
      start_row: draft.value.start_row,
      submitted_at: new Date().toISOString(),
    })
    if (result.entry?.form) hydrate(result.entry.form, draft.value.sample_no)
    workingSample.value = draft.value.sample_no
    notice.value = result.message
    if (!result.ok) errorMessage.value = ''
    if (result.entry?.receipt) receiptDetail.value = result.entry.receipt
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '提交失败'
  } finally {
    submitting.value = false
  }
}

async function claim() {
  if (!form.value) return
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await claimFormalParse({
      batch_id: form.value.关联批次,
      claimant: claimant.value || draft.value.submitter,
      claimed_at: new Date().toISOString(),
    })
    notice.value = result.message
    if (!result.ok) errorMessage.value = result.message
    const entry = result.entry as { form?: ReviewForm } | undefined
    if (entry?.form) hydrate(entry.form)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '领取失败'
  }
}

watch(
  () => props.initialBatch,
  (value) => {
    if (value && value !== batchId.value) {
      batchId.value = value
      void loadForm()
    }
  },
)

onMounted(() => {
  if (batchId.value) void loadForm()
})
</script>

<style scoped>
.delegated-panel {
  margin: 16px 0 24px;
  padding: 16px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
}
.delegated-head { display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.delegated-head h3 { margin: 0; }
.delegated-batch { display: flex; gap: 8px; align-items: flex-end; }
.delegated-batch span { display: block; font-size: 12px; color: var(--muted); }
.delegated-batch input,
.delegated-form input,
.delegated-form select {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 13px;
}
.delegated-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin: 12px 0; }
.delegated-card { border: 1px solid var(--border); border-radius: 8px; padding: 12px; background: #fafbfc; }
.delegated-card h4 { margin: 0 0 8px; }
.delegated-card dl { display: grid; grid-template-columns: auto 1fr; gap: 4px 10px; margin: 0; }
.delegated-card dt { color: var(--muted); font-size: 12px; }
.delegated-card dd { margin: 0; font-size: 13px; }
.delegated-form { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; margin: 12px 0; padding: 12px; border: 1px dashed var(--border); border-radius: 8px; }
.delegated-form h4 { width: 100%; margin: 0; }
.delegated-form label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.delegated-options { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 8px 0; padding: 10px; border: 1px solid #f0c36d; border-radius: 8px; background: #fff8e6; }
.delegated-options p { width: 100%; margin: 0; }
.notice-text { color: #166534; font-size: 13px; }
.conflict-text { color: #b45309; font-size: 13px; }
.mono { font-family: monospace; }
</style>
