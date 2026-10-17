// 与 everrec-server 的 GET /api/v1/devices 对应

export type Status = "ok" | "offline_local" | "abnormal" | "lost";

export interface LogLine {
  at: string;
  level: string;
  message: string;
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
  problems: string[];
  restarts: number;
  pending_sync: number;
  disk_used: number;
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
  const r = await fetch(`/api/v1/devices${q}`);
  if (!r.ok) throw new Error(`服务器返回 ${r.status}`);
  return r.json();
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
  const r = await fetch(`/api/v1/recordings?${params}`);
  if (!r.ok) throw new Error(`服务器返回 ${r.status}`);
  return r.json();
}

export function recordingFileUrl(id: number): string {
  return `/api/v1/recordings/${id}/file`;
}

export async function setRecordingLock(id: number, locked: boolean): Promise<void> {
  const r = await fetch(`/api/v1/recordings/${id}/${locked ? "lock" : "unlock"}`, { method: "POST" });
  if (!r.ok) throw new Error(`服务器返回 ${r.status}`);
}

// ---- 登录（tasks.md WP5） ----

export async function login(password: string): Promise<void> {
  const r = await fetch("/api/v1/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail ?? `服务器返回 ${r.status}`);
  }
}

export async function logout(): Promise<void> {
  await fetch("/api/v1/logout", { method: "POST" });
}

/** 探测是否已登录：只有 401 算未登录，其他错误（如服务端 5xx）不拦住页面。 */
export async function checkAuth(): Promise<boolean> {
  const r = await fetch("/api/v1/devices");
  return r.status !== 401;
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
  fps: number[];
  recommended_quality: string;
  recommended_resolution: string;
  recommended_fps: number;
}

export async function fetchSettings(): Promise<SettingsState> {
  const r = await fetch("/api/v1/settings");
  if (!r.ok) throw new Error(`服务器返回 ${r.status}`);
  return r.json();
}

export async function fetchQuality(): Promise<QualityInfo> {
  const r = await fetch("/api/v1/quality");
  if (!r.ok) throw new Error(`服务器返回 ${r.status}`);
  return r.json();
}

async function putJson(url: string, data: unknown): Promise<void> {
  const r = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail ?? `服务器返回 ${r.status}`);
  }
}

export async function putGlobalSettings(data: Record<string, unknown>): Promise<void> {
  await putJson("/api/v1/settings/global", data);
}

export async function putDeviceSettings(code: string, data: Record<string, unknown>): Promise<void> {
  await putJson(`/api/v1/settings/device/${encodeURIComponent(code)}`, data);
}

export async function deleteDeviceSettings(code: string): Promise<void> {
  const r = await fetch(`/api/v1/settings/device/${encodeURIComponent(code)}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`服务器返回 ${r.status}`);
}

export async function putLocationOptions(data: Record<string, string[]>): Promise<void> {
  await putJson("/api/v1/location-options", data);
}

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
  const r = await fetch("/api/v1/releases");
  if (!r.ok) throw new Error(`服务器返回 ${r.status}`);
  return r.json();
}

export async function uploadRelease(file: File, version: string, notes: string): Promise<void> {
  const form = new FormData();
  form.append("file", file);
  const params = new URLSearchParams({ version, notes });
  const r = await fetch(`/api/v1/releases?${params}`, { method: "POST", body: form });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail ?? `服务器返回 ${r.status}`);
  }
}

export async function deleteRelease(id: number): Promise<void> {
  const r = await fetch(`/api/v1/releases/${id}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`服务器返回 ${r.status}`);
}
