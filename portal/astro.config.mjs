import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";

// Astro Starlight + Scalar Interactive Contract Portal (MIT)
// Bound to stage host loopback 127.0.0.1:8005 / container gateway 10.250.0.1:8000
export default defineConfig({
  site: "https://docs.int.aiops-flow.io",
  server: {
    host: "127.0.0.1",
    port: 8005,
  },
  integrations: [
    starlight({
      title: "AIOps Platform & Engramory Documentation Portal",
      description:
        "Dual-Consumer AI-First Documentation, 10-Layer SDD Projections, Executable Runbooks & Scalar API Playground",
      sidebar: [
        {
          label: "Architecture & Specifications",
          autogenerate: { directory: "reference" },
        },
        {
          label: "Operational Runbooks",
          autogenerate: { directory: "runbooks" },
        },
        {
          label: "API & FastMCP Reference",
          autogenerate: { directory: "contracts" },
        },
      ],
    }),
  ],
});
