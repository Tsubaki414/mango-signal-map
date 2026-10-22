"use client";

import { useState } from "react";
import type { Circle, Preset, Target } from "@/lib/api";
import { Avatar } from "./Avatar";
import styles from "./TargetsV3.module.css";

const MAX_FOCUS = 3;
const SHOWN = 5;

type Props = {
  circles: Circle[];
  targets: Target[];
  presets: Preset[];
  focus: string[];
  onToggleFocus: (id: string) => void;
  /** 快速选择：一次把一组圈层设为本轮重点（再点一次取消）。 */
  onApplyPreset: (p: Preset) => void;
  onOpenPerson: (t: Target) => void;
  notice?: string | null;
  children?: React.ReactNode;
};

/* 02 目标圈层。
 *
 * 客户**不逐个勾选目标人物** —— 早期版本那样做，每勾一个名单就收窄（26 → 4），
 * 客户越参与结果越少。这里只能对**圈层**做「设为本轮重点」，最多 3 个，而且
 * 只重排不过滤，数量永不下降。
 *
 * 圈层是第一等公民，目标人物是它的证据：所以卡片主体是 who / pick 两段文案，
 * 人物列表在右侧作为佐证，而不是反过来。
 *
 * 快速选择是同一个机制的捷径：一个预设就是本领域里常被一起选的几个圈层，
 * 点一下等于把它们逐个「设为本轮重点」。它不另起一份名单，也不让客户逐个
 * 勾人 —— 成员就是下面圈层卡里的那些人。 */
export function TargetsV3({
  circles,
  targets,
  presets,
  focus,
  onToggleFocus,
  onApplyPreset,
  onOpenPerson,
  notice,
  children,
}: Props) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const atCap = focus.length >= MAX_FOCUS;

  return (
    <section id="targets" className={styles.section} data-screen-label="目标圈层">
      <div className={styles.eyebrow}>02 /// 你会进入谁的视野</div>
      <h2 className={styles.title}>
        先确定要让哪几类人看到，再反推可投放的创作者。
      </h2>
      <p className={styles.note}>
        这里列的不是要投放的 KOL，而是你想影响的人。标记重点只改变排序，名单不会变少。
      </p>
      {notice && <p className={styles.note} style={{ color: "var(--msm-warn)" }}>{notice}</p>}

      {presets.length > 0 && (
        <div className={styles.presets}>
          <div className={styles.pickTag}>快速选择</div>
          <div className={styles.presetRow}>
            {presets.map((p) => {
              const on =
                p.circles.length === focus.length && p.circles.every((c) => focus.includes(c));
              const labels = circles.filter((c) => p.circles.includes(c.id)).map((c) => c.label);
              return (
                <button
                  key={p.id}
                  type="button"
                  className={`${styles.focusBtn} ${styles.presetBtn} ${on ? styles.focusBtnOn : ""}`}
                  onClick={() => onApplyPreset(p)}
                  aria-pressed={on}
                  title={p.why}
                >
                  <span aria-hidden="true">{on ? "■" : "□"}</span>
                  {p.label}
                  <span className={styles.presetMeta}>
                    {p.coverage.targets} 人 · {labels.join(" + ")}
                  </span>
                </button>
              );
            })}
          </div>
          <p className={styles.pickText}>
            一个预设就是常被一起选的几个圈层，点选即设为本轮重点；再点一次取消。
          </p>
        </div>
      )}

      <div className={styles.cards}>
        {circles.map((c, i) => {
          const people = targets.filter((t) => t.circle === c.id);
          const on = focus.includes(c.id);
          const open = expanded[c.id];
          const shown = open ? people : people.slice(0, SHOWN);
          return (
            <article key={c.id} className={`${styles.card} ${on ? styles.cardOn : ""}`}>
              <div className={styles.left}>
                <div className={styles.meta}>
                  {String(i + 1).padStart(2, "0")} · {people.length} 人 · {c.value}
                </div>
                <h3 className={styles.label}>{c.label}</h3>
                <p className={styles.who}>{c.who}</p>

                <div className={styles.pick}>
                  <div className={styles.pickTag}>KOL 选择逻辑</div>
                  <p className={styles.pickText}>{c.pick}</p>
                </div>

                <button
                  type="button"
                  className={`${styles.focusBtn} ${on ? styles.focusBtnOn : ""}`}
                  onClick={() => onToggleFocus(c.id)}
                  disabled={!on && atCap}
                  aria-pressed={on}
                  title={!on && atCap ? `最多标记 ${MAX_FOCUS} 个重点圈层` : undefined}
                >
                  <span aria-hidden="true">{on ? "■" : "□"}</span>
                  {on ? "已设为重点" : "设为本轮重点"}
                </button>
              </div>

              <div className={styles.people}>
                {people.length === 0 && (
                  <div className={styles.empty}>该圈层的代表人物待补充。</div>
                )}
                {shown.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    className={styles.person}
                    onClick={() => onOpenPerson(t)}
                  >
                    <Avatar src={t.avatarUrl} name={t.name} size={36} />
                    <span style={{ minWidth: 0 }}>
                      <span className={styles.personName}>{t.name}</span>
                      <span className={styles.personHandle}>{t.handle}</span>
                    </span>
                    {/* 没采到关注网络的人照常列出，只是标注 —— 藏起来会让客户
                        以为这个圈层就这么几个人。 */}
                    {t.needsReview ? (
                      <span className={styles.pending}>待核实</span>
                    ) : (
                      !t.collected && <span className={styles.pending}>关系待采集</span>
                    )}
                  </button>
                ))}
                {people.length > SHOWN && (
                  <button
                    type="button"
                    className={styles.more}
                    onClick={() => setExpanded((e) => ({ ...e, [c.id]: !open }))}
                  >
                    {open ? "收起" : `展开其余 ${people.length - SHOWN} 位 →`}
                  </button>
                )}
              </div>
            </article>
          );
        })}
      </div>
      {children}
    </section>
  );
}
