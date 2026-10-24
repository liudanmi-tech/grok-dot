import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 前端固定 5173（strictPort：端口被占用时直接报错，不会顺延到其他端口，也不会碰 8000）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8100" },
  },
  preview: { port: 4173, strictPort: true },
});
