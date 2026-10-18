import { useEffect, useState } from "react";
import { checkAuth, revokeDevice, type Device } from "./api";
import { ago, errMsg, STATUS_TEXT } from "./labels";

// 健康检查 ①②③④⑥（设计文档 6.1）
const CHECK_LABEL: Record<string, string> = {
  service: "服务",
  device: "摄像头",
  process: "录制进程",
  dataflow: "文件增长",
  storage: "存储",
};

function ownerText(d: Device): string {
  if (!d.owner) return "未指定";
  return [d.owner.name, d.owner.dept].filter(Boolean).join(" · ") + (d.owner.inherited ? `（继承自 ${d.owner.from_name}）` : "");
}

/** 一台设备的详情抽屉：看板和设备墙共用，避免两处各写一份。 */
export default function DeviceDrawer({
  device,
  onClose,
  onRevoked,
}: {
  device: Device;
  onClose: () => void;
  onRevoked?: () => void;
}) {
  const [canRevoke, setCanRevoke] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    checkAuth().then(setCanRevoke).catch(() => setCanRevoke(false));
  }, []);

  async function doRevoke() {
    setBusy(true);
    setError(null);
    try {
      await revokeDevice(device.code);
      onRevoked?.();
      onClose();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside
        className="drawer"
        role="dialog"
        aria-label={`${device.code} 详情`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="drawer-head">
          <h2 className="mono">{device.code}</h2>
          <button type="button" onClick={onClose} aria-label="关闭">×</button>
        </div>

        <dl>
          <dt>状态</dt>
          <dd>{STATUS_TEXT[device.status]}{device.paused ? `（${device.paused}）` : ""}</dd>
          <dt>电脑</dt><dd>{device.hostname} · Agent {device.version}</dd>
          <dt>画质</dt><dd>{device.profile ?? "—"}</dd>
          <dt>检查</dt>
          <dd>
            {Object.entries(device.checks)
              .map(([k, v]) => `${CHECK_LABEL[k] ?? k}${v === "ok" ? "正常" : v === "warn" ? "注意" : "异常"}`)
              .join(" · ") || "—"}
          </dd>
          <dt>磁盘</dt><dd>{Math.round(device.disk_used * 100)}%</dd>
          <dt>待上传</dt><dd>{device.pending_sync} 段</dd>
          <dt>最后消息</dt><dd>{ago(device.seconds_since_heartbeat)}</dd>
          <dt>责任人</dt><dd>{ownerText(device)}</dd>
        </dl>

        <h3>最近日志</h3>
        {device.recent_logs.length === 0 ? (
          <p className="muted">没有警告或错误</p>
        ) : (
          <ul className="logs mono">
            {device.recent_logs.slice().reverse().map((l, i) => (
              <li key={i}>
                <span>{new Date(l.at).toLocaleString("zh-CN", { hour12: false })}</span>{" "}
                <b>{l.level}</b> {l.message}
              </li>
            ))}
          </ul>
        )}

        <p style={{ marginTop: 16 }}>
          <a className="btn" href="#/playback">查看这台电脑的录像</a>
        </p>

        {canRevoke && (
          <div className="drawer-danger">
            {confirming ? (
              <>
                <p>吊销后这台电脑要重新注册才能上报状态；录像不受影响，照常存在本机。</p>
                <div className="actions">
                  <button className="btn bad" type="button" disabled={busy} onClick={doRevoke}>
                    {busy ? "吊销中…" : "确定吊销"}
                  </button>
                  <button className="btn" type="button" onClick={() => setConfirming(false)}>取消</button>
                </div>
              </>
            ) : (
              <button className="link" type="button" onClick={() => setConfirming(true)}>
                吊销这台电脑的凭据
              </button>
            )}
            {error && <p role="alert">{error}</p>}
          </div>
        )}
      </aside>
    </div>
  );
}
