import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

/**
 * Free ngrok tunnels may return an HTML interstitial unless this header is set
 * (see https://ngrok.com/docs/guides/device-gateway/client-setup/). Next.js
 * rewrites proxy `/geo-nyc-proxy/*` to the upstream API; without the header,
 * server-side fetches can get HTML instead of JSON.
 */
export function proxy(request: NextRequest) {
  if (!request.nextUrl.pathname.startsWith("/geo-nyc-proxy")) {
    return NextResponse.next();
  }
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("ngrok-skip-browser-warning", "1");
  return NextResponse.next({ request: { headers: requestHeaders } });
}

export const config = {
  matcher: "/geo-nyc-proxy/:path*",
};
