"use client";

import type { Candidate, Circle, Target } from "@/lib/api";
import { Avatar } from "./Avatar";
import styles from "./PersonPanel.module.css";

type Props = {
  target: Target;
  circle: Circle | undefined;
  /** 名单里哪些创作者连到了这个人 —— 「经由谁」才是这个面板的重点。 */
  bridges: Candidate[];
  onClose: () => void;
};

/* 目标人物详情。
 *
 * 刻意**不**提供「选中这个人」的操作：客户不逐个勾选目标人物，勾选会让名单
 * 越选越少。这里只回答三件事 —— 他是谁、为什么值得影响、名单里谁能连到他。
 *
 * 末尾那句免责不是客套：关注只证明关注在观察时存在，不证明背书、曝光或转发。 */
export function PersonPanel({ target, circle, bridges, onClose }: Props) {
  return (
    <div className={styles.scrim} onClick={onClose} role="presentation">
      <aside
        className={styles.panel}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={`${target.name} 的资料`}
      >
        <button type="button" className={styles.close} onClick={onClose} aria-label="关闭">
          ✕
        </button>

        <div className={styles.head}>
          <Avatar src={target.avatarUrl} name={target.name} size={56} />
          <div>
            <div className={styles.name}>{target.name}</div>
            <div className={styles.handle}>{target.handle}</div>
            {target.role && <div className={styles.role}>{target.role}</div>}
          </div>
        </div>

        {circle && (
          <div className={styles.row}>
            <div className={styles.k}>圈层</div>
            <div className={styles.v}>
              {circle.label} · {circle.value}
            </div>
          </div>
        )}

        {target.why && (
          <div className={styles.row}>
            <div className={styles.k}>为什么值得影响</div>
            <div className={styles.v}>{target.why}</div>
          </div>
        )}

        <div className={styles.row}>
          <div className={styles.k}>名单里谁能连到他</div>
          <div className={styles.v}>
            {bridges.length === 0 ? (
              // 零结果是「当前未观察到」，不是「两者无关系」。
              <span className={styles.pending}>当前未观察到名单中有创作者与他有公开连接。</span>
            ) : (
              <div className={styles.bridges}>
                {bridges.slice(0, 8).map((b) => {
                  const edge = b.edges.find((e) => e.targetId === target.id);
                  return (
                    <div key={b.id} className={styles.bridge}>
                      <Avatar src={b.avatarUrl} name={b.name} size={28} />
                      <span className={styles.bridgeName}>{b.name}</span>
                      <span className={styles.bridgeKind}>
                        {edge?.type === "cofollow" ? "关注" : edge?.type ?? ""}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        <div className={styles.row}>
          <div className={styles.k}>核验状态</div>
          <div className={styles.v}>
            <span className={styles.pending}>
              {target.reviewed ? "已人工核验" : "关系记录来自公开关注，尚未人工核验"}
            </span>
          </div>
        </div>

        <p className={styles.disclaimer}>
          公开关注只说明该连接在观察时存在，不代表对方一定会看到、回复或转发你的内容。
        </p>
      </aside>
    </div>
  );
}
