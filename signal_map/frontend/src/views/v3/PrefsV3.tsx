"use client";

import type { Group } from "@/lib/api";
import styles from "./PrefsV3.module.css";

type Props = {
  groups: Group[];
  loading: boolean;
  error: string | null;
  selected: string | null;
  /** 从会话恢复时的提示；没有恢复就是 null。 */
  restoredFrom?: string | null;
  onSelect: (id: string) => void;
};

/* 01 偏好。领域卡片来自 GET /api/groups —— 圈层数量和已采集数都是库里的真实
 * 覆盖度，不是写死的文案。采集为 0 的领域照常显示，只是把覆盖度如实标出来：
 * 缺数据降低置信度，从不隐藏选项。 */
export function PrefsV3({ groups, loading, error, selected, restoredFrom, onSelect }: Props) {
  return (
    <section id="prefs" className={styles.section} data-screen-label="偏好">
      <div className={styles.eyebrow}>01 /// 你想进入什么方向</div>
      <h2 className={styles.title}>先选一个领域，其余选项会跟着展开。</h2>
      {restoredFrom && <p className={styles.restored}>{restoredFrom}</p>}

      {loading && (
        <div className={styles.loading}>
          <svg viewBox="0 0 400 44" style={{ width: "min(400px,100%)", height: 44 }} aria-hidden="true">
            <g strokeWidth="2">
              <path d="M0 5H200l8 5h192" stroke="#5FC4BB" style={{ animation: "msm-band 1.4s infinite" }} />
              <path d="M0 18H120l8 5h272" stroke="#5FC4BB" style={{ animation: "msm-band 1.4s .2s infinite" }} />
              <path d="M0 31H260l8 5h132" stroke="rgba(243,241,234,.3)" style={{ animation: "msm-band 1.4s .4s infinite" }} />
              <path d="M0 44H80l8-5h312" stroke="rgba(243,241,234,.2)" style={{ animation: "msm-band 1.4s .6s infinite" }} />
            </g>
          </svg>
          <div className={styles.loadingNote}>正在读取创作者资源与公开关系记录…</div>
        </div>
      )}

      {error && (
        <div className={styles.error} role="alert">
          {error}
        </div>
      )}

      {!loading && !error && (
        <div className={styles.grid}>
          {groups.map((g, i) => {
            const collected = g.circles.reduce((n, c) => n + c.coverage.collected, 0);
            const targets = g.circles.reduce((n, c) => n + c.coverage.targets, 0);
            const on = selected === g.id;
            return (
              <button
                key={g.id}
                type="button"
                className={`${styles.card} ${on ? styles.cardOn : ""}`}
                onClick={() => onSelect(g.id)}
                aria-pressed={on}
              >
                {on && <span className={styles.dot} />}
                <span className={styles.cardNum}>{String(i + 1).padStart(2, "0")}</span>
                <span className={styles.cardLabel}>{g.label}</span>
                <span className={styles.cardDesc}>{g.desc}</span>
                <span className={styles.cardMeta}>
                  {g.circles.length} 个圈层 · {targets} 位目标人物
                  {collected < targets ? ` · ${collected} 位已采集关系` : " · 关系已采集"}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
