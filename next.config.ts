import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Allow the Next.js dev server to proxy API requests to the local FastAPI backend
  async rewrites() {
    return [
      {
        source      : "/api/:path*",
        destination : "http://localhost:8000/api/:path*",
      },
    ];
  },

  // Opt-out of type-checking during builds (types checked separately)
  typescript: {
    ignoreBuildErrors: false,
  },

  // Minimal bundle for faster cold starts
  experimental: {},
};

export default nextConfig;
