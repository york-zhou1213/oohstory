"use strict";

const root = document.documentElement;
const themeButton = document.querySelector("[data-theme-toggle]");
const storedTheme = (() => {
  try { return window.localStorage.getItem("oohstory-admin-theme"); }
  catch (_) { return null; }
})();
const applyTheme = (theme) => {
  const next = theme === "dark" ? "dark" : "light";
  root.dataset.theme = next;
  if (themeButton) {
    themeButton.setAttribute("aria-label", next === "dark" ? "切换为明亮主题" : "切换为深色主题");
    themeButton.title = next === "dark" ? "切换为明亮主题" : "切换为深色主题";
    themeButton.querySelector("span").textContent = next === "dark" ? "☀" : "◐";
  }
};
applyTheme(storedTheme || "light");
themeButton?.addEventListener("click", () => {
  const next = root.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next);
  try { window.localStorage.setItem("oohstory-admin-theme", next); }
  catch (_) { /* Theme preference is optional. */ }
});

const navToggle = document.querySelector("[data-nav-toggle]");
const navScrim = document.querySelector("[data-nav-scrim]");
const sidebar = document.querySelector("[data-sidebar]");
const setNavOpen = (open) => {
  document.body.classList.toggle("nav-open", open);
  navToggle?.setAttribute("aria-expanded", String(open));
  navToggle?.setAttribute("aria-label", open ? "关闭管理导航" : "打开管理导航");
  navScrim?.setAttribute("aria-hidden", String(!open));
  if (open) sidebar?.querySelector("a[aria-current='page']")?.focus({ preventScroll: true });
};
navToggle?.addEventListener("click", () => setNavOpen(!document.body.classList.contains("nav-open")));
navScrim?.addEventListener("click", () => setNavOpen(false));
sidebar?.addEventListener("click", (event) => {
  if (event.target.closest("a") && window.matchMedia("(max-width: 760px)").matches) setNavOpen(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && document.body.classList.contains("nav-open")) {
    setNavOpen(false);
    navToggle?.focus();
  }
});
window.matchMedia("(min-width: 761px)").addEventListener?.("change", (event) => {
  if (event.matches) setNavOpen(false);
});

document.addEventListener("submit", (event) => {
  const form = event.target.closest("form[data-confirm]");
  if (!form) return;
  const action = event.submitter?.value || form.querySelector('input[name="action"]')?.value || "操作";
  const target = form.querySelector('input[name="target"]')?.value || "该单元";
  if (action === "delete_catalog_books" && form.matches("[data-delete-catalog-form]")) {
    const selected = [
      ...document.querySelectorAll(`input[name="catalog_id"][form="${form.id}"]:checked`),
    ];
    const expected = `确认删除${selected.length}本书`;
    const confirmation = form.querySelector('input[name="confirmation"]');
    if (!selected.length || selected.length > 100 || confirmation?.value.trim() !== expected) {
      event.preventDefault();
      window.alert(
        !selected.length
          ? "请先明确勾选要删除的小说"
          : selected.length > 100
            ? "单次最多删除 100 本小说"
            : `二次确认短语不正确，请输入：${expected}`
      );
      confirmation?.focus();
      return;
    }
    const confirmed = window.confirm(
      `确认联动删除所选 ${selected.length} 本小说？\n\n书目、正文/章节、阅读映射、索引、封面和拆书成果会移入可恢复归档。`
    );
    if (!confirmed) event.preventDefault();
    return;
  }
  const prompt = `${form.dataset.confirm}\n\n${action} → ${target}`;
  if (!window.confirm(prompt)) event.preventDefault();
});

document.querySelectorAll(".catalog-bulk-actions").forEach((form) => {
  const counter = form.querySelector("[data-selection-count]");
  const update = () => {
    const total = document.querySelectorAll(
      `input[name="catalog_id"][form="${form.id}"]:checked`
    ).length;
    if (counter) counter.textContent = `已选择 ${total} 本`;
    if (form.matches("[data-delete-catalog-form]")) {
      const expected = `确认删除${total}本书`;
      const hint = form.querySelector("[data-delete-confirmation-hint]");
      const confirmation = form.querySelector('input[name="confirmation"]');
      if (hint) hint.textContent = expected;
      if (confirmation) confirmation.placeholder = total ? expected : "先选择小说";
    }
  };
  document.addEventListener("change", (event) => {
    if (event.target.matches(`input[name="catalog_id"][form="${form.id}"]`)) update();
  });
  update();
});

document.querySelectorAll("[data-select-page]").forEach((button) => {
  button.addEventListener("click", () => {
    const formId = button.dataset.selectPage;
    const boxes = [...document.querySelectorAll(`input[name="catalog_id"][form="${formId}"]:not(:disabled)`)];
    const shouldSelect = boxes.some((box) => !box.checked);
    boxes.forEach((box) => {
      box.checked = shouldSelect;
      box.dispatchEvent(new Event("change", { bubbles: true }));
    });
    button.textContent = shouldSelect ? "取消本页全选" : "选择本页全部";
  });
});

