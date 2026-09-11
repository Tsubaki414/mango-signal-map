"use client";

import { useEffect, useState } from "react";

/* 首屏波浪着色器，**客户端挂载后才渲染**。
 *
 * gradient-waves.js 在 connectedCallback 里改写自己的 style 并插入一个
 * <canvas>，所以它的客户端 DOM 永远不等于 SSR 出来的 HTML。
 * suppressHydrationWarning 只覆盖元素自身的属性，盖不住被注入的子节点，
 * React 仍会判定 hydration 失败并重建整棵子树 —— 着色器被销毁重建，首屏闪一下。
 * 干脆不让它参与 SSR。
 *
 * 定位写成内联 style 也是必需的：组件内部执行
 *     this.style.position = this.style.position || 'relative'
 * 只读内联值，CSS class 里的 absolute 满足不了它，波浪层会掉进文档流把下面的
 * 内容整体顶开。 */
export function GradientWaves() {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return null;

  return (
    <gradient-waves
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        top: 0,
        bottom: 0,
        width: "100%",
        height: "100%",
      }}
      horizon="#14302F"
      wave="#24605C"
      crest="#7FD8CE"
      speed="0.22"
      amplitude="2.2"
      tilt="1.18"
      zoom="1.05"
      height="6"
      fog-depth="26"
      opacity="0.9"
      brightness="1.15"
      grain-intensity="0.04"
      parallax="0.6"
      detail="medium"
      blend="screen"
      layer-opacity="0.72"
    />
  );
}
