import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http';
import { Observable, switchMap } from 'rxjs';

import { environment } from '../../environments/environment';

export type MfPlan = 'Regular' | 'Direct';
export type MfHorizon = '1Y' | '3Y' | '5Y' | '10Y';
export type MfNumberMap = Record<string, number | null>;

export interface MfCategorySummary {
  slug: string;
  name: string;
  asset_class: string;
  total_schemes: number;
  funds: { Regular: number; Direct: number; Other: number };
  single_plan: boolean;
  metrics_synced_at: string | null;
  nav_synced_schemes: number;
  last_nav_sync: string | null;
  last_holdings_sync: string | null;
  default_benchmark: string;
  has_data: boolean;
}

export interface MfSyncRun {
  id: number;
  status: 'running' | 'success' | 'partial' | 'failed';
  trigger: string;
  started_at: string;
  finished_at: string | null;
  current_step: string;
  scope: Record<string, unknown>;
  stats: Record<string, number | string>;
  errors: string[];
  error_count: number;
}

export interface MfCategoriesResponse {
  groups: Array<{ asset_class: string; categories: MfCategorySummary[] }>;
  last_sync: MfSyncRun | null;
  today: string;
}

export interface MfFixedIncome {
  weight_pct: number | null;
  count?: number;
  sovereign_pct: number | null;
  corporate_pct: number | null;
  cash_equivalent_pct?: number | null;
  money_market_pct: number | null;
  avg_maturity_years: number | null;
  maturity_coverage_pct?: number | null;
  maturity_buckets?: Record<string, number>;
}

export interface MfHoldingsSummary {
  as_of: string;
  prev_as_of: string | null;
  total: number;
  stocks: number;
  top10_pct: number | null;
  equity_pct: number | null;
  debt_pct: number | null;
  fund_unit_pct: number | null;
  commodity_pct: number | null;
  cash_pct: number | null;
  net_equity_pct: number | null;
  derivatives_net_pct: number | null;
  sectors: Record<string, number>;
  style: MfNumberMap | null;
  cap: MfNumberMap | null;
  changes: { new: number | null; exits: number | null; increased: number | null; decreased: number | null; unchanged: number | null };
  fixed_income: MfFixedIncome | null;
}

export interface MfScore {
  total: number | null;
  components: MfNumberMap;
  rank?: number;
}

export interface MfFund {
  code: string;
  name: string;
  short_name: string;
  amc: string;
  plan: string;
  option: string;
  isin: string;
  age_label: string;
  age_months: number | null;
  first_nav_date: string | null;
  aum: number | null;
  aum_period: string;
  ter: number | null;
  nav: number | null;
  nav_date: string | null;
  all_time_mdd: number | null;
  returns: MfNumberMap;
  calendar: MfNumberMap;
  metrics: Partial<Record<MfHorizon, MfNumberMap>>;
  calmar: Partial<Record<MfHorizon, number | null>>;
  holdings: MfHoldingsSummary | null;
  holdings_from_code: string | null;
  unique: { count: number; weight_pct: number; share_of_equity_pct: number | null };
  score: MfScore;
}

export interface MfBenchmark {
  slug: string;
  name: string;
  asset_type: string;
  latest_date: string | null;
  returns: MfNumberMap;
  calendar: MfNumberMap;
}

export interface MfFlowItem {
  isin: string;
  name: string;
  industry: string;
  holders: number;
  new: Array<{ fund: string; pct: number | null }>;
  exited: Array<{ fund: string; prev_pct: number | null }>;
  increased: number;
  decreased: number;
  total_change_pct: number;
  buyers: number;
  sellers: number;
  net_funds: number;
}

export interface MfCategoryAverage {
  aum: number | null;
  ter: number | null;
  age_months: number | null;
  all_time_mdd: number | null;
  returns: MfNumberMap;
  calendar: MfNumberMap;
  metrics: Partial<Record<MfHorizon, MfNumberMap>>;
  calmar: Partial<Record<MfHorizon, number | null>>;
  score: MfScore;
  holdings: (Record<string, any> & {
    sectors: Record<string, number>;
    style: MfNumberMap;
    cap: MfNumberMap;
    fixed_income: MfNumberMap;
  }) | null;
}

