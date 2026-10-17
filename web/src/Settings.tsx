import { useEffect, useState } from "react";
import {
  deleteDeviceSettings,
  fetchDevices,
  fetchQuality,
  fetchSettings,
  putDeviceSettings,
  putGlobalSettings,
  putLocationOptions,
  type QualityInfo,
  type SettingsState,
} from "./api";

// 码率自动算的公式见 core/profiles.py（BITS_PER_PIXEL=0.07），这里只为估算代价，不开放码率
const BITS_PER_PIXEL = 0.07;

function mbPerHour(width: number, height: number, fps: number): number {
  const kbps = Math.max(32, Math.round((width * height * fps * BITS_PER_PIXEL) / 1000));
  return (kbps * 3600) / 8 / 1000;
}

interface QualityValue {
  quality: string;
  resolution: string | null;
  fps: number | null;
}

function resolveEncoding(v: QualityValue, qi: QualityInfo): { res: string; fps: number; mbph: number } {
  const base = qi.quality.find((q) => q.quality === v.quality) ?? qi.quality[2];
  const res = v.resolution ?? base.resolution;
  const fps = v.fps ?? base.fps;
  const [w, h] = qi.resolution_sizes[res] ?? [1280, 720];
  return { res, fps, mbph: mbPerHour(w, h, fps) };
}

function QualityEditor({
  value,
  qi,
  cameraResolutions,
  onChange,
}: {
  value: QualityValue;
  qi: QualityInfo;
  cameraResolutions: string[];
  onChange: (v: QualityValue) => void;
}) {
  const advanced = value.resolution !== null || value.fps !== null;
  const base = qi.quality.find((q) => q.quality === value.quality) ?? qi.quality[2];
  const eff = resolveEncoding(value, qi);

  const canDo = (res: string): boolean => {
    if (cameraResolutions.length === 0) return true;
    const [, h] = qi.resolution_sizes[res] ?? [0, 0];
    return cameraResolutions.some((size) => {
      const parts = size.split("x");
      return parts.length === 2 && Number(parts[1]) >= h;
    });
  };

  return (
    <div className="quality-editor">
      <div className="slider-row">
        <span className="slider-label">画质</span>
        <div className="slider">
          {qi.quality.map((q) => (
            <button
              key={q.quality}
              type="button"
              className={`stop ${value.quality === q.quality ? "on" : ""}`}
              onClick={() => onChange({ ...value, quality: q.quality })}
              title={q.label}
            >
              {q.label}
              {q.recommended && <span className="rec">★推荐</span>}
            </button>
          ))}
        </div>
      </div>

      <details className="advanced" open={advanced}>
        <summary>高级（分辨率 / 帧率）</summary>
        <div className="slider-row">
          <span className="slider-label">分辨率</span>
          <div className="slider">
            {qi.resolutions.map((r) => {
              const disabled = !canDo(r);
              return (
                <button
                  key={r}
                  type="button"
                  className={`stop ${value.resolution === r ? "on" : ""}`}
                  disabled={disabled}
                  title={disabled ? "摄像头不支持" : ""}
                  onClick={() => onChange({ ...value, resolution: r })}
                >
                  {r.toUpperCase()}
                </button>
              );
            })}
          </div>
        </div>
        <div className="slider-row">
          <span className="slider-label">帧率</span>
          <div className="slider">
            {qi.fps.map((f) => (
              <button
                key={f}
                type="button"
                className={`stop ${value.fps === f ? "on" : ""}`}
                onClick={() => onChange({ ...value, fps: f })}
              >
                {f}
              </button>
            ))}
          </div>
        </div>
        <div className="advanced-actions">
          <span className="muted">
            {advanced ? `当前 ${eff.res.toUpperCase()} / ${eff.fps} 帧` : `跟随画质挡（${base.resolution.toUpperCase()} / ${base.fps} 帧）`}
          </span>
          {advanced && (
            <button
              type="button"
              className="link"
              onClick={() => onChange({ quality: value.quality, resolution: null, fps: null })}
            >
              恢复推荐
            </button>
          )}
        </div>
      </details>
    </div>
  );
}

