import { api } from "../../api/workbench.js";
import { empty, section } from "./panel.js";
import { table } from "./table.js";
import { el } from "../../utils/dom.js";
import { time } from "../../utils/format.js";

export const ReceiptStates = Object.freeze({
  restored: "已恢复",
  incomplete: "缺件",
  pending: "待核对",
});

export const ImportStates = Object.freeze({
  running: "导入中",
  done: "已完成",
  failed: "中断待恢复",
});

function badge(state, labels) {
  return el("span", {class: `status state-${state}`, "data-state": state}, labels[state] || state);
}

function part(content, key) {
  const value = content?.[key];
  if (!value) return "—";
  if (key === "erratum") {
    return `#${value.seq} ${value.issue_date} · ${value.chart} · ${value.notice}`;
  }
  return `${value.actor} · ${value.note}`;
}

function searchForm(onSearch) {
  const form = el("form", {class: "archive-search"},
    el("label", {class: "field"}, el("span", {}, "来源"),
      el("input", {name: "source", maxlength: 48, placeholder: "如 甲印务台"})),
    el("label", {class: "field"}, el("span", {}, "旧批号"),
      el("input", {name: "old_no", maxlength: 48, placeholder: "如 A-105"})),
    el("label", {class: "field"}, el("span", {}, "状态"),
      el("select", {name: "state"}, [el("option", {value: ""}, "全部状态"), ...Object.entries(ReceiptStates)
        .map(([value, label]) => el("option", {value}, label))])),
    el("button", {type: "submit"}, "查询补回单"));
  form.addEventListener("submit", event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form));
    onSearch(Object.fromEntries(Object.entries(values).filter(([, value]) => value)));
  });
  return form;
}

function receiptTable(receipts, user, onResolved) {
  if (!receipts.length) return empty("此来源下还没有补回的核收单。");
  return table(["来源", "旧批号", "原勘误", "原签字", "状态", "说明"], receipts.map(receipt => {
    const pending = receipt.state === "pending";
    const actions = [];
    if (pending && user.role === "reviewer") {
      actions.push(el("button", {type: "button", class: "quiet", onclick: () => onResolved(receipt.id, "adopt")}, "采后来单"));
      actions.push(el("button", {type: "button", class: "quiet", onclick: () => onResolved(receipt.id, "keep")}, "保留原单"));
    }
    const note = pending
      ? el("div", {class: "stack"}, el("p", {}, "同来源同号收到了内容不同的单，转待核对。"),
        el("details", {}, el("summary", {}, "查看后来收到的单"),
          el("p", {}, `勘误：${part(receipt.conflict, "erratum")}`),
          el("p", {}, `签字：${part(receipt.conflict, "signature")}`)),
        actions.length ? el("div", {class: "stack"}, actions) : empty("待复核员裁定。"))
      : receipt.note || "—";
    return [receipt.source, receipt.old_no, part(receipt.content, "erratum"),
      part(receipt.content, "signature"), badge(receipt.state, ReceiptStates), note];
  }), "补回核收单");
}

function importTable(imports, onSelect) {
  if (!imports.length) return empty("还没有补回留档。");
  return table(["来源", "留档", "进度", "状态", "处理统计", "提交时间"], imports.map(item => {
    const summary = Object.entries(item.counts || {})
      .filter(([, value]) => value)
      .map(([key, value]) => `${ReceiptStates[key] || key} ${value}`)
      .join(" · ") || "—";
    return [item.source,
      el("a", {href: "#", onclick: event => { event.preventDefault(); onSelect(item.id); }}, item.archive_key),
      `${item.cursor}/${item.total}`,
      el("div", {class: "stack"}, badge(item.state, ImportStates),
        item.state === "failed" ? el("p", {role: "alert"}, item.failure || "导入中断") : null),
      summary, time(item.created_at)];
  }), "留档导入");
}

