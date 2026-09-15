import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';

export interface WatchListProduct {
  id: number;
  product_type: 'MUTUAL_FUND' | 'PMS';
  name: string;
  provider: string | null;
  country: string | null;
  category: string | null;
  sub_category: string | null;
  isin: string | null;
  external_identifier: string | null;
  currency: string | null;
  source: string | null;
  source_reference: string | null;
  source_date: string | null;
  official_website: string | null;
  status: 'OWNED' | 'UNIVERSAL';
  ownership: Array<{
    family: string;
    portfolio: string;
    current_value: number;
    invested_value: number;
    quantity: number;
    current_value_per_unit: number;
    return_percent: number | null;
    xirr: number | null;
    holding_status: string;
  }>;
  performance: any[];
  metrics: Record<string, number | null>;
  mutual_fund: any | null;
  pms: any | null;
}

export interface WatchListResponse {
  count: number;
  next: string | null;
  previous: string | null;
  results: WatchListProduct[];
}

@Injectable({ providedIn: 'root' })
export class WatchListApiService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiUrl}/watch-list`;

  getProducts(filters: Record<string, string | number | undefined>): Observable<WatchListResponse> {
    let params = new HttpParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') params = params.set(key, String(value));
    });
    return this.http.get<WatchListResponse>(`${this.baseUrl}/products/`, { params, withCredentials: true });
  }

  getPerformance(productId: number): Observable<any> {
    return this.http.get(`${this.baseUrl}/products/${productId}/performance/`, { withCredentials: true });
  }

  refresh(): Observable<any> {
    return this.http.post(`${this.baseUrl}/refresh/`, {}, { withCredentials: true });
  }
}
