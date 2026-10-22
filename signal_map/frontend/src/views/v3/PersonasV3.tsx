"use client";

import { useEffect, useMemo, useState } from "react";
import { api, type Persona, type PersonaMember, type PersonaRankingResponse } from "@/lib/api";
import { Avatar } from "./Avatar";
import styles from "./PersonasV3.module.css";

type Row = PersonaMember & { personas: string[] };

function fmtFollowers(n: number | null): string {
  if (n == null) return "粉丝数未知";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M 粉丝`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K 粉丝`;
  return `${n} 粉丝`;
}

/* 人群预设：批量选择社工对象，再出跨圈层综合排名。
 *
 * 自成一块，不改动 02/03 的候选逻辑：这里的排名回答「所选几类人**共同**关注
 * 了谁」，和下面按圈层重排的候选名单是两个问题。
 *
 * 勾掉的人作为 exclude 发给后端 —— 客户只能从预设里减人，不能在这里加任意
 * handle；库外的名字走上面的「想覆盖但没看到的人」。 */
export function PersonasV3() {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  /** 被勾掉的成员，handle 小写 */
  const [unticked, setUnticked] = useState<Set<string>>(new Set());
  const [result, setResult] = useState<PersonaRankingResponse | null>(null);
  const [stale, setStale] = useState(false);
  const [busy, setBusy] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  useEffect(() => {
    api
      .personas()
      .then(setPersonas)
      .catch((e) => setLoadError(`读取人群预设失败：${e.message}`));
  }, []);

  const labelOf = useMemo(
    () => Object.fromEntries(personas.map((p) => [p.id, p.label])),
    [personas],
  );

  // 同一个人出现在多个预设里只列一次，但记下他代表哪几个圈子。
  const rows = useMemo(() => {
    const byHandle = new Map<string, Row>();
    for (const p of personas) {
      if (!selected.includes(p.id)) continue;
      for (const m of p.members) {
        const key = m.handle.toLowerCase();
        const row = byHandle.get(key);
        if (row) {
          if (!row.personas.includes(p.id)) row.personas.push(p.id);
        } else {
          byHandle.set(key, { ...m, personas: [p.id] });
        }
      }
    }
    return [...byHandle.values()];
  }, [personas, selected]);

  const tickedCount = rows.filter((r) => !unticked.has(r.handle.toLowerCase())).length;

  function togglePersona(id: string) {
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
    setStale(true);
  }

  function toggleMember(handle: string) {
    const key = handle.toLowerCase();
    setUnticked((cur) => {
      const next = new Set(cur);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    setStale(true);
  }

  async function run() {
    const exclude = rows.filter((r) => unticked.has(r.handle.toLowerCase())).map((r) => r.handle);
    setBusy(true);
    setRunError(null);
    try {
      setResult(await api.personaRanking(selected, exclude, 50));
      setStale(false);
    } catch (e) {
      setRunError(`生成综合排名失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section id="personas" className={styles.section} data-screen-label="人群预设">
      <div className={styles.eyebrow}>02B /// 按人群批量选择</div>
      <h2 className={styles.title}>请批量选择你的社工对象</h2>
      <p className={styles.note}>
        先选一类或几类人，再在展开的名单里勾掉不想算进来的人。名单里的人是你想影响的对象，不是要投放的
        KOL；综合排名列出的是他们共同关注的账号。
      </p>
      {loadError && <p className={styles.warn}>{loadError}</p>}

      <div className={styles.chips}>
        {personas.map((p) => {
          const on = selected.includes(p.id);
          return (
            <button
              key={p.id}
              type="button"
              className={`${styles.chip} ${on ? styles.chipOn : ""}`}
              onClick={() => togglePersona(p.id)}
              aria-pressed={on}
              title={p.why || undefined}
            >
              <span aria-hidden="true">{on ? "■" : "□"}</span>
              <span>{p.label}</span>
              <span className={styles.chipCount}>{p.members.length} 人</span>
            </button>
          );
        })}
      </div>

      {selected.length > 0 && (
        <div className={styles.descs}>
          {personas
            .filter((p) => selected.includes(p.id))
            .map((p) => (
              <p key={p.id} className={styles.desc}>
                <span className={styles.descLabel}>{p.label}</span>
                {p.desc}
                {p.why && <span className={styles.why}> {p.why}</span>}
              </p>
            ))}
        </div>
      )}

      {rows.length > 0 && (
        <>
          <div className={styles.listHead}>
            已选 {tickedCount} / {rows.length} 位目标人物
          </div>
          <div className={styles.members}>
            {rows.map((r) => {
              const on = !unticked.has(r.handle.toLowerCase());
              return (
                <label key={r.handle} className={`${styles.member} ${on ? "" : styles.memberOff}`}>
                  <input
                    type="checkbox"
                    className={styles.check}
                    checked={on}
                    onChange={() => toggleMember(r.handle)}
                  />
                  <Avatar src={r.avatarUrl} name={r.name} size={32} />
                  <span className={styles.memberBody}>
                    <span className={styles.memberName}>{r.name}</span>
                    <span className={styles.memberHandle}>{r.handle}</span>
                    {r.role && <span className={styles.memberRole}>{r.role}</span>}
                    <span className={styles.memberTags}>
                      {r.personas.map((pid) => (
                        <span key={pid} className={styles.tag}>
                          {labelOf[pid] ?? pid}
                        </span>
                      ))}
                      {r.needsReview && <span className={styles.tagWarn}>待核实</span>}
                      {/* 没采过的人照常列出 —— 他在综合分里不计入分母，不是「没关注」。 */}
                      {!r.collected && <span className={styles.tagWarn}>关系待采集</span>}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>

          <div className={styles.actions}>
            <button
              type="button"
              className={styles.runBtn}
              onClick={run}
              disabled={busy || tickedCount === 0}
            >
              {busy ? "生成中…" : "生成综合排名"}
            </button>
            {tickedCount === 0 && <span className={styles.hint}>至少保留一位目标人物。</span>}
            {result && stale && !busy && (
              <span className={styles.hint}>选择已变更，重新生成后排名才会更新。</span>
            )}
          </div>
          {runError && <p className={styles.warn}>{runError}</p>}
        </>
      )}

      {result && (
        <div className={styles.result}>
          <div className={styles.coverage}>
            {result.personas.map((p) => (
              <span key={p.id} className={p.inScore ? styles.tag : styles.tagWarn}>
                {p.label} · 已采集 {p.collected}/{p.targets}
                {!p.inScore && " · 不参与综合分"}
              </span>
            ))}
          </div>

          {result.items.length === 0 ? (
            <p className={styles.note}>当前未观察到所选目标人物共同关注的可投放账号。</p>
          ) : (
            <>
              <div className={styles.listHead}>
                显示前 {result.counts.total} 位，共 {result.counts.poolTotal} 位进入排名
              </div>
              <div className={styles.tableWrap}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>账号</th>
                      <th>状态</th>
                      <th>综合分</th>
                      <th>关注它的目标人物</th>
                      <th>覆盖的人群</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.items.map((it) => (
                      <tr key={it.handle ?? it.rank}>
                        <td className={styles.rank}>{it.rank}</td>
                        <td>
                          <span className={styles.who}>
                            <Avatar src={it.avatarUrl} name={it.name} size={32} />
                            <span className={styles.memberBody}>
                              <span className={styles.memberName}>{it.name}</span>
                              <span className={styles.memberHandle}>
                                {it.url ? (
                                  <a href={it.url} target="_blank" rel="noreferrer">
                                    {it.handle}
                                  </a>
                                ) : (
                                  it.handle
                                )}
                                {" · "}
                                {fmtFollowers(it.followers)}
                              </span>
                            </span>
                          </span>
                        </td>
                        <td>
                          <span className={it.source === "priced" ? styles.tagOk : styles.tagWarn}>
                            {it.source === "priced" ? "已报价" : "需建联"}
                          </span>
                        </td>
                        <td className={styles.score}>{it.score.toFixed(2)}</td>
                        <td className={styles.count} title={it.followedBy.join("、")}>
                          {it.targetsFollowing} 位
                        </td>
                        <td>
                          <span className={styles.memberTags}>
                            {it.byPersona.map((b) => (
                              <span
                                key={b.persona}
                                className={b.count > 0 ? styles.tag : styles.tagDim}
                              >
                                {b.label} {b.collected ? `${b.count}/${b.collected}` : "未采集"}
                              </span>
                            ))}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <p className={styles.footnote}>{result.note} 关注关系均为研究线索，尚未人工确认。</p>
          {result.uncollected.length > 0 && (
            <p className={styles.footnote}>
              以下目标人物的关注网络尚未采集，未计入排名：{result.uncollected.join("、")}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
