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
  ],
};

export default nextConfig;
