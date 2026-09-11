# vendor/

`motion.js` and `gradient-waves.js` are copied **verbatim** from
`signal_map/design_handoff/`. Do not edit them here and do not rewrite them as
React components without carrying over all four behaviours they already
implement:

- `prefers-reduced-motion` degradation
- `IntersectionObserver` — stop rendering frames off-screen
- `visibilitychange` — stop rendering frames in a background tab
- `ResizeObserver` + `disconnectedCallback` cleanup

They are zero-dependency self-registering custom elements, so they work in
React without a wrapper. `gradient-waves.js` is native WebGL2 — it does **not**
need `ogl` despite being a port of the ogl version.

To resync after a handoff update:

```bash
cp ../../design_handoff/{motion.js,gradient-waves.js} .
```

Tag types for JSX live in `src/types/custom-elements.d.ts`. Attributes are HTML
attributes, so every value is a string (`speed="0.22"`, not `speed={0.22}`).
