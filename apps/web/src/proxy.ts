import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

/**
 * Free ngrok tunnels return an HTML interstitial when the User-Agent looks
 * like a browser, unless `ngrok-skip-browser-warning` is set
 * (see https://ngrok.com/docs/guides/device-gateway/client-setup/).
 *
 * Next.js rewrites turn `/geo-nyc-proxy/*` into a server-side fetch to the
 * upstream API. We:
 *   1. Set the documented bypass header.
 *   2. Replace the forwarded User-Agent with a non-browser value so any other
 *      gateway abuse-detection step also lets us through.
 */
export function proxy(request: NextRequest) {
  if (!request.nextUrl.pathname.startsWith("/geo-nyc-proxy")) {
    return NextResponse.next();
  }
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("ngrok-skip-browser-warning", "1");
  requestHeaders.set("user-agent", "geo-nyc-vercel-proxy/1.0 (+https://geo-nyc.vercel.app)");
  return NextResponse.next({ request: { headers: requestHeaders } });
}

export const config = {
  matcher: "/geo-nyc-proxy/:path*",
};
