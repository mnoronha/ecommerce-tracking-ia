#!/usr/bin/env node
/**
 * Gerador de Relatórios de Tráfego Pago
 * ------------------------------------------------------------
 * Gera, para cada cliente:
 *   - Relatório Semanal HTML (+ versão e-mail com CSS inline via juice)
 *   - Relatório Mensal HTML (+ PDF via Puppeteer)
 *   - Resumo WhatsApp (.txt) para semanal e mensal
 *
 * Uso:
 *   node gerar_relatorio.js --cliente exemplo_ecommerce --tipo semanal
 *   node gerar_relatorio.js --cliente exemplo_ecommerce --tipo mensal
 *   node gerar_relatorio.js --todos --tipo semanal
 *   node gerar_relatorio.js --cliente exemplo_leads --tipo semanal --periodo "19/05 a 25/05/2025"
 */

const fs = require("fs");
const path = require("path");
const handlebars = require("handlebars");
const chalk = require("chalk");

// ── Diretórios ────────────────────────────────────────────────────────────────
const ROOT = __dirname;
const DIR = {
  config: path.join(ROOT, "config"),
  clientes: path.join(ROOT, "clientes"),
  templates: path.join(ROOT, "templates"),
  partials: path.join(ROOT, "templates", "partials"),
  styles: path.join(ROOT, "assets", "styles"),
  output: path.join(ROOT, "output"),
};

// ════════════════════════════════════════════════════════════════════════════
// 1. PARSE DE ARGUMENTOS
// ════════════════════════════════════════════════════════════════════════════
function parseArgs(argv) {
  const args = { cliente: null, tipo: "semanal", todos: false, periodo: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--cliente") args.cliente = argv[++i];
    else if (a === "--tipo") args.tipo = argv[++i];
    else if (a === "--periodo") args.periodo = argv[++i];
    else if (a === "--todos") args.todos = true;
    else if (a === "--help" || a === "-h") args.help = true;
  }
  return args;
}

function printHelp() {
  console.log(`
${chalk.bold("Gerador de Relatórios de Tráfego Pago")}

${chalk.cyan("Comandos:")}
  --cliente <id>     id do cliente (arquivo em clientes/<id>.json)
  --tipo <t>         semanal | mensal            (padrão: semanal)
  --todos            gera para todos os clientes
  --periodo "<p>"    sobrescreve o período exibido
  --help, -h         esta ajuda

${chalk.cyan("Exemplos:")}
  node gerar_relatorio.js --cliente exemplo_ecommerce --tipo semanal
  node gerar_relatorio.js --todos --tipo mensal
`);
}

// ════════════════════════════════════════════════════════════════════════════
// 2. FORMATADORES
// ════════════════════════════════════════════════════════════════════════════
function formatBRL(valor) {
  if (valor == null || isNaN(valor)) return "—";
  const n = Number(valor);
  const casas = Number.isInteger(n) ? 0 : 2;
  const s = n.toLocaleString("pt-BR", {
    minimumFractionDigits: casas,
    maximumFractionDigits: casas,
  });
  return `R$ ${s}`;
}

function formatNum(valor) {
  if (valor == null || isNaN(valor)) return "—";
  return Number(valor).toLocaleString("pt-BR");
}

function pctChange(atual, anterior) {
  if (atual == null || anterior == null || anterior === 0) return null;
  return ((atual - anterior) / anterior) * 100;
}

/** "▲ +18%" / "▼ -5%" / "→ estável" — direção pela variação bruta. */
function formatDelta(atual, anterior) {
  const p = pctChange(atual, anterior);
  if (p === null) return "→ s/ base";
  if (Math.abs(p) < 0.5) return "→ estável";
  const seta = p > 0 ? "▲" : "▼";
  const sinal = p > 0 ? "+" : "";
  return `${seta} ${sinal}${p.toFixed(0)}%`;
}

