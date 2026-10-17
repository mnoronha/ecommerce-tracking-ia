# Noro Platform — instruções para agentes

Monorepo: `apps/api` (FastAPI, Railway) e `apps/web` (Next.js, Vercel). Veja o README.

- **`apps/web` usa uma versão do Next.js com mudanças incompatíveis.** Antes de escrever código no front,
  leia o guia relevante em `apps/web/node_modules/next/dist/docs/` e respeite avisos de depreciação.
- A `main` faz deploy automático (Railway e Vercel). Antes de push na `main`:
  `cd apps/api && python -c "import app.main"` e `cd apps/web && npm run build`.
- Migrações novas vão em `apps/api/migrations/` com número sequencial; aplique no Supabase antes do deploy
  quando o código depender delas.
- Nunca grave email/telefone de cliente em texto: use `app/core/pii.py`.
- Nunca chame `send_*` de plataformas de anúncio sem `send_guard.sends_blocked(...)`.
