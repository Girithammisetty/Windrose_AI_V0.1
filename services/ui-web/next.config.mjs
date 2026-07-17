/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Per-route code splitting is the App Router default; we additionally keep the
  // heavy libraries (echarts, editors, diff) out of shared chunks via dynamic import.
  experimental: {
    optimizePackageImports: ["lucide-react", "@tanstack/react-table"],
  },
  eslint: {
    // Lint is run explicitly in CI via `pnpm lint`; don't fail production builds on it.
    ignoreDuringBuilds: true,
  },
};

export default nextConfig;