/** "▲ +R$ 6,00" / "▼ -R$ 4,00" — variação absoluta com prefixo. */
function formatDeltaAbs(atual, anterior, prefixo = "") {
  if (atual == null || anterior == null) return "→ s/ base";
  const diff = atual - anterior;
  if (Math.abs(diff) < 0.005) return "→ estável";
  const seta = diff > 0 ? "▲" : "▼";
  const sinal = diff > 0 ? "+" : "-";
  const abs = Math.abs(diff).toLocaleString("pt-BR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${seta} ${sinal}${prefixo}${abs}`;
}

function setaCanal(variacao) {
  if (variacao === "up") return "▲";
  if (variacao === "down") return "▼";
  return "→";
}

function iconCanal(tipo) {
  return (
    {
      meta: "📘",
      google: "🔴",
      tiktok: "🎵",
      pinterest: "📌",
    }[tipo] || "📡"
  );
}

// Ícone alternativo p/ texto WhatsApp (mais "redondo")
function iconCanalWpp(tipo) {
  return (
    {
      meta: "📘",
      google: "🔴",
      tiktok: "🎵",
      pinterest: "📌",
    }[tipo] || "📡"
  );
}

/**
 * Classe semântica de uma variação para colorir (verde/vermelho/cinza).
 * lowerIsBetter=true → cair é bom (CPL, CPA, CPM).
 */
function deltaClass(atual, anterior, lowerIsBetter = false) {
  const p = pctChange(atual, anterior);
  if (p === null || Math.abs(p) < 0.5) return "flat";
  const subiu = p > 0;
  const bom = lowerIsBetter ? !subiu : subiu;
  return bom ? "good" : "bad";
}

// ════════════════════════════════════════════════════════════════════════════
// 3. HELPERS HANDLEBARS
// ════════════════════════════════════════════════════════════════════════════
function registerHelpers() {
  handlebars.registerHelper("eq", (a, b) => a === b);
  handlebars.registerHelper("ne", (a, b) => a !== b);
  handlebars.registerHelper("gt", (a, b) => Number(a) > Number(b));
  handlebars.registerHelper("brl", (v) => formatBRL(v));
  handlebars.registerHelper("num", (v) => formatNum(v));
  handlebars.registerHelper("pct", (v, casas) => {
    if (v == null || isNaN(v)) return "—";
    const c = typeof casas === "number" ? casas : 1;
    return `${Number(v).toLocaleString("pt-BR", { minimumFractionDigits: c, maximumFractionDigits: c })}%`;
  });
  handlebars.registerHelper("roas", (v) =>
    v == null ? "—" : `${Number(v).toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}x`
  );
  handlebars.registerHelper("icone", (tipo) => iconCanal(tipo));
  handlebars.registerHelper("seta", (variacao) => setaCanal(variacao));
  handlebars.registerHelper("inc", (i) => i + 1);
  handlebars.registerHelper("upper", (s) => String(s || "").toUpperCase());
}

function registerPartials() {
  if (!fs.existsSync(DIR.partials)) return;
  for (const file of fs.readdirSync(DIR.partials)) {
    if (!file.endsWith(".html")) continue;
    const nome = path.basename(file, ".html");
    handlebars.registerPartial(nome, fs.readFileSync(path.join(DIR.partials, file), "utf8"));
  }
}

// ════════════════════════════════════════════════════════════════════════════
// 4. CONSTRUÇÃO DO CONTEXTO (enriquecimento dos dados p/ os templates)
// ════════════════════════════════════════════════════════════════════════════
function buildKpisSemanal(cliente, m) {
  const ecom = cliente.tipo === "ecommerce";
  if (ecom) {
    return [
      {
        cor: "var(--c-faturamento)",
        label: "Faturamento",
        valor: formatBRL(m.faturamento),
        delta: formatDelta(m.faturamento, m.faturamento_semana_ant),
        deltaClass: deltaClass(m.faturamento, m.faturamento_semana_ant),
        contexto: `Meta: ${formatBRL(cliente.semanal.meta_faturamento)}`,
      },
      {
        cor: "var(--c-roas)",
        label: "ROAS Geral",
        valor: `${Number(m.roas).toLocaleString("pt-BR", { minimumFractionDigits: 1 })}x`,
        delta: formatDelta(m.roas, m.roas_semana_ant),
        deltaClass: deltaClass(m.roas, m.roas_semana_ant),
        contexto: `Semana anterior: ${Number(m.roas_semana_ant).toLocaleString("pt-BR", { minimumFractionDigits: 1 })}x`,
      },
      {
        cor: "var(--c-pedidos)",
        label: "Pedidos",
        valor: formatNum(m.pedidos),
        delta: formatDelta(m.pedidos, m.pedidos_semana_ant),
        deltaClass: deltaClass(m.pedidos, m.pedidos_semana_ant),
        contexto: `Ticket médio: ${formatBRL(m.ticket_medio)}`,
      },
      {
        cor: "var(--c-cpa)",
        label: "CPA",
        valor: formatBRL(m.cpa),
        delta: m.cpa <= m.cpa_meta ? "✓ na meta" : "acima da meta",
        deltaClass: m.cpa <= m.cpa_meta ? "good" : "bad",
        contexto: `Meta: ${formatBRL(m.cpa_meta)}`,
      },
      {
        cor: "var(--c-ctr)",
        label: "CTR",
        valor: `${Number(m.ctr).toLocaleString("pt-BR", { minimumFractionDigits: 1 })}%`,
        delta: "",
        deltaClass: "flat",
        contexto: `Investimento: ${formatBRL(m.investimento)}`,
      },
      {
        cor: "var(--c-conv)",
        label: "Taxa de Conversão",
        valor: `${Number(m.taxa_conversao).toLocaleString("pt-BR", { minimumFractionDigits: 1 })}%`,
        delta: "",
        deltaClass: "flat",
        contexto: `Sessões: ${formatNum(m.sessoes)}`,
      },
    ];
  }
  // LEADS
  return [
    {
      cor: "var(--c-faturamento)",
      label: "Leads Gerados",
      valor: formatNum(m.leads),
      delta: formatDelta(m.leads, m.leads_semana_ant),
      deltaClass: deltaClass(m.leads, m.leads_semana_ant),
      contexto: `Meta: ${formatNum(cliente.semanal.meta_leads)}`,
    },
    {
      cor: "var(--c-cpa)",
      label: "CPL",
      valor: formatBRL(m.cpl),
      delta: formatDelta(m.cpl, m.cpl_semana_ant),
      deltaClass: deltaClass(m.cpl, m.cpl_semana_ant, true),
      contexto: `Meta: ${formatBRL(m.cpl_meta)}`,
    },
    {
      cor: "var(--c-roas)",
      label: "Investimento",
      valor: formatBRL(m.investimento),
      delta: "",
      deltaClass: "flat",
      contexto: `Budget: ${formatBRL(m.budget_semanal)}`,
    },
    {
      cor: "var(--c-pedidos)",
      label: "Leads Qualificados",
      valor: formatNum(m.leads_qualificados),
      delta: formatDelta(m.leads_qualificados, m.leads_qualificados_semana_ant),
      deltaClass: deltaClass(m.leads_qualificados, m.leads_qualificados_semana_ant),
      contexto: `Taxa de qualificação: ${m.taxa_qualificacao}%`,
    },
    {
      cor: "var(--c-conv)",
      label: "Taxa de Conversão",
      valor: `${Number(m.taxa_conversao).toLocaleString("pt-BR", { minimumFractionDigits: 1 })}%`,
      delta: formatDelta(m.taxa_conversao, m.taxa_conversao_semana_ant),
      deltaClass: deltaClass(m.taxa_conversao, m.taxa_conversao_semana_ant),
      contexto: `Cliques: ${formatNum(m.cliques)}`,
    },
    {
      cor: "var(--c-ctr)",
      label: "Agendamentos",
      valor: formatNum(m.agendamentos),
      delta: formatDelta(m.agendamentos, m.agendamentos_semana_ant),
      deltaClass: deltaClass(m.agendamentos, m.agendamentos_semana_ant),
      contexto: `Convertidos de leads qualificados`,
    },
  ];
}

function enrichCanaisSemanal(cliente, canais) {
  const ecom = cliente.tipo === "ecommerce";
  return (canais || []).map((c) => ({
    ...c,
    icone: iconCanal(c.tipo),
    seta: setaCanal(c.variacao),
    classe: c.variacao === "up" ? "good" : c.variacao === "down" ? "bad" : "flat",
    valor_principal: ecom ? formatBRL(c.receita) : formatNum(c.leads),
    metrica_secundaria: ecom
      ? `ROAS ${Number(c.roas).toLocaleString("pt-BR", { minimumFractionDigits: 1 })}x`
      : `CPL ${formatBRL(c.cpl)}`,
    investimento_fmt: formatBRL(c.investimento),
  }));
}

function buildKpisMensal(cliente, m) {
  const ecom = cliente.tipo === "ecommerce";
  const mk = (cor, label, valor, atual, mesAnt, anoAnt, lowerBetter = false) => ({
    cor,
    label,
    valor,
    mom: formatDelta(atual, mesAnt),
    momClass: deltaClass(atual, mesAnt, lowerBetter),
    yoy: anoAnt != null ? formatDelta(atual, anoAnt) : "",
    yoyClass: anoAnt != null ? deltaClass(atual, anoAnt, lowerBetter) : "flat",
  });
  if (ecom) {
    return [
      mk("var(--c-faturamento)", "Faturamento", formatBRL(m.faturamento), m.faturamento, m.faturamento_mes_ant, m.faturamento_ano_ant),
      mk("var(--c-roas)", "ROAS Geral", `${Number(m.roas).toLocaleString("pt-BR", { minimumFractionDigits: 1 })}x`, m.roas, m.roas_mes_ant, m.roas_ano_ant),
      mk("var(--c-pedidos)", "Pedidos", formatNum(m.pedidos), m.pedidos, m.pedidos_mes_ant, m.pedidos_ano_ant),
      mk("var(--c-cpa)", "CPA", formatBRL(m.cpa), m.cpa, m.cpa_mes_ant, m.cpa_ano_ant, true),
      mk("var(--c-ctr)", "Investimento", formatBRL(m.investimento), m.investimento, m.investimento_mes_ant, m.investimento_ano_ant),
      { cor: "var(--c-conv)", label: "Ticket Médio", valor: formatBRL(m.ticket_medio), mom: "", momClass: "flat", yoy: "", yoyClass: "flat" },
    ];
  }
  return [
    mk("var(--c-faturamento)", "Leads Gerados", formatNum(m.leads), m.leads, m.leads_mes_ant, m.leads_ano_ant),
    mk("var(--c-cpa)", "CPL", formatBRL(m.cpl), m.cpl, m.cpl_mes_ant, m.cpl_ano_ant, true),
    mk("var(--c-pedidos)", "Leads Qualificados", formatNum(m.leads_qualificados), m.leads_qualificados, m.leads_qualificados_mes_ant, null),
    mk("var(--c-roas)", "Agendamentos", formatNum(m.agendamentos), m.agendamentos, m.agendamentos_mes_ant, m.agendamentos_ano_ant),
    mk("var(--c-ctr)", "Investimento", formatBRL(m.investimento), m.investimento, m.investimento_mes_ant, m.investimento_ano_ant),
    { cor: "var(--c-conv)", label: "Taxa de Qualificação", valor: `${m.taxa_qualificacao}%`, mom: "", momClass: "flat", yoy: "", yoyClass: "flat" },
  ];
}

function buildYoYBanner(cliente, m) {
  const ecom = cliente.tipo === "ecommerce";
  if (ecom) {
    return [
      { label: "Faturamento", atual: formatBRL(m.faturamento), ant: formatBRL(m.faturamento_ano_ant), delta: formatDelta(m.faturamento, m.faturamento_ano_ant), classe: deltaClass(m.faturamento, m.faturamento_ano_ant) },
      { label: "ROAS", atual: `${m.roas}x`, ant: `${m.roas_ano_ant}x`, delta: formatDelta(m.roas, m.roas_ano_ant), classe: deltaClass(m.roas, m.roas_ano_ant) },
      { label: "Pedidos", atual: formatNum(m.pedidos), ant: formatNum(m.pedidos_ano_ant), delta: formatDelta(m.pedidos, m.pedidos_ano_ant), classe: deltaClass(m.pedidos, m.pedidos_ano_ant) },
      { label: "CPA", atual: formatBRL(m.cpa), ant: formatBRL(m.cpa_ano_ant), delta: formatDelta(m.cpa, m.cpa_ano_ant), classe: deltaClass(m.cpa, m.cpa_ano_ant, true) },
    ];
  }
  return [
    { label: "Leads", atual: formatNum(m.leads), ant: formatNum(m.leads_ano_ant), delta: formatDelta(m.leads, m.leads_ano_ant), classe: deltaClass(m.leads, m.leads_ano_ant) },
    { label: "CPL", atual: formatBRL(m.cpl), ant: formatBRL(m.cpl_ano_ant), delta: formatDelta(m.cpl, m.cpl_ano_ant), classe: deltaClass(m.cpl, m.cpl_ano_ant, true) },
    { label: "Agendamentos", atual: formatNum(m.agendamentos), ant: formatNum(m.agendamentos_ano_ant), delta: formatDelta(m.agendamentos, m.agendamentos_ano_ant), classe: deltaClass(m.agendamentos, m.agendamentos_ano_ant) },
    { label: "Investimento", atual: formatBRL(m.investimento), ant: formatBRL(m.investimento_ano_ant), delta: formatDelta(m.investimento, m.investimento_ano_ant), classe: "flat" },
  ];
}

function enrichCanaisMensal(cliente, canais) {
  const ecom = cliente.tipo === "ecommerce";
  return (canais || []).map((c) => {
    const metricas = [];
    if (ecom) {
      metricas.push({ label: "Receita", valor: formatBRL(c.receita), delta: c.var_receita_pct != null ? `${c.var_receita_dir === "up" ? "▲" : "▼"} ${c.var_receita_pct}%` : "", classe: c.var_receita_dir === "up" ? "good" : "bad" });
      metricas.push({ label: "Pedidos", valor: formatNum(c.pedidos), delta: c.var_pedidos_pct != null ? `${c.var_pedidos_dir === "up" ? "▲" : "▼"} ${c.var_pedidos_pct}%` : "", classe: c.var_pedidos_dir === "up" ? "good" : "bad" });
      metricas.push({ label: "CPM", valor: formatBRL(c.cpm), delta: c.var_cpm_pct != null ? `${c.var_cpm_dir === "down" ? "▼" : "▲"} ${c.var_cpm_pct}%` : "", classe: c.var_cpm_dir === "down" ? "good" : "bad" });
      metricas.push({ label: "CTR", valor: `${c.ctr}%`, delta: c.var_ctr_pp != null ? `${c.var_ctr_dir === "up" ? "▲" : "▼"} ${c.var_ctr_pp}pp` : "", classe: c.var_ctr_dir === "up" ? "good" : "bad" });
      const extra = c.frequencia != null ? { label: "Frequência", valor: String(c.frequencia).replace(".", ",") } : c.impression_share != null ? { label: "Impr. Share", valor: `${c.impression_share}%`, delta: c.var_is_pp != null ? `${c.var_is_dir === "up" ? "▲" : "▼"} ${Math.abs(c.var_is_pp)}pp` : "", classe: c.var_is_dir === "up" ? "good" : "bad" } : c.vtr_6s != null ? { label: "VTR 6s", valor: `${c.vtr_6s}%` } : null;
      if (extra) metricas.push(extra);
    } else {
      metricas.push({ label: "Leads", valor: formatNum(c.leads), delta: c.var_leads_pct != null ? `${c.var_leads_dir === "up" ? "▲" : "▼"} ${c.var_leads_pct}%` : "", classe: c.var_leads_dir === "up" ? "good" : "bad" });
      metricas.push({ label: "CPL", valor: formatBRL(c.cpl), delta: c.var_cpl_pct != null ? `${c.var_cpl_dir === "down" ? "▼" : "▲"} ${c.var_cpl_pct}%` : "", classe: c.var_cpl_dir === "down" ? "good" : "bad" });
      metricas.push({ label: "Qualificados", valor: formatNum(c.leads_qualificados) });
      metricas.push({ label: "CPM", valor: formatBRL(c.cpm), delta: c.var_cpm_pct != null ? `${c.var_cpm_dir === "down" ? "▼" : "▲"} ${c.var_cpm_pct}%` : "", classe: c.var_cpm_dir === "down" ? "good" : "bad" });
      metricas.push({ label: "CTR", valor: `${c.ctr}%` });
    }
    return {
      ...c,
      icone: iconCanal(c.tipo),
      investimento_fmt: formatBRL(c.investimento),
      destaque: ecom
        ? `ROAS ${Number(c.roas).toLocaleString("pt-BR", { minimumFractionDigits: 1 })}x`
        : `CPL ${formatBRL(c.cpl)}`,
      metricas,
    };
  });
}

function enrichCampanhas(cliente, campanhas) {
  const ecom = cliente.tipo === "ecommerce";
  return (campanhas || []).map((c) => ({
    ...c,
    canal_label: { meta: "Meta", google: "Google", tiktok: "TikTok", pinterest: "Pinterest" }[c.canal] || c.canal,
    investimento_fmt: formatBRL(c.investimento),
    resultado_fmt: ecom ? formatBRL(c.receita) : formatNum(c.leads),
    indice_fmt: ecom
      ? `${Number(c.roas).toLocaleString("pt-BR", { minimumFractionDigits: 1 })}x`
      : formatBRL(c.cpl),
    qtd_fmt: ecom ? formatNum(c.pedidos) : formatNum(c.leads_qualificados),
    custo_fmt: ecom ? formatBRL(c.cpa) : `${c.taxa_qualificacao}%`,
    status_label: { ativo: "Ativo", pausado: "Pausado", atencao: "Atenção" }[c.status] || c.status,
  }));
}

function buildContext(cliente, agencia, tipo, periodoOverride) {
  const d = cliente[tipo];
  if (!d) throw new Error(`Cliente '${cliente.id}' não tem bloco '${tipo}'.`);
  const ecom = cliente.tipo === "ecommerce";
  const base = {
    agencia,
    cliente: { id: cliente.id, nome: cliente.nome, tipo: cliente.tipo },
    tipo,
    ecommerce: ecom,
    leads: !ecom,
    perfil_label: ecom ? "E-commerce" : "Leads",
    data_geracao: new Date().toLocaleDateString("pt-BR"),
  };

  if (tipo === "semanal") {
    return {
      ...base,
      periodo: periodoOverride || d.periodo,
      semana_num: d.semana_num,
      kpis: buildKpisSemanal(cliente, d.metricas),
      canais: enrichCanaisSemanal(cliente, d.canais),
      destaques: d.destaques,
      acoes: d.acoes,
    };
  }

  // MENSAL
  return {
    ...base,
    mes: d.mes,
    ano: d.ano,
    mes_label: `${d.mes}/${d.ano}`,
    mes_anterior_label: d.mes_anterior_label,
    ano_anterior_label: d.ano_anterior_label,
    periodo: periodoOverride || `${d.mes}/${d.ano}`,
    resumo_executivo: d.resumo_executivo,
    kpis: buildKpisMensal(cliente, d.metricas),
    yoy: buildYoYBanner(cliente, d.metricas),
    canais: enrichCanaisMensal(cliente, d.canais),
    campanhas: enrichCampanhas(cliente, d.campanhas),
    criativos_destaque: d.criativos_destaque,
    destaques: d.destaques,
    atencoes: d.atencoes,
    plano: d.plano_proximo_mes,
    plano_meta_faturamento_fmt: d.plano_proximo_mes ? formatBRL(d.plano_proximo_mes.meta_faturamento) : "",
    plano_meta_leads_fmt: d.plano_proximo_mes ? formatNum(d.plano_proximo_mes.meta_leads) : "",
    plano_budget_fmt: d.plano_proximo_mes ? formatBRL(d.plano_proximo_mes.budget_total) : "",
  };
}

// ════════════════════════════════════════════════════════════════════════════
// 5. RESUMO WHATSAPP
// ════════════════════════════════════════════════════════════════════════════
function whatsappCanais(cliente, canais) {
  const ecom = cliente.tipo === "ecommerce";
  return canais
    .map((c) => {
      const ico = iconCanalWpp(c.tipo);
      const seta = setaCanal(c.variacao);
      if (ecom) {
        return `├ ${ico} ${c.nome}: ${formatBRL(c.receita)} · ROAS ${Number(c.roas).toLocaleString("pt-BR", { minimumFractionDigits: 1 })}x ${seta}`;
      }
      return `├ ${ico} ${c.nome}: ${formatNum(c.leads)} leads · CPL ${formatBRL(c.cpl)} ${seta}`;
    })
    .join("\n");
}

function whatsappSemanal(cliente, agencia, periodo) {
  const d = cliente.semanal;
  const m = d.metricas;
  const ecom = cliente.tipo === "ecommerce";
  const per = periodo || d.periodo;
  const canais = whatsappCanais(cliente, d.canais);

  if (ecom) {
    return `📊 *Relatório Semanal | ${cliente.nome}*
📅 Semana ${d.semana_num} · ${per}

💰 *Faturamento:* ${formatBRL(m.faturamento)}
📦 *Pedidos:* ${formatNum(m.pedidos)}
🎯 *ROAS Geral:* ${Number(m.roas).toLocaleString("pt-BR", { minimumFractionDigits: 1 })}x
💸 *Investimento:* ${formatBRL(m.investimento)}
🛒 *Ticket Médio:* ${formatBRL(m.ticket_medio)}
⚡ *CPA:* ${formatBRL(m.cpa)}

📈 *vs Semana Anterior*
├ Faturamento: ${formatDelta(m.faturamento, m.faturamento_semana_ant)}
├ Pedidos: ${formatDelta(m.pedidos, m.pedidos_semana_ant)}
└ ROAS: ${formatDelta(m.roas, m.roas_semana_ant)}

📡 *Por Canal*
${canais}

✅ *Destaque:* ${d.destaques.positivo}

⚠️ *Atenção:* ${d.destaques.atencao}

🚀 *Próxima semana:* ${d.destaques.proxima_semana}

_Relatório completo enviado por e-mail_ ✉️
_Dúvidas? É só falar!_ 👋

${agencia.nome}`;
  }

  // LEADS
  return `📊 *Relatório Semanal | ${cliente.nome}*
📅 Semana ${d.semana_num} · ${per}

🎯 *Leads Gerados:* ${formatNum(m.leads)}
✅ *Qualificados:* ${formatNum(m.leads_qualificados)} (${m.taxa_qualificacao}%)
📅 *Agendamentos:* ${formatNum(m.agendamentos)}
💸 *Investimento:* ${formatBRL(m.investimento)}
⚡ *CPL:* ${formatBRL(m.cpl)}

📈 *vs Semana Anterior*
├ Leads: ${formatDelta(m.leads, m.leads_semana_ant)}
├ Qualificados: ${formatDelta(m.leads_qualificados, m.leads_qualificados_semana_ant)}
├ Agendamentos: ${formatDelta(m.agendamentos, m.agendamentos_semana_ant)}
└ CPL: ${formatDeltaAbs(m.cpl, m.cpl_semana_ant, "R$ ")}

📡 *Por Canal*
${canais}

✅ *Destaque:* ${d.destaques.positivo}

⚠️ *Atenção:* ${d.destaques.atencao}

🚀 *Próxima semana:* ${d.destaques.proxima_semana}

_Relatório completo enviado por e-mail_ ✉️
_Dúvidas? É só falar!_ 👋

${agencia.nome}`;
}

function whatsappMensal(cliente, agencia, pdfNome) {
  const d = cliente.mensal;
  const m = d.metricas;
  const ecom = cliente.tipo === "ecommerce";
  const topAcoes = (d.plano_proximo_mes?.acoes || []).slice(0, 3).map((a, i) => `${i + 1}. ${a}`).join("\n");
  const atencao = (d.atencoes || [])[0];

  if (ecom) {
    return `📈 *Relatório Mensal | ${cliente.nome}*
🗓️ ${d.mes}/${d.ano}

💰 *Faturamento:* ${formatBRL(m.faturamento)}
🎯 *ROAS Geral:* ${Number(m.roas).toLocaleString("pt-BR", { minimumFractionDigits: 1 })}x
📦 *Pedidos:* ${formatNum(m.pedidos)}
💸 *Investimento:* ${formatBRL(m.investimento)}
⚡ *CPA:* ${formatBRL(m.cpa)}

📊 *vs ${d.mes_anterior_label}*
├ Faturamento: ${formatDelta(m.faturamento, m.faturamento_mes_ant)}
├ Pedidos: ${formatDelta(m.pedidos, m.pedidos_mes_ant)}
└ ROAS: ${formatDelta(m.roas, m.roas_mes_ant)}

📅 *vs ${d.ano_anterior_label}*
├ Faturamento: ${formatDelta(m.faturamento, m.faturamento_ano_ant)}
└ Pedidos: ${formatDelta(m.pedidos, m.pedidos_ano_ant)}

✅ *Resumo:* ${d.resumo_executivo}

${atencao ? `⚠️ *Atenção:* ${atencao.titulo}\n` : ""}
🚀 *Plano para ${d.plano_proximo_mes?.mes || "o próximo mês"}*
${topAcoes}

📄 *Relatório completo (PDF):* ${pdfNome}

_Qualquer dúvida, estamos à disposição!_ 👋

${agencia.nome}`;
  }

  // LEADS
  return `📈 *Relatório Mensal | ${cliente.nome}*
🗓️ ${d.mes}/${d.ano}

🎯 *Leads Gerados:* ${formatNum(m.leads)}
✅ *Qualificados:* ${formatNum(m.leads_qualificados)} (${m.taxa_qualificacao}%)
📅 *Agendamentos:* ${formatNum(m.agendamentos)}
💸 *Investimento:* ${formatBRL(m.investimento)}
⚡ *CPL:* ${formatBRL(m.cpl)}

📊 *vs ${d.mes_anterior_label}*
├ Leads: ${formatDelta(m.leads, m.leads_mes_ant)}
├ Agendamentos: ${formatDelta(m.agendamentos, m.agendamentos_mes_ant)}
└ CPL: ${formatDeltaAbs(m.cpl, m.cpl_mes_ant, "R$ ")}

📅 *vs ${d.ano_anterior_label}*
├ Leads: ${formatDelta(m.leads, m.leads_ano_ant)}
└ Agendamentos: ${formatDelta(m.agendamentos, m.agendamentos_ano_ant)}

✅ *Resumo:* ${d.resumo_executivo}

${atencao ? `⚠️ *Atenção:* ${atencao.titulo}\n` : ""}
🚀 *Plano para ${d.plano_proximo_mes?.mes || "o próximo mês"}*
${topAcoes}

📄 *Relatório completo (PDF):* ${pdfNome}

_Qualquer dúvida, estamos à disposição!_ 👋

${agencia.nome}`;
}

// ════════════════════════════════════════════════════════════════════════════
// 6. RENDER + GRAVAÇÃO
// ════════════════════════════════════════════════════════════════════════════
function aplicarCoresAgencia(css, agencia) {
  return css
    .replace(/__COR_PRIMARIA__/g, agencia.cor_primaria || "#6c47ff")
    .replace(/__COR_SECUNDARIA__/g, agencia.cor_secundaria || "#a855f7");
}

function renderHTML(tipo, ctx, agencia) {
  const tplPath = path.join(DIR.templates, `${tipo}.html`);
  const cssPath = path.join(DIR.styles, `${tipo}.css`);
  const tpl = handlebars.compile(fs.readFileSync(tplPath, "utf8"));
  let css = fs.readFileSync(cssPath, "utf8");
  css = aplicarCoresAgencia(css, agencia);
  return tpl({ ...ctx, css });
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function slugMes(mensal) {
  return `${String(mensal.mes).toLowerCase()}_${mensal.ano}`;
}

async function gerarPDF(htmlPath, pdfPath) {
  const puppeteer = require("puppeteer");
  const browser = await puppeteer.launch({ headless: "new", args: ["--no-sandbox", "--disable-setuid-sandbox"] });
  try {
    const page = await browser.newPage();
    await page.goto(`file://${path.resolve(htmlPath)}`, { waitUntil: "networkidle0" });
    await page.pdf({
      path: pdfPath,
      format: "A4",
      printBackground: true,
      margin: { top: 0, right: 0, bottom: 0, left: 0 },
    });
  } finally {
    await browser.close();
  }
}

// ════════════════════════════════════════════════════════════════════════════
// 7. PIPELINE POR CLIENTE
// ════════════════════════════════════════════════════════════════════════════
async function processarCliente(clienteId, tipo, periodoOverride, agencia) {
  const clientePath = path.join(DIR.clientes, `${clienteId}.json`);
  if (!fs.existsSync(clientePath)) {
    throw new Error(`Cliente não encontrado: ${clientePath}`);
  }
  const cliente = JSON.parse(fs.readFileSync(clientePath, "utf8"));
  const outDir = path.join(DIR.output, cliente.id);
  ensureDir(outDir);

  const ctx = buildContext(cliente, agencia, tipo, periodoOverride);
  const html = renderHTML(tipo, ctx, agencia);

  const resultado = { cliente, tipo, arquivos: {} };

  if (tipo === "semanal") {
    const hoje = new Date().toISOString().slice(0, 10);
    const htmlPath = path.join(outDir, `semanal_${hoje}.html`);
    fs.writeFileSync(htmlPath, html, "utf8");
    resultado.arquivos.html = htmlPath;

    // versão e-mail com CSS inline
    try {
      const juice = require("juice");
      const emailHtml = juice(html);
      const emailPath = path.join(outDir, `semanal_email_${hoje}.html`);
      fs.writeFileSync(emailPath, emailHtml, "utf8");
      resultado.arquivos.email = emailPath;
    } catch (e) {
      console.warn(chalk.yellow(`  ⚠ juice indisponível — versão e-mail não gerada (${e.message})`));
    }

    const wpp = whatsappSemanal(cliente, agencia, periodoOverride);
    const wppPath = path.join(outDir, `whatsapp_semanal_${hoje}.txt`);
    fs.writeFileSync(wppPath, wpp, "utf8");
    resultado.arquivos.whatsapp = wppPath;
    resultado.periodo = periodoOverride || cliente.semanal.periodo;
    resultado.semana_num = cliente.semanal.semana_num;
  } else {
    const slug = slugMes(cliente.mensal);
    const htmlPath = path.join(outDir, `mensal_${slug}.html`);
    fs.writeFileSync(htmlPath, html, "utf8");
    resultado.arquivos.html = htmlPath;

    const pdfPath = path.join(outDir, `mensal_${slug}.pdf`);
    try {
      await gerarPDF(htmlPath, pdfPath);
      resultado.arquivos.pdf = pdfPath;
    } catch (e) {
      console.warn(chalk.yellow(`  ⚠ PDF não gerado (Puppeteer): ${e.message}`));
    }

    const pdfNome = path.basename(pdfPath);
    const wpp = whatsappMensal(cliente, agencia, pdfNome);
    const wppPath = path.join(outDir, `whatsapp_mensal_${slug}.txt`);
    fs.writeFileSync(wppPath, wpp, "utf8");
    resultado.arquivos.whatsapp = wppPath;
    resultado.periodo = `${cliente.mensal.mes}/${cliente.mensal.ano}`;
  }

  return resultado;
}

function imprimirResultado(r) {
  const perfil = r.cliente.tipo === "ecommerce" ? "E-commerce" : "Leads";
  const rel = (p) => path.relative(ROOT, p).replace(/\\/g, "/");
  console.log("");
  console.log(chalk.green.bold("✅ Relatório gerado com sucesso!"));
  console.log("");
  console.log(`${chalk.bold("Cliente")}  : ${r.cliente.nome} (${perfil})`);
  if (r.tipo === "semanal") {
    console.log(`${chalk.bold("Período")}  : Semana ${r.semana_num} · ${r.periodo}`);
  } else {
    console.log(`${chalk.bold("Período")}  : ${r.periodo}`);
  }
  console.log(`${chalk.bold("Tipo")}     : ${r.tipo === "semanal" ? "Semanal" : "Mensal"}`);
  console.log("");
  if (r.arquivos.html) console.log(`📄 HTML       → ${chalk.cyan(rel(r.arquivos.html))}`);
  if (r.arquivos.email) console.log(`📧 E-mail     → ${chalk.cyan(rel(r.arquivos.email))}`);
  if (r.arquivos.pdf) console.log(`📋 PDF        → ${chalk.cyan(rel(r.arquivos.pdf))}`);
  if (r.arquivos.whatsapp) console.log(`📱 WhatsApp   → ${chalk.cyan(rel(r.arquivos.whatsapp))}`);
  console.log("");
  console.log(chalk.dim("Próximos passos:"));
  console.log(chalk.dim("  1. Abra o HTML no browser para visualizar"));
  console.log(chalk.dim("  2. Copie o texto WhatsApp para o grupo do cliente"));
  console.log(chalk.dim(r.tipo === "mensal" ? "  3. Envie o PDF por e-mail" : "  3. Envie o HTML (versão e-mail) por e-mail"));
}

// ════════════════════════════════════════════════════════════════════════════
// 8. MAIN
// ════════════════════════════════════════════════════════════════════════════
async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) return printHelp();

  if (!["semanal", "mensal"].includes(args.tipo)) {
    console.error(chalk.red(`Tipo inválido: '${args.tipo}'. Use 'semanal' ou 'mensal'.`));
    process.exit(1);
  }

  const agencia = JSON.parse(fs.readFileSync(path.join(DIR.config, "agencia.json"), "utf8"));
  registerHelpers();
  registerPartials();

  let clienteIds = [];
  if (args.todos) {
    clienteIds = fs
      .readdirSync(DIR.clientes)
      .filter((f) => f.endsWith(".json"))
      .map((f) => path.basename(f, ".json"));
  } else if (args.cliente) {
    clienteIds = [args.cliente];
  } else {
    console.error(chalk.red("Informe --cliente <id> ou --todos."));
    printHelp();
    process.exit(1);
  }

  console.log(chalk.bold(`\n🚀 Gerando ${args.tipo} para ${clienteIds.length} cliente(s)...\n`));

  let ok = 0;
  for (const id of clienteIds) {
    try {
      const r = await processarCliente(id, args.tipo, args.periodo, agencia);
      imprimirResultado(r);
      ok++;
    } catch (e) {
      console.error(chalk.red(`\n❌ Falha em '${id}': ${e.message}`));
    }
  }

  console.log(chalk.bold(`\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`));
  console.log(chalk.green.bold(`Concluído: ${ok}/${clienteIds.length} relatório(s) gerado(s).`));
  console.log("");
}

main().catch((e) => {
  console.error(chalk.red(`Erro fatal: ${e.stack || e.message}`));
  process.exit(1);
});
