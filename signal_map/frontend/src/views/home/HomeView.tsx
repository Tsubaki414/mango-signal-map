"use client";

import { useState } from "react";
import * as C from "./copy";
import styles from "./HomeView.module.css";

type Props = {
  /** Called with a validated Brief code. Stage 2 (`understand`) is not built
   *  yet, so page.tsx leaves this undefined and a correct code is a no-op. The
   *  wrong-code path is fully wired and observable today. */
  onOpenBrief?: (token: string) => void;
  onStartNew?: () => void;
  showDemoNotice?: boolean;
};

/* Accepts a bare code, or a pasted link carrying `?p=` / `/p/`, because the
 * consultant sends a link and the client pastes whatever they were sent. */
function extractToken(raw: string): string {
  return (raw.match(/(?:p=|\/p\/)([\w-]+)/) || [])[1] || raw.trim();
}

/* Phase 1 has no backend, so `demo` is the only code that resolves. Real codes
 * are issued and validated server-side -- see the handoff's 交付后仍需完成 #1. */
function resolves(token: string): boolean {
  return token === "demo";
}

export function HomeView({ onOpenBrief, onStartNew, showDemoNotice = true }: Props) {
  const [tokenEntryOpen, setTokenEntryOpen] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  const [tokenError, setTokenError] = useState(false);

  function submit() {
    const token = extractToken(tokenInput);
    if (!resolves(token)) {
      setTokenError(true); // stays on the page by design -- never navigates on a bad code
      return;
    }
    setTokenError(false);
    onOpenBrief?.(token);
  }

  return (
    <>
      <section className={styles.hero} data-screen-label="首页">
        {/* Three graphic layers, back to front: shader, ring, horizon bands. */}
        {/* Inline positioning is required, not a style preference -- see the
         * note at the top of HomeView.module.css. */}
        <gradient-waves
          style={{ position: "absolute", left: 0, right: 0, top: 0, bottom: 0, width: "100%", height: "100%" }}
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

        <svg
          viewBox="0 0 1440 900"
          preserveAspectRatio="xMidYMax slice"
          className={styles.ring}
          aria-hidden="true"
        >
          <defs>
            <filter id="msm-glow" x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur stdDeviation="6" />
            </filter>
          </defs>
          {/* Blurred underlay + crisp stroke that draws itself in over 2.4s. */}
          <circle
            cx="1040"
            cy="560"
            r="300"
            fill="none"
            stroke="#F3F1EA"
            strokeWidth="6"
            opacity=".4"
            filter="url(#msm-glow)"
          />
          <circle
            cx="1040"
            cy="560"
            r="300"
            fill="none"
            stroke="#F3F1EA"
            strokeWidth="1.6"
            strokeDasharray="2400"
            className={styles.ringDraw}
          />
        </svg>

        <div className={styles.horizon}>
          <svg viewBox="0 0 1000 120" preserveAspectRatio="none" className={styles.bands} aria-hidden="true">
            <g stroke="#F3F1EA" strokeWidth="1.5" vectorEffect="non-scaling-stroke">
              <path d="M0 26H420l14 6h566" opacity=".45" />
              <path d="M180 52H700l14 6h286" opacity=".3" />
              <path d="M60 78H380l14 6h546" opacity=".18" />
              <path d="M300 104H620l14 6h366" opacity=".1" />
            </g>
          </svg>
        </div>

        <div className={styles.content}>
          <div className={styles.eyebrow}>{C.EYEBROW}</div>
          <h1 className={styles.title}>
            <em>{C.TITLE_EM}</em>
            <br />
            {C.TITLE_REST}
          </h1>
          <p className={styles.lede}>{C.LEDE}</p>

          <div className={styles.ctas}>
            <button
              type="button"
              className={styles.ctaPrimary}
              onClick={() => setTokenEntryOpen((v) => !v)}
              aria-expanded={tokenEntryOpen}
            >
              {C.CTA_BRIEF} <span aria-hidden="true">→</span>
            </button>
            <button type="button" className={styles.ctaSecondary} onClick={() => onStartNew?.()}>
              {C.CTA_NEW}
            </button>
          </div>

          {tokenEntryOpen && (
            <div className={styles.tokenEntry}>
              <div className={styles.tokenRow}>
                <input
                  className={styles.tokenInput}
                  value={tokenInput}
                  onChange={(e) => {
                    setTokenInput(e.target.value);
                    setTokenError(false);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") submit();
                  }}
                  placeholder={C.TOKEN_PLACEHOLDER}
                  aria-label="Brief 码"
                  aria-invalid={tokenError || undefined}
                  autoFocus
                />
                <button type="button" className={styles.tokenSubmit} onClick={submit}>
                  {C.TOKEN_SUBMIT}
                </button>
              </div>
              {tokenError && (
                <span className={styles.tokenError} role="alert">
                  {C.TOKEN_ERROR}
                </span>
              )}
            </div>
          )}
        </div>

        <div className={styles.slashFlow}>
          <div>{C.SLASH_FLOW}</div>
        </div>
      </section>

      <section className={styles.flow}>
        {C.FLOW.map((s) => (
          <div key={s.n} className={styles.flowCell}>
            <div className={styles.flowNum}>{s.n}</div>
            <h3 className={styles.flowTitle}>{s.title}</h3>
            <p className={styles.flowBody}>{s.body}</p>
          </div>
        ))}
      </section>

      <section className={styles.outcome}>
        <div>
          <div className={styles.outcomeEyebrow}>{C.OUTCOME_EYEBROW}</div>
          <h2 className={styles.outcomeTitle}>{C.OUTCOME_TITLE}</h2>
          <p className={styles.outcomeBody}>{C.OUTCOME_BODY}</p>
        </div>
        <div className={styles.outcomeList}>
          {C.OUTCOME_ROWS.map((r) => (
            <div key={r.n} className={styles.outcomeRow}>
              <span className={styles.outcomeNum}>{r.n}</span>
              <div>
                <div className={styles.outcomeRowTitle}>{r.title}</div>
                <div className={styles.outcomeRowBody}>{r.body}</div>
              </div>
            </div>
          ))}
        </div>
      </section>

      <footer className={styles.footer}>
        <span>{C.COPYRIGHT}</span>
        {showDemoNotice && <span>{C.DEMO_NOTICE}</span>}
      </footer>
    </>
  );
}
