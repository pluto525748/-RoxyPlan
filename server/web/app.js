const byId = (id) => document.getElementById(id);
const tokenStorageKey = "roxy_local_token";
const conversationStorageKey = "roxy_conversation_id";

const messages = byId("messages");
const chatForm = byId("chatForm");
const messageInput = byId("messageInput");
const sendButton = byId("sendButton");
const errorMessage = byId("errorMessage");
const connectionStatus = byId("connectionStatus");
const tokenPanel = byId("tokenPanel");
const tokenInput = byId("tokenInput");
const statusPanel = byId("statusPanel");
let activeMemoryView = "confirmed";

function requestHeaders() {
  const headers = {"Content-Type": "application/json"};
  const token = sessionStorage.getItem(tokenStorageKey);
  if (token) headers["X-Roxy-Token"] = token;
  return headers;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {...requestHeaders(), ...(options.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) tokenPanel.hidden = false;
    const detail = payload.detail;
    const message = typeof detail === "string" ? detail : detail?.message;
    throw new Error(message || `请求失败（${response.status}）`);
  }
  return payload;
}

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function showError(text) {
  errorMessage.textContent = text;
  errorMessage.hidden = !text;
}

function emptyState(text) {
  return element("div", "empty-state", text);
}

function currentConversationId() {
  return localStorage.getItem(conversationStorageKey) || "local_default";
}

function setCurrentConversation(id) {
  localStorage.setItem(conversationStorageKey, id);
}

function appendMessage(sender, text, className) {
  const wrapper = element("div", `message ${className}`);
  wrapper.append(element("span", "sender", sender), element("p", "", text));
  messages.append(wrapper);
  messages.scrollTop = messages.scrollHeight;
  return wrapper;
}

function showWelcome() {
  messages.replaceChildren();
  appendMessage("Roxy", "你好。今天想从哪件事开始？", "roxy-message");
}

function renderConversation(session) {
  messages.replaceChildren();
  byId("currentSessionTitle").textContent = session.title || "新对话";
  const entries = Array.isArray(session.messages) ? session.messages : [];
  if (!entries.length) {
    showWelcome();
    return;
  }
  entries.forEach((item) => {
    const role = item.role === "user" ? "user-message" : "roxy-message";
    const sender = item.role === "user" ? "你" : "Roxy";
    appendMessage(sender, String(item.content || ""), role);
  });
}

async function loadCurrentConversation() {
  try {
    const session = await api(`/v1/conversations/${encodeURIComponent(currentConversationId())}`);
    renderConversation(session);
  } catch (error) {
    if (currentConversationId() === "local_default") {
      byId("currentSessionTitle").textContent = "新对话";
      showWelcome();
      return;
    }
    showError(error.message);
  }
}

function setSending(sending) {
  messageInput.disabled = sending;
  sendButton.disabled = sending;
  sendButton.textContent = sending ? "发送中" : "发送";
  connectionStatus.lastChild.textContent = sending ? "正在等待回复" : "本地服务";
}

async function sendMessage(text) {
  setSending(true);
  showError("");
  const pending = appendMessage("Roxy", "正在思考...", "roxy-message pending-message");
  try {
    const payload = await api("/v1/agent/requests", {
      method: "POST",
      body: JSON.stringify({message: text, conversation_id: currentConversationId()}),
    });
    pending.querySelector("p").textContent = payload.message || "没有收到有效回复。";
    pending.classList.remove("pending-message");
  } catch (error) {
    pending.remove();
    showError(error.message || "连接失败，请确认电脑端服务仍在运行。");
  } finally {
    setSending(false);
    messageInput.focus();
  }
}

async function loadPlans() {
  const payload = await api("/v1/plans");
  const tasks = payload.data?.tasks || [];
  const list = byId("planList");
  list.replaceChildren();
  byId("planCount").textContent = `${tasks.length} 件`;
  if (!tasks.length) return list.append(emptyState("今天还没有计划。"));
  tasks.forEach((task) => {
    const row = element("div", `item-row${task.done ? " done" : ""}`);
    const check = element("button", "check-button", task.done ? "✓" : "○");
    check.type = "button";
    check.title = task.done ? "已完成" : "标记完成";
    check.disabled = Boolean(task.done);
    check.addEventListener("click", async () => {
      await api(`/v1/plans/${task.id}/complete`, {method: "POST"});
      loadPlans().catch(handleError);
    });
    const main = element("div", "item-main");
    main.append(element("p", "item-title", String(task.title || "")));
    const actions = element("div", "item-actions");
    const remove = element("button", "danger-button compact", "删除");
    remove.type = "button";
    remove.addEventListener("click", () => deletePlan(task));
    actions.append(remove);
    row.append(check, main, actions);
    list.append(row);
  });
}

