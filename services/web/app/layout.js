import { Inter, JetBrains_Mono } from "next/font/google";
import { cookies } from "next/headers";
import { createElement as h } from "react";
import "./globals.css";
import { COOKIE, themeAttr, themeFrom } from "./theme.js";

// Self-hosted at build time by next/font: no request to Google at runtime. Inter carries everything, as in Datavine
// (slice D1): the interface and the things people read. JetBrains Mono is only for what really is code.
const sans = Inter({ subsets: ["latin"], display: "swap", variable: "--font-sans" });
const mono = JetBrains_Mono({ subsets: ["latin"], weight: ["400"], display: "swap", variable: "--font-mono" });

export const metadata = { title: "Aerugo", description: "Your company's knowledge, within your permissions." };
export const viewport = {
  themeColor: [{ media: "(prefers-color-scheme: light)", color: "#f4f6f8" }, { media: "(prefers-color-scheme: dark)", color: "#1a1c22" }],
  colorScheme: "light dark",
};

// The page arrives in the theme its reader chose (slice D2): the server reads their cookie, so there's no flash.
export default async function RootLayout({ children }) {
  const store = await cookies();
  const theme = themeFrom(`${COOKIE}=${store.get(COOKIE)?.value || ""}`);
  return h("html", { lang: "en", className: `${sans.variable} ${mono.variable}`, "data-theme": themeAttr(theme) }, h("body", null, children));
}
