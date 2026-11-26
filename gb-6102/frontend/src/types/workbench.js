/**
 * @typedef {{id:number, code:string, title:string, description:string, payload:Object, created_by:string}} Case
 * @typedef {{id:number, case_id:number, case_title:string, status:string, parameter:number, result:Object|null}} Run
 * @typedef {{run_id:number, actor:string, note:string, created_at:number}} Review
 * @typedef {{id:number, source:string, total:number, cursor:number, state:string, failure:string|null, counts:Object|null, created_at:number}} ArchiveImport
 * @typedef {{id:number, source:string, old_no:string, content:Object, state:string, note:string|null, conflict:Object|null}} ArchiveReceipt
 */

/** Reject a malformed configuration before rendering its navigation. */
export function validateConfig(config) {
  if (!config || !Array.isArray(config.modules) || config.modules.length !== 4 ||
      !Number.isInteger(config.default_parameter) || !config.title) {
    throw new Error("工作台配置不完整");
  }
  return config;
}
