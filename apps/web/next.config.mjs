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
  async redirects() {
    return [
      { source: "/", destination: "/operator", permanent: true },
      {
        source: "/expansion",
        destination: "/operator?ws=network",
        permanent: true,
      },
      {
        source: "/map",
        destination: "/operator?ws=network",
        permanent: true,
      },
      {
        source: "/w/expansion/:path*",
        destination: "/operator?ws=network",
        permanent: true,
      },
      {
        source: "/pricing",
        destination: "/operator?ws=growth",
        permanent: true,
      },
      {
        source: "/adlift",
        destination: "/operator?ws=growth",
        permanent: true,
      },
      {
        source: "/audit",
        destination: "/operator?ws=govern",
        permanent: true,
      },
      {
        source: "/admin/:path*",
        destination: "/operator?ws=govern",
        permanent: true,
      },
      { source: "/avm", destination: "/operator", permanent: true },
      { source: "/interventions", destination: "/operator", permanent: true },
      { source: "/learning", destination: "/operator", permanent: true },
      { source: "/netplan", destination: "/operator", permanent: true },
      { source: "/notifications", destination: "/operator", permanent: true },
      { source: "/operations", destination: "/operator", permanent: true },
      { source: "/search", destination: "/operator", permanent: true },
      { source: "/settings", destination: "/operator", permanent: true },
      { source: "/tasks", destination: "/operator", permanent: true },
      { source: "/w/:path*", destination: "/operator", permanent: true },
    ];
  },
};

export default nextConfig;