export function archivePanel(user) {
  const issue = el("p", {role: "alert", class: "feedback"});
  const results = el("div", {class: "stack"});
  const importsBox = el("div", {});
  const upload = el("form", {id: "archive-form", class: "archive-upload"},
    el("label", {class: "field"}, el("span", {}, "印务台（来源）"),
      el("input", {name: "source", required: true, maxlength: 48})),
    el("label", {class: "field"}, el("span", {}, "留档标识"),
      el("input", {name: "archive_key", maxlength: 96, placeholder: "可留空：相同内容即同一份留档"})),
    el("label", {class: "field"}, el("span", {}, "留档明细（JSON 数组）"),
      el("textarea", {name: "entries", required: true, rows: 10, spellcheck: "false"},
        `[{"old_no": "A-105", "erratum": {"seq": 105, "issue_date": "2026-11-23", "chart": "CN-12301", "notice": "外堤浮标灯质更正为黄闪"}, "signature": {"actor": "reviewer", "note": "复核无误", "created_at": 1700000000}}]`)),
    issue, el("button", {type: "submit"}, "补回留档"));

  let lastParams = {};
  let detailId = null;
  let pending = 0;

  async function loadReceipts(params = lastParams, importId = detailId) {
    lastParams = params;
    detailId = importId;
    const ticket = ++pending;
    results.replaceChildren(el("p", {}, "正在查询…"));
    try {
      const receipts = await api.receipts(params);
      if (ticket !== pending) return;
      const detail = importId ? await api.archive(importId) : null;
      if (ticket !== pending) return;
      results.replaceChildren(
        detail ? el("div", {class: "import-line"},
          el("p", {}, `留档 #${detail.id} · ${detail.source} · 进度 ${detail.cursor}/${detail.total} · ${ImportStates[detail.state]}`),
          detail.state === "done" ? null
            : el("button", {type: "button", class: "quiet", onclick: () => resumeImport(detail.id)}, "接着恢复")) : null,
        receiptTable(receipts, user, resolve));
    } catch (error) {
      if (ticket === pending) results.replaceChildren(el("p", {role: "alert"}, error.message));
    }
  }

  async function loadImports() {
    try {
      importsBox.replaceChildren(importTable(await api.archives(), id => loadReceipts(lastParams, id)));
    } catch (error) {
      importsBox.replaceChildren(el("p", {role: "alert"}, error.message));
    }
  }

  async function resolve(id, decision) {
    issue.textContent = "";
    try {
      await api.resolveReceipt(id, decision);
      await loadReceipts();
      await loadImports();
    } catch (error) {
      issue.textContent = error.message;
    }
  }

  async function resumeImport(id) {
    issue.textContent = "";
    try {
      await api.resumeArchive(id);
      await loadReceipts(lastParams, id);
      await loadImports();
      setTimeout(() => refreshImport(id), 900);
    } catch (error) {
      issue.textContent = error.message;
    }
  }

  async function refreshImport(id) {
    try {
      const detail = await api.archive(id);
      if (detailId === id) await loadReceipts(lastParams, id);
      await loadImports();
      if (detail.state !== "done") setTimeout(() => refreshImport(id), 900);
    } catch {
      // The next manual refresh reports the failure.
    }
  }

  upload.addEventListener("submit", async event => {
    event.preventDefault();
    issue.textContent = "";
    let entries;
    try {
      entries = JSON.parse(new FormData(upload).get("entries"));
    } catch {
      issue.textContent = "留档明细 JSON 无法解析，请检查括号、引号和逗号";
      return;
    }
    const button = upload.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const form = new FormData(upload);
      const body = {source: form.get("source"), entries};
      if (form.get("archive_key")) body.archive_key = form.get("archive_key");
      const view = await api.uploadArchive(body);
      await loadReceipts(lastParams, null);
      await loadImports();
      await loadReceipts(lastParams, view.id);
      if (view.state !== "done") setTimeout(() => refreshImport(view.id), 900);
    } catch (error) {
      issue.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });

  loadImports();
  loadReceipts({}, null);
  return section("留档补回", upload, searchForm(params => loadReceipts(params, null)),
    el("p", {class: "empty"}, "同一旧批号按来源分别可查；本库原有的核收单仍按批次号查询，不在此列。"),
    results, el("h2", {}, "导入进度"),
    el("p", {class: "empty"}, "补回按批推进，中断后可接着恢复；重试同一份留档不会重复建单。"),
    importsBox);
}
