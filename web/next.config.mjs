/** @type {import('next').NextConfig} */
const nextConfig = {
  // Proxy /api/* to FastAPI so the browser talks to one origin (no CORS).
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${process.env.API_URL ?? "http://localhost:8100"}/api/:path*` }];
  },
};

export default nextConfig;