export default function Settings() {
  const [settings, setSettings] = useState<SettingsState | null>(null);
  const [qi, setQi] = useState<QualityInfo | null>(null);
  const [deviceCount, setDeviceCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // 全局画质编辑态
  const [quality, setQuality] = useState<QualityValue>({ quality: "", resolution: null, fps: null });
  const [watermark, setWatermark] = useState(true);
  const [retentionDays, setRetentionDays] = useState(90);
  const [rollover, setRollover] = useState(true);
  const [quota, setQuota] = useState(20);
  const [segment, setSegment] = useState(300);

  // 位置选项编辑态
  const [locText, setLocText] = useState<Record<string, string>>({});

  // 单设备覆盖编辑态
  const [devices, setDevices] = useState<Record<string, Record<string, unknown>>>({});
  const [editDevice, setEditDevice] = useState<string | null>(null);
  const [newDevice, setNewDevice] = useState("");
  const [cameraMap, setCameraMap] = useState<Record<string, string[]>>({});

  async function load() {
    try {
      const [s, q, devs] = await Promise.all([fetchSettings(), fetchQuality(), fetchDevices("")]);
      setSettings(s);
      setQi(q);
      setDeviceCount(devs.items.length);
      setDevices(s.devices);
      setCameraMap(Object.fromEntries(devs.items.map((d) => [d.code, d.camera_resolutions])));
      const g = s.global;
      setQuality({
        quality: (g.quality as string) ?? q.recommended_quality,
        resolution: (g.resolution as string | null) ?? null,
        fps: (g.fps as number | null) ?? null,
      });
      setWatermark((g.watermark as boolean) ?? true);
      setRetentionDays((g.retention_days as number) ?? 90);
      setRollover((g.rollover_enabled as boolean) ?? true);
      setQuota((g.local_quota_gb as number) ?? 20);
      setSegment((g.segment_seconds as number) ?? 300);
      setLocText(Object.fromEntries(
        Object.entries(s.location_options).map(([k, v]) => [k, v.join(", ")]),
      ));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function saveGlobal() {
    setError(null);
    setSaved(false);
    try {
      await putGlobalSettings({
        quality: quality.quality,
        resolution: quality.resolution,
        fps: quality.fps,
        watermark,
        retention_days: retentionDays,
        rollover_enabled: rollover,
        local_quota_gb: quota,
        segment_seconds: segment,
      });
      setSaved(true);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function saveDevice(code: string) {
    setError(null);
    const data = devices[code] ?? {};
    try {
      await putDeviceSettings(code, data as Record<string, unknown>);
      setEditDevice(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function removeDevice(code: string) {
    await deleteDeviceSettings(code);
    setEditDevice(null);
    await load();
  }

  async function addDevice() {
    const code = newDevice.trim();
    if (!code) return;
    await putDeviceSettings(code, {});
    setNewDevice("");
    await load();
  }

  async function saveLocations() {
    setError(null);
    const data: Record<string, string[]> = {};
    for (const [k, v] of Object.entries(locText)) {
      data[k] = v.split(",").map((s) => s.trim()).filter(Boolean);
    }
    await putLocationOptions(data);
    await load();
  }

  const eff = qi ? resolveEncoding(quality, qi) : null;
  const dailyGb = eff ? (deviceCount * eff.mbph * 24) / 1000 : 0;
  const daysLeft = dailyGb > 0 && settings ? settings.disk.free_gb / dailyGb : null;
  const notEnough = daysLeft !== null && daysLeft < retentionDays;

  if (!settings || !qi) {
    return (
      <main className="page">
        {error ? <div className="alert"><strong>出错了</strong><span>{error}</span></div> : <p className="muted empty">加载设置…</p>}
      </main>
    );
  }

  return (
    <main className="page settings">
      <div className="page-head">
        <h1>设置</h1>
        <span className="muted">配置版本 {settings.config_version}</span>
      </div>

      {error && (
        <div className="alert" role="alert"><strong>保存失败</strong><span>{error}</span></div>
      )}
      {saved && <div className="alert" role="status"><strong>已保存</strong><span>各设备在下一个分段边界生效</span></div>}

      <section className="card-block">
        <h2>画质</h2>
        <QualityEditor value={quality} qi={qi} cameraResolutions={[]} onChange={setQuality} />
        {eff && (
          <div className={`cost ${notEnough ? "warn" : ""}`}>
            每台每小时上限约 <strong>{Math.round(eff.mbph)} MB</strong> ·
            全部电脑每天约 <strong>{dailyGb.toFixed(1)} GB</strong>
            {daysLeft !== null && <> · 按剩余磁盘约还能存 <strong>{Math.floor(daysLeft)} 天</strong></>}
            {notEnough && <span className="warn-text">（存不够保留天数 {retentionDays} 天）</span>}
            <div className="muted">按上限估算，实际通常更低</div>
          </div>
        )}
      </section>

      <section className="card-block">
        <h2>录制与存储</h2>
        <div className="field">
          <label className="check"><input type="checkbox" checked={watermark} onChange={(e) => setWatermark(e.target.checked)} /> 画面叠加时间与位置编号水印</label>
        </div>
        <div className="field-row">
          <label>本地配额（GB）<input type="number" value={quota} onChange={(e) => setQuota(Number(e.target.value))} /></label>
          <label>分段时长（秒）<input type="number" value={segment} onChange={(e) => setSegment(Number(e.target.value))} /></label>
        </div>
        <div className="field-row">
          <label>保留天数<input type="number" value={retentionDays} onChange={(e) => setRetentionDays(Number(e.target.value))} /></label>
          <label className="check"><input type="checkbox" checked={rollover} onChange={(e) => setRollover(e.target.checked)} /> 滚动覆盖（关闭后不再自动删除旧录像）</label>
        </div>
      </section>

      <section className="card-block">
        <h2>位置选项</h2>
        <p className="muted">桌面端设置里按这里维护的州 / 区 / 楼栋 / 楼层下拉选择。</p>
        {["state", "district", "building", "floor"].map((level) => (
          <div className="field" key={level}>
            <label>
              {level === "state" ? "州" : level === "district" ? "区" : level === "building" ? "楼栋" : "楼层"}
              <input value={locText[level] ?? ""} onChange={(e) => setLocText({ ...locText, [level]: e.target.value })} placeholder="逗号分隔" />
            </label>
          </div>
        ))}
      </section>

      <section className="card-block">
        <h2>单独设置的电脑</h2>
        <p className="muted">对某一台（或编号前缀）单独覆盖全局设置，其他台不变。</p>
        <div className="device-list">
          {Object.entries(devices).map(([code, data]) => (
            <div className="device-row" key={code}>
              <span className="mono">{code}</span>
              <span className="muted">{(data.quality as string) ?? "（跟随全局）"}</span>
              <button type="button" className="link" onClick={() => setEditDevice(code)}>编辑</button>
              <button type="button" className="link" onClick={() => removeDevice(code)}>删除</button>
            </div>
          ))}
          {Object.keys(devices).length === 0 && <p className="muted">还没有单独设置。</p>}
        </div>
        <div className="field-row">
          <input className="mono" placeholder="位置编号，如 HD-A-012" value={newDevice} onChange={(e) => setNewDevice(e.target.value)} />
          <button type="button" className="primary" onClick={addDevice}>添加</button>
        </div>
      </section>

      <div className="save-bar">
        <button type="button" className="primary" onClick={saveGlobal}>保存全局设置</button>
        <button type="button" className="btn" onClick={saveLocations}>保存位置选项</button>
      </div>

      {editDevice && (
        <div className="drawer-backdrop" onClick={() => setEditDevice(null)}>
          <aside className="drawer" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-head">
              <h2 className="mono">{editDevice}</h2>
              <button type="button" onClick={() => setEditDevice(null)} aria-label="关闭">×</button>
            </div>
            <QualityEditor
              value={{
                quality: ((devices[editDevice]?.quality as string) ?? quality.quality),
                resolution: (devices[editDevice]?.resolution as string | null) ?? null,
                fps: (devices[editDevice]?.fps as number | null) ?? null,
              }}
              qi={qi}
              cameraResolutions={cameraMap[editDevice] ?? []}
              onChange={(v) => setDevices({ ...devices, [editDevice]: { ...devices[editDevice], ...v } })}
            />
            <button type="button" className="primary" onClick={() => saveDevice(editDevice)}>保存</button>
          </aside>
        </div>
      )}
    </main>
  );
}
