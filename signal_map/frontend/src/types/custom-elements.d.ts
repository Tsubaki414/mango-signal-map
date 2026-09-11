/* The handoff's three motion web components plus the hero shader are reused
 * verbatim (public/vendor/*.js) rather than rewritten, so React needs to know
 * the tags exist. Attributes are all strings -- these are HTML custom elements,
 * not React components, so numbers must be passed as strings. */

import type React from "react";

type CustomEl<P = Record<string, unknown>> = React.DetailedHTMLProps<
  React.HTMLAttributes<HTMLElement>,
  HTMLElement
> &
  P;

declare global {
  namespace React {
    namespace JSX {
      interface IntrinsicElements {
        "gradient-waves": CustomEl<{
          horizon?: string;
          wave?: string;
          crest?: string;
          speed?: string;
          amplitude?: string;
          "wave-scale"?: string;
          "wave-ratio"?: string;
          swell?: string;
          turbulence?: string;
          tilt?: string;
          zoom?: string;
          height?: string;
          "fog-depth"?: string;
          detail?: "low" | "medium" | "high";
          brightness?: string;
          opacity?: string;
          grain?: string;
          "grain-intensity"?: string;
          parallax?: string;
          mouse?: string;
          blend?: string;
          "layer-opacity"?: string;
        }>;
        "count-up": CustomEl<{ value?: string; duration?: string }>;
        "type-line": CustomEl<{ text?: string; active?: string; speed?: string }>;
        "dot-grid": CustomEl<{
          gap?: string;
          dot?: string;
          shift?: string;
          color?: string;
        }>;
      }
    }
  }
}

export {};
