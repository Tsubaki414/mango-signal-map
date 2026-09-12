"use client";

import { useState } from "react";
import { tierMark, type Candidate, type Circle } from "@/lib/api";
import styles from "./RailV3.module.css";

type Props = {
  /** 未保留任何人时显示「实时反馈」，保留之后切成「你的名单」。 */
  candidates: Candidate[];
  kept: Candidate[];
  circles: Circle[];
  focus: string[];
  tick: string;
};

/* 右栏实时反馈。
 *
 * 它的职责是让客户**每次选择都立刻看到后果**——上一版最大的问题是客户改了条件
 * 却看不出变化，于是不停地改，越改越没底。
 *
 * 两个阶段：还没保留人时回答「现在有多少人可连接」；保留之后回答「这份名单
 * 长什么样」。同一个位置，问题随阶段变。 */
export function RailV3({ candidates, kept, circles, focus, tick }: Props) {
  const [methodOpen, setMethodOpen] = useState(false);
  const phase = kept.length ? "list" : "prefs";

  const focusLabels = circles.filter((c) => focus.includes(c.id)).map((c) => c.label);
  const keptCircles = new Set(kept.flatMap((k) => k.circles));
  const missing = circles.filter((c) => !keptCircles.has(c.id));

  return (
    <aside className={styles.rail} data-screen-label="实时反馈" data-print-hide="true">
      <div className={styles.title}>{phase === "prefs" ? "实时反馈" : "你的名单"}</div>

      <div className={styles.big}>{phase === "prefs" ? candidates.length : kept.length}</div>
      <div className={styles.bigLabel}>
        {phase === "prefs" ? "位可连接的创作者" : "位已保留"}
      </div>
      <div className={styles.tick}>{tick}</div>

      {phase === "prefs" ? (
        <>
          <Block title={focusLabels.length ? "本轮重点圈层" : "目标圈层"}>
            <Chips items={focusLabels.length ? focusLabels : circles.map((c) => c.label)} />
          </Block>
          <Block title="当前最匹配">
            <Rows
              rows={candidates.slice(0, 5).map((m) => ({ k: m.name ?? "", v: String(m.fit) }))}
            />
          </Block>
          {candidates.length > 0 && (
            <a className={styles.cta} href="#list">
              看推荐名单 ↓
            </a>
          )}
        </>
      ) : (
        <>
          <Block title="已覆盖圈层">
            <Chips items={circles.filter((c) => keptCircles.has(c.id)).map((c) => c.label)} />
          </Block>
          <Block title="名单构成">
            <Rows
              rows={kept.map((m) => ({
                k: m.name ?? "",
                // 只有档位。金额从不到达前端。
                v: m.source === "priced" ? tierMark(m.tier) : "待建联",
              }))}
            />
          </Block>
          <Block title="下一步可拓展">
            <p className={styles.text}>
              {missing.length
                ? `${missing.map((c) => c.label).join("、")}目前还没有已保留的创作者连接。`
                : "你选的圈层都已有创作者连接，可以继续加深覆盖。"}
            </p>
          </Block>
          <a className={styles.cta} href="#confirm">
            确认这份名单 ↓
          </a>
        </>
      )}

      <button type="button" className={styles.method} onClick={() => setMethodOpen((v) => !v)}>
        这份名单怎么来的 {methodOpen ? "−" : "+"}
      </button>
      {methodOpen && (
        <ol className={styles.steps}>
          <li>取目标人物的公开关注网络</li>
          <li>保留被多位目标人物共同关注的账号</li>
          <li>排除买不到内容位的身份（创始人、机构、监管者）</li>
          <li>按重点圈层的连接强度排序，不按综合分</li>
          <li>标注报价与商务状态，未询价的不折算成零</li>
        </ol>
      )}
    </aside>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className={styles.block}>
      <div className={styles.blockTitle}>{title}</div>
      {children}
    </div>
  );
}

function Chips({ items }: { items: string[] }) {
  if (!items.length) return <p className={styles.text}>暂无</p>;
  return (
    <div className={styles.chips}>
      {items.map((x) => (
        <span key={x} className={styles.chip}>
          {x}
        </span>
      ))}
    </div>
  );
}

function Rows({ rows }: { rows: { k: string; v: string }[] }) {
  if (!rows.length) return <p className={styles.text}>暂无</p>;
  return (
    <div className={styles.rows}>
      {rows.map((r) => (
        <div key={r.k} className={styles.row}>
          <span className={styles.rowK}>{r.k}</span>
          <span className={styles.rowV}>{r.v}</span>
        </div>
      ))}
    </div>
  );
}
