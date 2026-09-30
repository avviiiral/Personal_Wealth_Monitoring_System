import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';

export interface MISHolding {
  family_name: string;
  portfolio: string;
  asset_class: string;
  sub_class: string;
  asset_name: string;
  asset_id: number | null;
  isin: string | null;
  symbol: string | null;
  quantity: number | null;
  average_cost: number | null;
  invested_value: number;
  current_price: number | null;
  current_value: number;
  pnl: number;
  pnl_percentage: number | null;
  xirr: number | null;
}

export interface MISAssetClassSummary {
  asset_class: string;
  invested_value: number;
  current_value: number;
  pnl: number;
  pnl_percentage: number;
}

export interface MISReport {
  family_name: string;
  reporting_date: string;
  summary: {
    total_invested: number;
    total_current_value: number;
    total_pnl: number;
    pnl_percentage: number;
    number_of_holdings: number;
  };
  asset_class_summary: MISAssetClassSummary[];
  holdings: MISHolding[];
}

@Injectable({ providedIn: 'root' })
export class MISReportService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = environment.apiUrl;

  getReport(): Observable<MISReport> {
    return this.http.get<MISReport>(this.baseUrl + '/api/portfolio/mis-report/', {
      withCredentials: true,
    });
  }

  downloadReport(): Observable<Blob> {
    return this.http.get(this.baseUrl + '/api/portfolio/mis-report/download/', {
      responseType: 'blob',
      withCredentials: true,
    });
  }
}
