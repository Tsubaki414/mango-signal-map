"use client";

import { tierMark, type CandidatesResponse } from "@/lib/api";
import styles from "./CandidatesV3.module.css";

/* 03 推荐名单（第一版：只读渲染，保留/比较尚未接）。
 *
 * 两类候选刻意用同一种卡片、不同标记：priced 是已有报价可立即确认的人，
 * discovered 是 Mango 还没联系过、靠公开关注关系发现出来的人。把后者藏起来
 * 或降级成"备选"会丢掉这个产品最值钱的部分。
 *
 * 价格只出现档位。后端也不下发金额，所以这里没有任何可以格式化成价格的东西。 */
export function CandidatesV3({
  data,
  loading,
}: {
  data: CandidatesResponse | null;
  loading: boolean;
}) {
  if (loading) {
    return (
      <section id="list" className={styles.section}>
        <div className={styles.eyebrow}>03 /// 推荐名单</div>
        <p className={styles.note}>正在按公开关注关系反推可投放创作者…</p>
      </section>
    );
  }
  if (!data) return null;

  const { counts, coverage, items } = data;

  return (
    <section id="list" className={styles.section} data-screen-label="推荐名单">
      <div className={styles.eyebrow}>03 /// 推荐名单</div>
      <h2 className={styles.title}>能把内容送进这些人信息流的创作者。</h2>
      <p className={styles.note}>
        名单由 {coverage.collected} 位已采集关注网络的目标人物反推而来。
        关系记录来自公开关注，<span className={styles.unverified}>尚未人工核验</span>
        ，不代表对方一定会看到或转发。
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

      <div className={styles.grid}>
        {items.map((it) => (
          <article key={it.id} className={styles.card}>
            <div className={styles.head}>
              <span className={styles.name}>{it.name ?? "资料待补充"}</span>
              <span className={styles.handle}>{it.handle}</span>
              <span
                className={`${styles.srcTag} ${
                  it.source === "priced" ? styles.srcPriced : styles.srcDiscovered
                }`}
              >
                {it.source === "priced" ? "已有报价" : "新发现"}
              </span>
            </div>

            <div className={styles.row}>
              {it.followers != null && <span>{it.followers.toLocaleString()} 关注者</span>}
              {it.source === "priced" && <span>{tierMark(it.tier)}</span>}
              <span>{it.edges.length} 条连接</span>
            </div>

            <div className={styles.biz}>{it.bizLabel}</div>

            <div className={styles.edges}>
              {it.source === "discovered" && it.discoveryPath?.length
                ? `经由 ${it.discoveryPath.length} 位目标人物的共同关注发现`
                : `${it.edges.length} 位目标人物关注了他`}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
