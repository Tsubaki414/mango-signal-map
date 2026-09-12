"use client";

import { useEffect, useState } from "react";
import styles from "./RevealV3.module.css";

type Props = {
  lines: string[];
  onDone: () => void;
};

/* 生成揭示：四行逐条点亮，行间隔 1150ms，点任意处跳过。
 *
 * 它不是装饰。这四行说的是**这份名单是怎么算出来的** —— 读了哪些条件、展开了
 * 谁的关注网络、从多少人里收敛到多少人、按什么排序。客户在等待的几秒里读到
 * 这些，名单出现时就不是一个黑箱结果。
 *
 * 所以「跳过」必须存在（第二次之后没人想再看），但默认要播。 */
export function RevealV3({ lines, onDone }: Props) {
  const [step, setStep] = useState(0);

  useEffect(() => {
    if (step >= lines.length) {
      const t = setTimeout(onDone, 700);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => setStep((s) => s + 1), step === 0 ? 300 : 1150);
    return () => clearTimeout(t);
  }, [step, lines.length, onDone]);

  return (
    <div className={styles.scrim} onClick={onDone} role="presentation">
      <div className={styles.inner}>
        <div className={styles.eyebrow}>正在生成你的名单</div>
        {lines.map((text, i) => (
          <p
            key={i}
            className={styles.line}
            style={{ color: i < step ? "#F3F1EA" : "rgba(243,241,234,.34)" }}
          >
            {text}
          </p>
        ))}
        <svg viewBox="0 0 400 44" className={styles.bands} aria-hidden="true">
          <g strokeWidth="2">
            {[0, 1, 2, 3].map((i) => (
              <path
                key={i}
                d={["M0 5H200l8 5h192", "M0 18H120l8 5h272", "M0 31H260l8 5h132", "M0 44H80l8-5h312"][i]}
                stroke={i < step ? "#5FC4BB" : "rgba(243,241,234,.2)"}
                style={{ animation: `msm-band 1.4s ${i * 0.2}s infinite` }}
              />
            ))}
          </g>
        </svg>
        <button type="button" className={styles.skip} onClick={onDone}>
          点击任意处跳过
        </button>
      </div>
    </div>
  );
}
