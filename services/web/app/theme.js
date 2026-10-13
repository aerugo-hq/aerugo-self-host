// The theme (slice D2): light, dark, or the system's. The choice is a cookie, so the server renders the page in it from
// the first paint, with no flash and no inline script. "System" puts no attribute on <html>, and the stylesheet's
// media query follows the operating system. Light is the default (DESIGN.md: light-first).
export const THEMES = [["light", "Light"], ["dark", "Dark"], ["system", "System"]];
export const COOKIE = "aerugo-theme";
const VALUES = new Set(THEMES.map(([value]) => value));

// The choice in a Cookie header or document.cookie; anything else is the default.
export function themeFrom(cookie = "") {
  const m = String(cookie).match(/(?:^|;\s*)aerugo-theme=([a-z]+)/);
  return m && VALUES.has(m[1]) ? m[1] : "light";
}

// What <html> carries: "light" or "dark", or nothing, so the system's preference decides.
export const themeAttr = (theme) => (theme === "dark" ? "dark" : theme === "system" ? undefined : "light");

// Switches now and for next time; the map listens for "aerugo:theme" to repaint in the new colors.
export function applyTheme(theme, doc = document, win = window) {
  if (!VALUES.has(theme)) return;
  doc.cookie = `${COOKIE}=${theme}; path=/; max-age=31536000; samesite=lax`;
  const attr = themeAttr(theme);
  if (attr) doc.documentElement.dataset.theme = attr;
  else delete doc.documentElement.dataset.theme;
  win.dispatchEvent(new Event("aerugo:theme"));
}
