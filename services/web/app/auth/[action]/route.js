// /auth/login, /auth/callback, /auth/logout: OIDC authorization code with PKCE, exchanged by this server.
import { NextResponse } from "next/server";
import { COOKIE_OPTIONS, MAX_SECONDS, SESSION_COOKIE, STATE_COOKIE, SignInError, WEB_URL, endSession, finishLogin, startLogin } from "../../../lib/session.js";

export const dynamic = "force-dynamic";

const unavailable = () => new NextResponse("Sign-in is unavailable right now. Try again in a moment.",
                                           { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8", "Retry-After": "5" } });

export async function GET(request, { params }) {
  const { action } = await params;
  if (action === "login") {
    let started;
    try {
      started = await startLogin();
    } catch (e) {
      console.error(`sign-in couldn't start: ${e.message}`);
      return unavailable();
    }
    const { url, state } = started;
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
      res.cookies.set(SESSION_COOKIE, sid, { ...COOKIE_OPTIONS, maxAge: MAX_SECONDS }); // no longer than the session
      res.cookies.set(STATE_COOKIE, "", { ...COOKIE_OPTIONS, maxAge: 0 });
      return res;
    } catch (e) {
      if (!(e instanceof SignInError)) {  // the database or the identity provider failed: say so, never its own words
        console.error(`sign-in couldn't finish: ${e.message}`);
        return unavailable();
      }
      return new NextResponse(`Sign-in failed: ${e.message}. Start again at /auth/login.`,
                              { status: 400, headers: { "Content-Type": "text/plain; charset=utf-8" } });
    }
  }
  if (action === "logout") {
    let where;
    try {
      where = await endSession(request.cookies.get(SESSION_COOKIE)?.value);
    } catch (e) {
      console.error(`sign-out couldn't remove the session: ${e.message}`);
      return unavailable(); // the cookie stays, so the person can try again rather than think they've signed out
    }
    const res = NextResponse.redirect(where);
    res.cookies.set(SESSION_COOKIE, "", { ...COOKIE_OPTIONS, maxAge: 0 });
    return res;
  }
  return new NextResponse("Not found", { status: 404 });
}
