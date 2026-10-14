import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 3000,
    // Local dev (outside Docker Compose): forward API calls to the backend.
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
  preview: {
    host: "0.0.0.0",
    port: 3000,
  },
});
