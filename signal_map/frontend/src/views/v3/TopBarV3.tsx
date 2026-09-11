"use client";

import { useState } from "react";
import styles from "./TopBarV3.module.css";

/* v3 是单页滚动，所以顶栏是锚点而不是阶段机。
 *
 * `04 确认` 是**加上去的**，不在 Claude Design 的 v3 原型里：原型只有三个锚点，
 * 最后一步不在导航中。单页滚动本来就比分步流程更容易失去方位感，把终点从导航
 * 里拿掉会让客户不知道还剩什么。这是本次复刻里唯一有意的增补。 */
export const ANCHORS = [
  { id: "prefs", label: "偏好" },
  { id: "targets", label: "目标圈层" },
  { id: "list", label: "推荐名单" },
  { id: "confirm", label: "确认" },
] as const;

type Props = {
  /** 已经推进到的锚点 id，用于给导航一个完成态 */
  reached?: string[];
  saveNote?: string;
  onBrief?: (code: string) => Promise<string | null> | string | null;
};

export function TopBarV3({ reached = [], saveNote = "", onBrief }: Props) {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);

  async function submit() {
    if (!onBrief) return;
    const err = await onBrief(code.trim());
    setMsg(
      err
        ? { text: err, ok: false }
        : { text: "已带入项目资料", ok: true },
    );
  }

  return (
    <>
      <header className={styles.bar}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/assets/mango-mark.png" alt="Mango Labs" className={styles.mark} />
        <span className={styles.wordmark}>MangoLabs</span>
        <span className={styles.divider} />
        <span className={styles.product}>SIGNAL MAP</span>

        <nav className={styles.nav}>
          {ANCHORS.map((a) => (
            <a
              key={a.id}
              href={`#${a.id}`}
              className={`${styles.anchor} ${reached.includes(a.id) ? styles.anchorReached : ""}`}
            >
              {a.label}
            </a>
          ))}
        </nav>

        <div className={styles.right}>
          <span className={styles.save} aria-live="polite">
            {saveNote}
          </span>
          <button type="button" className={styles.brief} onClick={() => setOpen((v) => !v)}>
            {open ? "收起" : "我有 BRIEF 码"}
          </button>
        </div>
      </header>

      {open && (
        <div className={styles.briefBar}>
          <span className={styles.briefHint}>已有项目资料？输入 Mango 给你的 Brief 码</span>
          <input
            className={styles.briefInput}
            value={code}
            onChange={(e) => {
              setCode(e.target.value);
              setMsg(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
            placeholder="演示输入 demo"
            aria-label="Brief 码"
          />
          <button type="button" className={styles.briefGo} onClick={submit}>
            带入
          </button>
          {msg && (
            <span
              className={styles.briefMsg}
              style={{ color: msg.ok ? "var(--msm-accent)" : "var(--msm-warn)" }}
              role={msg.ok ? undefined : "alert"}
            >
              {msg.text}
            </span>
          )}
        </div>
      )}
    </>
  );
}
