import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";

// Astro Starlight + Scalar Interactive Contract Portal (MIT)
// Local dev/preview bound to loopback 127.0.0.1:4321 (stage MCP occupies 127.0.0.1:8005)
export default defineConfig({
  site: "https://docs.int.aiops-flow.io",
  server: {
    host: "127.0.0.1",
    port: 4321,
  },
  integrations: [
    starlight({
      title: "AIOps Platform & Engramory Documentation Portal",
      description:
        "Dual-Consumer AI-First Documentation, 10-Layer SDD Projections, Executable Runbooks & Scalar API Playground",
      sidebar: [
        {
          label: "Architecture & Specifications",
          items: [{ autogenerate: { directory: "reference" } }],
        },
        {
          label: "Operational Runbooks",
          items: [{ autogenerate: { directory: "runbooks" } }],
        },
        {
          label: "API & FastMCP Reference",
          items: [{ autogenerate: { directory: "contracts" } }],
        },
      ],
    }),
  ],
});
