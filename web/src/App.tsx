import { useEffect, useState } from "react";
import DeviceWall from "./DeviceWall";
import Playback from "./Playback";
import Settings from "./Settings";

// 管理网站一共 3 页（设计文档第 10 节）。用 hash 路由：`#/` 设备墙、`#/playback` 录像回放、`#/settings` 设置。
function route(): "wall" | "playback" | "settings" {
  if (window.location.hash.startsWith("#/playback")) return "playback";
  if (window.location.hash.startsWith("#/settings")) return "settings";
  return "wall";
}

export default function App() {
  const [page, setPage] = useState<"wall" | "playback" | "settings">(route());

  useEffect(() => {
    const onHash = () => setPage(route());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <circle cx="12" cy="12" r="9" />
            <circle cx="12" cy="12" r="3.5" fill="currentColor" stroke="none" />
          </svg>
          <span>EverRec 恒录</span>
        </div>
        <nav aria-label="主导航">
          <a className={page === "wall" ? "active" : ""} href="#/">设备墙</a>
          <a className={page === "playback" ? "active" : ""} href="#/playback">录像回放</a>
          <a className={page === "settings" ? "active" : ""} href="#/settings">设置</a>
        </nav>
      </header>
      {page === "wall" ? <DeviceWall /> : page === "playback" ? <Playback /> : <Settings />}
    </div>
  );
}
