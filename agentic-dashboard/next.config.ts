import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  // Disambiguate workspace root — silences the "multiple lockfiles" warning.
  turbopack: { root: path.join(__dirname) },
};

export default nextConfig;
