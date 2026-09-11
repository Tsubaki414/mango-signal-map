"use client";

import { useEffect, useState } from "react";
import { api, type CandidatesResponse, type Group } from "@/lib/api";
import { useSession, type SessionPrefs } from "@/lib/useSession";
import { TopBarV3 } from "@/views/v3/TopBarV3";
import { HeroV3 } from "@/views/v3/HeroV3";
import { PrefsV3 } from "@/views/v3/PrefsV3";
import { CandidatesV3 } from "@/views/v3/CandidatesV3";

/* v3 是一条链接里的单页：Hero → 01 偏好 → 02 目标圈层 → 03 名单 → 04 确认，
 * 渐进展开（选了领域才出后面的东西）。所以状态留在这一层，不走路由 —— 换 URL
 * 会重挂 WebGL 着色器并丢掉选择。 */
export default function Page() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [prefs, setPrefs] = useState<SessionPrefs>({});
  const [candidates, setCandidates] = useState<CandidatesResponse | null>(null);
  const [candLoading, setCandLoading] = useState(false);

  const session = useSession();

  useEffect(() => {
    api
      .groups()
      .then(setGroups)
      .catch((e) => setError(`读取领域失败：${e.message}。请确认后端已启动。`))
      .finally(() => setLoading(false));
  }, []);

  // 客户回到同一条链接时，把上次的偏好读回来。
  useEffect(() => {
    if (session.restored && session.state?.prefs) {
      setPrefs(session.state.prefs as SessionPrefs);
    }
  }, [session.restored, session.state]);

  const group = prefs.group ?? null;

  useEffect(() => {
    if (!group) return;
    setCandLoading(true);
    setCandidates(null);
    api
      .candidates(group, 60, {
        markets: prefs.markets,
        domains: prefs.domains,
        audiences: prefs.audiences,
      })
      .then(setCandidates)
      .catch((e) => setError(`读取候选失败：${e.message}`))
      .finally(() => setCandLoading(false));
  }, [group, prefs.markets, prefs.domains, prefs.audiences]);

  function pickGroup(id: string) {
    // 换领域要清掉方向/人群：圈层与细分方向按领域分套，跨领域的 id 会筛出空结果。
    const next: SessionPrefs = { group: id, domains: [], markets: [], audiences: [] };
    setPrefs(next);
    session.save({ prefs: next as Record<string, unknown> });
  }

  const reached = ["prefs", ...(group ? ["targets", "list"] : [])];

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
          session.save({ prefs: next as Record<string, unknown> });
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
      {group && <CandidatesV3 data={candidates} loading={candLoading} />}
    </div>
  );
}