const serviceSearch = document.querySelector("[data-service-search]");
const serviceStatus = document.querySelector("[data-service-status]");
const serviceCount = document.querySelector("[data-service-count]");
const serviceCards = [...document.querySelectorAll("[data-service-card]")];
const filterServices = () => {
  const query = (serviceSearch?.value || "").trim().toLocaleLowerCase("zh-CN");
  const status = serviceStatus?.value || "all";
  let visible = 0;
  serviceCards.forEach((card) => {
    const matchesText = !query || (card.dataset.serviceText || "").toLocaleLowerCase("zh-CN").includes(query);
    const matchesStatus = status === "all" || card.dataset.serviceState === status;
    card.hidden = !(matchesText && matchesStatus);
    if (!card.hidden) visible += 1;
  });
  if (serviceCount) serviceCount.textContent = `显示 ${visible} / ${serviceCards.length} 个单元`;
};
serviceSearch?.addEventListener("input", filterServices);
serviceStatus?.addEventListener("change", filterServices);

const auditSearch = document.querySelector("[data-audit-search]");
const auditCount = document.querySelector("[data-audit-count]");
const auditRows = [...document.querySelectorAll("[data-audit-row]")];
auditSearch?.addEventListener("input", () => {
  const query = auditSearch.value.trim().toLocaleLowerCase("zh-CN");
  let visible = 0;
  auditRows.forEach((row) => {
    row.hidden = Boolean(query) && !(row.dataset.auditText || "").toLocaleLowerCase("zh-CN").includes(query);
    if (!row.hidden) visible += 1;
  });
  if (auditCount) auditCount.textContent = `显示 ${visible} / ${auditRows.length} 条`;
});

const refreshingJob = document.querySelector("[data-job-refresh]");
if (refreshingJob) {
  window.setTimeout(() => window.location.reload(), 5000);
}

const taskConfig = document.querySelector("[data-task-config]");
if (taskConfig) {
  const runner = document.querySelector("#task-runner");
  const profile = document.querySelector("#task-profile");
  const reasoning = document.querySelector("#task-reasoning");
  const syncProfiles = () => {
    if (!runner || !profile) return;
    let first = null;
    [...profile.options].forEach((option) => {
      const visible = option.dataset.runner === runner.value;
      option.hidden = !visible;
      option.disabled = !visible;
      if (visible && !first) first = option;
    });
    if (profile.selectedOptions[0]?.disabled && first) first.selected = true;
    syncReasoning();
  };
  const syncReasoning = () => {
    if (!runner || !profile || !reasoning) return;
    let first = null;
    [...reasoning.options].forEach((option) => {
      const visible = option.dataset.runner === runner.value && option.dataset.profile === profile.value;
      option.hidden = !visible;
      option.disabled = !visible;
      if (visible && !first) first = option;
    });
    if (reasoning.selectedOptions[0]?.disabled && first) first.selected = true;
  };
  runner?.addEventListener("change", syncProfiles);
  profile?.addEventListener("change", syncReasoning);
  syncProfiles();
  document.querySelectorAll(".deconstruction-action-form").forEach((form) => {
    form.addEventListener("submit", () => {
      [["runner_id", runner], ["profile_id", profile], ["reasoning_effort", reasoning]].forEach(([name, field]) => {
        if (!field) return;
        let hidden = form.querySelector(`input[name="${name}"]`);
        if (!hidden) {
          hidden = document.createElement("input");
          hidden.type = "hidden";
          hidden.name = name;
          form.appendChild(hidden);
        }
        hidden.value = field.value;
      });
    });
  });
}

document.querySelectorAll("[data-cover-upload]").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = form.querySelector('input[type="file"]')?.files?.[0];
    const csrf = form.querySelector('input[name="csrf_token"]')?.value || "";
    const status = form.querySelector('[role="status"]');
    if (!file) return;
    if (file.size < 1024 || file.size > 12 * 1024 * 1024) {
      if (status) status.textContent = "文件大小必须在 1KB–12MB 之间";
      return;
    }
    if (status) status.textContent = "正在安全上传…";
    try {
      const response = await fetch(form.dataset.url, {
        method: "POST",
        headers: { "Content-Type": file.type, "X-CSRF-Token": csrf },
        body: file,
        credentials: "same-origin",
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "上传失败");
      if (status) status.textContent = payload.message || "封面已保存";
    } catch (error) {
      if (status) status.textContent = error.message || "上传失败";
    }
  });
});

document.querySelectorAll("[data-book-cover]").forEach((image) => {
  image.addEventListener("error", () => {
    image.hidden = true;
    image.closest(".catalog-novel-cover")?.classList.add("cover-missing");
  });
});
