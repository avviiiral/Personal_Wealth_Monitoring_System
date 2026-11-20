import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';

export interface MutualFundResearchReportResponse {
  available: boolean;
  message?: string;
  id?: number;
  report_key?: string;
  report_date?: string;
  report_cycle?: string;
  source_file?: string;
  imported_at?: string;
  payload?: {
    RAW: any;
    P2P: any;
    ATTR: any;
  };
}

export interface MutualFundResearchUniverseOption {
  id: number;
  report_key: string;
  report_date: string;
  report_cycle: string;
  source_file: string;
  category: string;
  fund_count: number;
  imported_at: string;
}

export interface MutualFundResearchUniversesResponse {
  available: boolean;
  count: number;
  results: MutualFundResearchUniverseOption[];
  message?: string;
}

export interface IndiaMutualFundFeedRecord {
  fund_name: string;
  asset_class: string | null;
  category: string | null;
  latest_nav: number | null;
  nav_date?: string | null;
  percentage_change: number | null;
  asset_size: number | null;
  returns: Record<string, number | null>;
  star_rating: number | null;
  scheme_code?: string | null;
  isin_growth?: string | null;
  isin_dividend?: string | null;
}

export interface IndiaMutualFundFeedResponse {
  available: boolean;
  provider_configured: boolean;
  source: 'IndianAPI' | 'AMFI';
  source_label: string;
  report_date: string | null;
  imported_at: string | null;
  count: number;
  total_count: number;
  message: string;
  funds: IndiaMutualFundFeedRecord[];
}

export interface IndianApiUsageResponse {
  month: string;
  monthly_budget: number;
  requests_used: number;
  requests_remaining: number;
  last_request_timestamp: string | null;
  last_refresh_status: string;
  last_refresh_at: string | null;
  last_refresh_error: string;
  budget_exhausted: boolean;
  provider_configured: boolean;
  data_last_updated_at: string | null;
  data_report_date: string | null;
}

export interface IndianApiRefreshResponse {
  refreshed: boolean;
  cached?: boolean;
  created?: boolean;
  message: string;
  data_last_updated_at?: string | null;
  usage: IndianApiUsageResponse;
}

export interface PortfolioAllocationRow {
  name: string;
  market_value: string;
  weight_pct: string | null;
}

export interface PersonalMutualFundHolding {
  scheme_id: number;
  scheme_name: string;
  amc_name: string | null;
  category: string;
  plan: string | null;
  option: string | null;
  units: string;
  nav: string | null;
  nav_date: string | null;
  valuation_source: string;
  invested_value: string;
  market_value: string;
  unrealized_gain: string;
  unrealized_gain_pct: string | null;
  xirr_pct: number | null;
}

export interface PersonalMutualFundPortfolioResponse {
  available: boolean;
  family_id: number;
  as_of: string | null;
  disclosure_as_of: string | null;
  source?: string;
  summary: {
    holdings_count: number;
    invested_value: string;
    market_value: string;
    unrealized_gain: string;
    unrealized_gain_pct: string | null;
    gross_purchase_outflow: string;
    redemption_proceeds: string;
    dividend_income: string;
    simple_total_return_pct: string | null;
    xirr_pct: number | null;
  };
  holdings: PersonalMutualFundHolding[];
  allocations: {
    categories: PortfolioAllocationRow[];
    amcs: PortfolioAllocationRow[];
    sectors: PortfolioAllocationRow[];
    top_stocks: PortfolioAllocationRow[];
  };
  message: string;
}

export interface MutualFundAnalyticsResponse {
  filters: {
    category: string | null;
    provider: string | null;
    plan: string | null;
    ranking_period: string;
    categories: string[];
    providers: string[];
  };
  summary: {
    fund_count: number;
    funds_with_metrics: number;
    aum: number | string;
    metrics_as_of: string | null;
    average_returns: Record<string, {
      average_return: number | string | null;
      covered_funds: number;
      total_funds: number;
    }>;
  };
  benchmark: {
    name: string | null;
    as_of?: string;
    periods?: Record<string, number | string | null>;
    available?: boolean;
    message?: string;
  };
  ranking_period: string;
  rankings: MutualFundAnalyticsFund[];
  funds: MutualFundAnalyticsFund[];
}

export interface MutualFundAnalyticsFund {
  id: number;
  name: string;
  provider: string | null;
  category: string | null;
  isin: string | null;
  scheme_code: string | null;
  plan: string | null;
  option: string | null;
  benchmark: string | null;
  aum: number | string | null;
  expense_ratio: number | string | null;
  as_of: string | null;
  source: string | null;
  returns: Record<string, number | string | null>;
}

export interface SebiCategorySummaryRow {
  Level_1_Branch?: string;
  Level_2_Group?: string;
  Level_3_Category?: string;
  Plan?: string;
  Fund_Count?: number | string;
  Category_Avg_CAGR_1Y_pct?: number | string | null;
  Category_Avg_CAGR_3Y_pct?: number | string | null;
  Category_Median_CAGR_3Y_pct?: number | string | null;
  Category_Avg_CAGR_5Y_pct?: number | string | null;
  Category_Avg_Rolling_3Y_CAGR_Mean_pct?: number | string | null;
  Category_Avg_Annualized_Volatility_pct?: number | string | null;
  Category_Avg_Sharpe_Ratio?: number | string | null;
  Category_Avg_Max_Drawdown_pct?: number | string | null;
  [field: string]: unknown;
}

