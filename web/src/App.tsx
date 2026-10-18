import { useEffect, useState } from "react";
import Board from "./Board";
import DeviceWall from "./DeviceWall";
import Playback from "./Playback";
import Settings from "./Settings";

// 管理网站：`#/` 看板（巡视图，一屏看全）、`#/wall` 设备墙（工作台）、`#/playback` 录像回放、`#/settings` 设置。
type Page = "board" | "wall" | "playback" | "settings";
function route(): Page {
  if (window.location.hash.startsWith("#/wall")) return "wall";
  if (window.location.hash.startsWith("#/playback")) return "playback";
  if (window.location.hash.startsWith("#/settings")) return "settings";
  return "board";
}

export default function App() {
  const [page, setPage] = useState<Page>(route());

  useEffect(() => {
    const onHash = () => setPage(route());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <circle cx="12" cy="12" r="9" />
            <circle cx="12" cy="12" r="3.5" fill="currentColor" stroke="none" />
          </svg>
          <span>EverRec 恒录</span>
        </div>
        <nav aria-label="主导航">
          <a className={page === "board" ? "active" : ""} href="#/">看板</a>
          <a className={page === "wall" ? "active" : ""} href="#/wall">设备墙</a>
          <a className={page === "playback" ? "active" : ""} href="#/playback">录像回放</a>
          <a className={page === "settings" ? "active" : ""} href="#/settings">设置</a>
        </nav>
      </header>
      {page === "board" ? <Board /> : page === "wall" ? <DeviceWall /> : page === "playback" ? <Playback /> : <Settings />}
    </>
  );
}
