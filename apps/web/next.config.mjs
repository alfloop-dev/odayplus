/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Workspace packages ship raw TS/TSX (.ts source); Next must transpile them.
  transpilePackages: [
    "@oday-plus/ui",
    "@oday-plus/design-tokens",
    "@oday-plus/domain-types",
  ],
};

export default nextConfig;
