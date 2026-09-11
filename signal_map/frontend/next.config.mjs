/** @type {import('next').NextConfig} */
const API = process.env.SIGNAL_MAP_API ?? "http://127.0.0.1:8100";

const nextConfig = {
  reactStrictMode: true,
  // The handoff ships PNG marks; next/image would add layout machinery around a
  // 26px logo for nothing, so plain <img> is used and this stays off.
  images: { unoptimized: true },

  // Same-origin proxy to the FastAPI app. Calling it cross-origin from the
  // browser would mean CORS plus a public API host; proxying keeps the backend
  // address out of the client bundle and off the network tab.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};

export default nextConfig;
