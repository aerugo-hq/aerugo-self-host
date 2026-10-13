// /auth/login, /auth/callback, /auth/logout: OIDC authorization code with PKCE, exchanged by this server.
import { NextResponse } from "next/server";
import { COOKIE_OPTIONS, SESSION_COOKIE, STATE_COOKIE, WEB_URL, endSession, finishLogin, startLogin } from "../../../lib/session.js";

export const dynamic = "force-dynamic";

export async function GET(request, { params }) {
  const { action } = await params;
  if (action === "login") {
    const { url, state } = startLogin();
    const res = NextResponse.redirect(url);
    res.cookies.set(STATE_COOKIE, state, { ...COOKIE_OPTIONS, maxAge: 600 }); // binds the callback to this browser
    return res;
  }
  if (action === "callback") {
    const q = request.nextUrl.searchParams;
    try {
      const sid = await finishLogin({ state: q.get("state") || "", code: q.get("code") || "",
                                      cookieState: request.cookies.get(STATE_COOKIE)?.value });
      const res = NextResponse.redirect(`${WEB_URL}/`);
      res.cookies.set(SESSION_COOKIE, sid, COOKIE_OPTIONS);
      res.cookies.set(STATE_COOKIE, "", { ...COOKIE_OPTIONS, maxAge: 0 });
      return res;
    } catch (e) {
      return new NextResponse(`Sign-in failed: ${e.message}. Start again at /auth/login.`,
                              { status: 400, headers: { "Content-Type": "text/plain; charset=utf-8" } });
    }
  }
  if (action === "logout") {
    const res = NextResponse.redirect(endSession(request.cookies.get(SESSION_COOKIE)?.value));
    res.cookies.set(SESSION_COOKIE, "", { ...COOKIE_OPTIONS, maxAge: 0 });
    return res;
  }
  return new NextResponse("Not found", { status: 404 });
}
