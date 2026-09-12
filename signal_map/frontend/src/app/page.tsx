"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type CandidatesResponse,
  type Group,
  type Target,
  type Taxonomy,
} from "@/lib/api";
import { useSession, type SessionPrefs } from "@/lib/useSession";
import { findSwap, suggestLineup, suggestNote } from "@/lib/suggest";
import { TopBarV3 } from "@/views/v3/TopBarV3";
import { HeroV3 } from "@/views/v3/HeroV3";
import { PrefsV3 } from "@/views/v3/PrefsV3";
import { PrefGroups } from "@/views/v3/PrefGroups";
import { TargetsV3 } from "@/views/v3/TargetsV3";
import { WantedV3, type Wanted } from "@/views/v3/WantedV3";
import { CandidatesV3, type Kept } from "@/views/v3/CandidatesV3";
import { PersonPanel } from "@/views/v3/PersonPanel";
import { ConfirmV3 } from "@/views/v3/ConfirmV3";
import { RailV3 } from "@/views/v3/RailV3";
import { RevealV3 } from "@/views/v3/RevealV3";

const MAX_FOCUS = 3;

export default function Page() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [prefs, setPrefs] = useState<SessionPrefs>({});
  const [focus, setFocus] = useState<string[]>([]);
  const [kept, setKept] = useState<Kept[]>([]);
  const [custom, setCustom] = useState<Wanted[]>([]);
  const [focusNotice, setFocusNotice] = useState<string | null>(null);
  const [tick, setTick] = useState("每次选择都会立刻重排名单。");

  const [targets, setTargets] = useState<Target[]>([]);
  const [candidates, setCandidates] = useState<CandidatesResponse | null>(null);
  const [candLoading, setCandLoading] = useState(false);
  const [openPerson, setOpenPerson] = useState<Target | null>(null);
  const [revealing, setRevealing] = useState(false);
  const revealed = useRef(false);

  const session = useSession();
  const group = prefs.group ?? null;

  useEffect(() => {
    Promise.all([api.groups(), api.taxonomy()])
      .then(([g, t]) => {
        setGroups(g);
        setTaxonomy(t);
      })
      .catch((e) => setError(`读取配置失败：${e.message}。请确认后端已启动。`))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!session.restored || !session.state) return;
    if (session.state.prefs) setPrefs(session.state.prefs as SessionPrefs);
    setFocus(session.state.focus ?? []);
    setCustom((session.state.custom ?? []) as Wanted[]);
    const plan = session.state.plan ?? "A";
    setKept((session.state.plans?.[plan as "A" | "B"] ?? []) as Kept[]);
    revealed.current = true; // 回访不再播揭示动画
  }, [session.restored, session.state]);

  useEffect(() => {
    if (!group) return;
    api.targets(group).then(setTargets).catch(() => setTargets([]));
  }, [group]);

  useEffect(() => {
    if (!group) return;
    setCandLoading(true);
    api
      .candidates(
        group,
        60,
        { markets: prefs.markets, domains: prefs.domains, audiences: prefs.audiences },
        focus,
      )
      .then((res) => {
        setCandidates((prev) => {
          if (prev) {
            const delta = res.counts.total - prev.counts.total;
            setTick(
              delta === 0
                ? "候选数量不变，只是顺序变了 —— 标记重点从不减少名单。"
                : delta > 0
                  ? `新增 ${delta} 位候选。`
                  : `减少 ${-delta} 位候选。`,
            );
          }
          return res;
        });
        if (!revealed.current) {
          revealed.current = true;
          setRevealing(true);
        }
      })
      .catch((e) => setError(`读取候选失败：${e.message}`))
      .finally(() => setCandLoading(false));
  }, [group, prefs.markets, prefs.domains, prefs.audiences, focus]);

  const circles = useMemo(
    () => groups.find((g) => g.id === group)?.circles ?? [],
    [groups, group],
  );
  const items = candidates?.items ?? [];
  const keptIds = useMemo(() => new Set(kept.map((k) => k.id)), [kept]);
  const keptItems = useMemo(() => items.filter((i) => keptIds.has(i.id)), [items, keptIds]);

  const persist = useCallback(
    (patch: { prefs?: SessionPrefs; focus?: string[]; kept?: Kept[] }) => {
      session.save({
        ...(patch.prefs ? { prefs: patch.prefs as Record<string, unknown> } : {}),
        ...(patch.focus ? { focus: patch.focus } : {}),
        ...(patch.kept ? { plans: { A: patch.kept, B: [] } } : {}),
      });
    },
    [session],
  );

  function updatePrefs(patch: Partial<SessionPrefs>) {
    setPrefs((cur) => {
      const next = { ...cur, ...patch };
      persist({ prefs: next });
      return next;
    });
  }

  function pickGroup(id: string) {
    // 换领域要清掉一切：圈层与细分方向按领域分套，跨领域 id 会筛出空结果。
    const next: SessionPrefs = { group: id, domains: [], markets: [], audiences: [], brand: [] };
    setPrefs(next);
    setFocus([]);
    setKept([]);
    revealed.current = false;
    persist({ prefs: next, focus: [], kept: [] });
  }

  function toggleFocus(id: string) {
    setFocus((cur) => {
      let next: string[];
      if (cur.includes(id)) {
        next = cur.filter((x) => x !== id);
        setFocusNotice(null);
      } else if (cur.length >= MAX_FOCUS) {
        next = [...cur.slice(1), id];
        setFocusNotice(`最多标记 ${MAX_FOCUS} 个重点圈层，已替换掉最早选择的一个。`);
      } else {
        next = [...cur, id];
        setFocusNotice(null);
      }
      persist({ focus: next });
      return next;
    });
  }

  const mutateKept = useCallback(
    (fn: (cur: Kept[]) => Kept[], note?: string) => {
      setKept((cur) => {
        const next = fn(cur);
        persist({ kept: next });
        return next;
      });
      if (note) setTick(note);
    },
    [persist],
  );

  function keep(id: string) {
    const item = items.find((i) => i.id === id);
    mutateKept(
      (cur) => (cur.some((k) => k.id === id) ? cur : [...cur, { id, format: "post" }]),
      item ? `保留 ${item.name}，覆盖 ${item.edges.length} 位目标人物。` : undefined,
    );
  }

  function remove(id: string) {
    const item = items.find((i) => i.id === id);
    mutateKept((cur) => cur.filter((k) => k.id !== id), item ? `移出 ${item.name}。` : undefined);
  }

  function swap(id: string) {
    const from = items.find((i) => i.id === id);
    if (!from) return;
    const to = findSwap(from, items, keptIds);
    if (!to) return;
    mutateKept(
      (cur) => cur.map((k) => (k.id === id ? { id: to.id, format: k.format } : k)),
      `${from.name} 换成 ${to.name}（同圈层）。`,
    );
  }

  function applySuggestion() {
    const ids = suggestLineup(items, circles, focus);
    const picked = items.filter((i) => ids.includes(i.id));
    mutateKept(
      () => ids.map((id) => ({ id, format: "post" })),
      suggestNote(picked, circles, focus),
    );
  }

  async function addWanted(name: string, hint: string) {
    if (!session.id) return;
    const res = await api.addWanted(session.id, name, hint);
    setCustom(res.custom as Wanted[]);
    setTick("已记录，Mango 会核查后回复是否可覆盖。");
  }

  const reached = [
    "prefs",
    ...(group ? ["targets", "list"] : []),
    ...(kept.length ? ["confirm"] : []),
  ];

  const revealLines = useMemo(
    () => [
      `读取你的方向：${groups.find((g) => g.id === group)?.label ?? ""}`,
      `展开 ${circles.length} 个圈层、${targets.filter((t) => t.collected).length} 位代表人物的公开关注网络` +
        (focus.length
          ? `，重点：${circles.filter((c) => focus.includes(c.id)).map((c) => c.label).join("、")}`
          : ""),
      `收敛出 ${candidates?.counts.total ?? 0} 位有可循路径的人选`,
      `按"谁更可能被他们看到"排序完成，可以开始挑了`,
    ],
    [groups, group, circles, targets, focus, candidates],
  );

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <TopBarV3
        reached={reached}
        saveNote={session.saveNote}
        onBrief={async (code) => {
          const res = await fetch(`/api/brief/${encodeURIComponent(code)}`);
          if (!res.ok) return "未找到该 Brief，请核对或联系你的 Mango 顾问。";
          const brief = await res.json();
          const next: SessionPrefs = brief.prefs ?? {};
          setPrefs(next);
          persist({ prefs: next });
          return null;
        }}
      />
      <HeroV3 />

      {/* 主体两栏：左内容、右实时反馈。选了领域才出右栏 —— 没有可反馈的东西时
          摆一个空侧栏只是占地方。 */}
      <div className={group ? "msm-with-rail" : undefined}>
        <main style={{ minWidth: 0 }}>
          <PrefsV3
            groups={groups}
            loading={loading}
            error={error}
            selected={group}
            restoredFrom={session.restored && group ? "上次的选择已带回" : null}
            onSelect={pickGroup}
          >
            {group && (
              <PrefGroups
                group={groups.find((g) => g.id === group)!}
                taxonomy={taxonomy}
                prefs={prefs}
                onChange={updatePrefs}
              />
            )}
          </PrefsV3>

          {group && (
            <TargetsV3
              circles={circles}
              targets={targets}
              focus={focus}
              notice={focusNotice}
              onToggleFocus={toggleFocus}
              onOpenPerson={setOpenPerson}
            >
              <WantedV3 custom={custom} onAdd={addWanted} />
            </TargetsV3>
          )}

          {group && (
            <CandidatesV3
              data={candidates}
              loading={candLoading}
              circles={circles}
              targets={targets}
              focus={focus}
              kept={kept}
              onKeep={keep}
              onRemove={remove}
              onSwap={swap}
              onFormat={(id, f) =>
                mutateKept((cur) => cur.map((k) => (k.id === id ? { ...k, format: f } : k)))
              }
              onApplySuggestion={applySuggestion}
              suggestionNote={
                items.length
                  ? suggestNote(
                      items.filter((i) => suggestLineup(items, circles, focus).includes(i.id)),
                      circles,
                      focus,
                    )
                  : ""
              }
            />
          )}

          {group && kept.length > 0 && (
            <ConfirmV3
              kept={kept}
              items={items}
              circles={circles}
              focus={focus}
              submittedAt={session.state?.submittedAt ?? null}
              onSubmit={session.submit}
            />
          )}
        </main>

        {group && (
          <RailV3
            candidates={items}
            kept={keptItems}
            circles={circles}
            focus={focus}
            tick={tick}
          />
        )}
      </div>

      {openPerson && (
        <PersonPanel
          target={openPerson}
          circle={circles.find((c) => c.id === openPerson.circle)}
          bridges={items.filter((i) => i.edges.some((e) => e.targetId === openPerson.id))}
          onClose={() => setOpenPerson(null)}
        />
      )}
      {revealing && <RevealV3 lines={revealLines} onDone={() => setRevealing(false)} />}
    </div>
  );
}
