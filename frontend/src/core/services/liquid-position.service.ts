import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';

export interface LiquidPositionPair {
  daj: number;
  djt: number;
  total: number;
}

export interface LiquidPositionLine extends LiquidPositionPair {
  id: string;
  label: string;
}

export interface LiquidPositionDocument {
  bank: {
    daj: number;
    djt: number;
  };
  commitments: Array<{
    id: string;
    label: string;
    daj: number;
    djt: number;
  }>;
  deployment: Array<{
    id: string;
    label: string;
    daj: number;
    djt: number;
  }>;
}

export interface LiquidPositionReport {
  as_of_date: string;
  unit_label: string;
  can_edit: boolean;
  updated_at: string | null;
  liquid_mv: LiquidPositionPair;
  arbitrage_mv: LiquidPositionPair;
  bank: LiquidPositionPair;
  available_liquidity: LiquidPositionPair;
  commitments: LiquidPositionLine[];
  net_available_amount: LiquidPositionPair;
  deployment: LiquidPositionLine[];
  total_deployment_plan: LiquidPositionPair;
  balance: LiquidPositionPair;
}

@Injectable({ providedIn: 'root' })
export class LiquidPositionService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = environment.apiUrl + '/api/portfolio/liquid-position/';

  private readCsrfToken(): string | null {
    for (const cookie of document.cookie.split(';')) {
      const [name, ...valueParts] = cookie.trim().split('=');
      if (name === 'csrftoken') return decodeURIComponent(valueParts.join('='));
    }
    return null;
  }

  getPosition(asOfDate: string): Observable<LiquidPositionReport> {
    return this.http.get<LiquidPositionReport>(this.baseUrl, {
      withCredentials: true,
      params: { as_of: asOfDate },
    });
  }

  savePosition(asOfDate: string, document: LiquidPositionDocument): Observable<LiquidPositionReport> {
    const csrfToken = this.readCsrfToken();
    const headers = csrfToken
      ? new HttpHeaders({ 'X-CSRFToken': csrfToken, 'Content-Type': 'application/json' })
      : undefined;

    return this.http.put<LiquidPositionReport>(
      this.baseUrl,
      { document },
      {
        withCredentials: true,
        params: { as_of: asOfDate },
        headers,
      },
    );
  }
}
