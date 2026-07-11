/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Emit a self-contained server bundle (.next/standalone) for slim Docker images.
  output: "standalone",
  // Workspace packages ship raw TS/TSX (.ts source); Next must transpile them.
  transpilePackages: [
    "@oday-plus/ui",
    "@oday-plus/design-tokens",
    "@oday-plus/domain-types",
    "@oday-plus/openapi-client",
  ],
  async rewrites() {
    const apiBaseUrl = process.env.ODP_API_BASE_URL || "http://127.0.0.1:8099";
    return [
      {
        source: "/api/v1/:path*",
        destination: `${apiBaseUrl}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
