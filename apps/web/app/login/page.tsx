import { redirect } from 'next/navigation'
import { cookies } from 'next/headers'

async function doLogin(formData: FormData) {
  'use server'
  const password = (formData.get('password') as string || '').trim()
  const correct  = process.env.DASHBOARD_PASSWORD

  if (!correct) throw new Error('DASHBOARD_PASSWORD env var not set')

  if (password === correct) {
    const jar = await cookies()
    jar.set('dash_auth', password, {
      httpOnly: true,
      secure:   process.env.NODE_ENV === 'production',
      sameSite: 'lax',
      maxAge:   60 * 60 * 24 * 30, // 30 days
      path:     '/',
    })
    const from = (formData.get('from') as string) || '/dashboard'
    redirect(from)
  }

  redirect('/login?error=1')
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; from?: string }>
}) {
  const params = await searchParams
  const hasError = params.error === '1'
  const from     = params.from || '/dashboard'

  return (
    <div className="min-h-screen bg-[#0f1117] flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-indigo-600/20 border border-indigo-500/30 mb-4">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#818cf8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
            </svg>
          </div>
          <h1 className="text-xl font-bold text-white">Ecommerce Tracking IA</h1>
          <p className="text-sm text-slate-500 mt-1">by Pareto Plus</p>
        </div>

        {/* Card */}
        <div className="bg-[#1a1f2e] rounded-2xl border border-[#2a2f3e] p-6">
          <h2 className="text-base font-semibold text-white mb-1">Acesso ao Dashboard</h2>
          <p className="text-xs text-slate-500 mb-5">Insira a senha de acesso fornecida pela Pareto Plus</p>

          <form action={doLogin}>
            <input type="hidden" name="from" value={from} />

            <div className="mb-4">
              <label htmlFor="password" className="block text-xs font-medium text-slate-400 mb-1.5">
                Senha
              </label>
              <input
                id="password"
                name="password"
                type="password"
                autoFocus
                autoComplete="current-password"
                placeholder="••••••••"
                className={`w-full bg-[#0f1117] border rounded-lg px-3 py-2.5 text-sm text-white placeholder-slate-600 outline-none transition-colors focus:border-indigo-500 ${
                  hasError ? 'border-red-500/60' : 'border-[#2a2f3e]'
                }`}
              />
              {hasError && (
                <p className="text-xs text-red-400 mt-1.5">Senha incorreta. Tente novamente.</p>
              )}
            </div>

            <button
              type="submit"
              className="w-full bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium py-2.5 rounded-lg transition-colors"
            >
              Entrar
            </button>
          </form>
        </div>

        <p className="text-center text-xs text-slate-600 mt-5">
          Pareto Plus © {new Date().getFullYear()}
        </p>
      </div>
    </div>
  )
}
