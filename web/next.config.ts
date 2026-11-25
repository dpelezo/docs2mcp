import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // On Vercel, /api/call is the Python function in api/call.py. Locally, point
  // LIVE_API_URL at it running under uvicorn (see README.md).
  async rewrites() {
    const live = process.env.LIVE_API_URL;
    return live ? [{ source: "/api/call", destination: `${live}/api/call` }] : [];
  },
};

export default nextConfig;
