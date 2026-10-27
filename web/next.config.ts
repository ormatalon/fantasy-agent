import type { NextConfig } from "next";

// A static site in out/, served by the FastAPI backend at /.
const nextConfig: NextConfig = {
  output: "export",
};

export default nextConfig;
