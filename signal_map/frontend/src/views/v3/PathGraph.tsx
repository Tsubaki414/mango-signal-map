"use client";

import { useMemo, useState } from "react";
import { tierMark, type Candidate, type Circle, type Target } from "@/lib/api";
import styles from "./PathGraph.module.css";

const TEAL = "#5FC4BB";
const ROW_H = 42;
const TOP = 8;
//: 三列的横向位置，单位是百分比×10（SVG viewBox 宽 1000）。
const COLS = [
  { x: 0, w: 30 },
  { x: 36, w: 32 },
  { x: 74, w: 26 },
];

type Props = {
  kept: Candidate[];
  targets: Target[];
  circles: Circle[];
};

/* 注意力路径图：已保留的创作者 → 目标人物 → 圈层。
 *
 * 三件事必须保持原样，改了就变成一张误导人的图：
 *
 * 1. **虚线 = 未核验。** 实线只给 verified 的边。当前全库 human_confirmed 为 0，
 *    所以这张图现在**全是虚线** —— 这是对的，它如实说明了「这些关系还没有人
 *    确认过」。把它画成实线好看，但那是伪造证据。
 * 2. **没有已保留的人就不画。** 空图会被读成「没有路径」，而实际是「你还没选人」。
 * 3. 点节点只做高亮，不做筛选。
 */
export function PathGraph({ kept, targets, circles }: Props) {
  const [focusNode, setFocusNode] = useState<string | null>(null);

  const graph = useMemo(() => {
    if (!kept.length) return null;
    const targetById = new Map(targets.map((t) => [t.id, t]));
    const tIds = [...new Set(kept.flatMap((k) => k.edges.map((e) => e.targetId)))].filter((id) =>
      targetById.has(id),
    );
    if (!tIds.length) return null;
    const circleIds = [...new Set(tIds.map((id) => targetById.get(id)?.circle))].filter(
      (c): c is string => Boolean(c),
    );

    const rows = Math.max(kept.length, tIds.length, circleIds.length);
    const h = TOP + rows * ROW_H + 8;
    const yOf = (n: number, i: number) =>
      TOP + (h - TOP - 8) / 2 - (n * ROW_H) / 2 + i * ROW_H + 4;

    type Node = {
      id: string;
      kind: "k" | "t" | "c";
      lx: number;
      lw: number;
      ty: number;
      label: string;
      sub: string;
    };
    const nodes: Node[] = [
      ...kept.map((m, i) => ({
        id: m.id,
        kind: "k" as const,
        lx: COLS[0].x,
        lw: COLS[0].w,
        ty: yOf(kept.length, i),
        label: m.name ?? "",
        sub: m.source === "priced" ? tierMark(m.tier) : "新发现",
      })),
      ...tIds.map((id, i) => ({
        id,
        kind: "t" as const,
        lx: COLS[1].x,
        lw: COLS[1].w,
        ty: yOf(tIds.length, i),
        label: targetById.get(id)?.name ?? id,
        sub: "",
      })),
      ...circleIds.map((c, i) => ({
        id: c,
        kind: "c" as const,
        lx: COLS[2].x,
        lw: COLS[2].w,
        ty: yOf(circleIds.length, i),
        label: circles.find((x) => x.id === c)?.label ?? c,
        sub: "",
      })),
    ];

    const linked = (id: string) => {
      if (!focusNode) return true;
      if (id === focusNode) return true;
      for (const m of kept) {
        const hits = m.edges.filter((e) => tIds.includes(e.targetId));
        const ids = [m.id, ...hits.map((e) => e.targetId)];
        const cs = hits.map((e) => targetById.get(e.targetId)?.circle).filter(Boolean);
        if ([...ids, ...cs].includes(focusNode) && [...ids, ...cs].includes(id)) return true;
      }
      return false;
    };

    const pos = (id: string) => nodes.find((n) => n.id === id);
    const edges: {
      x1: number; y1: number; x2: number; y2: number;
      stroke: string; w: number; dash: string; op: number;
    }[] = [];

    for (const m of kept) {
      for (const e of m.edges) {
        const a = pos(m.id);
        const b = pos(e.targetId);
        if (!a || !b) continue;
        edges.push({
          x1: (a.lx + a.lw) * 10,
          y1: a.ty + 17,
          x2: b.lx * 10,
          y2: b.ty + 17,
          stroke: e.verified ? TEAL : "rgba(243,241,234,.55)",
          w: e.verified ? 1.4 : 1,
          // 虚线 = 待核验。当前所有边都是虚线，如实反映核验状态。
          dash: e.verified ? "" : "4 5",
          op: Math.min(linked(m.id) ? 1 : 0.28, linked(e.targetId) ? 1 : 0.28),
        });
      }
    }
    for (const id of tIds) {
      const a = pos(id);
      const c = targetById.get(id)?.circle;
      const b = c ? pos(c) : undefined;
      if (!a || !b) continue;
      edges.push({
        x1: (a.lx + a.lw) * 10,
        y1: a.ty + 17,
        x2: b.lx * 10,
        y2: b.ty + 17,
        stroke: "rgba(95,196,187,.5)",
        w: 1,
        dash: "",
        op: Math.min(linked(id) ? 1 : 0.28, linked(b.id) ? 1 : 0.28),
      });
    }
    return { h, nodes, edges, linked };
  }, [kept, targets, circles, focusNode]);

  if (!graph) return null;

  return (
    <div className={styles.wrap}>
      <div className={styles.head}>
        <span className={styles.title}>注意力路径</span>
        <span className={styles.note}>
          {focusNode ? "再次点击取消" : "点击节点看它的路径"}
        </span>
      </div>

      <div className={styles.cols}>
        <span>已保留的创作者</span>
        <span>目标人物</span>
        <span>目标圈层</span>
      </div>

      <div className={styles.canvas} style={{ height: graph.h }}>
        <svg
          viewBox={`0 0 1000 ${graph.h}`}
          preserveAspectRatio="none"
          className={styles.svg}
          aria-hidden="true"
        >
          {graph.edges.map((e, i) => (
            <line
              key={i}
              x1={e.x1}
              y1={e.y1}
              x2={e.x2}
              y2={e.y2}
              stroke={e.stroke}
              strokeWidth={e.w}
              strokeDasharray={e.dash || undefined}
              opacity={e.op}
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>

        {graph.nodes.map((n) => (
          <button
            key={`${n.kind}-${n.id}`}
            type="button"
            className={`${styles.node} ${n.kind === "t" ? styles.nodeTarget : ""}`}
            style={{
              left: `${n.lx}%`,
              width: `${n.lw}%`,
              top: n.ty,
              opacity: graph.linked(n.id) ? 1 : 0.28,
            }}
            onClick={() => setFocusNode((f) => (f === n.id ? null : n.id))}
          >
            <span className={styles.nodeLabel}>{n.label}</span>
            {n.sub && <span className={styles.nodeSub}>{n.sub}</span>}
          </button>
        ))}
      </div>

      <div className={styles.legend}>
        <span className={styles.legendDash} /> 待核验（公开关注）
        <span className={styles.legendSolid} /> 已核验
        <span className={styles.legendNote}>
          目前全部关系均未经人工核验，所以图中都是虚线。
        </span>
      </div>
    </div>
  );
}
