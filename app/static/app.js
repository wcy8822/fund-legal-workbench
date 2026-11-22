'use strict';

const app = document.getElementById('app');
const modal = document.getElementById('modal');
const modalContent = document.getElementById('modal-content');
const state = { projects: [], detail: null, projectId: null, tab: 'contracts', contractId: null, showArchived: false, search: '', contractSearch: '', issueFilter: 'all', issueScope: 'latest', diff: null, official: [], loadingRoute: 0 };
const researchByProject = new Map();
const RESEARCH_STATUS = { prepared: '已准备官方检索任务', completed: '检索已返回', no_results: '本次未找到结果', not_configured: '外部检索未配置', authentication_failed: '连接认证未通过', rate_limited: '达到服务限额', error: '检索未完成', unknown: '状态待核对' };
const RESEARCH_PURPOSE = { current_rules: '所选时点适用规则', recent_updates: '近期公布与更新（检索时近一个月）', as_of: '指定历史时点' };
const RESEARCH_KIND = { contract_version: '合同版本', review_issue: '审核意见', recorded_source: '已录入法源', adopted_expression: '已采纳表达' };
const EVIDENCE_STATUS = { project_record: '项目记录', recorded_source_snapshot: '已录入来源快照 · 本次未重新核验', historical_adoption_record: '历史人工采用记录', unverified_search_excerpt: '搜索摘要 · 待核对原文' };
const CONNECTION_STATUS = { candidate_not_connected: '候选 · 未连接', not_authorized: '未授权', manual_only: '仅手工查询', configured_unverified: '已配置 · 未验证', disabled: '未启用', missing_api_key: '缺少服务端密钥', invalid_api_key: '服务端密钥格式无效', invalid_mode: '配置模式无效' };
const KIND = { initial: '初稿', counterparty: '对方来稿', revision: '修订稿', final: '定稿', signed: '签署件' };
const STATUS = { open: '待处理', in_review: '处理中', resolved: '已确认关闭', closed: '已确认关闭', carried: '带入待复核' };
const CATEGORY = { law: '法律法规', legislation: '法律法规', regulation: '法律法规', judicial_interpretation: '司法解释', regulatory_rule: '监管规则', self_regulatory_rule: '自律规则', guiding_case: '指导性案例', reference_case: '参考案例', judgment: '普通裁判', contract: '合同约定', other: '其他资料' };
const E = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const textValue = value => typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value);
const date = value => value ? String(value).replace('T', ' ').slice(0, 16) : '未记录';
const shortDate = value => value ? String(value).slice(0, 10) : '未记录';
const safeURL = value => { try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) ? u.href : null; } catch (_) { return null; } };
const badge = (label, tone = '') => `<span class="badge ${E(tone)}">${E(label)}</span>`;
const isClosed = issue => ['resolved', 'closed'].includes(issue.status);
const latestIssues = () => {
  const latest = new Map();
  for (const issue of state.detail?.issues || []) {
    const root = issue.root_id || issue.id;
    const previous = latest.get(root);
    if (!previous || String(issue.created_at).localeCompare(String(previous.created_at)) >= 0) latest.set(root, issue);
  }
  return [...latest.values()];
};
const outstandingIssues = () => state.detail?.outstanding_issues || latestIssues().filter(i => !isClosed(i));
const contractOpenCount = cid => {
  const contract = (state.detail?.contracts || []).find(c => String(c.id) === String(cid));
  return contract?.open_issue_count ?? outstandingIssues().filter(i => String(i.contract_id) === String(cid)).length;
};
const isApproved = item => {
  if (typeof item.approval_valid === 'boolean') return item.approval_valid;
  const contract = (state.detail?.contracts || []).find(c => String(c.id) === String(item.contract_id));
  return item.status === 'approved' && !contract?.archived && !state.detail?.project?.archived && String(contract?.current_version_id) === String(item.version_id) && item.approved_revision === state.detail?.project?.review_revision;
};
const allVersions = () => (state.detail?.contracts || []).flatMap(c => (c.versions || []).map(v => ({ ...v, contract_title: c.title, contract_id: c.id })));
const selectedContract = () => (state.detail?.contracts || []).find(c => String(c.id) === String(state.contractId));
const currentVersion = contract => (contract?.versions || []).find(v => String(v.id) === String(contract.current_version_id)) || (contract?.versions || []).at(-1);
const projectPath = () => `/api/projects/${encodeURIComponent(state.projectId)}`;

