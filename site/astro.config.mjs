import { defineConfig } from "astro/config";

export default defineConfig({
  site: "https://behindthewatt.com",
  output: "static",
  trailingSlash: "always",
  build: { format: "directory" },
});
