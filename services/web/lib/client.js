// Browser-side helper. Every call goes through this app's /api proxy, which adds the signed-in user's
// token on the server; the browser never sees it.
async function result(r) {
  if (r.status === 401) {
    window.location.assign("/auth/login");
    throw new Error("Signing you in…");
  }
  const text = await r.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = text;
  }
  if (!r.ok) {
    const detail = data && typeof data === "object" ? data.detail : data;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

export async function kl(path, { method = "GET", body } = {}) {
  return result(await fetch(`/api/${path}`, {
    method,
    cache: "no-store",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  }));
}

// A file upload (an import): the browser sends the form as it is, boundary and all, and the proxy passes it on.
export async function klUpload(path, form) {
  return result(await fetch(`/api/${path}`, { method: "POST", cache: "no-store", body: form }));
}

export const day = (iso) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });

const UNITS = [["year", 31536e6], ["month", 2592e6], ["week", 6048e5], ["day", 864e5], ["hour", 36e5], ["minute", 6e4]];

// "3 days ago", "in 2 hours", "now"
export function ago(iso) {
  const diff = (typeof iso === "number" ? iso : Date.parse(iso)) - Date.now();
  const format = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [unit, size] of UNITS) {
    if (Math.abs(diff) >= size) return format.format(Math.round(diff / size), unit);
  }
  return "just now";
}
