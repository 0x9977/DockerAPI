# DockerAPI Web 前端

Vite + Vue 3 + TypeScript + vue-router + Naive UI(暗色主题),SSE 使用 `@microsoft/fetch-event-source`。

## 开发

```bash
npm install
npm run dev    # http://localhost:5173,/api 代理到 http://127.0.0.1:8000
```

## 构建

```bash
npm run build  # 产物 dist/,由 FastAPI 托管(拷贝至 backend/app/static)
```

目录: `src/api`(fetch 封装)、`src/router`、`src/layouts`(应用壳)、`src/pages`、`src/components`(Sparkline/StateBadge/LogViewer)、`src/composables`(usePoll)、`src/utils`(sse/feedback/format/icons)。