async function api(path, options = {}) {
  const init = { ...options, headers: { ...(options.headers || {}) } };
  if (init.body && !(init.body instanceof FormData)) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(init.body);
  }
  const response = await fetch(path, init);
  let result;
  try { result = await response.json(); } catch (_) { result = null; }
  if (!response.ok) {
    const detail = result?.detail || result?.message || `请求未完成（${response.status}）`;
    const error = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    error.status = response.status;
    throw error;
  }
  return result;
}
let toastTimer;
function toast(message, error = false) {
  const box = document.getElementById('toast');
  box.textContent = message;
  box.className = `toast visible${error ? ' error' : ''}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.className = 'toast'; }, error ? 8000 : 4200);
}
function loading() { app.innerHTML = '<div class="loading"><span class="spinner"></span>正在读取项目资料…</div>'; }
async function action(fn, button, success) {
  if (button?.disabled) return;
  if (button) button.disabled = true;
  try { await fn(); if (success) toast(success); }
  catch (error) { toast(error.message, true); }
  finally { if (button?.isConnected) button.disabled = false; }
}
async function refresh() {
  state.detail = await api(projectPath());
  const visible = (state.detail.contracts || []).filter(c => state.showArchived || !c.archived);
  if (!visible.some(c => String(c.id) === String(state.contractId))) state.contractId = visible[0]?.id ?? null;
  renderProject();
}
async function route() {
  const request = ++state.loadingRoute;
  const parts = location.hash.replace(/^#\/?/, '').split('/');
  if (parts[0] === 'project' && parts[1]) {
    const id = decodeURIComponent(parts[1]);
    if (id !== state.projectId) { state.contractId = null; state.diff = null; state.contractSearch = ''; }
    state.projectId = id;
    state.tab = ['contracts', 'issues', 'sources', 'deliverables'].includes(parts[2]) ? parts[2] : 'contracts';
    loading();
    try {
      const detail = await api(projectPath());
      if (request !== state.loadingRoute) return;
      state.detail = detail;
      const visible = (detail.contracts || []).filter(c => state.showArchived || !c.archived);
      if (!visible.some(c => String(c.id) === String(state.contractId))) state.contractId = visible[0]?.id ?? null;
      renderProject();
      if (state.tab === 'sources' && !state.official.length) loadOfficial();
    } catch (error) { if (request === state.loadingRoute) renderError(error); }
  } else {
    state.projectId = null; state.detail = null; loading();
    try {
      const result = await api('/api/projects');
      if (request !== state.loadingRoute) return;
      state.projects = Array.isArray(result) ? result : result.projects || [];
      renderProjects();
    } catch (error) { if (request === state.loadingRoute) renderError(error); }
  }
}
function renderError(error) {
  app.innerHTML = `<div class="empty full"><div class="empty-icon">!</div><h3>暂时没有打开工作台</h3><p>${E(error.message)}</p><button data-action="retry">重新读取</button></div>`;
}
function renderProjects() {
  const projects = state.projects.filter(p => !p.archived && `${p.name} ${p.description || ''}`.toLowerCase().includes(state.search.toLowerCase()));
  app.innerHTML = `
    <section class="page-heading"><div><div class="eyebrow">INVESTMENT LEGAL WORKSPACE</div><h1>投资项目</h1><p>从一份初稿到可交付的版本，让每次审查都有据可循。</p></div><div class="heading-actions"><button data-action="new-project">＋ 新建项目</button><button class="primary" data-action="create-demo">体验虚构投资项目 <span aria-hidden="true">↗</span></button></div></section>
    <section class="intro-strip"><div class="intro-copy"><div class="intro-label">01</div><div><h2>沿用 Word 习惯，把版本和意见留在项目里</h2><p>上传原件 → 查看两版差异 → 逐条记录意见 → 人工批准交付。无需配置 AI 即可演示。</p></div></div><span class="badge copper">真实合同试点前需另行验收</span></section>
    <div class="toolbar"><h2>全部项目 <span>${projects.length} 个项目</span></h2><div class="toolbar-actions"><label class="sr-only" for="project-search">查找项目</label><input id="project-search" class="search" type="search" placeholder="按项目名称查找" value="${E(state.search)}"></div></div>
    ${projects.length ? `<div class="project-grid">${projects.map(p => `<a class="project-card" href="#project/${encodeURIComponent(p.id)}/contracts"><div class="card-top"><span class="project-icon" aria-hidden="true">▤</span>${badge(p.jurisdiction === 'CN' ? '中国大陆' : p.jurisdiction || '待设置', 'gray')}</div><h3>${E(p.name)}</h3><p>${E(p.description || '合同、审核意见、法源与交付物统一留存。')}</p><div class="card-bottom"><span>判断时点 ${E(shortDate(p.as_of))}</span><span class="card-arrow" aria-hidden="true">↗</span></div></a>`).join('')}</div>` : `<div class="empty full"><div class="empty-icon">▤</div><h3>${state.search ? '没有匹配的项目' : '从第一个投资项目开始'}</h3><p>${state.search ? '试试其他项目名称。' : '创建空项目，或使用虚构资料走完“初稿—来稿—修订—交付”的完整流程。'}</p>${state.search ? '' : '<button data-action="create-demo" class="primary">打开虚构演示</button>'}</div>`}`;
  const search = document.getElementById('project-search');
  search?.addEventListener('input', () => {
    const start = search.selectionStart;
    state.search = search.value; renderProjects();
    const updated = document.getElementById('project-search'); updated.focus();
    try { updated.setSelectionRange(start, start); } catch (_) { /* Search inputs vary across browsers. */ }
  });
}
function journey() {
  const contracts = state.detail.contracts || [];
  const contract = selectedContract() || contracts[0];
  if (!contract || !state.detail.project.name.includes('虚构') || !contract.title.includes('演示数据')) return '';
  const versions = contract.versions || [];
  const count = versions.length;
  const issues = state.detail.issues || [];
  const deliveries = state.detail.deliverables || [];
  const step = deliveries.some(isApproved) ? 6 : deliveries.length ? 5 : count >= 3 ? 4 : issues.length ? 3 : count >= 2 ? 2 : 1;
  const labels = ['上传初稿', '查看差异', '记录意见', '复核新版', '生成交付', '人工批准'];
  let actions = count < 2 ? '<button class="small" data-action="demo-version" data-stage="2">载入对方版 →</button>' : !issues.length ? '<button class="small" data-action="analyze">提取演示建议 →</button>' : count < 3 ? '<button class="small" data-action="demo-version" data-stage="3">载入修订稿 →</button>' : state.tab === 'issues' ? '<button class="small" data-action="go-deliverables">准备生成交付 →</button>' : '<button class="small" data-action="verify-new">核对遗留问题 →</button>';
  if (deliveries.length && !deliveries.some(isApproved)) actions = '<button class="small" data-action="go-deliverables">查看交付与批准 →</button>';
  if (deliveries.some(isApproved)) actions = badge('演示已走通 · 意见继续保留', 'green');
  return `<div class="journey"><span class="journey-label">演示路径</span><div class="journey-steps">${labels.map((label, i) => `<span class="journey-step ${i + 1 < step ? 'done' : i + 1 === step ? 'current' : ''}"><span class="step-dot">${i + 1 < step ? '✓' : i + 1}</span>${label}</span>`).join('')}</div>${actions}</div>`;
}
function renderProject() {
  const { project, contracts = [], issues = [], sources = [], deliverables = [] } = state.detail;
  const tabs = [['contracts', '合同', contracts.filter(c => !c.archived).length], ['issues', '审核意见', outstandingIssues().length], ['sources', '法源', sources.length], ['deliverables', '交付物', deliverables.length]];
  const body = { contracts: renderContracts, issues: renderIssues, sources: renderSources, deliverables: renderDeliverables }[state.tab]();
  app.innerHTML = `<div class="breadcrumb"><a href="#projects">投资项目</a><span>/</span>${E(project.name)}</div>
    <section class="page-heading mb0"><div><div class="eyebrow">PROJECT WORKSPACE</div><h1>${E(project.name)}</h1><p>${E(project.description || '按合同保留完整版本、审查依据和交付记录。')}</p><div class="project-meta"><span>${E(project.jurisdiction === 'CN' ? '中国大陆' : project.jurisdiction)}</span><span>判断时点 ${E(shortDate(project.as_of))}</span><span>本地资料 · 人工确认</span></div></div><div class="heading-actions"><button data-action="new-contract">＋ 新增合同</button></div></section>
    <nav class="project-tabs" aria-label="项目内容">${tabs.map(([key, label, count]) => `<a class="tab ${state.tab === key ? 'active' : ''}" ${state.tab === key ? 'aria-current="page"' : ''} href="#project/${encodeURIComponent(project.id)}/${key}">${label}<span class="tab-count">${count}</span></a>`).join('')}</nav>
    ${journey()}${body}`;
}
function renderContracts() {
  const contracts = (state.detail.contracts || []).filter(c => (state.showArchived || !c.archived) && `${c.title} ${c.counterparty || ''}`.toLowerCase().includes(state.contractSearch.toLowerCase()));
  if (state.contractSearch && !contracts.some(c => String(c.id) === String(state.contractId))) state.contractId = contracts[0]?.id ?? null;
  const contract = selectedContract();
  return `<div class="contract-layout"><aside class="panel contracts-panel"><div class="panel-head"><h2>项目合同</h2><button class="ghost small" data-action="new-contract" aria-label="新增合同">＋</button></div><div class="contract-search"><label class="sr-only" for="contract-search">查询合同台账</label><input id="contract-search" type="search" placeholder="查找合同 / 交易对方" value="${E(state.contractSearch)}"></div><div class="contract-list">${contracts.length ? contracts.map(c => `<button class="contract-item ${String(c.id) === String(state.contractId) ? 'selected' : ''}" data-action="select-contract" data-id="${E(c.id)}"><h3>${E(c.title)}</h3><p>${E(c.counterparty || '交易对方待补充')}</p><div class="meta-row"><span class="muted small-text">${c.versions?.length || 0} 个版本</span>${badge(c.archived ? '已归档' : currentVersion(c) ? `当前 V${currentVersion(c).number}` : '待上传', c.archived ? 'gray' : 'green')}</div></button>`).join('') : `<div class="empty"><p>${state.contractSearch ? '没有匹配的合同' : '尚未加入合同'}</p></div>`}</div><div class="contract-list-footer"><label class="checkbox-row"><input type="checkbox" id="show-archived" ${state.showArchived ? 'checked' : ''}>显示已归档合同</label></div></aside><section>${contract ? renderContract(contract) : `<div class="empty full"><div class="empty-icon">＋</div><h3>先建立一份合同台账</h3><p>输入合同名称后，上传 DOCX、PDF 或 Markdown 原件。正文修改会形成新版本。</p><button class="primary" data-action="new-contract">新增合同</button></div>`}</section></div>`;
}
function renderContract(contract) {
  const versions = [...(contract.versions || [])].sort((a, b) => a.number - b.number);
  const current = currentVersion(contract);
  const diff = state.diff && String(state.diff.contractId) === String(contract.id) ? state.diff : null;
  const versionOption = (v, selected) => `<option value="${E(v.id)}" ${String(v.id) === String(selected) ? 'selected' : ''}>V${E(v.number)} · ${E(KIND[v.kind] || v.kind)} · ${E(v.filename)}</option>`;
  return `<div class="panel"><div class="contract-top"><div><h2>${E(contract.title)}</h2><div class="manual-tags"><span class="muted small-text">${E(contract.counterparty || '交易对方待补充')}</span>${current ? badge(`当前 V${current.number} · ${KIND[current.kind] || current.kind}`, 'green') : ''}</div></div><div class="actions"><button class="small" data-action="edit-contract">台账</button><button class="primary small" data-action="upload-version" ${contract.archived ? 'disabled' : ''}>↑ 上传新版本</button></div></div><div class="panel-body">${contract.archived ? '<div class="notice warning archived-note">此合同已归档，原件与审查记录继续保留。恢复后可继续上传新版本。</div>' : ''}<div class="notice">在 Word 中继续编辑，再上传新版本。原件不会被覆盖；文本对比用于定位变化，不重建 Word 修订痕迹。</div>${current?.warnings?.length ? `<div class="notice warning mt12">本版解析需注意：<br>${current.warnings.map(warning => E(warning)).join('<br>')}</div>` : ''}<div class="section-label"><h3>版本记录</h3><span class="small-text muted">原件与历史长期保留</span></div>
    ${versions.length ? `<table class="version-list"><thead><tr><th>版本</th><th>原文件</th><th>解析状态</th><th>上传时间</th><th><span class="sr-only">操作</span></th></tr></thead><tbody>${[...versions].reverse().map(v => `<tr><td><div class="version-number">V${E(v.number)}</div>${String(v.id) === String(contract.current_version_id) ? badge('当前', 'green') : ''}</td><td><div class="version-file">${E(v.filename)}</div><div>${badge(KIND[v.kind] || v.kind, v.kind === 'signed' ? 'copper' : 'gray')} <span class="hash" title="SHA-256: ${E(v.sha256)}">${E((v.sha256 || '').slice(0, 12))}</span></div></td><td>${parseBadge(v.parse_status)}</td><td><span class="muted">${E(date(v.created_at))}</span></td><td><div class="version-actions"><a class="button small" href="${projectPath()}/versions/${encodeURIComponent(v.id)}/original" download>取回原件</a>${String(v.id) !== String(contract.current_version_id) ? `<button class="ghost small" data-action="set-current" data-id="${E(v.id)}">设为当前</button>` : ''}</div></td></tr>`).join('')}</tbody></table>` : '<div class="empty"><p>还没有版本。上传初稿，即可开始留下每一轮谈判的记录。</p></div>'}
    ${versions.length >= 2 ? `<div class="section-label"><h3>两版文本差异</h3><span class="small-text muted">可自行选择任意两版</span></div><div class="compare-controls"><label class="sr-only" for="diff-left">旧版本</label><select id="diff-left">${versions.map(v => versionOption(v, diff?.leftId || versions.at(-2).id)).join('')}</select><span>→</span><label class="sr-only" for="diff-right">新版本</label><select id="diff-right">${versions.map(v => versionOption(v, diff?.rightId || versions.at(-1).id)).join('')}</select><button class="small" data-action="compare">查看差异</button></div>${diff ? renderDiff(diff.data) : '<div class="diff-empty small-text">选择两版并查看变化。未解析的扫描件需先完成 OCR。</div>'}` : ''}
    ${current?.text ? `<details><summary>查看当前版提取文字 · V${E(current.number)}（用于定位，不替代原件）</summary><div class="approval-preview mt12" tabindex="0">${E(current.text)}</div></details>` : ''}
    <div class="sample-links"><span class="muted">虚构样例：</span><a href="/api/demo/samples/1.docx" download>初稿 DOCX</a><a href="/api/demo/samples/2.docx" download>对方版 DOCX</a><a href="/api/demo/samples/3.docx" download>修订稿 DOCX</a><a href="/api/demo/samples/3.pdf" download>PDF</a><a href="/api/demo/samples/3.md" download>Markdown</a></div>
    ${renderAudit()}</div></div>`;
}
function parseBadge(status) {
  if (['needs_ocr', 'pending_ocr', 'ocr_required'].includes(status)) return badge('待 OCR', 'copper');
  if (['parsed', 'ok', 'text_extracted'].includes(status)) return badge('文本已提取', 'green');
  if (['needs_review', 'unsupported', 'failed', 'parse_error'].includes(status)) return badge('待人工处理', 'copper');
  return badge(status || '待解析', 'gray');
}
function renderDiff(data) {
  const summaries = Array.isArray(data.summary) ? data.summary : data.summary ? [data.summary] : [];
  const lines = data.lines || [];
  return `${summaries.length ? `<div class="diff-summary">${summaries.map(s => `<div>${E(textValue(s))}</div>`).join('')}</div>` : ''}<div class="diff-legend"><span><i class="legend-square legend-added"></i>新版新增</span><span><i class="legend-square legend-removed"></i>旧版删除</span></div><div class="diff-box" aria-label="合同文本差异">${lines.length ? lines.map(line => `<div class="diff-line ${['add', 'remove', 'equal'].includes(line.type) ? line.type : 'equal'}"><span class="diff-sign" aria-hidden="true">${line.type === 'add' ? '+' : line.type === 'remove' ? '−' : ' '}</span><span>${E(line.text)}</span></div>`).join('') : '<div class="diff-empty">未检测到文本差异。</div>'}</div>`;
}
function renderAudit() {
  const events = [...(state.detail.audit || [])].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 20);
  return `<details><summary>操作记录 · ${state.detail.audit?.length || 0} 条</summary>${events.length ? events.map(item => `<div class="audit-row"><time>${E(date(item.created_at || item.timestamp))}</time><span>${E(item.action || item.event)}${item.actor ? ` · ${E(item.actor)}` : ''}${item.detail ? `<br><span class="muted">${E(textValue(item.detail))}</span>` : ''}</span></div>`).join('') : '<p class="small-text muted">操作完成后将在这里留存记录。</p>'}</details>`;
}
function renderIssues() {
  const latest = latestIssues();
  const issues = state.issueScope === 'history' ? (state.detail.issues || []) : latest;
  const open = outstandingIssues();
  const shown = state.issueFilter === 'all' ? issues : state.issueFilter === 'open' ? issues.filter(i => !isClosed(i)) : issues.filter(isClosed);
  return `<div class="metric-row"><div class="metric"><div class="metric-label">仍待人工确认</div><div class="metric-value copper">${open.length}</div></div><div class="metric"><div class="metric-label">已确认关闭</div><div class="metric-value">${latest.filter(isClosed).length}</div></div><div class="metric"><div class="metric-label">当前外部模型</div><div class="metric-value metric-word">未启用</div></div></div>
    <div class="toolbar"><h2>逐条意见 <span>保留每个问题的处理历史</span></h2><div class="toolbar-actions"><label class="sr-only" for="issue-scope">记录范围</label><select id="issue-scope"><option value="latest" ${state.issueScope === 'latest' ? 'selected' : ''}>最新处理记录</option><option value="history" ${state.issueScope === 'history' ? 'selected' : ''}>全部版本历史</option></select><label class="sr-only" for="issue-contract">提取建议使用的合同</label><select id="issue-contract" class="issue-contract-select">${(state.detail.contracts || []).filter(c => !c.archived).map(c => `<option value="${E(c.id)}" ${String(c.id) === String(state.contractId) ? 'selected' : ''}>${E(c.title)} · 当前版</option>`).join('')}</select><label class="sr-only" for="issue-filter">意见状态筛选</label><select id="issue-filter"><option value="all" ${state.issueFilter === 'all' ? 'selected' : ''}>全部状态</option><option value="open" ${state.issueFilter === 'open' ? 'selected' : ''}>待处理 / 处理中</option><option value="resolved" ${state.issueFilter === 'resolved' ? 'selected' : ''}>已确认关闭</option></select><button data-action="new-issue">＋ 手工记录</button><button class="primary" data-action="analyze" ${!currentVersion(selectedContract()) ? 'disabled' : ''}>提取演示建议</button></div></div><div class="notice mb20">演示提取仅识别虚构样例中的预设问题。AI 草稿、带入的旧意见都需法务复核；上传新版不会自动结案。默认展示每个问题的最新处理记录，可切换查看全部版本历史。</div>
    <div class="issue-list">${shown.length ? shown.map((issue, i) => renderIssue(issue, i)).join('') : `<div class="empty full"><div class="empty-icon">≋</div><h3>${issues.length ? '这一状态下暂无意见' : '把审查结论放回具体条款'}</h3><p>手工记录问题，或载入对方版后提取演示建议。每条意见保留原文、风险、建议、待确认事项和依据。</p></div>`}</div>`;
}
function sourceTimingHTML(source, projectAsOf) {
  const sourceAsOf = source?.as_of;
  const differs = sourceAsOf && projectAsOf && String(sourceAsOf).slice(0, 10) !== String(projectAsOf).slice(0, 10);
  return `<p class="small-text muted">项目判断时点：${E(shortDate(projectAsOf))}<br>法源记录判断时点：${E(shortDate(sourceAsOf))}<br>来源核验时间：${E(date(source?.verified_at))}</p>${differs ? '<div class="notice warning">法源记录与项目的判断时点不同，须复核该依据对本项目的适用性。</div>' : ''}`;
}
function renderIssue(issue, index) {
  const version = allVersions().find(v => String(v.id) === String(issue.version_id));
  const originVersion = allVersions().find(v => String(v.id) === String(issue.origin_version_id));
  const contract = (state.detail.contracts || []).find(c => String(c.id) === String(issue.contract_id));
  const historical = contract && String(contract.current_version_id) !== String(issue.version_id);
  const source = (state.detail.sources || []).find(s => String(s.id) === String(issue.basis_source_id));
  const sourceURL = safeURL(source?.url);
  const carried = issue.carried_from_id || issue.carried_from || issue.origin_issue_id;
  const anchor = { exact: '本版条款可定位', changed: '条款已变化，须重新核对', missing: '本版未定位到原条款' }[issue.anchor_status] || '条款定位待核对';
  const quote = issue.current_quote || (!carried ? issue.quote : '') || '本版未定位到对应条款，请人工复核。';
  return `<article class="issue-card"><div class="issue-head"><div class="issue-index">${String(index + 1).padStart(2, '0')}</div><div class="issue-title"><h3>${E(issue.title || issue.risk || '待复核事项')}</h3><p>${E(version?.contract_title || '合同')} · ${version ? `V${E(version.number)}` : E(issue.version_id)} · ${E(issue.clause || '条款待定位')}${historical ? ' · 历史版本遗留，须核对当前稿' : ''}</p></div>${badge(STATUS[issue.status] || issue.status || '待处理', isClosed(issue) ? 'green' : 'copper')}</div><div class="issue-body"><div><h4>本版条款原文</h4><div class="anchor-notice">${E(anchor)}</div><div class="quote">${E(quote)}</div>${carried ? `<details class="issue-history"><summary>查看原意见引文 · ${originVersion ? `V${E(originVersion.number)}` : '原始版本'}（未覆盖）</summary><div class="quote">${E(issue.quote)}</div></details>` : ''}<h4>建议</h4><p>${E(issue.suggestion || '待填写')}</p><h4>待确认事项</h4><p class="mb0">${E(issue.pending || '需由法务复核后明确')}</p></div><div class="issue-basis"><h4>依据与出处</h4>${source ? `${sourceURL ? `<a href="${E(sourceURL)}" target="_blank" rel="noopener noreferrer">${E(source.title)} ↗</a>` : `<p>${E(source.title)}</p>`}<p>${E(CATEGORY[source.category] || source.category || '资料')} · ${E(source.effect_status || '效力待核验')}</p><div class="quote">${E(issue.basis_quote || source.citation || '未录入引文')}</div>${sourceTimingHTML(source, state.detail.project.as_of)}` : '<p>合同约定 / 人工依据说明</p>'}${issue.basis_note ? `<div class="source-note">${E(issue.basis_note)}</div>` : !source ? '<p>尚未补充具体依据，需人工核验。</p>' : ''}${issue.resolution_note ? `<h4 class="mt12">人工结案说明</h4><p>${E(issue.resolution_note)}</p>` : ''}</div></div>${renderAdoptedExpressions(issue)}<div class="issue-footer"><small>${carried ? '由旧版带入，原意见保留；条款变更不等于问题已关闭。' : '原文与依据均需人工复核。'}${issue.ai_generated || issue.provider || issue.generated_by || issue.origin === 'demo_rule_draft' ? ' 演示建议草稿。' : ''}</small><div class="actions"><button class="small" data-action="rewrite-issue" data-id="${E(issue.id)}">改写 / 查看表达</button>${!isClosed(issue) ? `<button class="small" data-action="issue-status" data-id="${E(issue.id)}" data-status="in_review">标记处理中</button><button class="small" data-action="issue-status" data-id="${E(issue.id)}" data-status="resolved">人工确认关闭</button>` : `<button class="small" data-action="issue-status" data-id="${E(issue.id)}" data-status="open">重新打开</button>`}</div></div></article>`;
}
async function loadOfficial() {
  try {
    const result = await api('/api/sources/official');
    state.official = Array.isArray(result) ? result : result.sources || result.portals || result.official || [];
    if (state.tab === 'sources' && state.detail) renderProject();
  } catch (error) { toast(error.message, true); }
}
function renderSources() {
  const sources = state.detail.sources || [];
  return `${renderResearch()}<div class="toolbar"><h2>项目引用资料 <span>${sources.length} 条</span></h2><button data-action="new-source">＋ 手工录入法源</button></div><div class="source-grid"><section class="panel">${sources.length ? sources.map(s => { const url = safeURL(s.url); return `<article class="source-card"><div class="manual-tags">${badge(CATEGORY[s.category] || s.category || '资料', 'gray')}${badge(s.effect_status || '效力待核验', s.effect_status === '现行有效' ? 'green' : 'copper')}${s.is_demo || s.demo ? badge('演示数据', 'copper') : ''}</div><h3>${url ? `<a href="${E(url)}" target="_blank" rel="noopener noreferrer">${E(s.title)} ↗</a>` : E(s.title)}</h3><div class="source-quote preserve-wrap">${E(s.citation || s.quote || '尚未录入原文引文')}</div><div class="source-metadata"><span>法域：${E(s.jurisdiction === 'CN' ? '中国大陆' : s.jurisdiction || '待填')}</span><span>判断时点：${E(shortDate(s.as_of || state.detail.project.as_of))}</span><span>公布：${E(shortDate(s.published_at))}</span><span>生效：${E(shortDate(s.effective_at))}</span><span>核验：${E(date(s.verified_at))}</span><span>资料编号：${E(s.id)}</span></div><p>${E(s.verification_note || '使用前请人工核对适用范围与时效。')}</p></article>`; }).join('') : '<div class="empty"><div class="empty-icon">§</div><h3>为判断保留可追溯的依据</h3><p>在官方来源查阅后，录入原文链接、引文与效力信息。案例与规范文件需分别标明类型。</p></div>'}</section><aside><div class="panel"><div class="panel-head"><h2>官方来源入口</h2>${badge('仅跳转', 'gray')}</div>${state.official.length ? state.official.map(s => { const url = safeURL(s.url || s.homepage); return `<a class="official-item" ${url ? `href="${E(url)}" target="_blank" rel="noopener noreferrer"` : ''}><span aria-hidden="true">↗</span><strong>${E(s.name || s.title)}</strong><small>${E(s.description || s.note || s.api_status || s.access_note || '请按该平台的访问规则检索与核验。')}</small></a>`; }).join('') : '<div class="panel-body small-text muted">正在读取来源入口…</div>'}</div><div class="notice mt18">法律法规、司法解释、监管与自律规则、指导性案例、参考案例、普通裁判分别记录。公开裁判文书不等于公开案卷。</div><div class="notice mt12">北大法宝等商用数据接口暂未接入。覆盖范围、授权、额度和更新机制须完成供应商核实后才能启用。</div></aside></div>`;
}

function researchState(projectId = state.projectId) {
  if (!researchByProject.has(projectId)) researchByProject.set(projectId, { capabilities: null, runs: [], selectedId: null, loaded: false, loading: false, error: '' });
  return researchByProject.get(projectId);
}
const researchPath = projectId => `/api/projects/${encodeURIComponent(projectId)}/research`;
function renderResearchIfVisible(projectId) {
  if (state.projectId === projectId && state.tab === 'sources' && state.detail) renderProject();
}
function rememberResearchRun(projectId, run) {
  const session = researchState(projectId);
  session.runs = [run, ...session.runs.filter(item => String(item.id) !== String(run.id))].sort((a, b) => String(b.started_at).localeCompare(String(a.started_at)));
  session.selectedId = run.id;
  session.error = '';
  renderResearchIfVisible(projectId);
}
function renderResearch() {
  const session = researchState();
  const cap = session.capabilities;
  const run = session.runs.find(item => String(item.id) === String(session.selectedId));
  const connectionLabel = !cap ? '尚未读取连接状态' : cap.public_search?.available ? '已配置公开检索 · 尚未验证连通性' : '公开检索使用官方跳转';
  return `<section class="panel research-workspace" aria-labelledby="research-title">
    <div class="research-head"><div><div class="eyebrow">RESEARCH & EVIDENCE</div><h2 id="research-title">找到依据，保留核对过程</h2><p>项目资料在本地查找；公开法源使用另行填写的脱敏问题。</p></div><button class="small" data-action="research-capabilities">连接与范围</button></div>
    <div class="research-entry-grid"><div class="research-entry"><span class="research-scope">本项目 · 本地</span><h3>找回合同、问题与已有判断</h3><p>查询已上传版本、审核意见、已录入法源和已采纳表达。</p><button data-action="research-local">查当前项目资料</button></div><div class="research-entry"><span class="research-scope">公开资料 · 按时点</span><h3>核对现行规则与最新公布</h3><p>先找候选原文，再人工核对条款、适用范围和效力。</p><button class="primary" data-action="research-public">查公开法源</button></div></div>
    <div class="research-connection"><span>${E(connectionLabel)}</span><span>公司系统与整台电脑未接入</span></div>
    <div class="research-history"><div class="research-history-head"><div><h3>检索记录</h3><p>读取记录不会重新发起联网检索。</p></div><button class="small" data-action="research-refresh" ${session.loading ? 'disabled' : ''}>${session.loading ? '正在读取…' : session.loaded ? '刷新记录' : '读取已有记录'}</button></div>
      ${session.error ? `<div class="notice danger">${E(session.error)}</div>` : ''}
      ${session.runs.length ? `<div class="field research-run-picker"><label for="research-run">选择本项目的一次检索</label><select id="research-run">${session.runs.map(item => `<option value="${E(item.id)}" ${String(item.id) === String(session.selectedId) ? 'selected' : ''}>${E(date(item.started_at))} · ${item.scope === 'public' ? '公开' : '本项目'} · ${E(item.query)} · ${E(RESEARCH_STATUS[item.status] || item.status)}</option>`).join('')}</select></div>${run ? renderResearchRun(run) : ''}` : `<p class="research-empty">${session.loaded ? '当前项目还没有检索记录。可以从上方两个入口开始。' : '本次页面尚未读取已有记录。查询结果将保留在对应项目中。'}</p>`}
    </div></section>`;
}
function renderResearchRun(run) {
  const isPublic = run.scope === 'public';
  const parameters = run.parameters || {};
  const candidates = run.results || [];
  const portals = run.official_portals || [];
  const transmission = run.sent_externally === true ? '已尝试外部请求（不证明服务方收到）' : run.sent_externally === false ? '本次未尝试外部请求' : '外部发送状态未知，请核对服务记录';
  const emptyMessages = { prepared: '检索任务已准备，可打开下方官方入口查询，再录入实际核对的原文。', not_configured: '外部检索未配置。请通过官方入口查询并留存原文依据。', no_results: '本次查询未返回匹配结果。可调整问题或转到官方来源继续核对；不能据此判断规定或案例不存在。', authentication_failed: '服务认证未通过，本次未取得候选结果。仍可使用官方入口。', rate_limited: '服务限额阻止了本次检索。可稍后手动重试，或使用官方入口。', error: '本次检索未完成。请查看记录中的提示，或转到官方入口。' };
  return `<article class="research-run ${isPublic ? 'research-public' : 'research-local'}">
    <div class="research-run-title"><div class="manual-tags">${badge(isPublic ? '公开法源检索' : '当前项目资料', isPublic ? 'copper' : 'gray')}${badge(RESEARCH_STATUS[run.status] || run.status || '状态待核对', run.status === 'completed' ? 'green' : 'gray')}</div>${isPublic ? `<button class="small" data-action="research-copy" data-id="${E(run.id)}">复制脱敏问题</button>` : ''}</div>
    <p class="research-question preserve-wrap">${E(run.query)}</p><div class="research-metadata"><span>检索渠道：${E(run.provider || '未记录')}</span><span>检索开始（UTC）：${E(date(run.started_at))}</span><span>检索完成（UTC）：${E(date(run.completed_at))}</span>${isPublic ? `<span>法域：${E(parameters.jurisdiction === 'CN' ? '中国大陆' : parameters.jurisdiction || '未记录')}</span><span>判断时点：${E(shortDate(parameters.as_of))}</span><span>目的：${E(RESEARCH_PURPOSE[parameters.purpose] || parameters.purpose || '未记录')}</span>` : `<span>历史版本：${parameters.include_history ? '包含' : '按本次当前版本范围'}</span>`}<span>${E(transmission)}</span></div>
    <div class="notice research-coverage">${E(run.coverage || (isPublic ? '搜索结果仅为候选资料，须核对原文与适用时点。' : '范围限当前项目中已保存且可提取的资料。'))}</div>
    ${(run.warnings || []).length ? `<ul class="research-warnings">${run.warnings.map(item => `<li>${E(item)}</li>`).join('')}</ul>` : ''}
    ${candidates.length ? `<div class="research-results">${candidates.map(item => renderResearchResult(run, item)).join('')}</div>` : `<p class="research-empty">${E(isPublic ? emptyMessages[run.status] || '这次记录没有候选结果。尚不能据此判断相关依据不存在。' : '本次在所选项目资料范围内未找到匹配内容。尚未上传、未提取或未纳入范围的资料不在结果中。')}</p>`}
    ${isPublic ? `<div class="research-manual"><div><h4>在官方原文中继续核对</h4><p>登录、验证码和访问限制由你在官方页面处理。工作台不会自动操作浏览器。</p></div>${portals.length ? `<div class="research-portals">${portals.map(portal => { const url = safeURL(portal.url); return url ? `<a href="${E(url)}" target="_blank" rel="noopener noreferrer">${E(portal.title)} ↗</a>` : `<span>${E(portal.title)}</span>`; }).join('')}</div>` : ''}<button class="small" data-action="research-adopt" data-id="${E(run.id)}">录入自行核对的原文</button></div>` : ''}
    ${(run.adoptions || []).length ? `<details class="research-adoptions"><summary>本次人工录入 ${run.adoptions.length} 条来源</summary>${run.adoptions.map(item => `<p>来源编号 ${E(item.source_id)} · ${E(date(item.created_at))}<br>${E(item.note || '')}</p>`).join('')}</details>` : ''}
  </article>`;
}
function renderResearchResult(run, item) {
  const isPublic = run.scope === 'public';
  const url = safeURL(item.url);
  const version = !isPublic && allVersions().find(v => String(v.id) === String(item.version_id));
  const adopted = (run.adoptions || []).some(record => String(record.candidate_id) === String(item.id));
  return `<section class="research-result"><div class="manual-tags">${badge(isPublic ? '搜索摘要 · 待核对原文' : RESEARCH_KIND[item.kind] || item.kind || '项目资料', isPublic ? 'copper' : 'gray')}${adopted ? badge('已有人工录入记录', 'green') : ''}</div><h4>${url ? `<a href="${E(url)}" target="_blank" rel="noopener noreferrer">${E(item.title)} ↗</a>` : E(item.title)}</h4><p class="research-excerpt preserve-wrap">${E(item.excerpt || '没有可展示的摘要，请打开原文核对。')}</p><div class="research-metadata"><span>位置：${E(item.locator || '未提供')}</span>${item.version_number != null ? `<span>版本：V${E(item.version_number)}</span>` : ''}${item.reported_published_at ? `<span>搜索服务报告的公布日期：${E(item.reported_published_at)}（待核对，不代表生效日期）</span>` : ''}${item.evidence_status ? `<span>记录状态：${E(EVIDENCE_STATUS[item.evidence_status] || item.evidence_status)}</span>` : ''}</div>${item.notice ? `<p class="research-form-note">${E(item.notice)}</p>` : ''}${url ? `<p class="research-url">${E(url)}</p>` : ''}${item.sha256 ? `<p class="hash research-hash">原件 SHA-256：${E(item.sha256)}</p>` : ''}<div class="research-result-actions">${isPublic ? adopted ? '<button class="small" data-action="research-jump-sources">查看项目引用资料</button>' : `<button class="small" data-action="research-adopt" data-id="${E(run.id)}" data-candidate-id="${E(item.id)}">核对原文并录入</button>` : `<button class="small" data-action="research-open-entry" data-id="${E(run.id)}" data-candidate-id="${E(item.id)}">回到项目记录</button>${version ? `<a class="button small" href="${projectPath()}/versions/${encodeURIComponent(version.id)}/original" download>取回关联原件</a>` : ''}`}</div></section>`;
}
async function loadResearch(projectId = state.projectId) {
  const session = researchState(projectId);
  if (session.loading) return;
  session.loading = true; session.error = ''; renderResearchIfVisible(projectId);
  try {
    const results = await Promise.allSettled([api('/api/retrieval/capabilities'), api(`${researchPath(projectId)}/runs`)]);
    if (results[0].status === 'fulfilled') session.capabilities = results[0].value;
    if (results[1].status === 'fulfilled') {
      session.runs = [...results[1].value].sort((a, b) => String(b.started_at).localeCompare(String(a.started_at)));
      session.loaded = true;
      if (!session.runs.some(item => String(item.id) === String(session.selectedId))) session.selectedId = session.runs[0]?.id || null;
    }
    session.error = results.filter(result => result.status === 'rejected').map(result => result.reason.message).join('；');
  } finally { session.loading = false; renderResearchIfVisible(projectId); }
}
async function selectResearchRun(id) {
  const projectId = state.projectId;
  const run = await api(`${researchPath(projectId)}/runs/${encodeURIComponent(id)}`);
  rememberResearchRun(projectId, run);
}
function openLocalResearch() {
  const projectId = state.projectId;
  showModal({ title: '查当前项目资料', description: '只查询这个项目中已保存的资料，本次请求留在工作台服务内。', body: `${field('查找词或短语', 'query', '', { required: true, rows: 3, placeholder: '例如：通知对象', hint: '输入2至200字。按已录入文字匹配；多个词用空格分开，结果需同时包含这些词。此入口不读取整台电脑或公司系统。' })}<label class="checkbox-row"><input type="checkbox" name="include_history">同时查找历史合同版本及历史审核记录</label>`, submit: '在当前项目中查找', onSubmit: async form => {
    const query = String(form.get('query') || '').trim();
    if (query.length < 2 || query.length > 200) throw new Error('请输入2至200字的查找词或短语。');
    const run = await api(`${researchPath(projectId)}/local`, { method: 'POST', body: { query, include_history: form.has('include_history') } });
    rememberResearchRun(projectId, run); toast('本项目检索记录已保存');
  } });
  document.getElementById('f-query').minLength = 2;
  document.getElementById('f-query').maxLength = 200;
}
async function openPublicResearch() {
  const projectId = state.projectId;
  const capabilities = await api('/api/retrieval/capabilities');
  if (state.projectId !== projectId) return;
  researchState(projectId).capabilities = capabilities;
  renderResearchIfVisible(projectId);
  const enabled = capabilities.public_search?.available === true;
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  showModal({ title: '查公开法源', description: '另外填写已脱敏的法律问题，检索结果会先保存为候选资料。', body: `<div class="notice ${enabled ? '' : 'warning'} mb18">${E(capabilities.public_search?.notice || (enabled ? '已配置公开检索服务；尚未验证连通性，仅在点击联网检索后尝试请求。' : '当前使用官方跳转与手工录入，可先准备检索任务。'))}</div>${field('已脱敏的法律问题', 'question', '', { required: true, rows: 4, placeholder: '例如：中国大陆股权投资协议中，解除通知的送达与生效需要核对哪些现行规定？', hint: '填写4至600字的抽象法律问题。不要包含客户或项目名称、金额、交易安排、合同全文或内部意见。' })}<div class="field-grid"><div class="field"><label for="f-jurisdiction">法域</label><select id="f-jurisdiction" name="jurisdiction"><option value="CN">中国大陆</option></select></div>${field('法律判断时点', 'as_of', today, { type: 'date', required: true, hint: '默认本机今天，可改为交易发生日。判断时点、实际检索时间、公布日期和生效日期分别记录。' })}</div><div class="field"><label for="f-purpose">检索目的</label><select id="f-purpose" name="purpose">${Object.entries(RESEARCH_PURPOSE).map(([key, label]) => `<option value="${key}">${label}</option>`).join('')}</select><small>历史判断请选择目标日期；“近期”按实际检索时的近一个月筛选公布信息，不表示这些规定已生效。</small></div><label class="checkbox-row"><input type="checkbox" name="deidentified_confirmed" required>我已移除客户和项目名称、交易秘密与合同内容，确认以上仅为可供公开检索的法律问题。</label><p class="research-form-note">“准备官方检索任务”只保存问题和官方入口。${enabled ? '“发送问题并联网检索”会尝试把以上问题与所选检索条件发送给已配置的检索服务。' : '当前没有已配置的外部检索连接。'} 没有搜索结果不代表依据不存在。</p>`, submit: '准备官方检索任务', onSubmit: async (form, event) => {
    const mode = event.submitter?.dataset.researchMode === 'search' ? 'search' : 'prepare';
    if (mode === 'search' && !enabled) throw new Error('当前未配置可用的外部检索服务，请准备官方检索任务。');
    const question = String(form.get('question') || '').trim();
    if (question.length < 4 || question.length > 600) throw new Error('请输入4至600字的脱敏法律问题。');
    const submits = [...event.target.querySelectorAll('button[type="submit"]')];
    submits.forEach(button => { button.disabled = true; });
    try {
      const run = await api(`${researchPath(projectId)}/public`, { method: 'POST', body: { question, jurisdiction: 'CN', as_of: form.get('as_of'), purpose: form.get('purpose'), deidentified_confirmed: form.has('deidentified_confirmed'), mode } });
      rememberResearchRun(projectId, run); toast(mode === 'search' ? '本次检索结果与状态已保存，请核对候选原文' : '官方检索任务已保存');
    } finally { submits.forEach(button => { button.disabled = false; }); }
  } });
  document.getElementById('f-question').maxLength = 600;
  document.getElementById('f-question').minLength = 4;
  if (enabled) {
    const buttons = modalContent.querySelector('.modal-actions');
    const prepare = buttons.querySelector('button[type="submit"]');
    prepare.classList.remove('primary');
    const search = document.createElement('button');
    search.type = 'submit'; search.className = 'primary'; search.dataset.researchMode = 'search';
    search.textContent = '发送问题并联网检索'; buttons.append(search);
  }
}
async function openResearchCapabilities() {
  const projectId = state.projectId;
  const cap = await api('/api/retrieval/capabilities');
  if (state.projectId !== projectId) return;
  researchState(projectId).capabilities = cap; renderResearchIfVisible(projectId);
  showModal({ title: '连接与检索范围', description: '以下是配置状态；已配置不代表已验证连通性、账号授权、检索结果或法律原文。', submit: '关闭', onSubmit: async () => {}, body: `<div class="research-capability"><h3>本项目资料 ${badge('本地范围', 'gray')}</h3><p>${E(cap.internal_search?.notice || '限当前项目已保存的合同、审核意见、法源与表达记录。')}</p></div><div class="research-capability"><h3>公开检索 ${badge(cap.public_search?.available ? '已配置 · 未验证' : '使用官方跳转', cap.public_search?.available ? 'copper' : 'gray')}</h3><p>${E(cap.public_search?.notice || '')}</p><small>提供方 ${E(cap.public_search?.provider || '未配置')} · 模式 ${E(cap.public_search?.mode || '未记录')} · 状态 ${E(CONNECTION_STATUS[cap.public_search?.status] || cap.public_search?.status || '未记录')}</small></div><div class="research-capability"><h3>浏览器与电脑 ${badge('手工接力', 'gray')}</h3><p>${E(cap.browser?.notice || '仅提供官方跳转，由你登录和核对原文。未接入电脑读取或自动浏览器操作。')}</p></div>${(cap.connectors || []).map(item => { const url = safeURL(item.url); return `<div class="research-capability"><h3>${url ? `<a href="${E(url)}" target="_blank" rel="noopener noreferrer">${E(item.title)} ↗</a>` : E(item.title)} ${badge(CONNECTION_STATUS[item.status] || item.status || '未连接', 'gray')}</h3><p>${E(item.notice || '尚未连接，不会查询此系统。')}</p><small>连接方式 ${E(item.transport || '待确定')} · 范围 ${E(item.scope || '未授权')}</small></div>`; }).join('')}<div class="notice mt18">公开检索不会自动携带本地检索内容。接入企业知识库或系统时，需在实际服务中落实项目权限与审计后再启用。</div>` });
}
async function copyResearchQuestion(id) {
  const run = researchState().runs.find(item => String(item.id) === String(id));
  if (!run || run.scope !== 'public') throw new Error('只能从公开检索记录复制已填写的脱敏问题。');
  try { await navigator.clipboard.writeText(run.query); toast('已复制这次填写的脱敏问题'); }
  catch (_) {
    showModal({ title: '复制脱敏问题', description: '浏览器未提供剪贴板权限，可以选择下方文字手工复制。', body: `<div class="field"><label for="research-copy-question">公开检索问题</label><textarea id="research-copy-question" rows="5" readonly>${E(run.query)}</textarea></div>`, submit: '关闭', onSubmit: async () => {} });
    document.getElementById('research-copy-question').select();
  }
}
async function adoptResearchSource(runId, candidateId) {
  const projectId = state.projectId;
  const run = await api(`${researchPath(projectId)}/runs/${encodeURIComponent(runId)}`);
  if (state.projectId !== projectId) return;
  if (run.scope !== 'public') throw new Error('请从公开法源检索记录中录入出处。');
  const candidate = candidateId ? (run.results || []).find(item => String(item.id) === String(candidateId)) : null;
  if (candidateId && !candidate) throw new Error('没有找到这条候选记录，请刷新检索记录后重试。');
  if (candidate && (run.adoptions || []).some(item => String(item.candidate_id) === String(candidate.id))) {
    rememberResearchRun(projectId, run);
    document.querySelector('.source-grid')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    toast('此候选已有人工录入记录。新增证据请新建检索任务，并追加来源。');
    return;
  }
  const sourceURL = safeURL(candidate?.url);
  showModal({ title: '核对原文并录入法源', description: '记录你实际打开原文后的核对结果；不会自动关闭意见或批准交付。', body: `<div class="notice mb18">本次检索法域：中国大陆 · 判断时点：${E(shortDate(run.parameters?.as_of))}。录入将沿用这两个条件；需要其他时点时，请新建检索任务。</div>${candidate ? `<details class="research-candidate-reference"><summary>查看搜索摘要（仅作查找线索）</summary><p class="preserve-wrap">${E(candidate.excerpt || '未提供摘要')}</p>${sourceURL ? `<a href="${E(sourceURL)}" target="_blank" rel="noopener noreferrer">打开候选原文 ↗</a>` : ''}<p>搜索摘要不会自动填入准确引文。</p></details>` : ''}${field('文件 / 案例完整名称', 'title', candidate?.title || '', { required: true })}<div class="field"><label for="f-category">来源类型</label><select id="f-category" name="category" required><option value="">请选择实际类型</option>${Object.entries(CATEGORY).filter(([key]) => !['legislation', 'regulation', 'contract'].includes(key)).map(([key, label]) => `<option value="${key}">${label}</option>`).join('')}</select></div>${field('实际核对的原文链接', 'url', sourceURL || '', { type: 'url', required: true })}${field('准确原文引文与条款编号', 'citation', '', { rows: 5, required: true, hint: '从实际原文逐字核对，并保留条款位置。不要将搜索摘要当作法律原文。' })}<div class="field-grid">${field('核对后的公布日期', 'published_at', '', { type: 'date', hint: '无法确定时留空并在说明中记录。' })}${field('核对后的生效日期', 'effective_at', '', { type: 'date' })}</div><div class="field"><label for="f-effect_status">在判断时点的效力状态</label><select id="f-effect_status" name="effect_status" required><option value="">请选择核对结果</option><option>待核验</option><option>现行有效</option><option>尚未生效</option><option>已失效</option><option>已修订</option><option>不适用（案例 / 合同）</option></select></div>${field('核验说明与仍待确认的范围', 'verification_note', '', { rows: 4, required: true, placeholder: '说明核对的平台、原文位置、适用范围、修订情况和仍未确定的事项；虚构样例明确标注演示数据。' })}<div class="checks"><label class="checkbox-row"><input type="checkbox" name="original_checked" required>我已实际打开原文，核对名称、来源类型、条款编号及引文。</label><label class="checkbox-row"><input type="checkbox" name="as_of_checked" required>我已按上述判断时点核对公布、生效、修订与适用情况；无法确定的已如实记录。</label><label class="checkbox-row"><input type="checkbox" name="human_confirmed" required>我确认保存这条人工核验记录。核验时间表示本次人工录入，不表示系统已抓取或证明原文。</label></div>`, submit: '保存人工核验来源', onSubmit: async form => {
    const body = { ...Object.fromEntries(form), candidate_id: candidate?.id || null, human_confirmed: form.has('human_confirmed'), original_checked: form.has('original_checked'), as_of_checked: form.has('as_of_checked'), is_demo: form.has('is_demo') };
    if (String(body.title).length > 300) throw new Error('搜索标题超过300字，请按原文填写完整文件名称。');
    if (String(body.url).length > 2000) throw new Error('原文链接超过2000字，请核对可以长期引用的官方原文链接。');
    if (!safeURL(body.url)) throw new Error('请使用有效的 http 或 https 原文链接。');
    const result = await api(`${researchPath(projectId)}/runs/${encodeURIComponent(run.id)}/adopt-source`, { method: 'POST', body });
    run.adoptions = [...(run.adoptions || []).filter(item => String(item.id) !== String(result.adoption.id)), result.adoption];
    rememberResearchRun(projectId, run);
    if (state.projectId === projectId) {
      state.detail.sources = [...(state.detail.sources || []).filter(item => String(item.id) !== String(result.source.id)), result.source];
      renderResearchIfVisible(projectId);
      try {
        const detail = await api(`/api/projects/${encodeURIComponent(projectId)}`);
        if (state.projectId === projectId) { state.detail = detail; renderResearchIfVisible(projectId); }
      } catch (_) { toast('来源已保存；项目资料读取未完成，可重新进入项目刷新。', true); return; }
    }
    toast('人工核验来源已保存，并关联这次检索记录');
  } });
  for (const [name, limit] of Object.entries({ title: 300, url: 2000, citation: 10000, verification_note: 1800 })) document.getElementById(`f-${name}`).maxLength = limit;
  modalContent.querySelector('.checks').insertAdjacentHTML('beforeend', '<label class="checkbox-row"><input type="checkbox" name="is_demo">这是虚构演示数据，仅用于演示，不作为真实法律依据。</label>');
}
function openResearchEntry(runId, candidateId) {
  const run = researchState().runs.find(item => String(item.id) === String(runId));
  const item = (run?.results || []).find(result => String(result.id) === String(candidateId));
  if (!item || run.scope !== 'project') return;
  const version = allVersions().find(v => String(v.id) === String(item.version_id));
  if (version) state.contractId = version.contract_id;
  const fallback = { contract_version: 'contracts', review_issue: 'issues', recorded_source: 'sources', adopted_expression: 'issues' };
  const entry = ['contracts', 'issues', 'sources'].includes(item.entry) ? item.entry : fallback[item.kind] || 'sources';
  if (entry === 'issues') { state.issueFilter = 'all'; state.issueScope = 'history'; }
  if (entry === 'sources') { document.querySelector('.source-grid')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
  location.hash = `project/${encodeURIComponent(state.projectId)}/${entry}`;
}
function renderDeliverables() {
  const versions = allVersions();
  const selected = currentVersion(selectedContract());
  const items = state.detail.deliverables || [];
  const openIssues = outstandingIssues().length;
  return `<div class="toolbar"><h2>准备交付 <span>先生成，再人工批准</span></h2>${badge('仅下载 · 不自动发送', 'gray')}</div><div class="notice warning mb18">对外稿由已提取文字重新生成，可能不保留原 Word 的排版、表格、脚注及修订痕迹。法务须对照原件检查内容和版式，确认批注、修订与隐藏信息检查结果后批准。</div><div class="panel export-builder"><div><label for="export-version">使用哪个版本</label><select id="export-version">${versions.map(v => `<option value="${E(v.id)}" ${String(v.id) === String(selected?.id) ? 'selected' : ''}>${E(v.contract_title)} · V${E(v.number)} · ${E(KIND[v.kind] || v.kind)}</option>`).join('')}</select></div><div><label for="export-kind">文件用途</label><select id="export-kind"><option value="internal">内部审核清单</option><option value="external">对外清洁阅读稿</option></select></div><div><label for="export-format">文件格式</label><select id="export-format"><option value="docx">DOCX</option><option value="md">Markdown</option><option value="pdf">PDF</option></select></div><button class="primary" data-action="generate-deliverable" ${versions.length ? '' : 'disabled'}>生成文件</button></div>${openIssues ? `<div class="notice mb20">项目仍有 ${openIssues} 个尚未关闭的问题（已按问题去重，可能关联历史版本）。生成文件不会将意见关闭；对外批准时需人工核对是否允许带着未结事项交付。</div>` : ''}<div class="deliverable-list">${items.length ? [...items].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).map(d => renderDeliverable(d)).join('') : '<div class="empty full"><div class="empty-icon">↓</div><h3>每份对外文件，都有明确的批准状态</h3><p>内部清单可直接下载。对外文件生成后处于待批准状态，批准前不能通过对外交付接口下载。</p></div>'}</div>${renderAudit()}`;
}
function renderDeliverable(d) {
  const external = d.kind === 'external';
  const approved = isApproved(d);
  const version = allVersions().find(v => String(v.id) === String(d.version_id));
  const stale = external && d.status === 'approved' && !approved;
  const status = external ? approved ? '已批准 · 可交付' : stale ? '须重新复核' : '待人工批准' : '内部使用';
  const checks = d.inspection || d.checks || d.safety_checks || d.metadata_check;
  return `<article class="deliverable"><div class="file-icon ${external ? 'external' : ''}">${E((d.format || 'file').toUpperCase())}</div><div><div class="manual-tags"><h3>${E(d.filename || `${external ? '对外清洁阅读稿' : '内部审核清单'}.${d.format}`)}</h3>${badge(status, approved ? 'green' : external ? 'copper' : 'gray')}</div><p>${E(version?.contract_title || '合同')} · ${version ? `V${E(version.number)}` : E(d.version_id)} · 生成于 ${E(date(d.created_at))}</p><div class="deliverable-checks"><span>${external ? '重新生成的文字稿 · 需核对原件' : '保留问题、依据与处理状态'}</span>${checks ? '<span>生成检查结果已记录</span>' : ''}${approved ? `<span>批准于 ${E(date(d.approved_at))}</span>` : ''}</div></div><div class="actions">${!external || approved ? `<a class="button small ${approved ? 'primary' : ''}" href="${projectPath()}/deliverables/${encodeURIComponent(d.id)}/download" download>${external ? '下载交付件' : '下载内部稿'}</a>` : `<button class="small primary" data-action="approve-deliverable" data-id="${E(d.id)}">人工审核与批准</button><button class="small" disabled title="人工批准后才能下载">待批准</button>`}</div></article>`;
}

function showModal({ title, description = '', body, submit = '保存', onSubmit }) {
  modalContent.innerHTML = `<form id="modal-form"><div class="modal-header"><div><h2>${E(title)}</h2>${description ? `<p>${E(description)}</p>` : ''}</div><button type="button" class="ghost" data-close aria-label="关闭">✕</button></div><div class="modal-body"><div class="form-error" id="form-error" role="alert"></div>${body}</div><div class="modal-actions"><button type="button" data-close>取消</button><button type="submit" class="primary">${E(submit)}</button></div></form>`;
  if (!modal.open) modal.showModal();
  modalContent.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => modal.close()));
  document.getElementById('modal-form').addEventListener('submit', async event => {
    event.preventDefault();
    const button = event.submitter;
    const errorBox = document.getElementById('form-error');
    errorBox.className = 'form-error';
    button.disabled = true;
    try { await onSubmit(new FormData(event.target), event); modal.close(); }
    catch (error) { errorBox.textContent = error.message; errorBox.className = 'form-error visible'; }
    finally { button.disabled = false; }
  });
}
function field(label, name, value = '', { type = 'text', required = false, hint = '', rows = null, placeholder = '' } = {}) {
  return `<div class="field"><label for="f-${E(name)}">${E(label)}</label>${rows ? `<textarea id="f-${E(name)}" name="${E(name)}" rows="${rows}" ${required ? 'required' : ''} placeholder="${E(placeholder)}">${E(value)}</textarea>` : `<input id="f-${E(name)}" name="${E(name)}" type="${E(type)}" value="${E(value)}" ${required ? 'required' : ''} placeholder="${E(placeholder)}">`}${hint ? `<small>${E(hint)}</small>` : ''}</div>`;
}
function newProject() {
  showModal({ title: '新建投资项目', description: '本地演示请只填写虚构信息。', body: `${field('项目名称', 'name', '', { required: true, placeholder: '例如：澄湖科技 A 轮投资（虚构）' })}${field('项目说明', 'description', '', { rows: 3, placeholder: '简要描述本轮投资与合同范围' })}<div class="field-grid"><div class="field"><label for="f-jurisdiction">法域</label><select id="f-jurisdiction" name="jurisdiction"><option value="CN">中国大陆</option></select></div>${field('法律判断时点', 'as_of', '2026-11-21', { type: 'date', required: true })}</div>`, submit: '建立项目', onSubmit: async form => { const result = await api('/api/projects', { method: 'POST', body: Object.fromEntries(form) }); const id = result.id || result.project_id || result.project?.id; location.hash = `project/${encodeURIComponent(id)}/contracts`; toast('项目已建立'); } });
}
function newContract() {
  showModal({ title: '新增合同台账', description: '先记录合同，再逐版上传原件。', body: `${field('合同名称', 'title', '', { required: true, placeholder: '例如：股权投资协议' })}${field('交易对方', 'counterparty', '', { placeholder: '演示请使用虚构名称' })}`, submit: '新增合同', onSubmit: async form => { const result = await api(`${projectPath()}/contracts`, { method: 'POST', body: Object.fromEntries(form) }); state.contractId = result.id || result.contract_id; state.contractSearch = ''; await refresh(); toast('合同台账已建立'); } });
}
function editContract() {
  const contract = selectedContract();
  showModal({ title: '更新合同台账', description: '此处只更新台账信息。修改合同正文，请上传新版本。', body: `${field('合同名称', 'title', contract.title, { required: true })}${field('交易对方', 'counterparty', contract.counterparty || '')}<label class="checkbox-row"><input type="checkbox" name="archived" ${contract.archived ? 'checked' : ''}>归档本合同（保留所有原件与审查记录，可恢复）</label>`, onSubmit: async form => { await api(`${projectPath()}/contracts/${encodeURIComponent(contract.id)}`, { method: 'PATCH', body: { title: form.get('title'), counterparty: form.get('counterparty'), archived: form.has('archived') } }); state.showArchived = form.has('archived'); await refresh(); toast(form.has('archived') ? '已归档，记录完整保留' : '合同台账已更新'); } });
}
function uploadVersion() {
  const contract = selectedContract();
  showModal({ title: '上传新的合同版本', description: '新版本独立保存，历史原件不会被覆盖。', body: `<div class="field file-drop"><label for="f-file">选择原文件</label><input id="f-file" name="file" type="file" accept=".docx,.pdf,.md,.markdown" required><small>DOCX、PDF、Markdown。扫描 PDF 可能标记为待 OCR。</small></div><div class="field"><label for="f-kind">版本类型</label><select id="f-kind" name="kind">${Object.entries(KIND).map(([key, label]) => `<option value="${key}" ${key === (contract.versions?.length ? 'revision' : 'initial') ? 'selected' : ''}>${label}</option>`).join('')}</select></div><label class="checkbox-row"><input type="checkbox" name="carry_issues" checked>将旧版未结意见带入新版，继续人工复核</label><div class="notice mt18">上传及文本提取不会覆盖 Word 原件；带入意见不会自动关闭。</div>`, submit: '保存新版本', onSubmit: async form => { form.set('carry_issues', form.has('carry_issues') ? 'true' : 'false'); await api(`${projectPath()}/contracts/${encodeURIComponent(contract.id)}/versions`, { method: 'POST', body: form, headers: { 'Idempotency-Key': crypto.randomUUID() } }); state.diff = null; await refresh(); toast('新版本已保存，历史原件保留'); } });
}
function newIssue() {
  const versions = allVersions();
  if (!versions.length) return toast('请先上传一份合同版本', true);
  const sources = state.detail.sources || [];
  const current = currentVersion(selectedContract());
  showModal({ title: '记录一条审核意见', description: '精确引用当前版本的条款原文，并保留可核对的依据。', body: `<div class="field"><label for="f-version_id">关联合同版本</label><select name="version_id" id="f-version_id">${versions.map(v => `<option value="${E(v.id)}" ${String(v.id) === String(current?.id) ? 'selected' : ''}>${E(v.contract_title)} · V${E(v.number)} · ${E(KIND[v.kind] || v.kind)}</option>`).join('')}</select></div>${field('条款位置', 'clause', '', { required: true, placeholder: '例如：第 8.2 条 解除通知' })}${field('准确原文', 'quote', '', { rows: 3, required: true })}${field('风险 / 问题', 'risk', '', { rows: 2, required: true })}${field('修改建议', 'suggestion', '', { rows: 2, required: true })}${field('待确认事项', 'pending', '', { rows: 2 })}<div class="field"><label for="f-basis_source_id">依据来源</label><select name="basis_source_id" id="f-basis_source_id"><option value="">仅依据合同约定 / 人工说明</option>${sources.map(s => `<option value="${E(s.id)}">${E(s.title)}</option>`).join('')}</select><small>来源未录入时，请先到“法源”保存出处。</small></div>${field('法源原文引文', 'basis_quote', '', { rows: 3, hint: '选择外部法源时必填，并须与法源记录的原文吻合。仅依据合同约定时留空。' })}${field('依据说明', 'basis_note', '', { rows: 3, hint: '写明合同条款或法源如何支持这条意见；合同约定不应被写成统一法定要求。' })}`, submit: '保存意见', onSubmit: async form => { await api(`${projectPath()}/issues`, { method: 'POST', body: { ...Object.fromEntries(form), basis_source_id: form.get('basis_source_id') || null, status: 'open' } }); await refresh(); toast('审核意见已保存'); } });
}
function changeIssue(id, status) {
  const issue = (state.detail.issues || []).find(i => String(i.id) === String(id));
  if (!issue) return;
  const closing = status === 'resolved';
  showModal({ title: closing ? '人工确认关闭意见' : status === 'open' ? '重新打开意见' : '标记为处理中', description: `${issue.clause || '合同条款'} · ${issue.risk || '审核意见'}`, body: `${field(closing ? '关闭依据与确认说明' : '处理说明', 'resolution_note', '', { rows: 4, required: true, placeholder: closing ? '说明已核对的版本、材料与结论；仅改了期限不代表其他问题已解决。' : '记录当前处理情况或重新打开的原因。' })}<label class="checkbox-row"><input name="human_confirmed" type="checkbox" required>我已人工核对该意见及关联条款，确认本次状态变更。</label>`, submit: closing ? '确认关闭' : '确认更新', onSubmit: async form => { await api(`${projectPath()}/issues/${encodeURIComponent(id)}`, { method: 'PATCH', body: { status, human_confirmed: form.has('human_confirmed'), resolution_note: form.get('resolution_note') } }); await refresh(); toast('意见状态已更新并记录'); } });
}
function newSource() {
  showModal({ title: '录入法源与核验记录', description: '保存原文链接和引文；按文件实际类型分类。', body: `${field('文件 / 案例名称', 'title', '', { required: true })}<div class="field-grid"><div class="field"><label for="f-category">来源类型</label><select id="f-category" name="category">${Object.entries(CATEGORY).filter(([key]) => !['legislation', 'regulation', 'contract'].includes(key)).map(([key, label]) => `<option value="${key}">${label}</option>`).join('')}</select></div><div class="field"><label for="f-jurisdiction">法域</label><select id="f-jurisdiction" name="jurisdiction"><option value="CN">中国大陆</option></select></div></div>${field('原文链接', 'url', '', { type: 'url', required: true, placeholder: 'https://...' })}${field('准确引文与条款编号', 'citation', '', { rows: 4, required: true })}<div class="field-grid">${field('公布日期', 'published_at', '', { type: 'date' })}${field('生效日期', 'effective_at', '', { type: 'date' })}</div><div class="field-grid">${field('判断时点', 'as_of', state.detail.project.as_of, { type: 'date', required: true })}<div class="field"><label for="f-effect_status">效力状态</label><select id="f-effect_status" name="effect_status"><option>待核验</option><option>现行有效</option><option>尚未生效</option><option>已失效</option><option>已修订</option><option>不适用（案例 / 合同）</option></select></div></div>${field('核验时间', 'verified_at', '', { type: 'datetime-local', hint: '仅在实际核验后填写；不确定可留空。' })}${field('核验说明', 'verification_note', '', { rows: 3, placeholder: '核对了什么、仍有哪些适用性或时效问题；样例请明确标“演示数据”。' })}`, submit: '保存来源', onSubmit: async form => { const body = Object.fromEntries(form); if (!safeURL(body.url)) throw new Error('请使用有效的 http 或 https 原文链接'); await api(`${projectPath()}/sources`, { method: 'POST', body }); await refresh(); toast('来源和核验记录已保存'); } });
}
async function approveDeliverable(id) {
  const d = (state.detail.deliverables || []).find(item => String(item.id) === String(id));
  if (!d) return;
  const preview = await api(`${projectPath()}/deliverables/${encodeURIComponent(id)}/preview`);
  const openIssues = contractOpenCount(d.contract_id);
  const checks = preview.inspection || d.inspection || {};
  const sourceSafety = preview.source_safety || d.source_safety || {};
  const findings = Array.isArray(sourceSafety.findings) ? sourceSafety.findings : [];
  showModal({ title: '人工审核与批准交付', description: '批准针对这一份固定版本的文件；不代表自动关闭审核意见。', body: `<div class="notice warning">${E(d.filename || '对外清洁阅读稿')}<br>这是从已提取文字重建的文件。原 Word 版式不会完整保留；已有修订可能带来语义歧义。请核对合同内容、附件、表格、脚注与签署需求。</div><div class="review-package"><div><h3>先打开实际文件核对</h3><p>审阅包仅供内部使用，包含待审文件、未批准说明及校验值；下载不会改变批准状态。</p></div><a class="button small" href="${projectPath()}/deliverables/${encodeURIComponent(id)}/review-copy" download>下载内部审阅包（待批准）</a></div><div class="check-section"><h3>待批准产物的文字预览</h3><p class="small-text muted">${E(preview.notice || '以下文字来自已生成文件，用于批准前核对；文字预览不能替代 Word 版式检查。')}</p><div class="approval-preview" tabindex="0">${E(preview.text || '无法提取预览文字，请勿批准。')}</div><p class="hash">文件校验值 ${E(preview.sha256 || d.sha256)}</p></div><div class="check-section"><h3>本次生成检查 ${checks.passed ? '· 已通过列明项目' : '· 未确认通过'}</h3><ul class="inspection-list">${(checks.checked || []).map(item => `<li>${E(item)}</li>`).join('')}</ul>${(checks.findings || []).length ? `<div class="notice danger">${checks.findings.map(item => E(item)).join('<br>')}</div>` : ''}<p class="small-text muted">${E(checks.scope || '结构检查不等于对内容、完整性与法律结论的保证。')}</p></div>${findings.length ? `<div class="notice warning">原件需特别核对：<br>${findings.map(item => E(item)).join('<br>')}</div>` : '<div class="notice">原件结构扫描未标记特别问题，仍需人工对照合同及附件核对完整性。</div>'}${field('批准说明', 'review_note', '', { rows: 4, required: true, placeholder: '记录已核对的内容、检查结果和交付范围；若仅可作为谈判阅读稿，请在这里明确。' })}<div class="checks"><label class="checkbox-row"><input name="human_confirmed" type="checkbox" required>我已打开内部审阅包中的实际文件，核对正文内容和布局，并与原合同及附件比对；已复核原件已有修订、隐藏内容，以及产物的批注、修订和元数据检查结果，批准此文件进入可交付状态。</label>${openIssues ? `<label class="checkbox-row"><input name="accept_open_issues" type="checkbox" required>我知悉此合同仍有 ${openIssues} 个未结问题（含历史版本遗留），已核对其与本次交付的关系，确认可在上述范围下交付。</label>` : ''}</div>`, submit: '人工批准此文件', onSubmit: async form => { await api(`${projectPath()}/deliverables/${encodeURIComponent(id)}/approve`, { method: 'POST', body: { human_confirmed: form.has('human_confirmed'), review_note: form.get('review_note'), accept_open_issues: form.has('accept_open_issues') } }); await refresh(); toast('已批准，可以下载此交付件'); } });
}

// Opinion wording stays separate from the underlying legal issue and its status.
const REWRITE_AUDIENCE = { internal: '内部法务', business: '业务沟通', counterparty: '对方批注' };
const REWRITE_TONE = { concise: '简洁', gentle: '温和', firm: '坚定' };
const REWRITE_STATUS = { prepared: '资料已准备', draft: '草稿待确认', adopted: '已人工采纳' };
const rewriteSessions = new Map();
let activeRewrite = null;
let rewriteOpenRequest = 0;

function rewriteSourceLabel(source) {
  return source === 'demo_template' ? '离线演示模板（非模型）' : source === 'manual_input' ? '人工录入 / 编辑' : '来源见记录';
}
function rewriteEditor(session) {
  const task = session.task;
  if (!task) return null;
  if (!session.editors.has(task.id)) {
    const d = task.latest_draft;
    session.editors.set(task.id, { text: d?.text || '', meaning_notes: d?.meaning_notes || '', note: task.adoption?.note || '', human_confirmed: false, meaning_confirmed: false, external_safe_confirmed: false, savedDraftId: d?.id || null });
  }
  return session.editors.get(task.id);
}
function rewriteDirty(session) {
  if (!session?.task) return false;
  const editor = rewriteEditor(session);
  return editor.text !== (session.task.latest_draft?.text || '') || editor.meaning_notes !== (session.task.latest_draft?.meaning_notes || '');
}
function sessionHasUnsavedRewrites(session) {
  for (const [id, editor] of session.editors) {
    const saved = session.savedDrafts.get(id) || { text: '', meaning_notes: '', note: '' };
    if (editor.text !== saved.text || editor.meaning_notes !== saved.meaning_notes || editor.note !== saved.note) return true;
  }
  return false;
}
function hasUnsavedRewrites() {
  return [...rewriteSessions.values()].some(sessionHasUnsavedRewrites);
}
function rememberRewriteTask(session, task, replaceEditor = false) {
  session.task = task;
  const d = task.latest_draft;
  session.savedDrafts.set(task.id, { text: d?.text || '', meaning_notes: d?.meaning_notes || '', note: task.adoption?.note || '' });
  const priorNote = session.editors.get(task.id)?.note || '';
  if (replaceEditor) session.editors.delete(task.id);
  const editor = rewriteEditor(session);
  if (replaceEditor) editor.note = task.adoption?.note || priorNote;
}
function renderAdoptedExpressions(issue) {
  const all = (state.detail.expression_revisions || []).filter(x => String(x.issue_id) === String(issue.id));
  if (!all.length) return '';
  const latest = new Map();
  for (const expression of [...all].sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)))) latest.set(expression.audience, expression);
  return `<section class="issue-expressions"><div class="rewrite-section-head"><h4>已采纳的表达</h4><small>原意见与问题状态保留 · 不代表交付批准</small></div>${[...latest.values()].map(x => `<div class="expression-item"><div class="expression-label">${badge(REWRITE_AUDIENCE[x.audience] || x.audience)}<span>V${E(x.version_number || "—")} · ${E(date(x.created_at))}</span>${x.stale ? badge("资料已变化，请复核", "copper") : ""}<button class="small" data-action="copy-expression" data-id="${E(x.id)}">复制文字</button></div><p class="preserve-wrap">${E(x.text)}</p>${x.stale ? `<p class="small-text rewrite-unsaved">${E(x.stale_reason)}</p>` : ""}</div>`).join('')}</section>`;
}
async function openRewrite(issueId) {
  const projectId = state.projectId;
  const request = ++rewriteOpenRequest;
  const path = `/api/projects/${encodeURIComponent(projectId)}`;
  const list = await api(`${path}/issues/${encodeURIComponent(issueId)}/rewrites`);
  if (request !== rewriteOpenRequest || state.projectId !== projectId) return;
  const key = `${projectId}:${issueId}`;
  let session = rewriteSessions.get(key);
  if (!session) {
    session = { projectId, issueId, path, list, task: null, audience: 'internal', tone: 'concise', mode: 'prepare', editors: new Map(), savedDrafts: new Map(), error: '', busy: false };
    rewriteSessions.set(key, session);
  }
  session.list = list;
  session.issue = (state.detail.issues || []).find(x => String(x.id) === String(issueId));
  session.error = '';
  if (session.task) {
    const task = await api(`${path}/rewrite-tasks/${encodeURIComponent(session.task.id)}`);
    if (request !== rewriteOpenRequest || state.projectId !== projectId) return;
    rememberRewriteTask(session, task);
  }
  activeRewrite = session;
  modal.classList.add('rewrite-dialog');
  renderRewriteWorkspace();
  if (!modal.open) modal.showModal();
}
function rewriteSnapshotHTML(task) {
  const s = task.context_snapshot || {};
  const v = s.target_version || {};
  const issue = s.issue || {};
  const source = s.source;
  const url = safeURL(source?.url);
  const gaps = [...(s.unknowns || []), ...(v.warnings || [])];
  return `<section class="rewrite-context"><div class="rewrite-context-row"><span>本次处理 <strong>${E(s.contract?.title || '所选合同')} · V${E(v.number || '—')}</strong></span><span>原意见依据 V${E(s.origin_version?.number || '—')}</span><span>${E(s.project?.jurisdiction || '法域待确认')} · 项目判断时点 ${E(s.project?.as_of || '未记录')}</span></div><p class="hash">合同校验值 ${E(v.sha256 || '未提供')}</p><details><summary>本次使用的条款、依据和缺项</summary><h4>${E(issue.clause || '条款位置待确认')} · 本次处理版本的原文</h4><div class="quote">${E(v.clause_text || '当前版本未定位到该条款，须先人工核对。')}</div>${issue.quote && issue.quote !== v.clause_text ? `<h4>原意见引用（原样保留）</h4><div class="quote">${E(issue.quote)}</div>` : ''}<h4>依据</h4>${source ? `<p>${url ? `<a href="${E(url)}" target="_blank" rel="noopener noreferrer">${E(source.title)} ↗</a>` : E(source.title)}<br>${E(source.effect_status || '效力待核验')}</p>${sourceTimingHTML(source, s.project?.as_of)}<div class="quote">${E(issue.basis_quote || source.citation || '')}</div>` : '<p>合同约定 / 人工依据说明</p>'}<p class="preserve-wrap">${E(issue.basis_note || '尚未提供具体依据说明')}</p>${gaps.length ? `<h4>尚未确认 / 解析提示</h4><ul class="inspection-list">${gaps.map(x => `<li>${E(textValue(x))}</li>`).join('')}</ul>` : ''}<p class="small-text muted">提示词版本 ${E(task.prompt_version)} · 资料已固定，后续版本变化不会悄悄替换这里的内容。</p><p class="hash">资料校验值 ${E(task.context_sha256)}</p></details></section>`;
}
function rewriteHistoryHTML(session) {
  const history = session.list.history || [];
  if (!history.length) return '';
  return `<details class="rewrite-history" ${!session.task ? 'open' : ''}><summary>历史改写任务 · ${history.length} 次</summary><div class="rewrite-history-list">${history.map(item => `<button type="button" class="rewrite-history-item ${session.task?.id === item.id ? 'selected' : ''}" data-rewrite-action="load-task" data-id="${E(item.id)}"><span>${E(REWRITE_AUDIENCE[item.audience] || item.audience)} · ${E(REWRITE_TONE[item.tone] || item.tone)} <small>${E(date(item.created_at))}</small></span><span>${badge(REWRITE_STATUS[item.status] || item.status, item.status === 'adopted' ? 'green' : 'gray')}${item.stale ? badge('资料已变化', 'copper') : ''}</span></button>`).join('')}</div></details>`;
}
function rewriteComparisonHTML(task) {
  const comparison = task.comparison;
  if (!comparison?.lines?.length) return '';
  return `<details class="rewrite-comparison"><summary>措辞对照 · 原建议与最近保存的草稿</summary><p class="small-text muted">${E(comparison.notice || '文本变化不等于含义核验；实质立场仍须人工确认。')} 此处不包含尚未保存的编辑。</p><div class="diff-box">${comparison.lines.map(line => `<div class="diff-line ${['equal', 'add', 'remove'].includes(line.type) ? line.type : 'equal'}"><span class="diff-sign">${line.type === 'add' ? '+' : line.type === 'remove' ? '−' : ' '}</span><span>${E(line.text)}</span></div>`).join('')}</div></details>`;
}
function renderRewriteWorkspace() {
  const s = activeRewrite;
  if (!s) return;
  const task = s.task;
  const editor = task ? rewriteEditor(s) : null;
  const dirty = task ? rewriteDirty(s) : false;
  const frozenIssue = task?.context_snapshot?.issue || s.issue || {};
  const canEdit = task && !task.stale && task.status !== 'adopted';
  const options = (entries, current) => Object.entries(entries).map(([value, label]) => `<option value="${E(value)}" ${value === current ? 'selected' : ''}>${E(label)}</option>`).join('');
  const creator = `<section class="rewrite-setup"><h3>这条意见准备给谁看？</h3><p class="small-text muted">选择用途和语气，系统带入原意见、关联条款和已有依据。</p><form data-rewrite-form="create"><div class="field-grid"><div class="field"><label for="rw-audience">阅读对象</label><select id="rw-audience" name="audience">${options(REWRITE_AUDIENCE, s.audience)}</select></div><div class="field"><label for="rw-tone">表达语气</label><select id="rw-tone" name="tone">${options(REWRITE_TONE, s.tone)}</select></div></div><div class="field"><label for="rw-mode">本次怎么准备</label><select id="rw-mode" name="mode"><option value="prepare" ${s.mode === 'prepare' ? 'selected' : ''}>准备资料与提示词，手工录入草稿</option>${s.list.demo_available ? `<option value="demo" ${s.mode === 'demo' ? 'selected' : ''}>离线演示（非模型，仅内置虚构样例）</option>` : ''}</select><small>尚未连接外部模型。准备资料不会自动向任何服务发送合同。</small></div>${!s.list.can_create ? `<div class="notice warning mb18">${E(s.list.blocked_reason || '当前记录暂不能新建改写任务，请核对最新意见与合同状态。')}</div>` : ''}<button type="submit" class="primary" ${!s.list.can_create ? 'disabled' : ''}>准备这次改写</button></form></section>`;
  let working = '';
  if (task) {
    const saved = task.latest_draft;
    const original = `问题：${frozenIssue.risk || '未提供'}\n\n原建议：${frozenIssue.suggestion || '未提供'}\n\n待确认：${frozenIssue.pending || '未提供'}`;
    working = `<div class="rewrite-task-head"><div>${badge(REWRITE_AUDIENCE[task.audience] || task.audience)} ${badge(REWRITE_TONE[task.tone] || task.tone, 'gray')} ${badge(REWRITE_STATUS[task.status] || task.status, task.status === 'adopted' ? 'green' : 'copper')}</div><button type="button" class="small" data-rewrite-action="new-task">新建另一版表达</button></div><p class="small-text muted">${E(task.source_notice || '本次未调用外部模型。')}</p>${task.stale ? `<div class="notice warning mb18"><strong>${task.status === 'adopted' ? '采纳后资料已变化，复用前请重新核对' : '本次资料已变化，暂不能保存或采纳'}</strong><br>${E(task.stale_reason || '请按最新资料重新准备。')}<br>已输入文字仍保留，可先复制后新建任务。</div>` : ''}${rewriteSnapshotHTML(task)}<details class="rewrite-prompt" ${!saved ? 'open' : ''}><summary>已准备的提示词与资料 · 仅供内部使用</summary><p class="small-text muted">可在已获准处理这些资料的环境使用；当前工作台不会调用模型。内容可能含内部事实和意见。</p><textarea id="rw-prompt" readonly rows="7" aria-label="完整任务提示词">${E(task.prompt_text || '')}</textarea><button type="button" class="small" data-rewrite-action="copy-prompt">复制完整提示词</button></details>${task.status === 'adopted' ? `<section class="rewrite-adopted"><div class="rewrite-section-head"><h3>已人工采纳的表达</h3>${badge('保留原意见与状态', 'green')}</div><p class="preserve-wrap">${E(task.adoption?.text || saved?.text || '')}</p><button type="button" class="small" data-rewrite-action="copy-adoption">复制这版文字</button><p class="small-text muted">采纳说明：${E(task.adoption?.note || '见记录')}<br>采纳时间 ${E(date(task.adoption?.created_at))} · 此记录不代表对外交付批准。</p></section>` : `<form data-rewrite-form="draft" class="rewrite-editor"><div class="rewrite-section-head"><h3>原意见与改写草稿</h3><span id="rw-dirty" class="small-text ${dirty ? 'rewrite-unsaved' : 'muted'}">${dirty ? '有未保存编辑' : saved ? '与已保存草稿一致' : '尚未录入草稿'}</span></div><div class="rewrite-columns"><section class="rewrite-original"><h4>原审核意见 · 保留不覆盖</h4><div class="preserve-wrap">${E(original)}</div></section><section><div class="field"><label for="rw-text">${E(REWRITE_AUDIENCE[task.audience] || task.audience)}文字草稿</label><textarea id="rw-text" name="text" rows="10" maxlength="12000" required placeholder="在这里录入或粘贴经授权环境生成的草稿，再人工核对。">${E(editor.text)}</textarea><small>含义仍须人工确认。复制按钮只取这段文字，不带内部说明。</small></div><div class="field"><label for="rw-meaning">含义变化与待核对事项</label><textarea id="rw-meaning" name="meaning_notes" rows="4" maxlength="4000" required placeholder="说明保留了哪些条件，有无新增承诺、删除保留事项或改变实质立场；尚不能确认的请明确记录。">${E(editor.meaning_notes)}</textarea><small>这是随草稿保存的说明，不是系统已完成语义核验的证明。</small></div></section></div><div class="rewrite-editor-actions"><button type="button" data-rewrite-action="copy-editor">复制这段文字</button><button type="submit" class="primary" ${!canEdit ? 'disabled' : ''}>保存为新草稿</button></div></form>${saved ? `<form data-rewrite-form="adopt" class="rewrite-adopt-form"><h3>采纳前确认</h3><p class="small-text muted">采纳对象：最近保存的草稿 ${E(saved.number || task.drafts?.length || '—')} · ${E(rewriteSourceLabel(saved.source))}。原意见、条款原件和问题状态保留。</p><div class="field"><label for="rw-note">采纳说明</label><textarea id="rw-note" name="note" rows="3" maxlength="4000" required placeholder="说明核对了哪些材料、采用范围，以及仍保留哪些事项。">${E(editor.note)}</textarea></div><div class="checks"><label class="checkbox-row"><input type="checkbox" name="human_confirmed" required ${editor.human_confirmed ? 'checked' : ''}>我已核对本次版本、条款和原意见，确认采用这版表达。</label><label class="checkbox-row"><input type="checkbox" name="meaning_confirmed" required ${editor.meaning_confirmed ? 'checked' : ''}>我已检查事实、承诺、实质立场和保留条件；待确认事项没有被润色为确定结论。</label>${task.audience === 'counterparty' ? `<label class="checkbox-row"><input type="checkbox" name="external_safe_confirmed" required ${editor.external_safe_confirmed ? 'checked' : ''}>我已确认这段对方批注没有夹带内部底线、评级或决策讨论；采纳本身不构成外发批准。</label>` : ''}</div><p id="rw-adopt-hint" class="small-text rewrite-unsaved">${dirty ? '请先保存当前编辑，再采纳最新草稿。' : task.stale ? '资料已变化，请重新准备。' : ''}</p><button id="rw-adopt-button" type="submit" class="primary" ${!task.can_adopt || dirty ? 'disabled' : ''}>人工采纳此草稿</button></form>` : ''}`}${rewriteComparisonHTML(task)}${(task.drafts || []).length ? `<details class="rewrite-draft-history"><summary>草稿修订记录 · ${task.drafts.length} 版</summary>${[...task.drafts].reverse().map(d => `<section class="rewrite-draft-record"><div class="rewrite-section-head"><h4>草稿 ${E(d.number || '—')} · ${E(rewriteSourceLabel(d.source))}</h4><button type="button" class="small" data-rewrite-action="copy-draft" data-id="${E(d.id)}">复制文字</button></div><p class="small-text muted">${E(date(d.created_at))}</p><p class="preserve-wrap">${E(d.text)}</p><details><summary>随稿保存的含义说明（内部）</summary><p class="preserve-wrap">${E(d.meaning_notes)}</p></details></section>`).join('')}</details>` : ''}`;
  }
  modalContent.innerHTML = `<div class="rewrite-workspace"><div class="modal-header"><div><div class="eyebrow">审核意见 · 表达修订</div><h2 id="rewrite-title">改写审核意见</h2><p>${E(frozenIssue.clause || '所选条款')} · ${E(frozenIssue.risk || '保留原立场，调整表达')}</p></div><button type="button" class="ghost" data-rewrite-action="close" aria-label="关闭改写窗口">✕</button></div><div class="modal-body"><ol class="rewrite-steps"><li class="${!task ? 'current' : 'done'}">选择用途</li><li class="${task && task.status !== 'adopted' ? 'current' : task ? 'done' : ''}">整理草稿</li><li class="${task?.status === 'adopted' ? 'done' : ''}">人工采纳</li></ol><div class="form-error ${s.error ? 'visible' : ''}" role="alert">${E(s.error)}</div>${task ? working : creator}${rewriteHistoryHTML(s)}</div><div class="modal-actions rewrite-footer"><div><small>关闭窗口保留本页未保存编辑；刷新页面前请先保存。</small><p id="rw-copy-feedback" class="small-text" role="status" aria-live="polite"></p></div><button type="button" data-rewrite-action="close">暂时关闭</button></div></div>`;
  modal.setAttribute('aria-labelledby', 'rewrite-title');
}
async function rewriteRun(job) {
  const s = activeRewrite;
  if (!s || s.busy) return;
  s.busy = true; s.error = '';
  modalContent.setAttribute('aria-busy', 'true');
  modalContent.querySelectorAll('button, input, select, textarea').forEach(b => { b.disabled = true; });
  try { await job(s); }
  catch (error) {
    s.error = error.message;
    if (error.status === 409 && s.task) {
      try { rememberRewriteTask(s, await api(`${s.path}/rewrite-tasks/${encodeURIComponent(s.task.id)}`)); } catch (_) {}
    }
  } finally {
    s.busy = false;
    modalContent.removeAttribute('aria-busy');
    if (activeRewrite === s) {
      if (state.projectId !== s.projectId) modal.close();
      else renderRewriteWorkspace();
    }
  }
}
async function refreshRewriteList(session) {
  try { session.list = await api(`${session.path}/issues/${encodeURIComponent(session.issueId)}/rewrites`); }
  catch (_) { session.error = '本次记录已保存，但历史列表暂未刷新。重新打开可继续查看。'; }
}
async function copyRewriteText(text, fallback) {
  if (!String(text || '').trim()) throw new Error('还没有可复制的文字');
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard_unavailable');
    await navigator.clipboard.writeText(String(text));
    const feedback = document.getElementById('rw-copy-feedback');
    if (feedback) feedback.textContent = '已复制所选文字；请在目标文档中人工核对后采用。';
    toast('已复制所选文字；请在目标文档中人工核对后采用');
  } catch (_) {
    if (fallback?.select) { fallback.focus(); fallback.select(); }
    const feedback = document.getElementById('rw-copy-feedback');
    if (feedback) feedback.textContent = '浏览器未允许自动复制，请选中文字后手工复制。';
    throw new Error('浏览器未允许自动复制，请选中文字后手工复制。');
  }
}
modalContent.addEventListener('input', event => {
  const s = activeRewrite;
  if (!s) return;
  const target = event.target;
  if (['audience', 'tone', 'mode'].includes(target.name)) s[target.name] = target.value;
  if (!s.task) return;
  const editor = rewriteEditor(s);
  if (['text', 'meaning_notes', 'note'].includes(target.name)) editor[target.name] = target.value;
  if (['human_confirmed', 'meaning_confirmed', 'external_safe_confirmed'].includes(target.name)) editor[target.name] = target.checked;
  if (['text', 'meaning_notes'].includes(target.name)) {
    // A new textual edit requires a fresh human acknowledgement, even if a checkbox was set before.
    editor.human_confirmed = false; editor.meaning_confirmed = false; editor.external_safe_confirmed = false;
    modalContent.querySelectorAll('.rewrite-adopt-form input[type=checkbox]').forEach(x => { x.checked = false; });
    const dirty = rewriteDirty(s);
    const label = document.getElementById('rw-dirty');
    if (label) { label.textContent = dirty ? '有未保存编辑' : '与已保存草稿一致'; label.className = `small-text ${dirty ? 'rewrite-unsaved' : 'muted'}`; }
    const button = document.getElementById('rw-adopt-button');
    if (button) button.disabled = dirty || !s.task.can_adopt;
    const hint = document.getElementById('rw-adopt-hint');
    if (hint) hint.textContent = dirty ? '请先保存当前编辑，再采纳最新草稿。' : s.task.stale ? '资料已变化，请重新准备。' : '';
  }
});
modalContent.addEventListener('submit', event => {
  const form = event.target.closest('[data-rewrite-form]');
  if (!form || !activeRewrite) return;
  event.preventDefault();
  const values = new FormData(form);
  if (form.dataset.rewriteForm === 'create') return rewriteRun(async s => {
    const task = await api(`${s.path}/issues/${encodeURIComponent(s.issueId)}/rewrites`, { method: 'POST', body: { audience: values.get('audience'), tone: values.get('tone'), mode: values.get('mode') } });
    rememberRewriteTask(s, task, true);
    await refreshRewriteList(s);
  });
  if (form.dataset.rewriteForm === 'draft') return rewriteRun(async s => {
    const task = await api(`${s.path}/rewrite-tasks/${encodeURIComponent(s.task.id)}/drafts`, { method: 'POST', body: { text: values.get('text'), meaning_notes: values.get('meaning_notes') } });
    rememberRewriteTask(s, task, true);
    await refreshRewriteList(s);
    toast('已保存新的草稿修订，原意见保留');
  });
  if (form.dataset.rewriteForm === 'adopt') return rewriteRun(async s => {
    if (rewriteDirty(s)) throw new Error('请先保存当前编辑，再采纳最新草稿。');
    const task = await api(`${s.path}/rewrite-tasks/${encodeURIComponent(s.task.id)}/adopt`, { method: 'POST', body: { draft_id: s.task.latest_draft.id, human_confirmed: values.has('human_confirmed'), meaning_confirmed: values.has('meaning_confirmed'), external_safe_confirmed: values.has('external_safe_confirmed'), note: values.get('note') } });
    rememberRewriteTask(s, task, true);
    await refreshRewriteList(s);
    if (state.projectId === s.projectId) {
      try { await refresh(); } catch (_) { s.error = '表达已采纳，但项目列表暂未刷新。关闭窗口后可重新读取。'; }
    }
    toast('已采纳这版表达；原意见、问题状态和合同原件保留');
  });
});
modalContent.addEventListener('click', event => {
  const button = event.target.closest('[data-rewrite-action]');
  const s = activeRewrite;
  if (!button || !s || s.busy) return;
  const command = button.dataset.rewriteAction;
  if (command === 'close') return modal.close();
  if (command === 'new-task') { s.task = null; s.error = ''; s.mode = 'prepare'; return renderRewriteWorkspace(); }
  if (command === 'load-task') return rewriteRun(async session => { rememberRewriteTask(session, await api(`${session.path}/rewrite-tasks/${encodeURIComponent(button.dataset.id)}`)); });
  if (command === 'copy-prompt') return action(() => copyRewriteText(s.task.prompt_text, document.getElementById('rw-prompt')), button);
  if (command === 'copy-editor') return action(() => copyRewriteText(rewriteEditor(s).text, document.getElementById('rw-text')), button);
  if (command === 'copy-adoption') return action(() => copyRewriteText(s.task.adoption?.text || s.task.latest_draft?.text), button);
  if (command === 'copy-draft') return action(() => copyRewriteText((s.task.drafts || []).find(d => d.id === button.dataset.id)?.text), button);
});
modal.addEventListener('cancel', event => { if (activeRewrite?.busy) event.preventDefault(); });
modal.addEventListener('close', () => {
  if (activeRewrite && sessionHasUnsavedRewrites(activeRewrite)) toast('未保存编辑已暂存在本页，重新打开这条意见可继续；刷新前请先保存。');
  activeRewrite = null;
  modal.classList.remove('rewrite-dialog');
  modal.removeAttribute('aria-labelledby');
});
window.addEventListener('beforeunload', event => { if (hasUnsavedRewrites()) { event.preventDefault(); event.returnValue = ''; } });


app.addEventListener('input', event => {
  if (event.target.id === 'contract-search') {
    const start = event.target.selectionStart;
    state.contractSearch = event.target.value; renderProject();
    const updated = document.getElementById('contract-search'); updated.focus();
    try { updated.setSelectionRange(start, start); } catch (_) {}
  }
});
app.addEventListener('change', event => {
  if (event.target.id === 'show-archived') { state.showArchived = event.target.checked; const visible = (state.detail.contracts || []).filter(c => state.showArchived || !c.archived); if (!visible.some(c => String(c.id) === String(state.contractId))) state.contractId = visible[0]?.id ?? null; renderProject(); }
  if (event.target.id === 'issue-scope') { state.issueScope = event.target.value; renderProject(); }
  if (event.target.id === 'issue-contract') { state.contractId = event.target.value; renderProject(); }
  if (event.target.id === 'issue-filter') { state.issueFilter = event.target.value; renderProject(); }
  if (event.target.id === 'research-run') action(() => selectResearchRun(event.target.value));
});
app.addEventListener('click', event => {
  const button = event.target.closest('[data-action]');
  if (!button) return;
  const command = button.dataset.action;
  if (command === 'retry') return route();
  if (command === 'go-deliverables') { location.hash = `project/${encodeURIComponent(state.projectId)}/deliverables`; return; }
  if (command === 'new-project') return newProject();
  if (command === 'new-contract') return newContract();
  if (command === 'edit-contract') return editContract();
  if (command === 'upload-version') return uploadVersion();
  if (command === 'new-issue') return newIssue();
  if (command === 'new-source') return newSource();
  if (command === 'research-local') return openLocalResearch();
  if (command === 'research-public') return action(() => openPublicResearch(), button);
  if (command === 'research-capabilities') return action(() => openResearchCapabilities(), button);
  if (command === 'research-refresh') return action(() => loadResearch(), button);
  if (command === 'research-copy') return action(() => copyResearchQuestion(button.dataset.id), button);
  if (command === 'research-adopt') return action(() => adoptResearchSource(button.dataset.id, button.dataset.candidateId), button);
  if (command === 'research-open-entry') return openResearchEntry(button.dataset.id, button.dataset.candidateId);
  if (command === 'research-jump-sources') { document.querySelector('.source-grid')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
  if (command === 'rewrite-issue') return action(() => openRewrite(button.dataset.id), button);
  if (command === 'copy-expression') return action(() => copyRewriteText((state.detail.expression_revisions || []).find(x => String(x.id) === String(button.dataset.id))?.text), button);
  if (command === 'issue-status') return changeIssue(button.dataset.id, button.dataset.status);
  if (command === 'approve-deliverable') return action(() => approveDeliverable(button.dataset.id), button);
  if (command === 'select-contract') { state.contractId = button.dataset.id; renderProject(); return; }
  if (command === 'create-demo') return action(async () => { const result = await api('/api/demo', { method: 'POST' }); state.contractId = result.contract_id; location.hash = `project/${encodeURIComponent(result.project_id)}/contracts`; }, button, '虚构项目已建立，初稿已入库');
  if (command === 'set-current') return action(async () => { const contract = selectedContract(); await api(`${projectPath()}/contracts/${encodeURIComponent(contract.id)}/current`, { method: 'POST', body: { version_id: button.dataset.id } }); await refresh(); }, button, '当前版本已更新，历史版本保留');
  if (command === 'demo-version') return action(async () => { const contract = selectedContract(); if (!contract) throw new Error('请先新增合同'); await api(`${projectPath()}/contracts/${encodeURIComponent(contract.id)}/demo-version`, { method: 'POST', body: { stage: Number(button.dataset.stage) } }); state.diff = null; await refresh(); const versions = selectedContract().versions || []; if (versions.length >= 2) { const left = versions.at(-2).id; const right = versions.at(-1).id; const data = await api(`${projectPath()}/diff?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`); state.diff = { contractId: contract.id, leftId: left, rightId: right, data }; if (state.tab === 'contracts') renderProject(); } }, button, '新版本已保存，旧意见不会自动关闭');
  if (command === 'compare') return action(async () => { const left = document.getElementById('diff-left').value; const right = document.getElementById('diff-right').value; if (left === right) throw new Error('请选择两个不同版本'); const contract = selectedContract(); const data = await api(`${projectPath()}/diff?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`); state.diff = { contractId: contract.id, leftId: left, rightId: right, data }; renderProject(); }, button);
  if (command === 'analyze' || command === 'verify-new') return action(async () => { const contract = selectedContract(); const version = currentVersion(contract); if (!version) throw new Error('请先上传合同版本'); const result = await api(`${projectPath()}/contracts/${encodeURIComponent(contract.id)}/analyze`, { method: 'POST', body: { version_id: version.id, provider: 'demo' } }); await refresh(); const hash = `#project/${encodeURIComponent(state.projectId)}/issues`; if (location.hash !== hash) location.hash = hash; const summary = Array.isArray(result.summary) ? result.summary.join('；') : textValue(result.summary); toast(summary || '演示建议已提取，请逐条人工复核'); }, button);
  if (command === 'generate-deliverable') return action(async () => { const version = document.getElementById('export-version').value; if (!version) throw new Error('请先上传合同版本'); const kind = document.getElementById('export-kind').value; const format = document.getElementById('export-format').value; await api(`${projectPath()}/deliverables`, { method: 'POST', body: { version_id: version, kind, format } }); await refresh(); }, button, '文件已生成；对外稿仍需人工批准');
});
window.addEventListener('hashchange', () => { if (activeRewrite && !activeRewrite.busy) modal.close(); route(); });
modal.addEventListener('click', event => { if (activeRewrite?.busy) return; if (event.target === modal) { const r = modal.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) modal.close(); } });
route();
