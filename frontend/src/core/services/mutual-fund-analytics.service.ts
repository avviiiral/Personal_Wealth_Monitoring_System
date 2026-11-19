import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';

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

@Injectable({ providedIn: 'root' })
export class MutualFundAnalyticsService {
  private readonly http = inject(HttpClient);
  private readonly url = `${environment.apiUrl}/api/watch-list/analytics/overview/`;

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
    return this.http.get<MutualFundAnalyticsResponse>(this.url, {
      params,
      withCredentials: true,
    });
  }
}
