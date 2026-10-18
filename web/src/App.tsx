import { useEffect, useState } from "react";
import { checkAuth, logout } from "./api";
import Board from "./Board";
import DeviceWall from "./DeviceWall";
import Login from "./Login";
import Playback from "./Playback";
import Settings from "./Settings";

// 管理网站：`#/` 看板大屏（只看红绿灯）、`#/wall` 设备墙、`#/playback` 录像回放、`#/settings` 设置。
type Page = "board" | "wall" | "playback" | "settings";
function route(): Page {
  if (window.location.hash.startsWith("#/wall")) return "wall";
  if (window.location.hash.startsWith("#/playback")) return "playback";
  if (window.location.hash.startsWith("#/settings")) return "settings";
  return "board";
}

export default function App() {
  const [page, setPage] = useState<Page>(route());
  // 未登录时 /api/v1/* 全 401，先探测再决定是否渲染页面（tasks.md WP5）
  const [auth, setAuth] = useState<"checking" | "in" | "out">("checking");

  useEffect(() => {
    let alive = true;
    checkAuth().then((ok) => alive && setAuth(ok ? "in" : "out"));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const onHash = () => setPage(route());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  if (auth === "checking") return null;
  if (auth === "out") return <Login onLogin={() => setAuth("in")} />;

  async function signOut() {
    await logout();
    setAuth("out");
  }

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
          <a className={page === "board" ? "active" : ""} href="#/">看板</a>
          <a className={page === "wall" ? "active" : ""} href="#/wall">设备墙</a>
          <a className={page === "playback" ? "active" : ""} href="#/playback">录像回放</a>
          <a className={page === "settings" ? "active" : ""} href="#/settings">设置</a>
        </nav>
        <button className="btn topbar-out" onClick={signOut}>退出登录</button>
      </header>
      {page === "board" ? <Board /> : page === "wall" ? <DeviceWall /> : page === "playback" ? <Playback /> : <Settings />}
    </div>
  );
}
