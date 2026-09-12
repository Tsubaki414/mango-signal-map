"use client";

import { useState } from "react";
import styles from "./WantedV3.module.css";

export type Wanted = { name: string; hint?: string | null; status?: string };

type Props = {
  custom: Wanted[];
  onAdd: (name: string, hint: string) => Promise<void> | void;
};

/* 客户手填的库外目标人物。
 *
 * 这些名字**不进目标人物库** —— 客户说某人重要，是一条待 BD 核查的线索，不是
 * 已确认的 Root。写进库会让一个未经核实的名字获得和已采集目标人物同等的地位，
 * 而后者至少解析过账号、采过关注网络。
 *
 * 所以这里的状态只有一种：待 Mango 核查。界面上不承诺「已加入」。 */
export function WantedV3({ custom, onAdd }: Props) {
  const [name, setName] = useState("");
  const [hint, setHint] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    const n = name.trim();
    if (!n || busy) return;
    setBusy(true);
    try {
      await onAdd(n, hint.trim());
      setName("");
      setHint("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.title}>还有想影响的人没在上面？</div>
      <p className={styles.note}>
        Mango 会核查他的公开网络，确认能否覆盖后回复你。
      </p>

      <div className={styles.form}>
        <input
          className={styles.input}
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
          placeholder="人物姓名或 @handle"
          aria-label="待核查的目标人物"
        />
        <input
          className={styles.input}
          value={hint}
          onChange={(e) => setHint(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
          placeholder="他是谁 / 为什么重要（可选）"
          aria-label="补充说明"
        />
        <button type="button" className={styles.add} onClick={submit} disabled={!name.trim() || busy}>
          {busy ? "提交中…" : "交 Mango 核查"}
        </button>
      </div>

      {custom.length > 0 && (
        <div className={styles.list}>
          {custom.map((c, i) => (
            <div key={`${c.name}-${i}`} className={styles.item}>
              <span className={styles.itemName}>{c.name}</span>
              {c.hint && <span className={styles.itemHint}>{c.hint}</span>}
              <span className={styles.itemStatus}>待 Mango 核查</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