async function deletePlan(task) {
  if (!window.confirm(`确定删除“${task.title}”吗？`)) return;
  const pending = await api(`/v1/plans/${task.id}`, {method: "DELETE"});
  const confirmation = pending.data?.confirmation;
  if (!confirmation?.confirmation_id) throw new Error("服务端没有返回有效确认信息。");
  const result = await api(`/v1/confirmations/${confirmation.confirmation_id}`, {method: "POST"});
  if (!result.success) throw new Error("删除没有执行成功。");
  await loadPlans();
}

async function loadActions() {
  const payload = await api("/v1/actions");
  const records = payload.data?.records || [];
  const list = byId("actionList");
  list.replaceChildren();
  byId("actionCount").textContent = `${records.length} 条`;
  if (!records.length) return list.append(emptyState("今天还没有行动记录。"));
  records.slice().reverse().forEach((record) => {
    const row = element("div", "item-row");
    const main = element("div", "item-main");
    main.append(
      element("p", "item-title", String(record.content || "")),
      element("p", "item-meta", `${record.time || ""} · ${record.source || "manual"}`),
    );
    row.append(main);
    list.append(row);
  });
}

async function loadGrowth() {
  const [reviewPayload, logPayload] = await Promise.all([
    api("/v1/growth/review"),
    api("/v1/growth/logs"),
  ]);
  byId("reviewText").textContent = reviewPayload.data?.review?.text || "今天还没有可复盘的内容。";
  const entries = logPayload.data?.entries || [];
  const list = byId("growthList");
  list.replaceChildren();
  byId("growthCount").textContent = `${entries.length} 天`;
  if (!entries.length) return list.append(emptyState("成长日志还是空的。"));
  entries.slice().reverse().forEach((entry) => {
    const review = entry.review || {};
    const row = element("div", "item-row");
    const main = element("div", "item-main");
    main.append(
      element("p", "item-title", String(entry.date || "")),
      element("p", "item-meta", `计划 ${review.total || 0} 件 · 完成 ${review.done || 0} 件 · 行动 ${(review.actions || []).length} 条`),
    );
    row.append(main);
    list.append(row);
  });
}

async function loadMemories(query = "") {
  const suffix = query ? `?query=${encodeURIComponent(query)}` : "";
  const payload = await api(`/v1/memories${suffix}`);
  const memoriesData = payload.data?.memories || [];
  const list = byId("memoryList");
  list.replaceChildren();
  byId("memoryCount").textContent = `${memoriesData.length} 条`;
  if (!memoriesData.length) return list.append(emptyState("没有找到符合条件的长期记忆。"));
  memoriesData.forEach((memory) => {
    const row = element("div", "item-row");
    const main = element("div", "item-main");
    const title = element("p", "item-title");
    title.append(element("span", "category-label", String(memory.category || "other")));
    title.append(document.createTextNode(String(memory.content || "")));
    const metadata = [];
    if (memory.importance !== undefined) metadata.push(`重要度 ${memory.importance}`);
    if (memory.source) metadata.push(`来源 ${memory.source}`);
    if (memory.created_at) metadata.push(String(memory.created_at));
    main.append(title);
    if (metadata.length) main.append(element("p", "item-meta", metadata.join(" · ")));
    row.append(main);
    list.append(row);
  });
}

