-- =============================================================================
-- PROJETO 2: Tracking com IA para Ecommerce
-- Arquivo: supabase/migrations/001_initial.sql
-- Versão: v2.0
-- =============================================================================

CREATE TABLE public.agencies (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  name TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE public.agency_members (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  agency_id UUID REFERENCES public.agencies(id) ON DELETE CASCADE,
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  role TEXT DEFAULT 'member' CHECK (role IN ('owner','admin','member')),
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(agency_id, user_id)
);

CREATE TABLE public.clients (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  agency_id UUID REFERENCES public.agencies(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  ecommerce_platform TEXT NOT NULL
    CHECK (ecommerce_platform IN ('shopify','nuvemshop','woocommerce')),
  shopify_domain TEXT,
  shopify_access_token TEXT,
  shopify_webhook_secret TEXT,
  nuvemshop_store_id TEXT,
  nuvemshop_access_token TEXT,
  woo_store_url TEXT,
  woo_consumer_key TEXT,
  woo_consumer_secret TEXT,
  woo_webhook_secret TEXT,
  webhooks_configured BOOLEAN DEFAULT false,
  pixel_id TEXT UNIQUE DEFAULT gen_random_uuid()::TEXT,
  tracking_cname TEXT,
  tracking_cname_verified BOOLEAN DEFAULT false,
  meta_pixel_id TEXT,
  meta_access_token TEXT,
  meta_ad_account_id TEXT,
  google_ads_customer_id TEXT,
  google_ads_conversion_action TEXT,
  slack_webhook_url TEXT,
  alert_email TEXT,
  monthly_revenue NUMERIC(12,2) DEFAULT 0,
  monthly_ad_spend NUMERIC(12,2) DEFAULT 0,
  monthly_roas NUMERIC(5,2) DEFAULT 0,
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE public.visitors (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  client_id UUID REFERENCES public.clients(id) ON DELETE CASCADE,
  visitor_id TEXT NOT NULL,
  email TEXT,
  phone TEXT,
  platform_customer_id TEXT,
  first_seen_at TIMESTAMPTZ DEFAULT now(),
  last_seen_at TIMESTAMPTZ DEFAULT now(),
  first_utm_source TEXT,
  first_utm_medium TEXT,
  first_utm_campaign TEXT,
  first_utm_content TEXT,
  first_platform TEXT,
  fbclid TEXT,
  gclid TEXT,
  total_pageviews INT DEFAULT 0,
  total_orders INT DEFAULT 0,
  total_revenue NUMERIC(12,2) DEFAULT 0,
  ltv NUMERIC(12,2) DEFAULT 0,
  lead_score INT DEFAULT 0 CHECK (lead_score BETWEEN 0 AND 100),
  lead_quality_score INT DEFAULT 0 CHECK (lead_quality_score BETWEEN 0 AND 100),
  UNIQUE(client_id, visitor_id)
);

CREATE TABLE public.orders (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  client_id UUID REFERENCES public.clients(id) ON DELETE CASCADE,
  visitor_id UUID REFERENCES public.visitors(id),
  platform_order_id TEXT NOT NULL,
  platform_order_number TEXT,
  platform_source TEXT NOT NULL
    CHECK (platform_source IN ('shopify','nuvemshop','woocommerce')),
  email TEXT,
  phone TEXT,
  total_price NUMERIC(12,2),
  currency TEXT DEFAULT 'BRL',
  financial_status TEXT,
  fulfillment_status TEXT,
  utm_source TEXT,
  utm_medium TEXT,
  utm_campaign TEXT,
  utm_content TEXT,
  ad_id TEXT,
  campaign_id TEXT,
  platform TEXT,
  is_first_purchase BOOLEAN DEFAULT false,
  is_repeat_purchase BOOLEAN DEFAULT false,
  capi_sent BOOLEAN DEFAULT false,
  capi_sent_at TIMESTAMPTZ,
  capi_lead_quality_score INT,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(client_id, platform_order_id)
);

CREATE TABLE public.ai_insights (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  client_id UUID REFERENCES public.clients(id) ON DELETE CASCADE,
  type TEXT CHECK (type IN ('weekly_report','anomaly','recommendation','pattern','creative_analysis')),
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  severity TEXT DEFAULT 'info' CHECK (severity IN ('info','warning','critical')),
  data JSONB,
  is_read BOOLEAN DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE public.alerts (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  client_id UUID REFERENCES public.clients(id) ON DELETE CASCADE,
  type TEXT CHECK (type IN ('cpa_spike','roas_drop','creative_paused','budget_exhausted','conversion_drop','anomaly','refund_spike')),
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  data JSONB,
  sent_via TEXT[],
  is_resolved BOOLEAN DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE public.ad_campaigns (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  client_id UUID REFERENCES public.clients(id) ON DELETE CASCADE,
  platform TEXT NOT NULL,
  campaign_id TEXT NOT NULL,
  campaign_name TEXT,
  adset_id TEXT,
  adset_name TEXT,
  ad_id TEXT,
  ad_name TEXT,
  creative_url TEXT,
  spend NUMERIC(12,2) DEFAULT 0,
  impressions INT DEFAULT 0,
  clicks INT DEFAULT 0,
  conversions INT DEFAULT 0,
  revenue NUMERIC(12,2) DEFAULT 0,
  roas NUMERIC(8,2) DEFAULT 0,
  avg_lead_quality_score INT,
  avg_ltv NUMERIC(12,2),
  repeat_purchase_rate NUMERIC(5,2),
  date DATE NOT NULL,
  synced_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(client_id, platform, ad_id, date)
);

ALTER TABLE public.agencies ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agency_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.visitors ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_insights ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ad_campaigns ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.get_user_agency_ids()
RETURNS SETOF UUID AS $$
  SELECT agency_id FROM public.agency_members WHERE user_id = auth.uid()
$$ LANGUAGE sql SECURITY DEFINER STABLE;

CREATE OR REPLACE FUNCTION public.get_user_client_ids()
RETURNS SETOF UUID AS $$
  SELECT id FROM public.clients WHERE agency_id IN (SELECT public.get_user_agency_ids())
$$ LANGUAGE sql SECURITY DEFINER STABLE;

CREATE POLICY agency_read ON public.agencies FOR SELECT
  USING (id IN (SELECT public.get_user_agency_ids()));
CREATE POLICY agency_update ON public.agencies FOR UPDATE
  USING (id IN (SELECT agency_id FROM public.agency_members WHERE user_id = auth.uid() AND role IN ('owner','admin')));
CREATE POLICY members_read ON public.agency_members FOR SELECT
  USING (agency_id IN (SELECT public.get_user_agency_ids()));
CREATE POLICY client_iso ON public.clients FOR ALL
  USING (agency_id IN (SELECT public.get_user_agency_ids()));
CREATE POLICY visitor_iso ON public.visitors FOR ALL
  USING (client_id IN (SELECT public.get_user_client_ids()));
CREATE POLICY order_iso ON public.orders FOR ALL
  USING (client_id IN (SELECT public.get_user_client_ids()));
CREATE POLICY insight_iso ON public.ai_insights FOR ALL
  USING (client_id IN (SELECT public.get_user_client_ids()));
CREATE POLICY alert_iso ON public.alerts FOR ALL
  USING (client_id IN (SELECT public.get_user_client_ids()));
CREATE POLICY campaign_iso ON public.ad_campaigns FOR ALL
  USING (client_id IN (SELECT public.get_user_client_ids()));

CREATE INDEX idx_agencies_slug ON public.agencies(slug);
CREATE INDEX idx_members_user ON public.agency_members(user_id);
CREATE INDEX idx_members_agency ON public.agency_members(agency_id);
CREATE INDEX idx_clients_agency ON public.clients(agency_id);
CREATE INDEX idx_clients_pixel ON public.clients(pixel_id);
CREATE INDEX idx_clients_platform ON public.clients(ecommerce_platform);
CREATE INDEX idx_visitors_client ON public.visitors(client_id);
CREATE INDEX idx_visitors_email ON public.visitors(client_id, email);
CREATE INDEX idx_visitors_visitor_id ON public.visitors(client_id, visitor_id);
CREATE INDEX idx_orders_client ON public.orders(client_id, created_at DESC);
CREATE INDEX idx_orders_platform ON public.orders(client_id, platform_source);
CREATE INDEX idx_orders_capi_pending ON public.orders(client_id, capi_sent) WHERE capi_sent = false;
CREATE INDEX idx_campaigns_client ON public.ad_campaigns(client_id, date DESC);
CREATE INDEX idx_insights_client ON public.ai_insights(client_id, created_at DESC);
CREATE INDEX idx_alerts_client ON public.alerts(client_id, is_resolved);

CREATE OR REPLACE FUNCTION public.update_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_agencies_updated BEFORE UPDATE ON public.agencies
  FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();
CREATE TRIGGER trg_clients_updated BEFORE UPDATE ON public.clients
  FOR EACH ROW EXECUTE FUNCTION public.update_updated_at();
