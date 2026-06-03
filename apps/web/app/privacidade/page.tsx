// Política de Privacidade — template. Ajuste razão social / contato conforme necessário.
export const metadata = { title: 'Política de Privacidade' }

const UPDATED = '03/06/2026'

export default function PrivacidadePage() {
  return (
    <div className="min-h-screen bg-[#0f1117] text-slate-300">
      <div className="max-w-3xl mx-auto px-6 py-12">
        <h1 className="text-2xl font-bold text-white">Política de Privacidade</h1>
        <p className="text-xs text-slate-500 mt-1">Última atualização: {UPDATED}</p>

        <div className="mt-4 text-xs text-amber-300/80 bg-amber-500/5 border border-amber-500/20 rounded-lg px-4 py-2">
          Modelo base — revise com seu jurídico e ajuste razão social, contato do encarregado (DPO) e bases legais.
        </div>

        <Section title="1. Quem somos">
          Esta plataforma de tracking e atribuição é operada pela agência para mensurar o desempenho de marketing
          das lojas (clientes) atendidas. Atuamos como <strong>operadora</strong> de dados pessoais em nome de cada
          loja, que é a <strong>controladora</strong>.
        </Section>

        <Section title="2. Dados que tratamos">
          Para medir conversões e atribuir vendas aos canais, coletamos: identificadores de navegação (cookies de
          primeira parte, IP, user-agent), eventos de comportamento no site (páginas, carrinho, checkout) e, no momento
          da compra, dados do pedido (email, telefone, nome, CEP, valor). Esses dados vêm do site da loja via nosso
          script e dos webhooks da plataforma de e-commerce.
        </Section>

        <Section title="3. Como usamos e protegemos">
          Os dados são usados exclusivamente para mensuração, atribuição e envio de conversões às plataformas de anúncio
          (Meta, Google, etc.). Dados pessoais enviados às plataformas são <strong>hasheados com SHA-256</strong> antes do
          envio. Credenciais de integração são <strong>encriptadas em repouso</strong>. O acesso ao painel é restrito à agência.
        </Section>

        <Section title="4. Retenção">
          Eventos brutos de navegação são mantidos por <strong>90 dias</strong> e depois eliminados automaticamente.
          Métricas agregadas e registros de pedidos (sem necessidade de identificação pessoal) podem ser mantidos por
          mais tempo para fins fiscais e analíticos.
        </Section>

        <Section title="5. Compartilhamento">
          Compartilhamos dados estritamente necessários com as plataformas de anúncio (para mensuração de conversão) e
          com provedores de infraestrutura (banco de dados, e-mail). Não vendemos dados pessoais.
        </Section>

        <Section title="6. Seus direitos (LGPD)">
          Você pode solicitar acesso, correção, portabilidade ou <strong>eliminação</strong> dos seus dados pessoais.
          Mediante solicitação, anonimizamos/removemos seus dados pessoais da base (mantendo apenas o registro financeiro
          mínimo exigido por lei, já sem identificação). Para exercer seus direitos, contate a loja onde realizou a compra
          ou a agência pelo e-mail abaixo.
        </Section>

        <Section title="7. Contato">
          Encarregado de dados (DPO): <span className="text-slate-200">privacidade@noroia.com</span> (ajuste para o seu contato).
        </Section>
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-6">
      <h2 className="text-sm font-semibold text-white">{title}</h2>
      <p className="text-sm text-slate-400 mt-1.5 leading-relaxed">{children}</p>
    </section>
  )
}
