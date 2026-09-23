import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http';
import { Observable, switchMap } from 'rxjs';

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
  benchmark: 'BSE 500 TRI' | 'Nifty 50' | null;
  status: 'OWNED' | 'UNIVERSAL' | 'WATCHLIST';
  is_watchlisted: boolean;
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

export interface WatchListFilterOptions {
  providers: string[];
  categories: string[];
}

@Injectable({ providedIn: 'root' })
export class WatchListApiService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiUrl}/api/watch-list`;

  getProducts(filters: Record<string, string | number | undefined>): Observable<WatchListResponse> {
    let params = new HttpParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        params = params.set(key, String(value));
      }
    });
    return this.http.get<WatchListResponse>(`${this.baseUrl}/products/`, {
      params,
      withCredentials: true,
    });
  }

  getFilters(productType: 'MUTUAL_FUND' | 'PMS'): Observable<WatchListFilterOptions> {
    return this.http.get<WatchListFilterOptions>(`${this.baseUrl}/filters/`, {
      params: new HttpParams().set('product_type', productType),
      withCredentials: true,
    });
  }

  updateBenchmark(productId: number, benchmark: 'BSE 500 TRI' | 'Nifty 50'): Observable<{ id: number; benchmark: string }> {
    return this.patchWithCsrf<{ id: number; benchmark: string }>(
      `${this.baseUrl}/products/${productId}/benchmark/`,
      { benchmark },
    );
  }

  getBenchmarkPerformance(productId: number, period: '1M' | '3M' | '6M' | '1Y' | '3Y' | '5Y' = '1Y'): Observable<any> {
    return this.http.get(
      `${this.baseUrl}/products/${productId}/benchmark-performance/`,
      {
        params: new HttpParams().set('period', period),
        withCredentials: true,
      },
    );
  }

  getPerformance(productId: number): Observable<any> {
    return this.http.get(`${this.baseUrl}/products/${productId}/performance/`, {
      withCredentials: true,
    });
  }

  refresh(): Observable<any> {
    return this.http.post(`${this.baseUrl}/refresh/`, {}, { withCredentials: true });
  }

  toggleWatch(productId: number): Observable<{ id: number; is_watchlisted: boolean }> {
    return this.http.post<{ id: number; is_watchlisted: boolean }>(
      `${this.baseUrl}/products/${productId}/toggle/`,
      {},
      { withCredentials: true },
    );
  }

  bulkAddToWatchList(productIds: number[]): Observable<{
    selected: number;
    added: number;
    already_watchlisted: number;
  }> {
    return this.postWithCsrf<{
      selected: number;
      added: number;
      already_watchlisted: number;
    }>(`${this.baseUrl}/bulk-add/`, { product_ids: productIds });
  }

  bulkRemoveFromWatchList(productIds: number[]): Observable<{
    selected: number;
    removed: number;
  }> {
    return this.postWithCsrf<{
      selected: number;
      removed: number;
    }>(`${this.baseUrl}/bulk-remove/`, { product_ids: productIds });
  }

  private patchWithCsrf<T>(url: string, body: unknown): Observable<T> {
    return this.http.get(`${environment.apiUrl}/api/health/`, {
      withCredentials: true,
      responseType: 'json',
    }).pipe(
      switchMap(() => {
        const token = this.getCsrfToken();
        const headers = token ? new HttpHeaders({ 'X-CSRFToken': token }) : undefined;
        return this.http.patch<T>(url, body, {
          withCredentials: true,
          headers,
        });
      }),
    );
  }

  private postWithCsrf<T>(url: string, body: unknown): Observable<T> {
    return this.http.get(`${environment.apiUrl}/api/health/`, {
      withCredentials: true,
      responseType: 'json',
    }).pipe(
      switchMap(() => {
        const token = this.getCsrfToken();
        const headers = token ? new HttpHeaders({ 'X-CSRFToken': token }) : undefined;
        return this.http.post<T>(url, body, {
          withCredentials: true,
          headers,
        });
      }),
    );
  }

  private getCsrfToken(): string | null {
    if (typeof document === 'undefined') return null;
    const match = document.cookie
      .split('; ')
      .find(cookie => cookie.startsWith('csrftoken='));
    return match ? decodeURIComponent(match.substring('csrftoken='.length)) : null;
  }
}
