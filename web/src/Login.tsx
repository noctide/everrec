import { useState } from "react";
import { login } from "./api";

// 登录页（tasks.md WP5）：一个管理员密码，会话 Cookie 由浏览器保管。
export default function Login({ onLogin, onCancel }: { onLogin: () => void; onCancel?: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError(null);
    try {
      await login(password);
      onLogin();
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <div className="brand login-brand">
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <circle cx="12" cy="12" r="9" />
            <circle cx="12" cy="12" r="3.5" fill="currentColor" stroke="none" />
          </svg>
          <span>EverRec 恒录</span>
        </div>
        <h1>管理员登录</h1>
        <label htmlFor="pw">管理员密码</label>
        <input
          id="pw"
          type="password"
          value={password}
          autoFocus
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <p className="login-error" role="alert">{error}</p>}
        <button className="primary" type="submit" disabled={!password || busy}>
          {busy ? "正在登录…" : "登录"}
        </button>
        {onCancel && (
          <button type="button" className="link login-back" onClick={onCancel}>
            返回，先看看状态
          </button>
        )}
      </form>
    </div>
  );
}
