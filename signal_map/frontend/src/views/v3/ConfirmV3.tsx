"use client";

import { useState } from "react";
import { tierMark, type Candidate, type Circle } from "@/lib/api";
import type { Kept } from "./CandidatesV3";
import { PRICE_NOTE, UNVERIFIED_NOTE } from "./copy";
import { FORMATS } from "./CandidateCard";
import styles from "./ConfirmV3.module.css";

type Props = {
  kept: Kept[];
  items: Candidate[];
  circles: Circle[];
  focus: string[];
  submittedAt: string | null;
  onSubmit: () => Promise<{ receipt: string } | null>;
};

/* 04 确认。
 *
 * 措辞在这一屏最要紧：客户在这里按下的是「交给 Mango 复核」，**不是购买**。
 * 所以回执里那句「确认不是购买」由后端生成并原样显示，前端不改写。
 *
 * 未询价的人不折算成 0 —— 预算分两段给：已知档位的和待确认的分开数，
 * 把未知混进合计会让客户以为那部分是免费的。 */
export function ConfirmV3({ kept, items, circles, focus, submittedAt, onSubmit }: Props) {
  const [receipt, setReceipt] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const byId = new Map(items.map((i) => [i.id, i]));
  const rows = kept
    .map((k) => ({ k, item: byId.get(k.id) }))
    .filter((r): r is { k: Kept; item: Candidate } => Boolean(r.item));

  const ready = rows.filter((r) => r.item.bizState === "ready");
  const pending = rows.filter((r) => r.item.bizState !== "ready");
  const coveredCircles = new Set(rows.flatMap((r) => r.item.circles));
  const focusLabels = circles.filter((c) => focus.includes(c.id)).map((c) => c.label);
  const missing = circles.filter((c) => !coveredCircles.has(c.id));

  async function submit() {
    setBusy(true);
    try {
      const res = await onSubmit();
      if (res) setReceipt(res.receipt);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section id="confirm" className={styles.section} data-screen-label="确认">
      <div className={styles.eyebrow}>04 /// Mango 这样理解你的需求</div>

      <h2 className={styles.title}>
        {focusLabels.length
          ? `你希望优先进入${focusLabels.join("、")}的视野。`
          : "你希望进入上述圈层的视野。"}
      </h2>
      <p className={styles.note}>
        已保留 {rows.length} 位创作者，覆盖 {circles.length} 个目标圈层中的{" "}
        {coveredCircles.size} 个。
        {missing.length > 0 && `${missing.map((c) => c.label).join("、")}下一步可拓展。`}
      </p>

      <div className={styles.table}>
        <div className={`${styles.row} ${styles.headRow}`}>
          <span>创作者</span>
          <span>合作形式</span>
          <span>档位</span>
          <span>商务状态</span>
        </div>
        {rows.map(({ k, item }) => (
          <div key={k.id} className={styles.row}>
            <span>
              {item.name}
              <span className={styles.handle}>{item.handle}</span>
            </span>
            <span>{FORMATS.find((f) => f.id === k.format)?.label ?? k.format}</span>
            {/* 只有档位。金额从不到达这一层。 */}
            <span className={styles.mono}>
              {item.source === "priced" ? tierMark(item.tier) : "—"}
            </span>
            <span className={styles.biz}>{item.bizLabel}</span>
          </div>
        ))}
      </div>

      {pending.length > 0 && (
        <div className={styles.pendingBox}>
          <div className={styles.pendingTitle}>仍需 Mango 核验</div>
          <p className={styles.pendingText}>
            其中 {pending.length} 位尚未建联或报价待确认，我们会先接洽再回复可行性与价格。
            这部分不计入已知档位合计 —— 未询价的人不折算成零。
          </p>
        </div>
      )}

      {/* 按执行队列分组：A/B/C 的处置方式完全不同，混在一张表里等于让 Mango
          自己再分一遍。C（媒体）尤其不能按 paid KOL 处理。 */}
      <div className={styles.queues}>
        {(["A", "B", "C", "D"] as const).map((q) => {
          const inQ = rows.filter((r) => r.item.queue === q);
          if (!inQ.length) return null;
          return (
            <div key={q} className={styles.queueBlock}>
              <div className={styles.queueHead}>
                <span className={styles.queueTag}>{q}</span>
                {inQ[0].item.queueLabel}
                <span className={styles.queueCount}>{inQ.length} 位</span>
              </div>
              <div className={styles.queueAction}>{inQ[0].item.queueAction}</div>
              <div className={styles.queueNames}>
                {inQ.map((r) => r.item.name).join("、")}
              </div>
            </div>
          );
        })}
      </div>

      <div className={styles.next}>
        <div className={styles.nextTitle}>Mango 接下来会做</div>
        <ol className={styles.steps}>
          <li>复核每一位的最新报价、档期与合作条件</li>
          <li>对尚未建联的创作者主动接洽，确认合作意愿</li>
          <li>核验公开关系记录，把未经确认的部分标注清楚</li>
          <li>返回一份可执行的阵容与预算方案</li>
        </ol>
      </div>

      {submittedAt || receipt ? (
        <div className={styles.receipt}>
          {receipt ?? `已于 ${new Date(submittedAt!).toLocaleString()} 提交，Mango 正在复核。`}
        </div>
      ) : (
        <button type="button" className={styles.submit} onClick={submit} disabled={busy}>
          {busy ? "提交中…" : "确认初步阵容 →"}
        </button>
      )}

      <p className={styles.disclaimer}>
        {PRICE_NOTE}
        {UNVERIFIED_NOTE}
      </p>
    </section>
  );
}
