import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable, switchMap } from 'rxjs';
import { environment } from '../../environments/environment';

export interface PortfolioSummary { total_invested: number; total_current_value: number; total_unrealized_pnl: number; pnl_percentage: number; number_of_holdings: number; }
export interface Holding { id: number; asset: number; asset_name: string; asset_category: string; asset_category_display: string; symbol: string | null; quantity: number; average_cost: number; invested_value: number; current_price: number; current_value: number; unrealized_pnl: number; pnl_percentage: number; updated_at: string; }
export interface Transaction { id: number; asset: number; asset_name: string; transaction_type: string; transaction_type_display: string; transaction_date: string; quantity: number; price_per_unit: number; amount: number; fees: number; notes: string | null; created_at: string; family_name?: string | null; portfolio?: string | null; asset_class?: string | null; sub_class?: string | null; underlying?: string | null; advisors?: string | null; isin?: string | null; }
export interface PortfolioAsset { id: number; name: string; category: string; category_display: string; symbol: string | null; isin: string | null; institution: string | null; currency: string; is_active: boolean; created_at: string; updated_at: string; }
export interface ApiListResponse<T> { count: number; results: T[]; }
export interface CreateAssetRequest { name: string; category: string; symbol?: string | null; isin?: string | null; institution?: string | null; currency?: string; }
export interface CreateTransactionRequest { asset: number; transaction_type: string; transaction_date: string; quantity: number; price_per_unit: number; amount: number; fees?: number; notes?: string | null; }
export interface UpdateTransactionRequest { family_name?: string | null; portfolio?: string | null; asset_class?: string | null; sub_class?: string | null; asset_name?: string | null; underlying?: string | null; advisors?: string | null; transaction_date?: string; transaction_type?: string; quantity?: number; price_per_unit?: number; amount?: number; fees?: number; notes?: string | null; }
export interface PortfolioAssetNode { id: number; family_name: string; asset_name: string; underlying: string; isin: string | null; advisors: string; quantity: number; average_cost: number; invested_value: number; current_price: number; current_value: number; pnl: number; pnl_percentage: number; xirr: number | null; asset_name_xirr?: number | null; sub_class_xirr?: number | null; sector: string | null; cap_type: string | null; amc_name: string | null; pe_ratio: number | null; pb_ratio: number | null; peg_ratio: number | null; roe: number | null; credit_rating: string | null; ytm: number | null; modified_duration: number | null; average_maturity: number | null; price_source: string | null; }
export interface SubClassNode { sub_class: string; assets: PortfolioAssetNode[]; asset_count: number; }
export interface AssetClassNode { asset_class: string; sub_classes: SubClassNode[]; sub_class_count: number; }
export interface PortfolioNode { portfolio: string; asset_classes: AssetClassNode[]; asset_class_count: number; }
export interface FamilyNode { family_name: string; portfolios: PortfolioNode[]; portfolio_count: number; }
export interface PortfolioTreeResponse { success: boolean; count: number; families: FamilyNode[]; }
export interface HoldingReportRow { id: number; family_name: string; portfolio: string; asset_class: string; asset_class_xirr: number | null; sub_class: string; asset_id: number; asset_name: string; underlying: string; underlying_xirr?: Record<string, { xirr: number | null; holding_percentage: number }>; isin: string | null; advisors: string; quantity: number; average_cost: number; invested_value: number; current_price: number; current_value: number; gain: number; gain_percentage: number; xirr: number | null; sub_class_xirr: number | null; asset_name_xirr: number | null; sector: string | null; cap_type: string | null; amc_name: string | null; }
export interface HoldingReportResponse { success: boolean; count: number; results: HoldingReportRow[]; }
export interface HoldingMatrixResponse { success: boolean; count: number; underlyings: string[]; results: Array<Record<string, number | string> & { asset_name: string }>; }
export interface MarketCapReportRow { family_name: string; sub_class: string; asset_name: string; small_cap: number | null; mid_cap: number | null; large_cap: number | null; unclassified: number | null; }
export interface MarketCapReportResponse { success: boolean; count: number; results: MarketCapReportRow[]; }

