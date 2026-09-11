# Signal Map — client surface

Reproduction of the Claude Design handoff in
[`../design_handoff/`](../design_handoff/). The handoff is marked 高保真: its
colours, sizes, weights, spacing and motion durations are final values, so this
app matches them rather than reinterpreting them.

**Phase 1 only.** Built: engineering skeleton, full design tokens, 56px top bar,
`home`. The other seven views and three overlays are not started.

```bash
npm install
npm run dev          # http://localhost:3100
npm run typecheck
npm run shot         # render comparison PNGs into shots/ (dev server must be up)
```

## Layout

```
src/
  app/layout.tsx        fonts + the two vendor scripts, loaded beforeInteractive
  app/globals.css       base + the keyframes the product is allowed to use
  app/page.tsx          view switch (one case today)
  styles/tokens.css     every design token, including ones home does not use
  components/TopBar     56px bar, four-stage progress
  views/home/           HomeView + its CSS module + copy.ts
  lib/stages.ts         view -> stage mapping, shared with the top bar
  types/                JSX tags for the vendor custom elements
public/vendor/          motion.js + gradient-waves.js, copied verbatim
```

## Conventions

- **No CSS framework.** Deliberate, per the handoff: Tailwind's defaults bring
  rounded corners and shadows, and this design has neither. CSS Modules only.
- **`border-radius: 0`** everywhere. Only a round avatar is exempt.
- **No shadows.** A selected state is `inset 0 0 0 1px <accent>`.
- **1px "tables"** are `display:grid; gap:1px; background:<hairline>` with
  children painting their own fill — not borders, which double up at seams.
- **Copy lives in `copy.ts` per view**, because
  `kol_database/docs/product-language.md` makes wording binding and inline
  strings drift.
- **Store field names follow the handoff's** (`view`, `maxStage`, `prefs`,
  `plans`, …) so the eventual backend contract needs no translation table.

## Verifying against the reference

`preview.html` is a **temporary** static harness. It links the real stylesheets
(CSS Module files use literal class names, so they apply unscoped) and mirrors
`HomeView.tsx`'s DOM, so it verifies the shipped CSS without needing a build.
It found both bugs in this phase. **Delete it once `npm run dev` works** — a
hand-copied DOM will drift from the component.

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless --enable-unsafe-swiftshader --force-prefers-reduced-motion \
  --virtual-time-budget=8000 --window-size=924,540 \
  --screenshot=shots/preview.png "file://$PWD/preview.html"
```

`--force-prefers-reduced-motion` is not optional: under `--virtual-time-budget`
CSS animations freeze at t=0, and every entrance animation uses `fill: both`,
so the whole hero renders at `opacity: 0`. Reduced motion sends them straight to
their end state. `924x540` is the reference screenshots' native size.

Current match on `01-home-hero.png`: whole frame mean abs diff **2.71/255**,
0.18% of pixels off by >32. Eyebrow, title and CTA text rows land on identical
scanlines. The residual is the shader, which is time-varying and cannot match
frame-for-frame.

## Handoff discrepancies found

Where `README.md` and the prototype disagree, the **prototype** was followed —
it is what the screenshots were taken from. Worth correcting in the handoff:

| Handoff README says | Prototype / reality |
|---|---|
| screenshots are "1440 宽实拍" | natively **924×540** |
| eyebrow `01 /// KOL 阵容配置器` | `01 /// KOL 阵容配置` (screenshot agrees) |
| ring at `cx 980 cy 640` | `cx 1040 cy 560` (screenshot agrees) |
| home has a 四格数字网格 (有报价创作者 / 平台 / Root 库 / 市场) | absent — `meta` is computed in state and never rendered |
| 五条阶梯信号带 | four `<path>`s |
| waves use CSS `mix-blend-mode:screen` + `opacity .72` | these are the component's `blend` / `layer-opacity` **attributes**; setting them in CSS does nothing (see below) |

### Two traps in `gradient-waves.js`

Both cost real debugging time here:

1. It writes `mix-blend-mode`, `opacity`, `width`, `height` and
   `pointer-events` as **inline styles on itself**, so any CSS class for those
   is dead. Pass `blend` / `layer-opacity` as attributes instead.
2. `this.style.position = this.style.position || 'relative'` reads the *inline*
   value only. A class saying `position:absolute` does **not** satisfy it, so
   the layer silently falls back to `relative`, joins the flow, and pushes the
   hero copy ~460px down. The element must be positioned by an inline `style`
   attribute — which is exactly what the prototype does.

## Known gaps in this phase

- `demo` is the only Brief code that resolves, and it currently goes nowhere —
  `understand` is not built. A wrong code shows the gold hint and stays put,
  which is the full designed behaviour for that branch.
- Below 768px is unimplemented, matching the handoff (its own AUDIT D3).
- Not wired to the FastAPI backend. Data will arrive through an adapter layer
  mirroring `design_handoff/data/adapter.js`.
- **`npm install` has not completed in this environment** — the registry keeps
  returning `ETIMEDOUT` partway through. The React/Next code is therefore
  written but not yet compiled or run; only the CSS is verified, via
  `preview.html`. Re-run `npm install` on a working network, then
  `npm run typecheck` and `npm run dev` before trusting the components.
