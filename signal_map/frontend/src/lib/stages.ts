/* The four-stage spine shown in the top bar.
 *
 * Field names follow the handoff's store (README "State Management") so that
 * the eventual backend contract does not need a translation table:
 *   view      -- which screen is showing
 *   maxStage  -- furthest stage reached, monotonic; drives the ✓ marks
 */

export type View =
  | "home"
  | "understand"
  | "start"
  | "configure"
  | "criteria"
  | "reveal"
  | "lineup"
  | "overview";

export type Stage = {
  n: string;
  label: string;
  view: View;
};

export const STAGES: readonly Stage[] = [
  { n: "01", label: "起点", view: "understand" },
  { n: "02", label: "偏好", view: "configure" },
  { n: "03", label: "名单", view: "lineup" },
  { n: "04", label: "阵容", view: "overview" },
] as const;

/* Which stage a view belongs to.
 *
 * `reveal` sits in stage 1 (偏好), not stage 2, even though it renders right
 * before the lineup: it is the generation moment, and the client has not
 * reached 名单 until they have one. Taken from the prototype's `cur` mapping --
 * worth stating because the obvious guess is wrong. */
const STAGE_OF: Record<View, number> = {
  home: 0,
  understand: 0,
  start: 0,
  configure: 1,
  criteria: 1,
  reveal: 1,
  lineup: 2,
  overview: 3,
};

export function stageOf(view: View): number {
  return STAGE_OF[view] ?? 0;
}