async function loadMemoryCandidates() {
  const payload = await api("/v1/memory-candidates");
  const candidates = payload.candidates || [];
  const list = byId("candidateList");
  list.replaceChildren();
  if (!candidates.length) return list.append(emptyState("现在没有待审核候选。"));
  candidates.forEach((candidate) => {
    const row = element("div", "item-row");
    const main = element("div", "item-main");
    const title = element("p", "item-title");
    title.append(element("span", "category-label", String(candidate.category || "other")));
    if (candidate.sensitivity && candidate.sensitivity !== "normal") {
      title.append(element("span", "sensitivity-label", "敏感信息"));
    }
    title.append(document.createTextNode(String(candidate.content || "")));
    main.append(
      title,
      element("p", "item-meta", `来源 ${candidate.source || "conversation"} · 置信度 ${candidate.confidence ?? "-"} · ${candidate.created_at || ""}`),
      element("p", "item-meta", `原因：${candidate.reason || "等待人工审核"}`),
      element("p", "item-meta", `原始表达：${candidate.source_text || ""}`),
    );
    const actions = element("div", "item-actions");
    const accept = element("button", "primary-button compact", "接受");
    accept.type = "button";
    accept.addEventListener("click", () => acceptCandidate(candidate).catch(handleError));
    const edit = element("button", "secondary-button compact", "编辑后接受");
    edit.type = "button";
    edit.addEventListener("click", () => acceptCandidate(candidate, true).catch(handleError));
    const reject = element("button", "danger-button compact", "拒绝");
    reject.type = "button";
    reject.addEventListener("click", () => rejectCandidate(candidate).catch(handleError));
    actions.append(accept, edit, reject);
    row.append(main, actions);
    list.append(row);
  });
}

async function acceptCandidate(candidate, editFirst = false) {
  let content = String(candidate.content || "");
  if (editFirst) {
    const edited = window.prompt("确认最终写入的长期记忆内容", content);
    if (!edited?.trim()) return;
    content = edited.trim();
  }
  const sensitive = candidate.sensitivity && candidate.sensitivity !== "normal" ? "\n这是一条敏感候选。" : "";
  if (!window.confirm(`确认写入以下长期记忆？\n\n${content}${sensitive}`)) return;
  const path = editFirst
    ? `/v1/memory-candidates/${candidate.id}/accept-edited`
    : `/v1/memory-candidates/${candidate.id}/accept`;
  const body = editFirst ? {content, confirmed: true} : {confirmed: true};
  const result = await api(path, {method: "POST", body: JSON.stringify(body)});
  if (result.status === "conflict") {
    await setMemoryView("conflicts");
    return;
  }
  await Promise.all([loadMemoryCandidates(), loadMemories(), loadMemoryAudit()]);
}

async function rejectCandidate(candidate) {
  if (!window.confirm(`确定拒绝候选“${candidate.content}”吗？`)) return;
  await api(`/v1/memory-candidates/${candidate.id}/reject`, {method: "POST"});
  await Promise.all([loadMemoryCandidates(), loadMemoryAudit()]);
}

async function rejectLowValueCandidates() {
  if (!window.confirm("确定批量拒绝低置信度或旧版模糊候选吗？高质量候选不会受影响。")) return;
  await api("/v1/memory-candidates/reject-low-value", {
    method: "POST",
    body: JSON.stringify({confirmed: true}),
  });
  await Promise.all([loadMemoryCandidates(), loadMemoryAudit()]);
}

async function loadMemoryConflicts() {
  const payload = await api("/v1/memory-conflicts");
  const conflicts = payload.conflicts || [];
  const list = byId("conflictList");
  list.replaceChildren();
  byId("conflictCount").textContent = `${conflicts.length} 条`;
  if (!conflicts.length) return list.append(emptyState("现在没有待处理冲突。"));
  conflicts.forEach((conflict) => {
    const row = element("div", "item-row");
    const main = element("div", "item-main");
    const title = element("p", "item-title", `冲突 ${conflict.id} · ${conflict.category || "other"} · ${conflict.relation || "conflict"}`);
    const comparison = element("div", "conflict-comparison");
    comparison.append(
      element("span", "", `旧：${conflict.old_memory?.content || "已不存在"}`),
      element("span", "", `新：${conflict.new_content || ""}`),
    );
    main.append(title, comparison, element("p", "item-meta", `${conflict.created_at || ""} · 来源 ${conflict.source || "unknown"}`));
    const actions = element("div", "item-actions");
    [
      ["保留旧", "keep_old"],
      ["使用新", "use_new"],
      ["合并", "merge"],
      ["两条都保留", "keep_both"],
      ["暂不处理", "defer"],
    ].forEach(([label, resolution]) => {
      const button = element("button", resolution === "use_new" ? "danger-button compact" : "secondary-button compact", label);
      button.type = "button";
      button.addEventListener("click", () => resolveConflict(conflict, resolution).catch(handleError));
      actions.append(button);
    });
    row.append(main, actions);
    list.append(row);
  });
}