@Injectable({ providedIn: 'root' })
export class PortfolioApiService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiUrl}/api/portfolio`;
  private readonly csrfUrl = `${environment.apiUrl}/api/health/`;
  private readonly requestOptions = { withCredentials: true };

  private getCsrfToken(): Observable<any> { return this.http.get(this.csrfUrl, { withCredentials: true }); }
  private readCsrfToken(): string {
    const cookies = document.cookie.split(';');
    for (const cookie of cookies) {
      const trimmed = cookie.trim();
      if (trimmed.startsWith('csrftoken=')) return decodeURIComponent(trimmed.substring('csrftoken='.length));
    }
    return '';
  }
  private getCsrfHeaders(): HttpHeaders {
    const csrfToken = this.readCsrfToken();
    let headers = new HttpHeaders();
    if (csrfToken) headers = headers.set('X-CSRFToken', csrfToken);
    return headers;
  }
  getSummary(): Observable<PortfolioSummary> { return this.http.get<PortfolioSummary>(`${this.baseUrl}/summary/`, this.requestOptions); }
  getHoldings(): Observable<ApiListResponse<Holding>> { return this.http.get<ApiListResponse<Holding>>(`${this.baseUrl}/holdings/`, this.requestOptions); }
  getTransactions(): Observable<ApiListResponse<Transaction>> { return this.http.get<ApiListResponse<Transaction>>(`${this.baseUrl}/transactions/`, this.requestOptions); }
  getAssets(): Observable<ApiListResponse<PortfolioAsset>> { return this.http.get<ApiListResponse<PortfolioAsset>>(`${this.baseUrl}/assets/`, this.requestOptions); }
  getPortfolioTree(filters: { family?: string; asset_class?: string; advisor?: string } = {}): Observable<PortfolioTreeResponse> {
    return this.http.get<PortfolioTreeResponse>(`${this.baseUrl}/tree/`, {
      ...this.requestOptions,
      params: {
        family: filters.family || '',
        asset_class: filters.asset_class || '',
        advisor: filters.advisor || '',
      },
    });
  }
  getHoldingReport(): Observable<HoldingReportResponse> { return this.http.get<HoldingReportResponse>(`${this.baseUrl}/holding-report/`, { ...this.requestOptions, params: { _t: Date.now().toString() } }); }
  getHoldingMatrix(): Observable<HoldingMatrixResponse> { return this.http.get<HoldingMatrixResponse>(`${this.baseUrl}/holding-matrix/`, { ...this.requestOptions, params: { _t: Date.now().toString() } }); }
  getEquityMarketCapReport(): Observable<MarketCapReportResponse> {
    return this.http.get<MarketCapReportResponse>(`${this.baseUrl}/equity-market-cap/`, { ...this.requestOptions, params: { _t: Date.now().toString() } });
  }
  uploadAssetUnderlying(assetId: number, file: File): Observable<any> {
    const formData = new FormData();
    formData.append('file', file);
    return this.getCsrfToken().pipe(
      switchMap(() =>
        this.http.post<any>(`${this.baseUrl}/assets/${assetId}/underlying/import/`, formData, {
          headers: this.getCsrfHeaders(),
          withCredentials: true,
        }),
      ),
    );
  }

  createAsset(payload: CreateAssetRequest): Observable<PortfolioAsset> {
    return this.getCsrfToken().pipe(switchMap(() => this.http.post<PortfolioAsset>(`${this.baseUrl}/assets/`, payload, { headers: this.getCsrfHeaders(), withCredentials: true })));
  }
  createTransaction(payload: CreateTransactionRequest): Observable<Transaction> {
    return this.getCsrfToken().pipe(switchMap(() => this.http.post<Transaction>(`${this.baseUrl}/transactions/`, payload, { headers: this.getCsrfHeaders(), withCredentials: true })));
  }
  updateTransaction(transactionId: number, payload: UpdateTransactionRequest): Observable<Transaction> {
    return this.getCsrfToken().pipe(switchMap(() => this.http.patch<Transaction>(`${this.baseUrl}/transactions/${transactionId}/`, payload, { headers: this.getCsrfHeaders(), withCredentials: true })));
  }
}
