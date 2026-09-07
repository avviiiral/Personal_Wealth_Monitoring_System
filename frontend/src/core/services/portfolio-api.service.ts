import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, switchMap } from 'rxjs';
import { environment } from '../../environments/environment';

export interface PortfolioSummary {
  total_invested: number;
  total_current_value: number;
  total_unrealized_pnl: number;
  pnl_percentage: number;
  number_of_holdings: number;
}

export interface Holding {
  id: number;
  asset: number;
  asset_name: string;
  asset_category: string;
  asset_category_display: string;
  symbol: string | null;
  quantity: number;
  average_cost: number;
  invested_value: number;
  current_price: number;
  current_value: number;
  unrealized_pnl: number;
  pnl_percentage: number;
  updated_at: string;
}

export interface Transaction {
  id: number;
  asset: number;
  asset_name: string;
  transaction_type: string;
  transaction_type_display: string;
  transaction_date: string;
  quantity: number;
  price_per_unit: number;
  amount: number;
  fees: number;
  notes: string | null;
  created_at: string;

  /* ========================================================
     HIERARCHY / CLASSIFICATION
     Already returned by the existing TransactionSerializer
     (backend/portfolio/serializers.py); added here so the
     Reports page can group by Family -> Sub Class -> Underlying
     without any backend change.
     ======================================================== */
  family_name?: string | null;
  portfolio?: string | null;
  asset_class?: string | null;
  sub_class?: string | null;
  underlying?: string | null;
  isin?: string | null;
}

export interface PortfolioAsset {
  id: number;
  name: string;
  category: string;
  category_display: string;
  symbol: string | null;
  isin: string | null;
  institution: string | null;
  currency: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ApiListResponse<T> {
  count: number;
  results: T[];
}

export interface CreateAssetRequest {
  name: string;
  category: string;
  symbol?: string | null;
  isin?: string | null;
  institution?: string | null;
  currency?: string;
}

export interface CreateTransactionRequest {
  asset: number;
  transaction_type: string;
  transaction_date: string;
  quantity: number;
  price_per_unit: number;
  amount: number;
  fees?: number;
  notes?: string | null;
}

/* ==========================================================
   PORTFOLIO TREE
   Family
      Portfolio
         Asset Class
            Sub Class
               Asset
   ========================================================== */

export interface PortfolioAssetNode {
  id: number;
  family_name: string;
  asset_name: string;
  underlying: string;
  isin: string | null;
  advisors: string;

  quantity: number;
  average_cost: number;
  invested_value: number;

  current_price: number;
  current_value: number;

  pnl: number;
  pnl_percentage: number;
  xirr: number | null;

  sector: string | null;
  cap_type: string | null;

  /* ============================================================
     Added alongside the SecurityMaster schema extension
     (investments/migrations/0007_...). All null whenever no
     SecurityMaster row exists for the asset yet, or the specific
     field hasn't been filled in via Django admin — never
     defaulted/fabricated on the backend, so treat null as
     "unknown", not zero.
     ============================================================ */
  amc_name: string | null;
  pe_ratio: number | null;
  pb_ratio: number | null;
  roe: number | null;
  credit_rating: string | null;
  ytm: number | null;
  modified_duration: number | null;
  average_maturity: number | null;

  price_source: string | null;
}

export interface SubClassNode {
  sub_class: string;
  assets: PortfolioAssetNode[];
  asset_count: number;
}

export interface AssetClassNode {
  asset_class: string;
  sub_classes: SubClassNode[];
  sub_class_count: number;
}

export interface PortfolioNode {
  portfolio: string;
  asset_classes: AssetClassNode[];
  asset_class_count: number;
}

export interface FamilyNode {
  family_name: string;
  portfolios: PortfolioNode[];
  portfolio_count: number;
}

export interface PortfolioTreeResponse {
  success: boolean;
  count: number;
  families: FamilyNode[];
}

@Injectable({
  providedIn: 'root',
})
export class PortfolioApiService {
  private readonly http = inject(HttpClient);

  private readonly baseUrl = `${environment.apiUrl}/api/portfolio`;

  private readonly csrfUrl = `${environment.apiUrl}/api/health/`;

  private readonly requestOptions = {
    withCredentials: true,
  };

  private getCsrfToken(): Observable<any> {
    return this.http.get(this.csrfUrl, {
      withCredentials: true,
    });
  }

  private readCsrfToken(): string {
    const cookies = document.cookie.split(';');

    for (const cookie of cookies) {
      const trimmedCookie = cookie.trim();

      if (trimmedCookie.startsWith('csrftoken=')) {
        return decodeURIComponent(trimmedCookie.substring('csrftoken='.length));
      }
    }

    return '';
  }

  private getCsrfHeaders(): HttpHeaders {
    const csrfToken = this.readCsrfToken();

    let headers = new HttpHeaders();

    if (csrfToken) {
      headers = headers.set('X-CSRFToken', csrfToken);
    }

    return headers;
  }

  getSummary(): Observable<PortfolioSummary> {
    return this.http.get<PortfolioSummary>(`${this.baseUrl}/summary/`, this.requestOptions);
  }

  getHoldings(): Observable<ApiListResponse<Holding>> {
    return this.http.get<ApiListResponse<Holding>>(
      `${this.baseUrl}/holdings/`,
      this.requestOptions,
    );
  }

  getTransactions(): Observable<ApiListResponse<Transaction>> {
    return this.http.get<ApiListResponse<Transaction>>(
      `${this.baseUrl}/transactions/`,
      this.requestOptions,
    );
  }

  getAssets(): Observable<ApiListResponse<PortfolioAsset>> {
    return this.http.get<ApiListResponse<PortfolioAsset>>(
      `${this.baseUrl}/assets/`,
      this.requestOptions,
    );
  }

  getPortfolioTree(): Observable<PortfolioTreeResponse> {
    return this.http.get<PortfolioTreeResponse>(`${this.baseUrl}/tree/`, this.requestOptions);
  }

  createAsset(payload: CreateAssetRequest): Observable<PortfolioAsset> {
    return this.getCsrfToken().pipe(
      switchMap(() =>
        this.http.post<PortfolioAsset>(`${this.baseUrl}/assets/`, payload, {
          headers: this.getCsrfHeaders(),
          withCredentials: true,
        }),
      ),
    );
  }

  createTransaction(payload: CreateTransactionRequest): Observable<Transaction> {
    return this.getCsrfToken().pipe(
      switchMap(() =>
        this.http.post<Transaction>(`${this.baseUrl}/transactions/`, payload, {
          headers: this.getCsrfHeaders(),
          withCredentials: true,
        }),
      ),
    );
  }
}
