import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';

export interface MISIPSRow {
  asset_class: string;
  family_values: Record<string, number>;
  grand_total: number;
  prior_family_values: Record<string, number>;
  prior_total: number;
  difference: number;
}

export interface MISDataSheetRow {
  asset_name: string;
  family_name: string;
  asset_class: string;
  sub_class: string;
  advisor: string;
  qty_units: number;
  rate: number;
  total_cost: number;
  opening_units: number;
  opening_nav: number | null;
  opening_amount: number;
  transaction_units: number;
  transaction_nav: number | null;
  transaction_amount: number;
  closing_units: number;
  closing_nav: number | null;
  closing_amount: number;
}

export interface MISFundSummaryRow {
  fund_type: string;
  rows: Array<{
    fund_name: string;
    total: number;
    market_value_label: string;
    asset_class: string;
    asset_name: string;
  }>;
  subtotal: number;
}

export interface MISReport {
  family_name: string;
  reporting_date: string;
  opening_date: string;
  prior_month_date: string;
  period_start: string;
  family_names: string[];
  ips: MISIPSRow[];
  data_sheet: MISDataSheetRow[];
  fund_type_summary: MISFundSummaryRow[];
  summary: {
    total_current_value: number;
    number_of_rows: number;
  };
}

@Injectable({ providedIn: 'root' })
export class MISReportService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = environment.apiUrl;

  getReport(fromDate?: string, toDate?: string): Observable<MISReport> {
    const params: Record<string, string> = {};
    if (fromDate) params['from_date'] = fromDate;
    if (toDate) params['to_date'] = toDate;
    return this.http.get<MISReport>(this.baseUrl + '/api/portfolio/mis-report/', {
      withCredentials: true,
      params,
    });
  }

  downloadReport(fromDate?: string, toDate?: string): Observable<Blob> {
    const params: Record<string, string> = {};
    if (fromDate) params['from_date'] = fromDate;
    if (toDate) params['to_date'] = toDate;
    return this.http.get(this.baseUrl + '/api/portfolio/mis-report/download/', {
      responseType: 'blob',
      withCredentials: true,
      params,
    });
  }
}
