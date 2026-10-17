# Noro Platform

Plataforma da Noro Labs: dashboard dos clientes, Agency OS e Noro Tracking.

| Pasta | O que é | Deploy |
| --- | --- | --- |
| `apps/api` | API FastAPI (webhooks, tracking, integrações, Agency API `/agency/v1`) | Railway (deploy automático da `main`) |
| `apps/web` | Front Next.js (`track.noroia.com.br`) | Vercel (deploy automático da `main`) |
| `pixel/noro-v4` | Noro Tracker v4.3 para Shopify (tema + Custom Pixel) e guia de instalação | Instalado na loja do cliente |
| `apps/api/migrations` | Migrações SQL da fase Agency OS / Tracking (aplicar no Supabase) | Manual |
| `supabase/migrations` | Migrações SQL antigas (até 032) | — |
| `scripts` | Smoke tests e utilitários (`python scripts/<nome>.py` a partir da raiz) | — |
| `docs` | PRD, contratos, auditorias antigas (`docs/archive`) e prévias de relatório | — |

## Antes de qualquer push

```bash
cd apps/api && python -c "import app.main"     # a API sobe? (erro de sintaxe derruba o Railway)
cd apps/api && pytest tests -q                  # testes
cd apps/web && npm run build                    # o front compila?
```

## Noro Tracking

- Envio de conversões às plataformas é bloqueado por `CONVERSION_SENDS_DISABLED` (Railway). `*` = nenhum cliente envia.
- Clientes são identificados por `email_hash` / `phone_hash` (LGPD) — ver `apps/api/app/core/pii.py`.
