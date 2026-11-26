export const States = Object.freeze({
  queued: "等待执行", done: "已完成", failed: "执行失败",
});

export const Actions = Object.freeze({
  create_case: "登记资料", enqueue: "提交执行", done: "执行完成", failed: "执行失败", review: "复核签署",
  archive_import: "补回留档", archive_resume: "恢复导入", archive_done: "补回完成",
  archive_restore: "恢复核收单", archive_conflict: "转待核对", archive_resolve: "裁定核收单",
  archive_failed: "补回中断",
});