async function resolveConflict(conflict, resolution) {
  let mergedContent = null;
  if (resolution === "merge") {
    const initial = `${conflict.old_memory?.content || ""}；${conflict.new_content || ""}`;
    const value = window.prompt("编辑合并后的最终长期记忆", initial);
    if (!value?.trim()) return;
    mergedContent = value.trim();
  }
  const labels = {keep_old: "保留旧记忆", use_new: "使用新记忆", merge: "合并记忆", keep_both: "两条都保留", defer: "暂不处理"};
  if (!window.confirm(`确定选择“${labels[resolution]}”吗？`)) return;
  await api(`/v1/memory-conflicts/${conflict.id}/resolve`, {
    method: "POST",
    body: JSON.stringify({resolution, merged_content: mergedContent, confirmed: true}),
  });
  await Promise.all([loadMemoryConflicts(), loadMemories(), loadMemoryAudit()]);
}

function auditValueText(value) {
  if (!value) return "无";
  if (typeof value === "string") return value;
  const parts = [];
  if (value.id) parts.push(`ID ${value.id}`);
  if (value.category) parts.push(String(value.category));
  if (value.status) parts.push(String(value.status));
  if (value.content_redacted) parts.push("内容已隐藏");
  return parts.join(" · ") || "已记录";
}

async function loadMemoryAudit() {
  const payload = await api("/v1/memory-audit?limit=100");
  const entries = payload.entries || [];
  const list = byId("auditList");
  list.replaceChildren();
  byId("auditCount").textContent = `${entries.length} 条`;
  if (!entries.length) return list.append(emptyState("还没有记忆治理记录。"));
  entries.slice().reverse().forEach((entry) => {
    const row = element("div", "item-row");
    const main = element("div", "item-main");
    main.append(
      element("p", "item-title", `${entry.action || "unknown"} · ${entry.memory_id || "-"}`),
      element("p", "item-meta", `旧：${auditValueText(entry.old_value)} → 新：${auditValueText(entry.new_value)}`),
      element("p", "item-meta", `${entry.timestamp || ""} · ${entry.source || "unknown"}`),
    );
    row.append(main);
    list.append(row);
  });
}

async function setMemoryView(name) {
  activeMemoryView = name;
  document.querySelectorAll("[data-memory-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.memoryPanel !== name;
  });
  document.querySelectorAll("[data-memory-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.memoryView === name);
  });
  const memoryLoaders = {
    confirmed: () => loadMemories(byId("memorySearchInput").value.trim()),
    candidates: loadMemoryCandidates,
    conflicts: loadMemoryConflicts,
    audit: loadMemoryAudit,
  };
  await memoryLoaders[name]?.();
}

async function loadSessions() {
  const sessions = await api("/v1/conversations");
  const list = byId("sessionList");
  list.replaceChildren();
  if (!sessions.length) return list.append(emptyState("还没有 Web 会话。"));
  sessions.forEach((session) => {
    const row = element("div", "item-row");
    const main = element("div", "item-main");
    main.append(
      element("p", "item-title", String(session.title || "新对话")),
      element("p", "item-meta", `${session.updated_at || ""} · ${session.message_count || 0} 条消息`),
    );
    const actions = element("div", "item-actions");
    const open = element("button", "secondary-button compact", "打开");
    open.type = "button";
    open.addEventListener("click", () => openSession(session.session_id));
    const rename = element("button", "text-button compact", "改名");
    rename.type = "button";
    rename.addEventListener("click", () => renameSession(session.session_id, session.title));
    actions.append(open, rename);
    row.append(main, actions);
    list.append(row);
  });
}

async function createSession() {
  const session = await api("/v1/conversations", {
    method: "POST",
    body: JSON.stringify({title: "新对话"}),
  });
  setCurrentConversation(session.session_id);
  byId("currentSessionTitle").textContent = session.title;
  showWelcome();
  setView("chat");
}

async function openSession(sessionId) {
  setCurrentConversation(sessionId);
  await loadCurrentConversation();
  setView("chat");
}

