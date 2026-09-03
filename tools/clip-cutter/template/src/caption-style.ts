// Caption look & placement — tweak here, then re-render.
// TOP: vertical anchor as % of frame height (block centered on it via translateY(-50%)).
//   lower % = higher on screen. 55% ≈ just above center; 67% ≈ lower third.
import type { CSSProperties } from "react";

export const TOP = "55%";

export const captionStyle = (fontFamily: string): CSSProperties => ({
  position: "absolute",
  top: TOP,
  left: 0,
  right: 0,
  transform: "translateY(-50%)",
  paddingLeft: 70,
  paddingRight: 70,
  fontFamily,
  fontWeight: 800,
  fontSize: 62, // ≈5.7% of 1080px width; scale proportionally at other widths
  lineHeight: 1.1,
  textAlign: "center",
  color: "#ffffff",
  whiteSpace: "pre-line", // keep the SRT's own line breaks
  WebkitTextStroke: "5px #000000", // ≈8% of font size
  paintOrder: "stroke fill", // stroke sits BEHIND the letters
  textShadow: "0 2px 5px rgba(0,0,0,0.45)",
});
