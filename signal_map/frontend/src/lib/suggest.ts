import type { Candidate, Circle } from "./api";

/* Mango 建议起点。
 *
 * 策略是**先铺圈层，再补强度**：按重点圈层逐个取该圈层里连接最强的一位，
 * 保证每个圈层至少有一条路径；名额有剩再按整体连接强度补满。
 *
 * 为什么不是「取前 N 名」：前 N 名很可能全部连着同一个圈层 —— 那样客户拿到的
 * 是一份很高分但只能进入一类人视野的名单，而他要的是覆盖。
 *
 * 上限 8 位。再多就不是「起点」而是「全都要」，客户反而无从下手。 */
export function suggestLineup(
  candidates: Candidate[],
  circles: Circle[],
  focus: string[],
  max = 8,
): string[] {
  if (!candidates.length) return [];
  const order = focus.length ? circles.filter((c) => focus.includes(c.id)) : circles;
  const picked: string[] = [];
  const used = new Set<string>();

  for (const c of order) {
    // 每个圈层**优先取已有报价的那一位**。
    //
    // 候选是按连接强度排序的，而名人型的「新发现」几乎总是压过已报价创作者
    // （他们被更多目标人物关注）。只取第一名的话，建议起点会全是待建联的人 ——
    // 实测 8 位里 0 位可立即确认。一份一个都推进不了的起点不是建议，是清单。
    const inCircle = candidates.filter((m) => !used.has(m.id) && m.circles.includes(c.id));
    const best = inCircle.find((m) => m.bizState === "ready") ?? inCircle[0];
    if (best) {
      picked.push(best.id);
      used.add(best.id);
    }
    if (picked.length >= max) return picked;
  }
  // candidates 已按连接强度排好序，顺序补满即可。
  for (const m of candidates) {
    if (picked.length >= max) break;
    if (!used.has(m.id)) {
      picked.push(m.id);
      used.add(m.id);
    }
  }
  return picked;
}

/** 建议的一句话说明。数字全部来自实际挑出来的人，不写死。 */
export function suggestNote(
  picked: Candidate[],
  circles: Circle[],
  focus: string[],
): string {
  const covered = new Set(picked.flatMap((p) => p.circles));
  const ready = picked.filter((p) => p.bizState === "ready").length;
  const focusLabels = circles.filter((c) => focus.includes(c.id)).map((c) => c.label);
  const head = focusLabels.length
    ? `优先覆盖${focusLabels.join("、")}，`
    : "先把每个圈层都打通一条路径，";
  return (
    `${head}这 ${picked.length} 位覆盖了 ${circles.length} 个圈层中的 ${covered.size} 个；` +
    `其中 ${ready} 位已有报价可立即确认，其余由 Mango 出面建联。`
  );
}

/** 换一个：同圈层、未被保留、连接强度次优的一位。 */
export function findSwap(
  target: Candidate,
  candidates: Candidate[],
  keptIds: Set<string>,
): Candidate | null {
  return (
    candidates.find(
      (m) =>
        m.id !== target.id &&
        !keptIds.has(m.id) &&
        m.circles.some((c) => target.circles.includes(c)),
    ) ?? null
  );
}
