"use client";

import { STAGES, stageOf, type View } from "@/lib/stages";
import styles from "./TopBar.module.css";

const BONE = "#F3F1EA";
const TEAL = "#5FC4BB";

type Props = {
  view: View;
  /** Furthest stage reached. Monotonic -- never decreases when navigating back. */
  maxStage?: number;
  /** Transient autosave hint; blank most of the time. */
  saveNote?: string;
  hasProject?: boolean;
  onNavigate?: (view: View) => void;
};

export function TopBar({
  view,
  maxStage = 0,
  saveNote = "",
  hasProject = false,
  onNavigate,
}: Props) {
  const cur = stageOf(view);
  const reached = Math.max(maxStage, cur);

  return (
    <header className={styles.bar}>
      <button
        type="button"
        className={styles.brand}
        onClick={() => onNavigate?.("home")}
        aria-label="回到起点"
      >
        {/* Handoff note: replace with the official SVG in production -- these
         * PNGs were cut out of a client-supplied image. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/assets/mango-mark.png" alt="Mango Labs" className={styles.mark} />
        <span className={styles.wordmark}>MangoLabs</span>
      </button>

      <span className={styles.divider} />
      <span className={styles.product}>SIGNAL MAP</span>

      <nav className={styles.steps}>
        {STAGES.map((s, i) => {
          const isCurrent = i === cur;
          const isDone = i < reached;
          // Stage 01 lands on the brief once one exists, and on home before that.
          const target: View = i === 0 && !hasProject ? "home" : s.view;
          return (
            <button
              key={s.n}
              type="button"
              className={styles.step}
              disabled={!isDone && !isCurrent}
              onClick={() => onNavigate?.(target)}
              aria-current={isCurrent ? "step" : undefined}
              style={{
                color: isCurrent ? BONE : isDone ? "rgba(243,241,234,.7)" : "rgba(243,241,234,.4)",
              }}
            >
              <span
                style={{ color: isCurrent || isDone ? TEAL : "rgba(243,241,234,.3)" }}
                aria-hidden={isDone ? true : undefined}
              >
                {isDone ? "✓" : s.n}
              </span>
              {s.label}
            </button>
          );
        })}
      </nav>

      <span className={styles.save} aria-live="polite">
        {saveNote}
      </span>
    </header>
  );
}
