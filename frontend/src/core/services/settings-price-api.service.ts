import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';

import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';

export interface SettingsPriceRow {
  asset_id: number;
  asset_name: string;
  category: string;
  currency: string;
  price: string | null;
  price_date: string | null;
  price_source: string | null;
  manual_override_enabled: boolean;
  updated_by: string | null;
  updated_at: string | null;
}

@Injectable({
  providedIn: 'root',
})
export class SettingsPriceApiService {
  private readonly http = inject(HttpClient);

  private readonly baseUrl = `${environment.apiUrl}/api/settings`;

  listPrices(): Observable<{ results: SettingsPriceRow[] }> {
    return this.http.get<{ results: SettingsPriceRow[] }>(`${this.baseUrl}/prices/`, {
      withCredentials: true,
    });
  }
}
