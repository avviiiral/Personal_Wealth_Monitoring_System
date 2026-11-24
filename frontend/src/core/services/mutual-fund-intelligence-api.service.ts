import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';

export interface MutualFundIntelligenceResponse {
  count: number;
  next: string | null;
  previous: string | null;
  results: {
    summary: Record<string, number | string | null>;
    filters: { amcs: string[]; categories: string[]; plans: string[]; options: string[] };
    data_sources: Array<{
      domain: string; status: string; completed_at: string | null;
      records_fetched: number; inserted: number; updated: number;
      skipped: number; failed: number; unmatched: number;
    }>;
    results: any[];
  };
}

export interface MutualFundDetailResponse {
  scheme: { id: number; scheme_code: string; scheme_name: string; amc: string; isin_growth: string | null; isin_idcw: string | null; category: string; plan: string; option: string };
  snapshot: Record<string, any> | null;
  nav_history: Array<{ date: string; nav: number }>;
  aum_history: Array<{ date: string; aum_cr: number; type: string }>;
  ter_history: Array<{ date: string; direct_pct: number | null; regular_pct: number | null }>;
  holdings: Array<Record<string, any>>;
  holdings_report_date: string | null;
  provider_metrics: Record<string, any> | null;
  holdings_changes: any;
  data_availability: Record<string, boolean | string | null>;
  holdings_periods: Array<string | { date?: string; as_of?: string; report_date?: string; holdings_count?: number }>;
}

@Injectable({ providedIn: 'root' })
export class MutualFundIntelligenceApiService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = environment.apiUrl + '/api/mutual-fund-analytics/charliealpha';

  getOverview(filters: Record<string, string | number | undefined> = {}): Observable<MutualFundIntelligenceResponse> {
    let params = new HttpParams();
    Object.entries(filters).forEach(([name, value]) => {
      if (value !== undefined && value !== null && value !== '') params = params.set(name, String(value));
    });
    return this.http.get<MutualFundIntelligenceResponse>(this.baseUrl + '/overview/', { params, withCredentials: true });
  }

  getFund(schemeCode: string, options: { navLimit?: number; fromDate?: string; toDate?: string; holdingsAsOf?: string } = {}): Observable<MutualFundDetailResponse> {
    let params = new HttpParams().set('nav_limit', String(options.navLimit ?? 1000));
    if (options.fromDate) params = params.set('from_date', options.fromDate);
    if (options.toDate) params = params.set('to_date', options.toDate);
    if (options.holdingsAsOf) params = params.set('holdings_as_of', options.holdingsAsOf);
    return this.http.get<MutualFundDetailResponse>(this.baseUrl + '/funds/' + encodeURIComponent(schemeCode) + '/', { params, withCredentials: true });
  }

  getBenchmarks(slug = ''): Observable<{ count: number; results: any[] }> {
    let params = new HttpParams();
    if (slug) params = params.set('slug', slug);
    return this.http.get<{ count: number; results: any[] }>(this.baseUrl + '/benchmarks/', { params, withCredentials: true });
  }

  getHoldingsPeriods(schemeCode = ''): Observable<any> {
    const path = schemeCode ? '/funds/' + encodeURIComponent(schemeCode) + '/holdings-periods/' : '/holdings-periods/';
    return this.http.get(this.baseUrl + path, { withCredentials: true });
  }

  getExposure(identifier: string, minPct = ''): Observable<any> {
    let params = new HttpParams();
    if (minPct) params = params.set('min_pct', minPct);
    return this.http.get(this.baseUrl + '/exposure/' + encodeURIComponent(identifier) + '/', { params, withCredentials: true });
  }

  getCategoryMetrics(slug: string): Observable<any> {
    return this.http.get(this.baseUrl + '/categories/' + encodeURIComponent(slug) + '/', { withCredentials: true });
  }

  compare(schemeCodes: string[], overlapOnly = false): Observable<any> {
    return this.http.post(this.baseUrl + '/compare/', { scheme_codes: schemeCodes, overlap_only: overlapOnly }, { withCredentials: true });
  }
}