"use client";

import { useState } from "react";

/* 头像。加载失败退回首字母方块。
 *
 * 兜底不是可选的：头像走 unavatar.io 这个第三方代理，会间歇性失败（v3 README
 * 第十节第 3 条自己也承认）。没有兜底的话，卡片上就是一个个空洞。
 *
 * 生产环境应改成自托管 —— 第三方代理会知道 Mango 在研究哪些账号。 */
export function Avatar({
  src,
  name,
  size = 44,
  round = true,
}: {
  src?: string | null;
  name?: string | null;
  size?: number;
  round?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const initial = (name || "?").trim().charAt(0).toUpperCase();
  const base: React.CSSProperties = {
    width: size,
    height: size,
    flex: "none",
    // 圆角 0 是全站规则，头像是唯一例外。
    borderRadius: round ? "50%" : 0,
    objectFit: "cover",
    background: "#152F31",
    boxShadow: "inset 0 0 0 1px rgba(243,241,234,.16)",
  };

  if (!src || failed) {
    return (
      <span
        aria-hidden="true"
        style={{
          ...base,
          display: "grid",
          placeItems: "center",
          fontFamily: "var(--msm-mono)",
          fontSize: Math.max(11, size * 0.36),
          color: "rgba(243,241,234,.65)",
        }}
      >
        {initial}
      </span>
    );
  }
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt="" style={base} onError={() => setFailed(true)} loading="lazy" />;
}
