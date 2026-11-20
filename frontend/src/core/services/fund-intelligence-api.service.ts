import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';

export type PeriodKey = '1m' | '3m' | '6m' | '1y' | '3y' | '5y';
export type ReturnKey = PeriodKey | 'since_inception' | 'cagr';
export type ReturnMap = Record<ReturnKey, number | null>;

export interface FundLiveFields {
  fund_name: string | null;
  latest_nav: number | null;
  aum: number | null;
  star_rating: number | null;
  returns: Record<PeriodKey, number | null>;
  match_score: number;
}

export interface FundIntelligenceFund {
  product_id: number;
  name: string;
  short_name: string;
  provider: string | null;
  category: string | null;
  isin: string | null;
  plan: string | null;
  option: string | null;
  fund_manager: string | null;
  benchmark: string | null;
  aum: number | null;
  expense_ratio: number | null;
  exit_load: string | null;
  risk_level: string | null;
  minimum_investment: number | null;
  latest_nav: number | null;
  latest_nav_date: string | null;
  as_of: string | null;
  returns: ReturnMap;
  vs_category: ReturnMap;
  live: FundLiveFields | null;
}

export interface FundIntelligenceSnapshot {
  meta: {
    category: string;
    plan: string;
    option: string;
    fund_count: number;
    stored_data_as_of: string | null;
    generated_at: string;
  };
  category_average: ReturnMap;
  funds: FundIntelligenceFund[];
  live: {
    status: 'ok' | 'disabled' | 'not_configured' | 'error';
    message: string;
    fetched_at: string | null;
    records_received: number;
    matched: number;
    unmatched: string[];
  };
  data_coverage: {
    available: string[];
    not_available: Array<{ item: string; reason: string }>;
  };
}

@Injectable({ providedIn: 'root' })
export class FundIntelligenceApiService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiUrl}/api/fund-intelligence`;

  getSnapshot(includeLive = true): Observable<FundIntelligenceSnapshot> {
    return this.http.get<FundIntelligenceSnapshot>(`${this.baseUrl}/snapshot/`, {
      params: { live: includeLive ? '1' : '0' },
      withCredentials: true,
    });
  }
}
