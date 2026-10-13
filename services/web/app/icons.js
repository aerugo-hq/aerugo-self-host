// A small inline icon set: hand-drawn 24px paths at a 1.5 stroke, colored by currentColor. Not a library.
import { createElement as h } from "react";

const PATHS = {
  home: ["M4 11.5 12 4.5l8 7", "M6 10v10h12V10", "M10 20v-5h4v5"],
  code: ["m8 8-4 4 4 4", "m16 8 4 4-4 4", "m14 4-4 16"],
  map: ["M12 8.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z", "M5.5 20.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z",
        "M18.5 20.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z", "m10.6 8 -3.7 7.6", "m13.4 8 3.7 7.6", "M8 18h8"],
  search: ["M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Z", "m20 20-3.6-3.6"],
  draft: ["M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z", "M14 3v5h5", "M9 13h6", "M9 17h4"],
  docs: ["M9 6V5a2 2 0 0 1 2-2h5l4 4v9a2 2 0 0 1-2 2h-1", "M16 3v4h4", "M4 10a2 2 0 0 1 2-2h5l4 4v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-9Z", "M11 8v4h4"],
  sliders: ["M4 6h10", "M18 6h2", "M16 4v4", "M4 12h4", "M12 12h8", "M10 10v4", "M4 18h12", "M20 18h0", "M18 16v4"],
  bell: ["M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9", "M10.3 21a1.94 1.94 0 0 0 3.4 0"],
  user: ["M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z", "M4 21a8 8 0 0 1 16 0"],
  link: ["M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7", "M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"],
  play: ["M8 5.5v13a1 1 0 0 0 1.5.86l10.4-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5Z"],
  pause: ["M8 5v14", "M16 5v14"],
  arrowRight: ["M5 12h14", "m13 6 6 6-6 6"],
  external: ["M14 4h6v6", "M10 14 20 4", "M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"],
  logout: ["M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4", "m16 17 5-5-5-5", "M21 12H9"],
  x: ["M18 6 6 18", "m6 6 12 12"],
  plus: ["M12 5v14", "M5 12h14"],
  minus: ["M5 12h14"],
  fit: ["M4 9V5a1 1 0 0 1 1-1h4", "M15 4h4a1 1 0 0 1 1 1v4", "M20 15v4a1 1 0 0 1-1 1h-4", "M9 20H5a1 1 0 0 1-1-1v-4"],
  shield: ["M12 3 5 6v6c0 4.5 3 8 7 9 4-1 7-4.5 7-9V6l-7-3Z"],
  menu: ["M4 7h16", "M4 12h16", "M4 17h16"],
  check: ["m5 12.5 4.5 4.5L19 7.5"],
  chevron: ["m9 6 6 6-6 6"],
  ask: ["M20 11.5a7.5 7.5 0 0 1-10.9 6.7L4 19.5l1.3-4.6A7.5 7.5 0 1 1 20 11.5Z", "M9.5 9.5a2.5 2.5 0 1 1 3.4 2.3c-.6.3-.9.8-.9 1.4", "M12 15.5h0"],
  keyboard: ["M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z", "M7 9h1", "M11 9h1", "M15 9h1", "M7 12h1", "M11 12h1", "M15 12h1", "M8 15.5h8"],
  refresh: ["M20 11a8 8 0 0 0-14.8-3.5", "M4 4v4h4", "M4 13a8 8 0 0 0 14.8 3.5", "M20 20v-4h-4"],
};

export function Icon({ name, size = 16 }) {
  return h("svg", { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
                    strokeWidth: 1.5, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true, focusable: "false" },
    (PATHS[name] || []).map((d, i) => h("path", { key: i, d })));
}
