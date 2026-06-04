# Relatórios de Tráfego Pago

Gerador de relatórios de performance de tráfego pago (Meta, Google, TikTok, Pinterest)
para clientes de agência. A partir de um JSON por cliente, gera:

- **Relatório Semanal HTML** — e-mail visual dark, objetivo (+ versão com CSS inline)
- **Relatório Mensal HTML + PDF** — completo, formatado para A4
- **Resumo WhatsApp** — texto pronto para colar no grupo do cliente (semanal e mensal)

Suporta dois perfis de cliente: **E-commerce** (faturamento, ROAS, pedidos, ticket médio)
e **Leads** (leads, CPL, qualificados, agendamentos). Os mesmos templates atendem aos
dois perfis via condicionais.

---

## 1. Instalação

Requer **Node.js 18+**.

```bash
cd relatorios-agencia
npm install
```

> O `npm install` baixa o Puppeteer (inclui um Chromium ~150 MB) — usado só para o PDF
> do relatório mensal. Se você só precisa de HTML/WhatsApp, o sistema funciona mesmo
> sem o Chromium (o PDF é apenas pulado com um aviso).

---

## 2. Configurar a agência

Edite `config/agencia.json`:

```json
{
  "nome": "Sua Agência",
  "email": "contato@suaagencia.com.br",
  "site": "www.suaagencia.com.br",
  "logo_texto": "AGÊNCIA",
  "cor_primaria": "#6c47ff",
  "cor_secundaria": "#a855f7"
}
```

`cor_primaria` e `cor_secundaria` são aplicadas automaticamente nos templates
(headers, gradientes, botões, números de ação).

---

## 3. Adicionar um cliente

Crie um arquivo `clientes/<id>.json`. O `<id>` é o nome do arquivo e identifica o cliente
nos comandos. Use `clientes/exemplo_ecommerce.json` ou `clientes/exemplo_leads.json`
como base.

Campo `tipo`: `"ecommerce"` ou `"leads"` — define quais métricas aparecem.
Campo `canais`: lista de plataformas ativas (`meta`, `google`, `tiktok`, `pinterest`).

Cada cliente tem dois blocos: `semanal` e `mensal` (detalhados na seção 5).

---

## 4. Comandos (CLI)

```bash
# Semanal de um cliente
node gerar_relatorio.js --cliente exemplo_ecommerce --tipo semanal

# Mensal (gera HTML + PDF + WhatsApp)
node gerar_relatorio.js --cliente exemplo_ecommerce --tipo mensal

# Todos os clientes de uma vez
node gerar_relatorio.js --todos --tipo semanal
node gerar_relatorio.js --todos --tipo mensal

# Sobrescrever o período exibido
node gerar_relatorio.js --cliente exemplo_leads --tipo semanal --periodo "19/05 a 25/05/2025"

# Ajuda
node gerar_relatorio.js --help
```

Atalhos via npm:

```bash
npm run semanal   # = --todos --tipo semanal
npm run mensal    # = --todos --tipo mensal
```

Os arquivos são gravados em `output/<id_cliente>/`:

| Tipo    | Arquivos gerados                                                   |
|---------|--------------------------------------------------------------------|
| Semanal | `semanal_AAAA-MM-DD.html`, `semanal_email_AAAA-MM-DD.html`, `whatsapp_semanal_AAAA-MM-DD.txt` |
| Mensal  | `mensal_<mes>_<ano>.html`, `mensal_<mes>_<ano>.pdf`, `whatsapp_mensal_<mes>_<ano>.txt` |

---

## 5. Estrutura do JSON de cliente

### Cabeçalho
| Campo    | Descrição                                            |
|----------|------------------------------------------------------|
| `id`     | Identificador (deve bater com o nome do arquivo)     |
| `nome`   | Nome de exibição do cliente                          |
| `tipo`   | `"ecommerce"` ou `"leads"`                           |
| `canais` | Plataformas ativas: `meta`, `google`, `tiktok`, `pinterest` |

### Bloco `semanal`
- `periodo`, `semana_num`, `meta_faturamento` (ecom) / `meta_leads` (leads)
- `metricas`: objeto com os KPIs da semana **e** os valores da semana anterior
  (`*_semana_ant`), usados para calcular as variações (▲/▼).
- `canais[]`: por canal — `nome`, `tipo`, `investimento`, `receita`+`roas` (ecom)
  ou `leads`+`cpl` (leads), e `variacao` (`up`/`down`/`flat`).
- `destaques`: `positivo`, `criativo`, `atencao`, `proxima_semana`.
- `acoes[]`: 3 ações recomendadas para a próxima semana.

### Bloco `mensal`
- `mes`, `ano`, `mes_anterior_label`, `ano_anterior_label`, meta do mês.
- `metricas`: KPIs do mês + comparativos mês anterior (`*_mes_ant`) e ano anterior (`*_ano_ant`).
- `canais[]`: detalhe por plataforma com `var_*_pct`/`var_*_dir` para as setas.
- `campanhas[]`: tabela de campanhas (nome, canal, investimento, resultado, índice, status).
- `criativos_destaque[]`: cards com `icone`, `nome`, `tipo`, `canal` e `metricas` (pills).
- `resumo_executivo`: parágrafo de abertura.
- `destaques[]`: cards de highlight com `tipo` (`positivo`/`negativo`/`neutro`/`insight`).
- `atencoes[]`: itens com `icone`, `titulo`, `descricao`, `prioridade` (`alta`/`media`).
- `plano_proximo_mes`: metas + lista de `acoes` para o mês seguinte.

> **Tolerância a campos ausentes:** se um canal/campanha não existir, a linha é
> apenas omitida — nada quebra. Métricas opcionais por canal (frequência, impression
> share, VTR) aparecem só quando presentes.

---

## 6. Como o PDF é gerado

O mensal é renderizado em HTML A4 (com `page-break-after` entre as 5 páginas) e
convertido em PDF pelo **Puppeteer** (Chromium headless), com `printBackground: true`
e margens zeradas. Se o Chromium não estiver disponível, o HTML continua sendo gerado
e o PDF é pulado com aviso no terminal.

---

## 7. Como enviar o relatório

- **E-mail (semanal):** use o arquivo `semanal_email_*.html` — o CSS já está inline
  (via [juice](https://www.npmjs.com/package/juice)), pronto para colar no corpo do e-mail.
- **E-mail (mensal):** anexe o `mensal_*.pdf`.
- **WhatsApp:** copie o conteúdo do `whatsapp_*.txt` (já com a formatação `*negrito*` do
  WhatsApp) e cole no grupo do cliente.

---

## Estrutura de pastas

```
relatorios-agencia/
├── gerar_relatorio.js     # CLI principal
├── config/agencia.json    # dados fixos da agência
├── clientes/*.json        # um arquivo por cliente
├── templates/             # Handlebars (semanal, mensal, partials)
├── assets/styles/         # CSS (cores da agência via placeholders)
└── output/                # relatórios gerados (gitignored)
```
