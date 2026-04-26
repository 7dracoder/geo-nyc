/**
 * Normalize and validate the upstream API origin used for rewrites (`next.config`)
 * and client proxy paths (`api.ts`). Rejects values that would produce invalid
 * rewrite destinations (e.g. empty hostname → Vercel `DNS_HOSTNAME_EMPTY`).
 */
export function safeUpstreamOrigin(raw: string | undefined): string {
  let s = (raw ?? "").trim();
  if (!s) return "";
  if (!/^https?:\/\//i.test(s)) {
    s = `https://${s}`;
  }
  s = s.replace(/\/$/, "");
  try {
    const u = new URL(s);
    if (u.protocol !== "http:" && u.protocol !== "https:") return "";
    if (!u.hostname || u.hostname.length === 0) return "";
    return s;
  } catch {
    return "";
  }
}
