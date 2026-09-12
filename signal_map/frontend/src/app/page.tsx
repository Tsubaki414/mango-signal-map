"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type CandidatesResponse, type Group, type Target } from "@/lib/api";
import { useSession, type SessionPrefs } from "@/lib/useSession";
import { TopBarV3 } from "@/views/v3/TopBarV3";
import { HeroV3 } from "@/views/v3/HeroV3";
import { PrefsV3 } from "@/views/v3/PrefsV3";
import { TargetsV3 } from "@/views/v3/TargetsV3";
import { CandidatesV3, type Kept } from "@/views/v3/CandidatesV3";
import { PersonPanel } from "@/views/v3/PersonPanel";
import { ConfirmV3 } from "@/views/v3/ConfirmV3";

const MAX_FOCUS = 3;

/* v3 是一条链接里的单页：Hero → 01 偏好 → 02 目标圈层 → 03 名单 → 04 确认，
 * 渐进展开（选了领域才出后面的东西）。状态留在这一层，不走路由 —— 换 URL 会
 * 重挂 WebGL 着色器并丢掉选择。 */
export default function Page() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [prefs, setPrefs] = useState<SessionPrefs>({});
  const [focus, setFocus] = useState<string[]>([]);
  const [kept, setKept] = useState<Kept[]>([]);
  const [focusNotice, setFocusNotice] = useState<string | null>(null);

  const [targets, setTargets] = useState<Target[]>([]);
  const [candidates, setCandidates] = useState<CandidatesResponse | null>(null);
  const [candLoading, setCandLoading] = useState(false);
  const [openPerson, setOpenPerson] = useState<Target | null>(null);

  const session = useSession();
  const group = prefs.group ?? null;

  useEffect(() => {
    api
      .groups()
      .then(setGroups)
      .catch((e) => setError(`读取领域失败：${e.message}。请确认后端已启动。`))
      .finally(() => setLoading(false));
  }, []);

  // 客户回到同一条链接时，把上次的偏好、重点圈层和名单读回来。
  useEffect(() => {
    if (!session.restored || !session.state) return;
    if (session.state.prefs) setPrefs(session.state.prefs as SessionPrefs);
    setFocus(session.state.focus ?? []);
    const plan = session.state.plan ?? "A";
    setKept((session.state.plans?.[plan as "A" | "B"] ?? []) as Kept[]);
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
      .then(setCandidates)
      .catch((e) => setError(`读取候选失败：${e.message}`))
      .finally(() => setCandLoading(false));
  }, [group, prefs.markets, prefs.domains, prefs.audiences, focus]);

  const circles = useMemo(
    () => groups.find((g) => g.id === group)?.circles ?? [],
    [groups, group],
  );

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

  function pickGroup(id: string) {
    // 换领域要清掉方向/人群/重点/名单：圈层与细分方向按领域分套，
    // 跨领域的 id 会筛出空结果。
    const next: SessionPrefs = { group: id, domains: [], markets: [], audiences: [] };
    setPrefs(next);
    setFocus([]);
    setKept([]);
    persist({ prefs: next, focus: [], kept: [] });
  }

  function toggleFocus(id: string) {
    setFocus((cur) => {
      let next: string[];
      if (cur.includes(id)) {
        next = cur.filter((x) => x !== id);
        setFocusNotice(null);
      } else if (cur.length >= MAX_FOCUS) {
        // 超出时挤掉最早的，并告知 —— 静默丢弃会让客户以为点击没生效。
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

  function keep(id: string) {
    setKept((cur) => {
      const next = cur.some((k) => k.id === id) ? cur : [...cur, { id, format: "post" }];
      persist({ kept: next });
      return next;
    });
  }

  function remove(id: string) {
    setKept((cur) => {
      const next = cur.filter((k) => k.id !== id);
      persist({ kept: next });
      return next;
    });
  }

  function setFormat(id: string, format: string) {
    setKept((cur) => {
      const next = cur.map((k) => (k.id === id ? { ...k, format } : k));
      persist({ kept: next });
      return next;
    });
  }

  const reached = ["prefs", ...(group ? ["targets", "list"] : []), ...(kept.length ? ["confirm"] : [])];

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
      <PrefsV3
        groups={groups}
        loading={loading}
        error={error}
        selected={group}
        restoredFrom={session.restored && group ? "上次的选择已带回" : null}
        onSelect={pickGroup}
      />
      {group && (
        <TargetsV3
          circles={circles}
          targets={targets}
          focus={focus}
          notice={focusNotice}
          onToggleFocus={toggleFocus}
          onOpenPerson={setOpenPerson}
        />
      )}
      {group && (
        <CandidatesV3
          data={candidates}
          loading={candLoading}
          circles={circles}
          focus={focus}
          kept={kept}
          onKeep={keep}
          onRemove={remove}
          onFormat={setFormat}
        />
      )}
      {group && kept.length > 0 && (
        <ConfirmV3
          kept={kept}
          items={candidates?.items ?? []}
          circles={circles}
          focus={focus}
          submittedAt={session.state?.submittedAt ?? null}
          onSubmit={session.submit}
        />
      )}
      {openPerson && (
        <PersonPanel
          target={openPerson}
          circle={circles.find((c) => c.id === openPerson.circle)}
          bridges={(candidates?.items ?? []).filter((i) =>
            i.edges.some((e) => e.targetId === openPerson.id),
          )}
          onClose={() => setOpenPerson(null)}
        />
      )}
    </div>
  );
}