export interface MfDashboard {
  category: {
    slug: string;
    name: string;
    asset_class: string;
    peer_count_all_plans: number | null;
    metrics_as_of: string | null;
    metrics_synced_at: string | null;
    total_schemes: number;
  };
  plan: MfPlan | null;
  plans: MfPlan[];
  horizon: MfHorizon;
  horizons: MfHorizon[];
  as_of: { nav: string | null; metrics: string | null; holdings: string | null };
  benchmark: MfBenchmark | null;
  benchmarks: Array<{ slug: string; name: string; asset_type: string }>;
  calendar_years: string[];
  funds: MfFund[];
  category_average: MfCategoryAverage;
  category_median_all_plans: Partial<Record<MfHorizon, MfNumberMap>>;
  activity: {
    new_entries: MfFlowItem[];
    full_exits: MfFlowItem[];
    net_buys: MfFlowItem[];
    net_sells: MfFlowItem[];
    sector_change: Array<{ sector: string; total_change_pct: number }>;
  };
  cap_classification: { amfi_period_end: string | null; amfi_rows: number; index_proxy_rows: number; method: 'AMFI' | 'INDEX_PROXY' | null };
  flags: { has_holdings: boolean; has_equity: boolean; has_fixed_income: boolean; has_returns: boolean; has_metrics: boolean };
  score_methodology: Array<{ key: string; weight: number; source: string; higher_is_better: boolean }>;
  sector_styles: Record<string, string>;
  last_sync: MfSyncRun | null;
}

export interface MfHoldingRow {
  isin: string;
  name: string;
  industry: string;
  type: string;
  pct: number | null;
  prev_pct: number | null;
  change_pct: number | null;
  status: string;
  cap?: string;
  style?: string;
  issuer_type?: string;
  maturity?: string | null;
  years_to_maturity?: number | null;
}

export interface MfSchemeDetail {
  scheme: Omit<MfFund, 'metrics' | 'calmar' | 'holdings' | 'holdings_from_code' | 'unique' | 'score' | 'age_months'> & {
    category: { slug: string; name: string; asset_class: string } | null;
    ter_date: string | null;
  };
  metrics: Partial<Record<MfHorizon, MfNumberMap>>;
  category_median_all_plans: Partial<Record<MfHorizon, MfNumberMap>>;
  benchmark: MfBenchmark | null;
  growth: { months: string[]; fund: number[]; benchmark: number[]; benchmark_name: string | null } | null;
  aum_history: Array<{ date: string; period: string; aum: number | null }>;
  holdings: {
    as_of: string | null;
    prev_as_of: string | null;
    summary: MfHoldingsSummary | null;
    rows: MfHoldingRow[];
    exits: MfHoldingRow[];
  };
  holding_history: Array<{
    as_of: string;
    total: number;
    stocks: number;
    top10_pct: number | null;
    equity_pct: number | null;
    debt_pct: number | null;
    cash_pct: number | null;
    cap: MfNumberMap | null;
    style: MfNumberMap | null;
  }>;
  unique_stocks: MfHoldingRow[];
  peers_count: number;
  holdings_from_code: string | null;
}

export interface MfSyncStatus {
  running: boolean;
  run: MfSyncRun | null;
}

@Injectable({ providedIn: 'root' })
export class MfIntelligenceApiService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiUrl}/api/mf-intelligence`;

  getCategories(): Observable<MfCategoriesResponse> {
    return this.http.get<MfCategoriesResponse>(`${this.baseUrl}/categories/`, { withCredentials: true });
  }

  getDashboard(slug: string, options: { plan?: MfPlan | null; horizon?: MfHorizon; benchmark?: string | null } = {}): Observable<MfDashboard> {
    let params = new HttpParams();
    if (options.plan) params = params.set('plan', options.plan);
    if (options.horizon) params = params.set('horizon', options.horizon);
    if (options.benchmark) params = params.set('benchmark', options.benchmark);
    return this.http.get<MfDashboard>(`${this.baseUrl}/categories/${encodeURIComponent(slug)}/dashboard/`, {
      params,
      withCredentials: true,
    });
  }

  getScheme(code: string, benchmark?: string | null): Observable<MfSchemeDetail> {
    const params = benchmark ? new HttpParams().set('benchmark', benchmark) : undefined;
    return this.http.get<MfSchemeDetail>(`${this.baseUrl}/schemes/${encodeURIComponent(code)}/`, {
      params,
      withCredentials: true,
    });
  }

  getSyncStatus(): Observable<MfSyncStatus> {
    return this.http.get<MfSyncStatus>(`${this.baseUrl}/sync/status/`, { withCredentials: true });
  }

  startSync(body: { category?: string | null; plan?: 'Regular' | 'Direct' | 'Both'; force?: boolean }): Observable<{ status: string; run: MfSyncRun | null }> {
    return this.http.get<{ csrf_token?: string }>(`${environment.apiUrl}/api/health/`, { withCredentials: true }).pipe(
      switchMap((health) => {
        const token = health.csrf_token || this.readCsrfToken();
        if (!token) {
          throw new Error('CSRF token was not provided by the backend.');
        }
        return this.http.post<{ status: string; run: MfSyncRun | null }>(`${this.baseUrl}/sync/`, body, {
          withCredentials: true,
          headers: new HttpHeaders({ 'X-CSRFToken': token }),
        });
      }),
    );
  }

  private readCsrfToken(): string | null {
    if (typeof document === 'undefined') return null;
    const match = document.cookie.split('; ').find((cookie) => cookie.startsWith('csrftoken='));
    return match ? decodeURIComponent(match.substring('csrftoken='.length)) : null;
  }
}
