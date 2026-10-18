// 与 everrec-server 的 GET /api/v1/devices 对应

// 所有请求都走这里：非 2xx 一律抛错，优先用服务器给的 detail 原话
async function request(url: string, init?: RequestInit): Promise<Response> {
  const r = await fetch(url, init);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `服务器返回 ${r.status}`);
  }
  return r;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  return (await request(url, init)).json();
}

function withJsonBody(method: string, data: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) };
}

export type Status = "ok" | "offline_local" | "abnormal" | "lost";

export interface LogLine {
  at: string;
  level: string;
  message: string;
}

export interface Owner {
  name: string;
  dept: string;
  note: string;
  from_id: number;
  from_name: string;
  inherited: boolean;
}

export interface Device {
  id: number;
  code: string;
  hostname: string;
  version: string;
  status: Status;
  last_heartbeat: string;
  seconds_since_heartbeat: number;
  recording: boolean;
  profile: string | null;
  current_segment: string | null;
  checks: Record<string, string>;
  location: Record<string, string>;
  node_id: number | null;
  owner: Owner | null;
  problems: string[];
  restarts: number;
  pending_sync: number;
  disk_used: number;
  has_thumbnail: boolean;
  thumbnail_at: string | null;
  recent_logs: LogLine[];
  camera_resolutions: string[];
}

export interface DevicesResponse {
  items: Device[];
  summary: Record<Status, number>;
  mass_lost: boolean;
}

export async function fetchDevices(prefix: string): Promise<DevicesResponse> {
  const q = prefix ? `?prefix=${encodeURIComponent(prefix)}` : "";
  return requestJson(`/api/v1/devices${q}`);
}

export interface Recording {
  id: number;
  code: string;
  start_at: string;
  end_at: string;
  size: number;
  integrity: string;
  status: string;
  locked: boolean;
}

export async function fetchRecordings(code: string, from: string, to: string): Promise<Recording[]> {
  const params = new URLSearchParams({ code, from, to });
  return requestJson(`/api/v1/recordings?${params}`);
}

export function recordingFileUrl(id: number): string {
  return `/api/v1/recordings/${id}/file`;
}

export async function setRecordingLock(id: number, locked: boolean): Promise<void> {
  await request(`/api/v1/recordings/${id}/${locked ? "lock" : "unlock"}`, { method: "POST" });
}

// ---- 登录（tasks.md WP5） ----

export async function login(password: string): Promise<void> {
  await request("/api/v1/login", withJsonBody("POST", { password }));
}

export async function logout(): Promise<void> {
  await fetch("/api/v1/logout", { method: "POST" });
}

/** 探测是否已登录（读接口公开后，改用专门的鉴权状态接口）。 */
export async function checkAuth(): Promise<boolean> {
  const r = await fetch("/api/v1/auth/status");
  if (!r.ok) return false;
  const body = await r.json().catch(() => ({}));
  return body.logged_in === true;
}

// ---- 设置页（tasks.md WP4） ----

export interface SettingsState {
  config_version: number;
  global: Record<string, unknown>;
  devices: Record<string, Record<string, unknown>>;
  prefixes: Record<string, Record<string, unknown>>;
  location_options: Record<string, string[]>;
  disk: { total_gb: number; free_gb: number; used_ratio: number };
}

export interface QualityInfo {
  quality: { quality: string; label: string; recommended: boolean; resolution: string; fps: number; mb_per_hour_max: number }[];
  resolutions: string[];
  resolution_sizes: Record<string, [number, number]>;
  mb_per_hour: Record<string, Record<string, number>>;
  fps: number[];
  recommended_quality: string;
  recommended_resolution: string;
  recommended_fps: number;
}

export async function fetchSettings(): Promise<SettingsState> {
  return requestJson("/api/v1/settings");
}

export async function fetchQuality(): Promise<QualityInfo> {
  return requestJson("/api/v1/quality");
}

async function putJson(url: string, data: unknown): Promise<void> {
  await request(url, withJsonBody("PUT", data));
}

export async function putGlobalSettings(data: Record<string, unknown>): Promise<void> {
  await putJson("/api/v1/settings/global", data);
}

export async function putDeviceSettings(code: string, data: Record<string, unknown>): Promise<void> {
  await putJson(`/api/v1/settings/device/${encodeURIComponent(code)}`, data);
}

export async function deleteDeviceSettings(code: string): Promise<void> {
  await request(`/api/v1/settings/device/${encodeURIComponent(code)}`, { method: "DELETE" });
}

// ---- 组织树与责任人 ----

export type Worst = Status | null;

export interface OrgNode {
  id: number;
  parent_id: number | null;
  level: string;
  level_name: string;
  code: string;
  name: string;
  owner_name: string;
  owner_dept: string;
  owner_note: string;
  owner: Owner | null;
  direct_devices: number;
  total: number;
  counts: Record<Status, number>;
  worst: Worst;
}

export interface OrgResponse {
  levels: { key: string; name: string }[];
  nodes: OrgNode[];
  unassigned: { total: number; counts: Record<Status, number>; worst: Worst };
}

export async function fetchOrg(): Promise<OrgResponse> {
  return requestJson("/api/v1/org");
}

export interface OrgNodeInput {
  parent_id?: number | null;
  code?: string;
  name?: string;
  owner_name?: string;
  owner_dept?: string;
  owner_note?: string;
}

async function sendOrg(method: string, url: string, data?: OrgNodeInput): Promise<void> {
  await request(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: data ? JSON.stringify(data) : undefined,
  });
}

export const createOrgNode = (d: OrgNodeInput) => sendOrg("POST", "/api/v1/org/nodes", d);
export const updateOrgNode = (id: number, d: OrgNodeInput) => sendOrg("PUT", `/api/v1/org/nodes/${id}`, d);
export const deleteOrgNode = (id: number) => sendOrg("DELETE", `/api/v1/org/nodes/${id}`);

// 安装包版本管理（tasks.md WP9）
export interface Release {
  id: number;
  version: string;
  filename: string;
  size: number;
  sha256: string;
  notes: string | null;
  created_at: string;
}

export async function fetchReleases(): Promise<Release[]> {
  return requestJson("/api/v1/releases");
}

export async function uploadRelease(file: File, version: string, notes: string): Promise<void> {
  const form = new FormData();
  form.append("file", file);
  const params = new URLSearchParams({ version, notes });
  await request(`/api/v1/releases?${params}`, { method: "POST", body: form });
}

export async function deleteRelease(id: number): Promise<void> {
  await request(`/api/v1/releases/${id}`, { method: "DELETE" });
}
