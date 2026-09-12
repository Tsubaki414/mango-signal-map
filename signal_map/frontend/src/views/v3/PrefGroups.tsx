"use client";

import { useState } from "react";
import type { Group, Taxonomy } from "@/lib/api";
import type { SessionPrefs } from "@/lib/useSession";
import styles from "./PrefGroups.module.css";

type Props = {
  group: Group;
  taxonomy: Taxonomy | null;
  prefs: SessionPrefs;
  onChange: (patch: Partial<SessionPrefs>) => void;
};

/* 01 偏好的其余六组。选了领域才出现 —— 渐进展开是 v3 的结构：一次只问
 * 当前这一步需要的东西。
 *
 * 预算与品牌折在「更多偏好」后面，因为它们**不改变候选池**，只影响排序与
 * 提示。把不改变结果的选项和改变结果的选项并排摆，会让客户以为每一项都很
 * 关键，于是不敢跳过。
 *
 * 所有选项都可以不选：缺失的偏好在后端是「该维度不参与筛选」，不是零分。 */
export function PrefGroups({ group, taxonomy, prefs, onChange }: Props) {
  const [moreOpen, setMoreOpen] = useState(false);
  if (!taxonomy) return null;

  const toggle = (key: "domains" | "markets" | "languages" | "audiences" | "brand", id: string) => {
    const cur = prefs[key] ?? [];
    onChange({ [key]: cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id] });
  };

  const Chips = ({
    items,
    selected,
    onPick,
  }: {
    items: { id: string; label: string }[];
    selected: string[];
    onPick: (id: string) => void;
  }) => (
    <div className={styles.chips}>
      {items.map((o) => {
        const on = selected.includes(o.id);
        return (
          <button
            key={o.id}
            type="button"
            className={`${styles.chip} ${on ? styles.chipOn : ""}`}
            onClick={() => onPick(o.id)}
            aria-pressed={on}
          >
            <span aria-hidden="true">{on ? "■" : "□"}</span>
            {o.label}
          </button>
        );
      })}
    </div>
  );

  // 细分方向只列当前领域下的，跨领域的 id 会筛出空结果。
  const domainItems = taxonomy.domains.filter((d) => group.domains.includes(d.id));

  return (
    <div className={styles.wrap}>
      <div className={styles.row}>
        <div className={styles.k}>细分方向</div>
        <Chips items={domainItems} selected={prefs.domains ?? []} onPick={(id) => toggle("domains", id)} />
      </div>

      <div className={styles.row}>
        <div className={styles.k}>市场与语言</div>
        <div>
          <Chips items={taxonomy.markets} selected={prefs.markets ?? []} onPick={(id) => toggle("markets", id)} />
          <div style={{ height: 10 }} />
          <Chips
            items={taxonomy.languages}
            selected={prefs.languages ?? []}
            onPick={(id) => toggle("languages", id)}
          />
        </div>
      </div>

      <div className={styles.row}>
        <div className={styles.k}>想影响的人群</div>
        <Chips
          items={taxonomy.audiences}
          selected={prefs.audiences ?? []}
          onPick={(id) => toggle("audiences", id)}
        />
      </div>

      <button type="button" className={styles.more} onClick={() => setMoreOpen((v) => !v)}>
        更多偏好 {moreOpen ? "−" : "+"}
      </button>

      {moreOpen && (
        <>
          <div className={styles.row}>
            <div className={styles.k}>预算</div>
            <div className={styles.chips}>
              {/* 预算是单选，而且「暂时不确定」是正当答案 —— 逼客户在还没看到
                  名单时就报一个数字，只会得到一个随口说的数字。 */}
              {[...taxonomy.budgets, { id: "", label: "暂时不确定" }].map((b) => {
                const on = (prefs.budget ?? "") === b.id;
                return (
                  <button
                    key={b.id || "none"}
                    type="button"
                    className={`${styles.chip} ${on ? styles.chipOn : ""}`}
                    onClick={() => onChange({ budget: b.id })}
                    aria-pressed={on}
                  >
                    <span aria-hidden="true">{on ? "●" : "○"}</span>
                    {b.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div className={styles.row}>
            <div className={styles.k}>内容与品牌偏好</div>
            <Chips
              items={taxonomy.brandPrefs ?? []}
              selected={prefs.brand ?? []}
              onPick={(id) => toggle("brand", id)}
            />
          </div>
        </>
      )}
    </div>
  );
}
