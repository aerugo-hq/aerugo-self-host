// The browser calls /api/<path>; this server adds the signed-in user's token and calls the API's /v1/<path>.
// Anonymous requests stop here with 401 and never reach the API.
import { NextResponse } from "next/server";
import { API_URL, SESSION_COOKIE, WEB_URL, accessToken } from "../../../lib/session.js";

export const dynamic = "force-dynamic";
const MAX_UPLOAD = 51 * 1024 * 1024; // the API takes exports up to 50 MB; a form adds a little

// M8: a write must come from the app's own pages. Browsers send Origin with every request that isn't a GET; when one
// doesn't (an older browser, an extension that strips it), its Referer must name the app instead (the app's
// Referrer-Policy sends it on its own requests), and a request with neither is refused. The session cookie's
// SameSite=Lax already keeps it off most cross-site writes; this closes the rest.
function fromTheApp(request) {
  const own = new URL(WEB_URL).origin;
  const origin = request.headers.get("origin");
  if (origin) return origin === own;
  try {
    return new URL(request.headers.get("referer") ?? "").origin === own;
  } catch {
    return false; // no Referer, or one that isn't an address
  }
}

async function forward(request, { params }) {
  const { path = [] } = await params;
  if (!path.length || path.some((p) => p === "" || p === "." || p === "..")) {
    return NextResponse.json({ detail: "bad path" }, { status: 400 });
  }
  if (request.method !== "GET" && !fromTheApp(request)) {
    return NextResponse.json({ detail: "cross-site request refused" }, { status: 403 });
  }
  let token;
  try {
    token = await accessToken(request.cookies.get(SESSION_COOKIE)?.value);
  } catch (e) {  // the sessions' database or the identity provider didn't answer: not a sign-out, try again shortly
    console.error(`session lookup failed: ${e.message}`);
    return NextResponse.json({ detail: "sign-in is unavailable right now" }, { status: 503, headers: { "Retry-After": "5" } });
  }
  if (!token) return NextResponse.json({ detail: "not signed in" }, { status: 401 });
  const init = { method: request.method, cache: "no-store", headers: { Authorization: `Bearer ${token}` } };
  if (request.method !== "GET") {
    const type = request.headers.get("content-type") || "";
    if (type.startsWith("multipart/form-data")) {
      // A file upload (an import): passed through byte for byte with its boundary, after a size check so an
      // oversized file is refused before it's buffered.
      if (Number(request.headers.get("content-length") || 0) > MAX_UPLOAD) {
        return NextResponse.json({ detail: "the file is over 50 MB" }, { status: 413 });
      }
      init.body = await request.arrayBuffer();
      init.headers["Content-Type"] = type;
    } else {
      init.body = await request.text();
      init.headers["Content-Type"] = "application/json";
    }
  }
  const r = await fetch(`${API_URL}/v1/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`, init);
  return new NextResponse(await r.text(), {
    status: r.status,
    headers: { "Content-Type": r.headers.get("content-type") || "application/json", "Cache-Control": "no-store" },
  });
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
