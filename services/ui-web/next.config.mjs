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
  // Baseline security headers (BRD 58 SEC-3). Applied here (not middleware.ts)
  // so they cover EVERY response -- middleware.ts's matcher explicitly skips
  // /login, /api, and static assets.
  async headers() {
    const common = [
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
    ];
    return [
      {
        // Everything except /embed/*: the main interactive app is never meant
        // to be framed anywhere, so this is a static, maximally strict policy.
        // /embed/* is excluded because middleware.ts sets its OWN per-tenant
        // CSP frame-ancestors there -- a second global CSP on the same
        // response would combine as an intersection (multiple CSP headers
        // are ANDed per directive) and silently block legitimate embedding.
        source: "/((?!embed).*)",
        headers: [
          ...common,
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
        ],
      },
      {
        source: "/embed/:path*",
        headers: common,
      },
    ];
  },
};

export default nextConfig;
