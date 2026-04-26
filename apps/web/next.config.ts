import type { NextConfig } from "next";

const upstreamApi = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/$/, "");

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
