/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Hide the Next.js dev-mode overlay button (the floating "N" indicator at the
  // bottom-left). Dev-only chrome — never rendered in a production build.
  devIndicators: false,
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
