// GitHub sends the browser back here after someone signs in to prove a login. The code goes straight from
// this server to the API, which exchanges it; no browser JavaScript ever sees it, or GitHub's token.
import { NextResponse } from "next/server";
import { API_URL, SESSION_COOKIE, WEB_URL, accessToken } from "../../../../lib/session.js";

export const dynamic = "force-dynamic";

export async function GET(request) {
  const token = await accessToken(request.cookies.get(SESSION_COOKIE)?.value);
  if (!token) return NextResponse.redirect(`${WEB_URL}/auth/login`);
  const q = request.nextUrl.searchParams;
  let outcome;
  try {
    const r = await fetch(`${API_URL}/v1/identities/github/complete`, {
      method: "POST", cache: "no-store",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ code: q.get("code") || "", state: q.get("state") || "" }),
    });
    const body = await r.json().catch(() => ({}));
    outcome = r.ok ? `linked:${body.linked}` : `error:${typeof body.detail === "string" ? body.detail : "linking didn't finish; try again"}`;
  } catch {
    outcome = "error:the platform couldn't be reached; try again";
  }
  return NextResponse.redirect(`${WEB_URL}/#/accounts/${encodeURIComponent(outcome)}`);
}
