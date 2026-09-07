import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';

import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';

@Injectable({
  providedIn: 'root',
})
export class WealthApiService {
  private readonly http = inject(HttpClient);

  /*
   * IMPORTANT:
   *
   * The backend URL comes from environment.apiUrl (see
   * src/environments/environment.ts), not a hardcoded string here.
   * In dev, that's http://localhost:8000 - keep the frontend on
   * http://localhost:4200 too, so the Django session cookie
   * belongs to the same site.
   */
  private readonly baseUrl = `${environment.apiUrl}/api/analytics/wealth`;

  getSummary(family?: string): Observable<any> {
    let params = new HttpParams();

    if (family) {
      params = params.set('family', family);
    }

    return this.http.get<any>(`${this.baseUrl}/summary/`, {
      params,
      withCredentials: true,
    });
  }

  getAllocation(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/allocation/`, {
      withCredentials: true,
    });
  }

  getPerformance(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/performance/`, {
      withCredentials: true,
    });
  }

  getXirr(family?: string): Observable<any> {
    let params = new HttpParams();

    if (family) {
      params = params.set('family', family);
    }

    return this.http.get<any>(`${this.baseUrl}/xirr/`, {
      params,
      withCredentials: true,
    });
  }

  getInvestmentSummary(family?: string): Observable<any> {
    let params = new HttpParams();

    if (family) {
      params = params.set('family', family);
    }

    return this.http.get<any>(`${this.baseUrl}/investment-summary/`, {
      params,
      withCredentials: true,
    });
  }

  getPerformanceBySubclass(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/performance-by-subclass/`, {
      withCredentials: true,
    });
  }

  getAllocationByAdvisor(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/allocation-by-advisor/`, {
      withCredentials: true,
    });
  }

  /**
   * Current-value allocation by AMC (Top AMC exposures, AMC
   * concentration) — see analytics.services.investment_summary.
   * InvestmentSummaryService.calculate_composition_by_amc.
   */
  getCompositionByAmc(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/composition-by-amc/`, {
      withCredentials: true,
    });
  }

  /**
   * Value-weighted P/E, P/B, ROE and market-cap allocation across
   * every equity/other-investment holding with SecurityMaster quant
   * data — see InvestmentSummaryService.calculate_equity_analysis.
   */
  getEquityAnalysis(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/equity-analysis/`, {
      withCredentials: true,
    });
  }

  /**
   * Value-weighted YTM / Modified Duration / Average Maturity and
   * credit-rating distribution across Fixed-Income-classified
   * holdings — see
   * InvestmentSummaryService.calculate_fixed_income_analysis.
   */
  getFixedIncomeAnalysis(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/fixed-income-analysis/`, {
      withCredentials: true,
    });
  }

  /**
   * Current-value allocation by sector, across every equity/other-
   * investment holding — see
   * InvestmentSummaryService.calculate_sector_allocation.
   */
  getSectorAllocation(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/sector-allocation/`, {
      withCredentials: true,
    });
  }

  /**
   * Current-value allocation by cap_type (Large/Mid/Small Cap),
   * across every equity/other-investment holding — see
   * InvestmentSummaryService.calculate_market_cap_allocation.
   */
  getMarketCapAllocation(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/market-cap-allocation/`, {
      withCredentials: true,
    });
  }

  /**
   * Current-value allocation by sub_class (Debt Mutual Fund,
   * Liquid Mutual Fund, InvITs, REITs, Gold Bond, Private Equity,
   * etc.), restricted to holdings with no cap_type on file — the
   * complementary breakdown to getMarketCapAllocation()'s
   * "Unclassified" slice. See
   * InvestmentSummaryService.calculate_non_stock_holding_types.
   */
  getNonStockHoldingTypes(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/non-stock-holding-types/`, {
      withCredentials: true,
    });
  }

  getPerformanceByAdvisor(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/performance-by-advisor/`, {
      withCredentials: true,
    });
  }

  getHistorical(days: number = 30, family?: string): Observable<any> {
    let params = new HttpParams().set('days', days);

    if (family) {
      params = params.set('family', family);
    }

    return this.http.get<any>(`${this.baseUrl}/historical/`, {
      params,
      withCredentials: true,
    });
  }
}
