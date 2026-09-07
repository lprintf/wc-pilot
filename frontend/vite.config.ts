import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import { resolve, dirname } from "node:path"
import { fileURLToPath } from "node:url"

const root = dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        landing: resolve(root, "index.html"),
        admin: resolve(root, "admin/index.html"),
        portal: resolve(root, "me/index.html"),
      },
    },
  },
})
