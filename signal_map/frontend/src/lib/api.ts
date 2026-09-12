/* Signal Map v3 API client.
 *
 * Types mirror design_handoff_v3/README.md §三 (数据模型) and the field names
 * the backend actually emits, so there is no translation layer to keep in sync.
 *
 * Everything here is read-only catalog data plus the session. Nothing in these
 * shapes carries a price amount -- the backend sends `tier` (1-4) and the UI
 * renders `$`..`$$$$`. If an amount ever appears in a response, that is a
 * backend leak, not something to format here.
 */

export type Circle = {
  id: string;
  label: string;
  value: string;
  /** 这类人是谁、为什么值得影响 */
  who: string;
  /** KOL 选择逻辑：据此如何挑人 */
  pick: string;
  coverage: { targets: number; collected: number };
};

export type Group = {
  id: string;
  label: string;
  desc: string;
  domains: string[];
  circles: Circle[];
};

export type Target = {
  id: string;
  name: string;
  handle: string;
  role: string;
  group: string;
  circle: string;
  why: string;
  markets: string[];
  audiences: string[];
  avatarUrl: string | null;
  /** 是否已采集到该目标人物的关注网络 */
  collected: boolean;
  /** 是否经过人工复核。当前全库为 false。 */
  reviewed: boolean;
};

export type Edge = {
  targetId: string;
  type: "cofollow" | "reply" | "quote" | "co_appear";
  count: number;
  lastSeen: string | null;
  verified: boolean;
  strength: "strong" | "medium" | "weak";
  evidence?: string;
};

export type Part = {
  id: string;
  label: string;
  weight: number;
  pct: number;
  note: string;
};

export type Face = {
  id: string;
  name: string;
  handle: string | null;
  avatarUrl: string | null;
};

export type Candidate = {
  id: string;
  name: string | null;
  handle: string | null;
  url: string | null;
  avatarUrl: string | null;
  /** 适配度。**永远和 parts 一起显示**，不单独出现。 */
  fit: number;
  band: string;
  bandLevel: 1 | 2 | 3 | 4;
  parts: Part[];
  /** 规则生成的推荐理由，不是模型写的。 */
  reason: string;
  overlapText: string;
  focusNote: string;
  circles: string[];
  faces: Face[];
  platform: string;
  followers: number | null;
  group: string;
  source: "priced" | "discovered";
  bizState: "ready" | "open_channel" | "needs_bd";
  bizEvidence: string;
  bizLabel: string;
  edges: Edge[];
  risk: string[];
  type?: string | null;
  tier?: 1 | 2 | 3 | 4 | null;
  quoteStatus?: "confirmed" | "historical_unverified";
  markets?: string[];
  languages?: string[];
  domains?: string[];
  audiences?: string[];
  bio?: string | null;
  /** 经由哪几位目标人物找到 —— discovered 侧的可解释性来源 */
  discoveryPath?: string[];
};

export type CandidatesResponse = {
  group: string;
  counts: {
    total: number;
    priced: number;
    discovered: number;
    /** 因所筛维度判不出来而保留的人数，界面要如实说明。 */
    keptAsUnknown: number;
  };
  coverage: { targets: number; collected: number; reviewed: number };
  items: Candidate[];
};

export type Meta = { kols: number; platforms: number; groups: number; markets: number };

export type Taxonomy = {
  markets: { id: string; label: string; en?: string }[];
  languages: { id: string; label: string }[];
  domains: { id: string; label: string; group: string }[];
  audiences: { id: string; label: string; sub?: string; why?: string }[];
  formats: { id: string; label: string }[];
  tiers: { id: number; mark: string; label: string }[];
  budgets: { id: string; label: string; lo: number; hi: number }[];
  brandPrefs: { id: string; label: string }[];
};

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { accept: "application/json" } });
  if (!res.ok) {
    throw new Error(`${path} -> ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export type SessionState = {
  id: string;
  prefs: Record<string, unknown> | null;
  focus: string[];
  custom: { name: string; hint?: string | null; status?: string }[];
  plans: { A: { id: string; format?: string }[]; B: { id: string; format?: string }[] };
  plan: string;
  listOpen: boolean;
  submittedAt: string | null;
  notice?: string;
};

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { "content-type": "application/json", accept: "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${method} ${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  meta: () => get<Meta>("/api/meta"),
  taxonomy: () => get<Taxonomy>("/api/taxonomy"),
  groups: () => get<Group[]>("/api/groups"),
  targets: (group: string) => get<Target[]>(`/api/targets?group=${encodeURIComponent(group)}`),
  candidates: (
    group: string,
    limit = 120,
    prefs?: Record<string, string[] | undefined>,
    focus?: string[],
  ) => {
    const q = new URLSearchParams({ group, limit: String(limit) });
    if (focus?.length) q.set("focus", focus.slice(0, 3).join(","));
    // 偏好直接进查询串，后端按已知值筛、缺失值保留并计数。
    for (const [k, v] of Object.entries(prefs ?? {})) {
      if (v && v.length) q.set(k, v.join(","));
    }
    return get<CandidatesResponse>(`/api/candidates?${q}`);
  },

  // 会话 id 由服务端签发 —— 客户端自己起名意味着能猜的 id 谁都能读能覆盖。
  createSession: () => send<SessionState>("POST", "/api/sessions"),
  getSession: (id: string) => get<SessionState>(`/api/sessions/${id}`),
  saveSession: (id: string, body: Partial<SessionState>) =>
    send<SessionState>("PUT", `/api/sessions/${id}`, body),
  // 客户手填的待核查目标。**不进目标人物库** —— 它是线索，不是已确认的 Root。
  addWanted: (id: string, name: string, hint: string) =>
    send<{ custom: { name: string; hint?: string | null; status?: string }[]; notice: string }>(
      "POST", `/api/sessions/${id}/wanted`, { name, hint: hint || null },
    ),

  submitSession: (id: string) =>
    send<{ id: string; submittedAt: string; kept: number; receipt: string }>(
      "POST", `/api/sessions/${id}/submit`,
    ),
};

/** 档位 → $ 记号。客户端只看得到记号，看不到金额。 */
export function tierMark(tier: number | null | undefined): string {
  return tier ? "$".repeat(tier) : "—";
}
