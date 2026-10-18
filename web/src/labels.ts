// 各页面共用的"人话"文案与小工具：看板、设备墙、详情抽屉都从这里取，避免各写一份。

import type { Status } from "./api";

export const STATUS_TEXT: Record<Status, string> = {
  ok: "正常录制",
  offline_local: "离线本地录制",
  abnormal: "没在录像",
  lost: "失联",
};

/** 多久以前：`12 秒前`、`3 小时前`、`2 天前`。 */
export function ago(sec: number): string {
  if (sec < 60) return `${Math.max(0, Math.round(sec))} 秒前`;
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} 小时前`;
  return `${Math.floor(sec / 86400)} 天前`;
}

/** 把异常转成一句能直接显示的话。 */
export function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
