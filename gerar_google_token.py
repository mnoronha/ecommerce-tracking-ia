"""
Gera um novo Google OAuth refresh token para o Google Ads API.
Rode com: python gerar_google_token.py

Precisa do GOOGLE_ADS_OAUTH_CLIENT_ID e GOOGLE_ADS_OAUTH_CLIENT_SECRET
que estão no Railway (Settings → Variables).
"""
import urllib.parse, sys

try:
    import httpx
except ImportError:
    print("Instalando httpx...")
    import subprocess; subprocess.check_call([sys.executable, "-m", "pip", "install", "httpx", "-q"])
    import httpx

print("=" * 60)
print("Gerador de Google Ads OAuth Refresh Token")
print("=" * 60)
print("\nPegue os valores em Railway → Variables\n")

client_id     = input("GOOGLE_ADS_OAUTH_CLIENT_ID: ").strip()
client_secret = input("GOOGLE_ADS_OAUTH_CLIENT_SECRET: ").strip()

if not client_id or not client_secret:
    print("❌ ID e secret são obrigatórios.")
    sys.exit(1)

SCOPES = " ".join([
    "https://www.googleapis.com/auth/adwords",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/content",
])
REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"

params = {
    "client_id":     client_id,
    "redirect_uri":  REDIRECT_URI,
    "scope":         SCOPES,
    "response_type": "code",
    "access_type":   "offline",
    "prompt":        "consent",
}

auth_url = "https://accounts.google.com/o/oauth2/auth?" + urllib.parse.urlencode(params)

print("\n" + "=" * 60)
print("Abra este link no navegador e autorize com o NOVO email Google:")
print("=" * 60)
print(f"\n{auth_url}\n")
print("IMPORTANTE: faça login com a conta que tem acesso ao MCC novo.")
print("Ao final, o Google vai mostrar um código de autorização.\n")

code = input("Cole o código de autorização aqui: ").strip()

resp = httpx.post(
    "https://oauth2.googleapis.com/token",
    data={
        "client_id":     client_id,
        "client_secret": client_secret,
        "code":          code,
        "redirect_uri":  REDIRECT_URI,
        "grant_type":    "authorization_code",
    },
    timeout=15.0,
)
data = resp.json()

if "refresh_token" in data:
    print("\n" + "=" * 60)
    print("✅ SUCESSO! Novo refresh token gerado:")
    print("=" * 60)
    print(f"\nREFRESH TOKEN:\n{data['refresh_token']}\n")
    print("Próximos passos:")
    print("1. Atualize GOOGLE_ADS_REFRESH_TOKEN no Railway com esse valor")
    print("2. Guarde o token — ele não aparece de novo")
    print("3. Informe o novo MCC ID (se mudou) para atualizar os clientes")
else:
    print("\n❌ Erro ao gerar token:")
    print(data)
    if "invalid_grant" in str(data):
        print("\nDica: o código expira em ~10 min. Gere um novo se demorou muito.")
