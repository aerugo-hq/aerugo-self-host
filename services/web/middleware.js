// M1 (docs/PRODUCTION_REVIEW.md): a Content Security Policy on every page. Scripts run only when they're the app's
// own: Next.js stamps this request's nonce on the scripts it renders ('strict-dynamic' lets those load the rest), so an
// injected <script> or inline handler never runs. Frames are only the video players the reader embeds (markdown.js
// builds their addresses: YouTube's privacy-enhanced domain, Vimeo, Loom); nothing may frame the app itself.
import { NextResponse } from "next/server";

const PLAYERS = "https://www.youtube-nocookie.com https://player.vimeo.com https://www.loom.com";

function policy(nonce) {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    "style-src 'self' 'unsafe-inline'",  // React's style attributes; no script can run from a style
    "img-src 'self' https:",             // the reader shows https images; an http one is only a link
    "media-src 'self' https:",           // and video files over https
    `frame-src ${PLAYERS}`,
    "worker-src 'self'",                // the map lays its graph out in a worker of its own
    "connect-src 'self'",
    "font-src 'self'",                   // next/font serves the app's fonts itself
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

export function middleware(request) {
  const nonce = btoa(crypto.randomUUID());
  const csp = policy(nonce);
  const headers = new Headers(request.headers);
  headers.set("Content-Security-Policy", csp);  // Next.js reads the nonce from here for the scripts it renders
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

// Pages only: the API proxy answers JSON, sign-in answers redirects, and static files carry no script of their own.
export const config = {
  matcher: [{ source: "/((?!api/|auth/|link/|_next/static|_next/image|brand/|favicon.ico).*)" }],
};
