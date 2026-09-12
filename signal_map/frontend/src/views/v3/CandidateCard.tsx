"use client";

import { useState } from "react";
import { tierMark, type Candidate } from "@/lib/api";
import { Avatar } from "./Avatar";
import styles from "./CandidateCard.module.css";

/** 合作形式。只列 X 上真实可买的三种，不做无法履约的选项。 */
export const FORMATS = [
  { id: "post", label: "单条 Post" },
  { id: "thread", label: "Thread" },
  { id: "spaces", label: "Spaces" },
] as const;

type Props = {
  item: Candidate;
  kept: boolean;
  format: string;
  onKeep: () => void;
  onRemove: () => void;
  onFormat: (f: string) => void;
};

export function CandidateCard({ item, kept, format, onKeep, onRemove, onFormat }: Props) {
  const [open, setOpen] = useState(false);

  return (
    <article className={`${styles.card} ${kept ? styles.kept : ""}`}>
      <div className={styles.head}>
        <Avatar src={item.avatarUrl} name={item.name} size={44} />
        <div className={styles.ident}>
          <span className={styles.name}>{item.name ?? "资料待补充"}</span>
          <div className={styles.sub}>
            {item.handle}
            {item.followers != null && ` · ${item.followers.toLocaleString()}`}
          </div>
        </div>
        {/* 点数字即展开分项：分数和它的依据永远是同一个交互。 */}
        <button
          type="button"
          className={styles.fitBox}
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          title="展开适配度的五条分项"
        >
          <div className={styles.fit}>{item.fit}</div>
          <span className={styles.band}>{item.band}</span>
        </button>
      </div>

      <div className={styles.faces}>
        {item.faces.length > 0 && (
          <span className={styles.faceStack}>
            {item.faces.map((f) => (
              <Avatar key={f.id} src={f.avatarUrl} name={f.name} size={22} />
            ))}
          </span>
        )}
        <span className={styles.overlap}>{item.overlapText}</span>
        {item.focusNote && <span className={styles.focusTag}>{item.focusNote}</span>}
        <span
          className={`${styles.srcTag} ${
            item.source === "priced" ? styles.srcPriced : styles.srcDiscovered
          }`}
          style={{ marginLeft: "auto" }}
        >
          {item.source === "priced" ? "已有报价" : "新发现"}
        </span>
      </div>

      <p className={styles.reason}>{item.reason}</p>

      {open && (
        <div className={styles.parts}>
          {item.parts.map((p) => (
            <div key={p.id} className={styles.part}>
              <span>{p.label}</span>
              <span className={styles.bar}>
                <span className={styles.barFill} style={{ width: `${p.pct}%` }} />
              </span>
              <span style={{ textAlign: "right", fontFamily: "var(--msm-mono)" }}>{p.pct}</span>
              <span className={styles.partNote}>{p.note}</span>
            </div>
          ))}
        </div>
      )}

      <div className={styles.formats}>
        {FORMATS.map((f) => (
          <button
            key={f.id}
            type="button"
            className={`${styles.format} ${format === f.id ? styles.formatOn : ""}`}
            onClick={() => onFormat(f.id)}
            aria-pressed={format === f.id}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className={styles.foot}>
        {/* 价格只以档位呈现。金额从不下发到这一层，所以这里也没有东西可以
            格式化成价格。 */}
        <span className={styles.tier}>{item.source === "priced" ? tierMark(item.tier) : "—"}</span>
        <span className={styles.biz}>{item.bizLabel}</span>
        <button
          type="button"
          className={`${styles.act} ${kept ? "" : styles.actKeep}`}
          onClick={kept ? onRemove : onKeep}
        >
          {kept ? "移出名单" : "保留"}
        </button>
      </div>
    </article>
  );
}