export interface SebiFundRankingRow {
  Scheme_Name?: string;
  Level_3_Category?: string;
  Plan?: string;
  CAGR_1Y_pct?: number | string | null;
  CAGR_3Y_pct?: number | string | null;
  CAGR_5Y_pct?: number | string | null;
  Rolling_3Y_CAGR_Mean_pct?: number | string | null;
  Annualized_Volatility_pct?: number | string | null;
  Sharpe_Ratio?: number | string | null;
  Sortino_Ratio?: number | string | null;
  Max_Drawdown_pct?: number | string | null;
  Max_Drawdown_Recovery_Days?: number | string | null;
  Category_Avg_CAGR_3Y_pct?: number | string | null;
  Outperformance_bps?: number | string | null;
  Percentile_Rank_3Y?: number | string | null;
  Quartile?: string | null;
  Beat_Category_Avg?: boolean | null;
  Expense_Ratio_pct?: number | string | null;
  [field: string]: unknown;
}

export interface SebiUniverseSummaryResponse {
  available: boolean;
  message?: string;
  generated_at?: string;
  source?: string;
  plans_processed?: string[];
  risk_free_rate_pct?: number;
  schemes_requested?: number;
  funds_with_history?: number;
  funds_without_history?: number;
  mfapi_cache_hits?: number;
  categories_count?: number;
  category_tree_nodes?: number;
  current_sebi_leaf_categories?: number;
  portfolio_overlap_pairs?: number;
  tree_summary?: SebiCategorySummaryRow[];
  top_q1_funds?: SebiFundRankingRow[];
  download_url?: string;
  workbook_path?: string;
  category_risk_chart?: string;
  top_q1_chart?: string;
  fetch_errors?: Array<{ scheme_code: string; error: string }>;
}

export interface SebiUniverseRankingsResponse {
  available: boolean;
  message?: string;
  count: number;
  page?: number;
  page_size?: number;
  results: SebiFundRankingRow[];
}

@Injectable({ providedIn: 'root' })
export class MutualFundAnalyticsService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiUrl}/api/mutual-fund-analytics/`;

  getResearchReport(reportId?: number): Observable<MutualFundResearchReportResponse> {
    let params = new HttpParams();
    if (reportId !== undefined && reportId !== null) {
      params = params.set('id', String(reportId));
    }
    return this.http.get<MutualFundResearchReportResponse>(
      `${this.baseUrl}research-report/`,
      { params, withCredentials: true },
    );
  }

  getResearchUniverses(): Observable<MutualFundResearchUniversesResponse> {
    return this.http.get<MutualFundResearchUniversesResponse>(
      `${this.baseUrl}research-universes/`,
      { withCredentials: true },
    );
  }

  getIndiaMarketFeed(): Observable<IndiaMutualFundFeedResponse> {
    return this.http.get<IndiaMutualFundFeedResponse>(
      `${this.baseUrl}india-market-feed/`,
      { withCredentials: true },
    );
  }

  getOverview(filters: {
    category?: string;
    provider?: string;
    plan?: string;
    benchmark?: string;
    ranking_period?: string;
  }): Observable<MutualFundAnalyticsResponse> {
    let params = new HttpParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        params = params.set(key, value);
      }
    });
    return this.http.get<MutualFundAnalyticsResponse>(`${this.baseUrl}overview/`, {
      params,
      withCredentials: true,
    });
  }

  getApiUsage(): Observable<IndianApiUsageResponse> {
    return this.http.get<IndianApiUsageResponse>(`${this.baseUrl}api-usage/`, { withCredentials: true });
  }

  refreshIndiaMarketFeed(): Observable<IndianApiRefreshResponse> {
    return this.http.post<IndianApiRefreshResponse>(`${this.baseUrl}refresh/`, {}, { withCredentials: true });
  }

  getSebiUniverseSummary(): Observable<SebiUniverseSummaryResponse> {
    return this.http.get<SebiUniverseSummaryResponse>(
      `${this.baseUrl}sebi/summary/`,
      { withCredentials: true },
    );
  }

  getSebiUniverseRankings(filters: {
    category?: string;
    plan?: string;
    quartile?: string;
    page?: number;
    page_size?: number;
  } = {}): Observable<SebiUniverseRankingsResponse> {
    let params = new HttpParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        params = params.set(key, String(value));
      }
    });
    return this.http.get<SebiUniverseRankingsResponse>(
      `${this.baseUrl}sebi/rankings/`,
      { params, withCredentials: true },
    );
  }

  getSebiWorkbookUrl(): string {
    return `${this.baseUrl}sebi/export/`;
  }

  getSebiCategoryRiskChart(): Observable<Blob> {
    return this.http.get(`${this.baseUrl}sebi/charts/category-risk/`, {
      responseType: 'blob',
      withCredentials: true,
    });
  }

  getSebiTopQ1Chart(): Observable<Blob> {
    return this.http.get(`${this.baseUrl}sebi/charts/top-q1/`, {
      responseType: 'blob',
      withCredentials: true,
    });
  }

  getPersonalPortfolio(): Observable<PersonalMutualFundPortfolioResponse> {
    return this.http.get<PersonalMutualFundPortfolioResponse>(
      `${environment.apiUrl}/api/mutual-funds/analytics/portfolio/`,
      { withCredentials: true },
    );
  }
}
