// L2: the web app's lint, run in CI (`make lint-web`) rather than inside `next build`, which skips it
// (next.config.mjs, ignoreDuringBuilds). Next's own rules for React, hooks, and Core Web Vitals.
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({ baseDirectory: import.meta.dirname });

const config = [
  { ignores: [".next/**", "node_modules/**", "public/**"] },
  ...compat.extends("next/core-web-vitals"),
];

export default config;
