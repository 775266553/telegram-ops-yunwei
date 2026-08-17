function showTemplateMessage(root, message, isError = false) {
  const element = root.querySelector("[data-template-message]");
  element.textContent = message;
  element.className = `banner ${isError ? "error" : "success"}`;
  element.hidden = false;
}

document.addEventListener("click", async (event) => {
  const regexTemplate = event.target.closest("[data-regex-template]");
  if (regexTemplate) {
    const tools = regexTemplate.closest("[data-regex-tools]");
    const textarea = document.getElementById(tools.dataset.target);
    const formula = regexTemplate.dataset.regexTemplate;
    textarea.value = textarea.value.trim() ? `${textarea.value.trimEnd()}\n${formula}` : formula;
    textarea.focus();
    return;
  }

  const chip = event.target.closest("[data-insert-variable]");
  if (chip) {
    const textarea = document.getElementById(chip.dataset.target);
    const value = chip.dataset.insertVariable;
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? textarea.value.length;
    textarea.setRangeText(value, start, end, "end");
    textarea.focus();
    return;
  }

  const action = event.target.closest("[data-template-action]");
  if (!action) return;
  const root = action.closest("[data-template-tools]");
  const select = root.querySelector("[data-template-select]");
  const textarea = document.getElementById(root.dataset.target);
  const nameInput = root.querySelector("[data-template-name]");

  if (action.dataset.templateAction === "apply") {
    const option = select.selectedOptions[0];
    if (!option || !option.value) return showTemplateMessage(root, "请先选择一个模板。", true);
    textarea.value = option.dataset.content || "";
    nameInput.value = option.textContent.trim();
    return showTemplateMessage(root, "模板已套用。再保存规则即可生效。");
  }

  if (action.dataset.templateAction === "save") {
    const form = new FormData();
    form.set("name", nameInput.value.trim());
    form.set("content", textarea.value.trim());
    const response = await fetch("/reply-templates/save", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) return showTemplateMessage(root, data.message || "模板保存失败。", true);
    const item = data.template;
    let option = Array.from(select.options).find((entry) => entry.value === String(item.id));
    if (!option) {
      option = document.createElement("option");
      select.appendChild(option);
    }
    option.value = item.id;
    option.textContent = item.name;
    option.dataset.content = item.content;
    option.selected = true;
    return showTemplateMessage(root, "模板已保存。现有同名模板会自动更新。");
  }

  if (action.dataset.templateAction === "delete") {
    const option = select.selectedOptions[0];
    if (!option || !option.value) return showTemplateMessage(root, "请先选择要删除的模板。", true);
    if (!window.confirm(`删除模板“${option.textContent.trim()}”？`)) return;
    const response = await fetch(`/reply-templates/${option.value}/delete`, { method: "POST" });
    const data = await response.json();
    if (!response.ok) return showTemplateMessage(root, data.message || "模板删除失败。", true);
    option.remove();
    nameInput.value = "";
    return showTemplateMessage(root, "模板已删除。");
  }
});

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-match-mode]")) {
    syncRuleMode(event.target);
    return;
  }
  if (event.target.matches("[data-schedule-account]")) {
    syncScheduleChats(event.target.form);
    return;
  }
  if (event.target.matches("[data-schedule-chat]")) {
    syncScheduleAccountForChat(event.target.form, event.target);
    syncScheduleChats(event.target.form);
    return;
  }
  if (!event.target.matches("[data-import-input]")) return;
  if (event.target.files.length) event.target.form.submit();
});

function syncScheduleChats(form) {
  const account = form?.querySelector("[data-schedule-account]");
  const chat = form?.querySelector("[data-schedule-chat]");
  if (!account || !chat) return;
  const accountIds = new Set(
    Array.from(form.querySelectorAll("[data-schedule-account]:checked")).map((option) => option.value).filter(Boolean)
  );
  if (!accountIds.size) {
    form.querySelectorAll("[data-schedule-chat]:checked").forEach((checkbox) => {
      checkbox.checked = false;
    });
  }
  Array.from(form.querySelectorAll("[data-schedule-chat]")).forEach((checkbox) => {
    const visible = !accountIds.size || !checkbox.dataset.accountId || accountIds.has(checkbox.dataset.accountId);
    const row = checkbox.closest("[data-schedule-chat-option]") || checkbox;
    row.hidden = !visible;
    if (!visible) checkbox.checked = false;
  });
  syncScheduleGroupRequirements(form);
}

function syncScheduleAccountForChat(form, chat) {
  if (!chat?.checked || !chat.dataset.accountId) return;
  const account = Array.from(form?.querySelectorAll("[data-schedule-account]") || [])
    .find((field) => field.value === chat.dataset.accountId);
  if (account) account.checked = true;
}

function syncScheduleGroupRequirements(form) {
  const isSchedule = form?.querySelector("[data-match-mode]")?.value === "schedule";
  ["[data-schedule-account]", "[data-schedule-chat]"].forEach((selector) => {
    const fields = Array.from(form?.querySelectorAll(selector) || []);
    const hasSelection = fields.some((field) => field.checked);
    fields.forEach((field, index) => {
      field.required = isSchedule && !hasSelection && index === 0;
    });
  });
}

function syncRuleMode(select) {
  const form = select.form;
  const isRegex = select.value === "regex";
  const isSchedule = select.value === "schedule";
  const sendMode = form?.querySelector("[data-send-mode]");
  const scheduledOption = sendMode?.querySelector('[data-schedule-only]');
  const keywordField = form?.querySelector("[data-match-keywords-field]");
  const scheduleFields = form?.querySelector("[data-schedule-fields]");
  const keywords = form?.querySelector("[name=keywords]");
  const messageLabel = form?.querySelector("[data-message-label]");
  const hint = form?.querySelector("[data-match-mode-hint]");
  const sendHint = form?.querySelector("[data-send-mode-hint]");

  document.querySelectorAll(`[data-regex-tools][data-mode-target="${select.id}"]`).forEach((tools) => {
    tools.hidden = !isRegex;
  });
  if (keywordField) keywordField.hidden = isSchedule;
  if (scheduleFields) scheduleFields.hidden = !isSchedule;
  if (keywords) keywords.required = !isSchedule;
  form?.querySelectorAll("[data-schedule-interval]").forEach((field) => {
    field.required = isSchedule;
  });
  syncScheduleGroupRequirements(form);
  if (sendMode && scheduledOption) {
    scheduledOption.hidden = !isSchedule;
    if (isSchedule) sendMode.value = "scheduled_group";
    else if (sendMode.value === "scheduled_group") sendMode.value = "record_only";
  }
  if (messageLabel) messageLabel.textContent = isSchedule ? "发送内容" : "回复内容";
  if (sendHint) sendHint.textContent = isSchedule
    ? "定时群发会按间隔创建发送任务，并沿用发送队列的限速和失败处理。"
    : "仅记录只生成监听记录，不会创建发送任务。";
  if (hint) hint.textContent = isSchedule
    ? "定时任务按间隔自动发送到指定群，不需要关键词。"
    : "关键词和正则用于匹配收到的消息；定时任务按时间自动发送。";
  syncScheduleChats(form);
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-match-mode]").forEach(syncRuleMode);
});
