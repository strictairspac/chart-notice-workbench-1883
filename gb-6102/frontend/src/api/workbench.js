import { request } from "./http.js";

export const api = {
  config: () => request("/workbench"),
  login: data => request("/auth/login", {method: "POST", data}),
  cases: () => request("/cases"),
  createCase: data => request("/cases", {method: "POST", data}),
  runs: () => request("/runs"),
  run: (id, signal) => request(`/runs/${id}`, {signal}),
  enqueue: (id, data) => request(`/cases/${id}/runs`, {method: "POST", data}),
  review: id => request(`/runs/${id}/review`),
  submitReview: (id, data) => request(`/runs/${id}/review`, {method: "POST", data}),
  export: id => request(`/runs/${id}/export`, {raw: true}),
  audit: () => request("/audit", {signal: AbortSignal.timeout(5000)}),
  archives: () => request("/archives"),
  archive: id => request(`/archives/${id}`),
  uploadArchive: data => request("/archives", {method: "POST", data}),
  resumeArchive: id => request(`/archives/${id}/resume`, {method: "POST"}),
  receipts: params => request(`/archives/receipts?${new URLSearchParams(params)}`),
  resolveReceipt: (id, decision) => request(`/archives/receipts/${id}/resolve`, {method: "POST", data: {decision}}),
};
