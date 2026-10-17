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
