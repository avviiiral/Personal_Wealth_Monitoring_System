import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http';

import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';

@Injectable({
  providedIn: 'root',
})
export class WealthApiService {
  private readonly http = inject(HttpClient);

  private readonly baseUrl = `${environment.apiUrl}/api/analytics/wealth`;

  getSummary(family?: string): Observable<any> {
    let params = new HttpParams();
    if (family) params = params.set('family', family);
    return this.http.get<any>(`${this.baseUrl}/summary/`, { params, withCredentials: true });
  }

  getAllocation(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/allocation/`, { withCredentials: true });
  }

  getPerformance(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/performance/`, { withCredentials: true });
  }

  getXirr(family?: string): Observable<any> {
    let params = new HttpParams();
    if (family) params = params.set('family', family);
    return this.http.get<any>(`${this.baseUrl}/xirr/`, { params, withCredentials: true });
  }

  getXirrByAssetClass(family?: string): Observable<any> {
    let params = new HttpParams();
    if (family) params = params.set('family', family);
    return this.http.get<any>(`${this.baseUrl}/performance-by-subclass/`, {
      params,
      withCredentials: true,
    });
  }

  getInvestmentSummary(family?: string): Observable<any> {
    let params = new HttpParams();
    if (family) params = params.set('family', family);
    return this.http.get<any>(`${this.baseUrl}/investment-summary/`, { params, withCredentials: true });
  }

  getStandardAllocations(family?: string): Observable<any> {
    let params = new HttpParams();
    if (family) params = params.set('family', family);
    return this.http.get<any>(`${this.baseUrl}/standard-allocations/`, { params, withCredentials: true });
  }

  saveStandardAllocations(
    allocations: Record<string, number>,
    family?: string,
  ): Observable<any> {
    let params = new HttpParams();
    if (family) params = params.set('family', family);

    const csrfToken = this.getCookie('csrftoken');
    const headers = csrfToken
      ? new HttpHeaders({ 'X-CSRFToken': csrfToken })
      : undefined;

    return this.http.put<any>(
      `${this.baseUrl}/standard-allocations/update/`,
      { allocations },
      { params, headers, withCredentials: true },
    );
  }

  private getCookie(name: string): string | null {
    if (typeof document === 'undefined') {
      return null;
    }

    const encodedName = `${name}=`;
    const cookies = document.cookie.split(';');

    for (const cookie of cookies) {
      const value = cookie.trim();

      if (!value.startsWith(encodedName)) {
        continue;
      }

      return decodeURIComponent(value.substring(encodedName.length));
    }

    return null;
  }

  getPerformanceBySubclass(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/performance-by-subclass/`, { withCredentials: true });
  }

  getAllocationByAdvisor(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/allocation-by-advisor/`, { withCredentials: true });
  }

  getCompositionByAmc(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/composition-by-amc/`, { withCredentials: true });
  }

  /**
   * Value-weighted P/E, P/B, PEG, ROE and market-cap allocation
   * across equity holdings with SecurityMaster quant data.
   */
  getEquityAnalysis(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/equity-analysis/`, { withCredentials: true });
  }

  getFixedIncomeAnalysis(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/fixed-income-analysis/`, { withCredentials: true });
  }

  getSectorAllocation(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/sector-allocation/`, { withCredentials: true });
  }

  getMarketCapAllocation(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/market-cap-allocation/`, { withCredentials: true });
  }

  getNonStockHoldingTypes(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/non-stock-holding-types/`, { withCredentials: true });
  }

  getPerformanceByAdvisor(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/performance-by-advisor/`, { withCredentials: true });
  }

  getHistorical(days: number = 30, family?: string): Observable<any> {
    let params = new HttpParams().set('days', days);
    if (family) params = params.set('family', family);
    return this.http.get<any>(`${this.baseUrl}/historical/`, { params, withCredentials: true });
  }

  getHistoricalRange(
    startDate: string,
    endDate: string,
    family?: string,
  ): Observable<any> {
    let params = new HttpParams()
      .set('start_date', startDate)
      .set('end_date', endDate);
    if (family) params = params.set('family', family);
    return this.http.get<any>(`${this.baseUrl}/historical/`, {
      params,
      withCredentials: true,
    });
  }

  getHistoricalByPeriod(
    period: 'this-month' | 'last-month' | 'inception',
    family?: string,
  ): Observable<any> {
    let params = new HttpParams().set('period', period);
    if (family) params = params.set('family', family);
    return this.http.get<any>(`${this.baseUrl}/historical-period/`, {
      params,
      withCredentials: true,
    });
  }
}
