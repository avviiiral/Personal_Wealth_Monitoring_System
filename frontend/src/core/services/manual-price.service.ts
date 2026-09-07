import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';

import { Observable, switchMap } from 'rxjs';
import { environment } from '../../environments/environment';

export interface ManualPriceResponse {
  success: boolean;
  message: string;

  data?: {
    asset_id: number;
    asset_name: string;
    price: string;
    price_date: string;
    current_price: string;
    current_value: string;
    unrealized_pnl: string;
  };
}

@Injectable({
  providedIn: 'root',
})
export class ManualPriceService {
  private readonly http = inject(HttpClient);

  private readonly baseUrl = `${environment.apiUrl}/api/portfolio`;

  private readonly csrfUrl = `${environment.apiUrl}/api/health/`;

  // ==========================================================
  // CSRF TOKEN
  // ==========================================================

  private readCsrfToken(): string {
    const cookies = document.cookie.split(';');

    for (const cookie of cookies) {
      const trimmed = cookie.trim();

      if (trimmed.startsWith('csrftoken=')) {
        return decodeURIComponent(trimmed.substring('csrftoken='.length));
      }
    }

    return '';
  }

  // ==========================================================
  // HEADERS
  // ==========================================================

  private getHeaders(): HttpHeaders {
    const csrfToken = this.readCsrfToken();

    let headers = new HttpHeaders();

    if (csrfToken) {
      headers = headers.set('X-CSRFToken', csrfToken);
    }

    return headers;
  }

  // ==========================================================
  // GET CSRF COOKIE
  // ==========================================================

  private getCsrfToken(): Observable<unknown> {
    return this.http.get(this.csrfUrl, {
      withCredentials: true,
    });
  }

  // ==========================================================
  // UPDATE PRICE
  // ==========================================================

  updatePrice(assetId: number, price: number, priceDate?: string): Observable<ManualPriceResponse> {
    return this.getCsrfToken().pipe(
      switchMap(() => {
        return this.http.put<ManualPriceResponse>(
          `${this.baseUrl}/assets/${assetId}/manual-price/`,
          {
            price,

            ...(priceDate
              ? {
                  price_date: priceDate,
                }
              : {}),
          },
          {
            headers: this.getHeaders(),

            withCredentials: true,
          },
        );
      }),
    );
  }

  // ==========================================================
  // DELETE PRICE
  // ==========================================================

  deletePrice(assetId: number): Observable<ManualPriceResponse> {
    return this.getCsrfToken().pipe(
      switchMap(() => {
        return this.http.delete<ManualPriceResponse>(
          `${this.baseUrl}/assets/${assetId}/manual-price/`,
          {
            headers: this.getHeaders(),

            withCredentials: true,
          },
        );
      }),
    );
  }
}
