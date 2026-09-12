"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type SessionState } from "./api";

/* 会话：开、存、回来时读回。
 *
 * id **只由服务端签发**，本地只负责记住它。客户端自己起名的 id（"acme-2026"）
 * 等于把凭证交给调用方 —— 这一层没有 per-client token，猜到 id 就能读别人的
 * 偏好和名单。所以这里从不构造 id，只存服务端给的那个。
 *
 * 存在 localStorage，键按 v3 README 的形状；提交过的会话不再写回（后端也会
 * 用 409 拒绝），因为那是一份 Mango 要去履约的名单。 */
const KEY = "mango-signal-map:session";

export type SessionPrefs = {
  group?: string | null;
  domains?: string[];
  markets?: string[];
  languages?: string[];
  audiences?: string[];
  budget?: string | null;
  brand?: string[];
};

export function useSession() {
  const [id, setId] = useState<string | null>(null);
  const [state, setState] = useState<SessionState | null>(null);
  const [restored, setRestored] = useState(false);
  const [saveNote, setSaveNote] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const stored = typeof window === "undefined" ? null : localStorage.getItem(KEY);
      if (stored) {
        try {
          const existing = await api.getSession(stored);
          if (!cancelled) {
            setId(existing.id);
            setState(existing);
            setRestored(true);
          }
          return;
        } catch {
          // 会话被清掉或过期：丢掉这个 id，重新开一个，而不是卡在错误页。
          localStorage.removeItem(KEY);
        }
      }
      const fresh = await api.createSession();
      if (cancelled) return;
      localStorage.setItem(KEY, fresh.id);
      setId(fresh.id);
      setState(fresh);
    })().catch(() => {
      /* 后端没起来时不阻断浏览：名单照常能看，只是不保存。 */
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const save = useCallback(
    async (patch: Partial<SessionState>) => {
      if (!id || state?.submittedAt) return;
      try {
        const next = await api.saveSession(id, {
          prefs: state?.prefs ?? null,
          focus: state?.focus ?? [],
          custom: state?.custom ?? [],
          plans: state?.plans ?? { A: [], B: [] },
          plan: state?.plan ?? "A",
          listOpen: state?.listOpen ?? false,
          ...patch,
        });
        setState(next);
        setSaveNote(next.notice ?? "已保存");
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => setSaveNote(""), 1600);
      } catch {
        setSaveNote("保存失败");
      }
    },
    [id, state],
  );

  const submit = useCallback(async () => {
    if (!id) return null;
    const res = await api.submitSession(id);
    setState((s) => (s ? { ...s, submittedAt: res.submittedAt } : s));
    return res;
  }, [id]);

  return { id, state, restored, saveNote, save, submit };
}