async function renameSession(sessionId, oldTitle) {
  const title = window.prompt("新的会话标题", oldTitle || "新对话");
  if (!title?.trim()) return;
  const session = await api(`/v1/conversations/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    body: JSON.stringify({title: title.trim()}),
  });
  if (sessionId === currentConversationId()) byId("currentSessionTitle").textContent = session.title;
  await loadSessions();
}

async function loadStatus() {
  try {
    const [state, modelStatus] = await Promise.all([
      api("/v1/status"),
      api("/v1/model-status"),
    ]);
    byId("serviceState").textContent = state.service === "ok" ? "正常" : "异常";
    byId("providerState").textContent = modelStatus.provider === "deepseek" ? "DeepSeek 在线" : modelStatus.provider === "ollama" ? "本地 Ollama" : "未配置";
    const deepseekStatus = modelStatus.deepseek?.status || "not_configured";
    byId("deepseekState").textContent = {online: "在线", offline: "离线", not_configured: "未配置"}[deepseekStatus] || "未知";
    byId("ollamaState").textContent = {online: "在线", offline: "离线", not_configured: "未配置"}[state.ollama] || "未知";
    byId("modelState").textContent = state.model || "未配置";
    byId("modeState").textContent = {economy: "省钱", auto: "自动", quality: "高质量", legacy: "兼容模式"}[modelStatus.model_mode] || "自动";
    byId("fallbackState").textContent = modelStatus.fallback_active ? "本地降级中" : "未启用";
    byId("knowledgeState").textContent = String(state.knowledge_files ?? 0);
  } catch (error) {
    byId("serviceState").textContent = "检查失败";
    byId("providerState").textContent = "未知";
    byId("deepseekState").textContent = "未知";
    byId("ollamaState").textContent = "未知";
  }
}

function handleError(error) {
  showError(error instanceof Error ? error.message : "操作没有完成，请稍后再试。");
}

const loaders = {
  chat: loadCurrentConversation,
  plans: loadPlans,
  actions: loadActions,
  growth: loadGrowth,
  memories: () => setMemoryView(activeMemoryView),
  sessions: loadSessions,
};

function setView(name) {
  document.querySelectorAll("[data-view-panel]").forEach((panel) => {
    const active = panel.dataset.viewPanel === name;
    panel.hidden = !active;
    panel.classList.toggle("active", active);
  });
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === name);
  });
  showError("");
  Promise.resolve(loaders[name]?.()).catch(handleError);
}

document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = messageInput.value.trim();
  if (!text) return;
  appendMessage("你", text, "user-message");
  messageInput.value = "";
  sendMessage(text);
});

messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
});

byId("planForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = byId("planInput");
  try {
    await api("/v1/plans", {method: "POST", body: JSON.stringify({title: input.value.trim()})});
    input.value = "";
    await loadPlans();
  } catch (error) { handleError(error); }
});

byId("actionForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = byId("actionInput");
  try {
    await api("/v1/actions", {method: "POST", body: JSON.stringify({content: input.value.trim()})});
    input.value = "";
    await loadActions();
  } catch (error) { handleError(error); }
});

byId("memorySearchForm").addEventListener("submit", (event) => {
  event.preventDefault();
  loadMemories(byId("memorySearchInput").value.trim()).catch(handleError);
});

document.querySelectorAll("[data-memory-view]").forEach((button) => {
  button.addEventListener("click", () => setMemoryView(button.dataset.memoryView).catch(handleError));
});

byId("rejectLowValueButton").addEventListener("click", () => rejectLowValueCandidates().catch(handleError));

byId("saveReviewButton").addEventListener("click", async () => {
  try {
    await api("/v1/growth/review/save", {method: "POST"});
    await loadGrowth();
  } catch (error) { handleError(error); }
});

byId("newSessionButton").addEventListener("click", () => createSession().catch(handleError));
byId("sessionCreateButton").addEventListener("click", () => createSession().catch(handleError));
byId("renameSessionButton").addEventListener("click", () => renameSession(currentConversationId(), byId("currentSessionTitle").textContent).catch(handleError));

byId("tokenToggle").addEventListener("click", () => {
  tokenPanel.hidden = !tokenPanel.hidden;
  if (!tokenPanel.hidden) {
    tokenInput.value = sessionStorage.getItem(tokenStorageKey) || "";
    tokenInput.focus();
  }
});

byId("saveTokenButton").addEventListener("click", () => {
  const value = tokenInput.value.trim();
  if (value) sessionStorage.setItem(tokenStorageKey, value);
  else sessionStorage.removeItem(tokenStorageKey);
  tokenInput.value = "";
  tokenPanel.hidden = true;
  showError("");
  loadStatus();
  loaders.chat().catch(handleError);
});

byId("statusToggle").addEventListener("click", () => {
  statusPanel.hidden = !statusPanel.hidden;
  if (!statusPanel.hidden) loadStatus();
});

showWelcome();
loadStatus();
loadCurrentConversation();
