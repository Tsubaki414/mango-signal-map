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
  /** 换一个：同圈层的次优人选。没有替代时不显示按钮，不给死路。 */
  onSwap?: () => void;
  swapLabel?: string | null;
};

/** 粉丝量缩写。分量靠这个数字传达，所以不能省。 */
function fmtFollowers(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(n);
}

export function CandidateCard({
  item, kept, format, onKeep, onRemove, onFormat, onSwap, swapLabel,
}: Props) {
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
        {/* 关系强度：分级必须和"所以该怎么做"一起出现，否则只是装饰标签。
            当前全库没有互动数据，所以这里几乎总是「仅单向关注」—— 如实显示，
            这比写「13 位目标人物 · 关注」更准，后者听起来比实际强。 */}
        <span
          className={`${styles.strength} ${styles["s_" + item.strength]}`}
          title={item.strengthAdvice}
        >
          {item.strengthLabel}
        </span>
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

      {/* 配置外的已观察账号。
       *
       * 这些不是客户选定的目标人物，所以不算进圈层覆盖，也不进路径图 ——
       * 但「谁在关注他」本身就是证据，而库里 294 个已采集账号里配置只覆盖
       * 19 个，扔掉剩下的等于浪费掉大部分已采到的观察。
       *
       * 粉丝量一起显示：分量由客户自己判断，我们不替他分类。 */}
      {item.otherSignals.length > 0 && (
        <div className={styles.others}>
          <span className={styles.othersTag}>另有关注</span>
          {item.otherSignals.map((o) => (
            <span key={o.handle} className={styles.other}>
              {o.handle}
              {o.followers != null && (
                <span className={styles.otherFollowers}>{fmtFollowers(o.followers)}</span>
              )}
              {o.interactions > 0 && <span className={styles.otherHot}>互动</span>}
            </span>
          ))}
        </div>
      )}

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
        {/* 队列字母是给 Mango 内部对齐流程用的，客户看到的是它的中文名和动作。 */}
        <span className={`${styles.queue} ${styles["q_" + item.queue]}`} title={item.queueAction}>
          {item.queue} · {item.queueLabel}
        </span>
        {onSwap && swapLabel && (
          <button type="button" className={styles.act} onClick={onSwap} title={`换成 ${swapLabel}`}>
            换一个
          </button>
        )}
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
