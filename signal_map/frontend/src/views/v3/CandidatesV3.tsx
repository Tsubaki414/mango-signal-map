"use client";

import type { CandidatesResponse, Circle, Target } from "@/lib/api";
import { PathGraph } from "./PathGraph";
import { CandidateCard } from "./CandidateCard";
import { PRICE_NOTE, UNVERIFIED_NOTE } from "./copy";
import styles from "./CandidatesV3.module.css";

export type Kept = { id: string; format: string };

type Props = {
  data: CandidatesResponse | null;
  loading: boolean;
  circles: Circle[];
  targets: Target[];
  focus: string[];
  kept: Kept[];
  onKeep: (id: string) => void;
  onRemove: (id: string) => void;
  onFormat: (id: string, format: string) => void;
  onSwap: (id: string) => void;
  onApplySuggestion: () => void;
  suggestionNote: string;
};

/* 03 推荐名单。
 *
 * 两类候选用同一种卡片、不同标记：priced 是已有报价可立即确认的人，discovered
 * 是 Mango 还没联系过、靠公开关注关系发现出来的。把后者藏起来或降级成「备选」
 * 会丢掉这个产品最值钱的部分。
 *
 * 已保留的人置顶，但**不从下面的候选里删掉**——客户要能看到自己是从哪一批里
 * 选出来的。 */
export function CandidatesV3({
  data,
  loading,
  circles,
  targets,
  focus,
  kept,
  onKeep,
  onRemove,
  onFormat,
  onSwap,
  onApplySuggestion,
  suggestionNote,
}: Props) {
  if (loading) {
    return (
      <section id="list" className={styles.section}>
        <div className={styles.eyebrow}>03 /// 精准进入目标信息流</div>
        <p className={styles.note}>正在按公开关注关系反推可投放创作者…</p>
      </section>
    );
  }
  if (!data) return null;

  const { counts, coverage, items } = data;
  const keptIds = new Set(kept.map((k) => k.id));
  const keptItems = items.filter((i) => keptIds.has(i.id));
  const formatOf = (id: string) => kept.find((k) => k.id === id)?.format ?? "post";

  const keptCircles = new Set(keptItems.flatMap((i) => i.circles));
  const readyCount = keptItems.filter((i) => i.bizState === "ready").length;
  const focusLabels = circles.filter((c) => focus.includes(c.id)).map((c) => c.label);

  return (
    <section id="list" className={styles.section} data-screen-label="推荐名单">
      <div className={styles.eyebrow}>03 /// 精准进入目标信息流</div>
      <h2 className={styles.title}>
        {focusLabels.length
          ? `已把与${focusLabels.join("、")}有连接的创作者排到前面。`
          : `${counts.priced} 位已有报价可立即确认，${counts.discovered} 位由 Mango 去建联。`}
      </h2>
      <p className={styles.note}>
        名单由 {coverage.collected} 位已采集关注网络的目标人物反推而来。
        <span className={styles.unverified}>{UNVERIFIED_NOTE}</span>
        {counts.keptAsUnknown > 0 &&
          ` 其中 ${counts.keptAsUnknown} 位在你筛选的维度上画像待确认，已保留在名单内。`}
      </p>

      <div className={styles.counts}>
        {[
          { k: "候选合计", v: counts.total },
          { k: "已有报价", v: counts.priced },
          { k: "新发现", v: counts.discovered },
          { k: "目标人物", v: `${coverage.collected}/${coverage.targets}` },
        ].map((c) => (
          <div key={c.k} className={styles.count}>
            <div className={styles.countK}>{c.k}</div>
            <div className={styles.countV}>{c.v}</div>
          </div>
        ))}
      </div>

      {keptItems.length > 0 && (
        <div className={styles.keptBox}>
          <div className={styles.keptTitle}>你的名单</div>
          {/* 整句一次拼好再输出。之前按条件拼片段，「可立即确认」为 0 时中段
              被跳过，句号和逗号直接撞在一起（「5 个。，另 8 位」）。 */}
          <p className={styles.keptLine}>
            {[
              `这 ${keptItems.length} 位打通了 ${circles.length} 个目标圈层中的 ${keptCircles.size} 个`,
              readyCount > 0 ? `${readyCount} 位可即刻推进档期` : null,
              keptItems.length - readyCount > 0
                ? `${keptItems.length - readyCount} 位由 Mango 出面建联`
                : null,
            ]
              .filter(Boolean)
              .join("，") + "。"}
          </p>
          <div className={styles.keptStats}>
            {[
              { k: "已保留", v: String(keptItems.length) },
              { k: "覆盖圈层", v: `${keptCircles.size}/${circles.length}` },
              { k: "可立即确认", v: String(readyCount) },
            ].map((s) => (
              <div key={s.k} className={styles.count}>
                <div className={styles.countK}>{s.k}</div>
                <div className={styles.countV}>{s.v}</div>
              </div>
            ))}
          </div>
          <p className={styles.disclaimer}>{PRICE_NOTE}</p>
        </div>
      )}

      {kept.length === 0 && suggestionNote && (
        <div className={styles.suggest}>
          <div className={styles.suggestTitle}>Mango 建议起点</div>
          <p className={styles.suggestNote}>{suggestionNote}</p>
          <button type="button" className={styles.suggestBtn} onClick={onApplySuggestion}>
            采用这个起点
          </button>
        </div>
      )}

      {keptItems.length > 0 && (
        <PathGraph kept={keptItems} targets={targets} circles={circles} />
      )}

      {/* 匹配的和相关性待确认的分开。
       *
       * 「缺数据不淘汰」不等于「缺数据可以混进匹配结果」：筛 dev_tools 时有
       * 5 位真的匹配、28 位只是我们不知道他写什么。排在一起等于告诉客户这
       * 33 个都合适。MrBeast 被 11 位 AI 目标人物关注是事实，但他是泛娱乐
       * 创作者 —— 用连接冒充相关性是这一屏最容易犯的错。 */}
      <div className={styles.grid}>
        {items.filter((i) => i.relevance === "matched").map((it) => (
          <CandidateCard
            key={it.id}
            item={it}
            kept={keptIds.has(it.id)}
            format={formatOf(it.id)}
            onKeep={() => onKeep(it.id)}
            onRemove={() => onRemove(it.id)}
            onFormat={(f) => onFormat(it.id, f)}
            onSwap={keptIds.has(it.id) ? () => onSwap(it.id) : undefined}
            swapLabel={keptIds.has(it.id) ? "同圈层的次优人选" : null}
          />
        ))}
      </div>

      {counts.relevanceUnknown > 0 && (
        <>
          <div className={styles.divider}>
            <span className={styles.dividerTitle}>相关性待确认 · {counts.relevanceUnknown} 位</span>
            <span className={styles.dividerNote}>
              这些账号确实被目标人物关注，但我们还没判定出他们的内容方向 ——
              保留在这里供你判断，不与上方候选比较。
            </span>
          </div>
          <div className={styles.grid}>
            {items.filter((i) => i.relevance === "unknown").map((it) => (
              <CandidateCard
                key={it.id}
                item={it}
                kept={keptIds.has(it.id)}
                format={formatOf(it.id)}
                onKeep={() => onKeep(it.id)}
                onRemove={() => onRemove(it.id)}
                onFormat={(f) => onFormat(it.id, f)}
              />
            ))}
          </div>
        </>
      )}
    </section>
  );
}
