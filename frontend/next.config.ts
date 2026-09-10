import type { NextConfig } from "next";

const apiBase = (process.env.API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  turbopack: { root: process.cwd() },
  outputFileTracingRoot: process.cwd(),
  async rewrites() {
    return [{ source: "/backend/:path*", destination: `${apiBase}/:path*` }];
  },
};

export default nextConfig;
