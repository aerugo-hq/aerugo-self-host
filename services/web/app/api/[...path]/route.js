// The browser calls /api/<path>; this server adds the signed-in user's token and calls the API's /v1/<path>.
// Anonymous requests stop here with 401 and never reach the API.
import { NextResponse } from "next/server";
import { API_TIMEOUT_MS, API_URL, SESSION_COOKIE, WEB_URL, accessToken } from "../../../lib/session.js";

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
  // M9: the API gets API_TIMEOUT_MS to answer, body included, and a request's body passes through as it arrives,
  // never held here whole: counted on the way and cut off past MAX_UPLOAD, whether or not its length was declared.
  const init = { method: request.method, cache: "no-store", signal: AbortSignal.timeout(API_TIMEOUT_MS),
                 headers: { Authorization: `Bearer ${token}` } };
  let tooLarge = false;
  if (request.method !== "GET" && request.body) {
    if (Number(request.headers.get("content-length") || 0) > MAX_UPLOAD) return overLimit();
    const type = request.headers.get("content-type") || "";
    let seen = 0;
    init.body = request.body.pipeThrough(new TransformStream({
      transform(chunk, controller) {
        seen += chunk.byteLength;
        if (seen > MAX_UPLOAD) {
          tooLarge = true;
          controller.error(new Error("over the upload limit"));
        } else {
          controller.enqueue(chunk);
        }
      },
    }));
    init.duplex = "half"; // a streamed request body
    // A file upload (an import) keeps its multipart boundary; anything else is the app's JSON.
    init.headers["Content-Type"] = type.startsWith("multipart/form-data") ? type : "application/json";
  }
  try {
    const r = await fetch(`${API_URL}/v1/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`, init);
    return new NextResponse(await r.text(), {
      status: r.status,
      headers: { "Content-Type": r.headers.get("content-type") || "application/json", "Cache-Control": "no-store" },
    });
  } catch (e) {
    if (tooLarge) return overLimit();
    if (e.name === "TimeoutError") {
      return NextResponse.json({ detail: "the platform didn't answer in time; try again" }, { status: 504 });
    }
    console.error(`the API couldn't be reached: ${e.name}`);
    return NextResponse.json({ detail: "the platform couldn't be reached; try again" }, { status: 502 });
  }
}

const overLimit = () => NextResponse.json({ detail: "the file is over 50 MB" }, { status: 413 });

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
