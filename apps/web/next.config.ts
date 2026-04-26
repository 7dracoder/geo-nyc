import type { NextConfig } from "next";
import { safeUpstreamOrigin } from "./upstreamUrl";

const upstreamApi = safeUpstreamOrigin(process.env.NEXT_PUBLIC_API_BASE_URL);

const nextConfig: NextConfig = {
  turbopack: {
    root: __dirname,
  },
  async rewrites() {
    if (!upstreamApi) return [];
    return [
      {
        source: "/geo-nyc-proxy/:path*",
        destination: `${upstreamApi}/:path*`,
      },
    ];
  },
};

export default nextConfig;
