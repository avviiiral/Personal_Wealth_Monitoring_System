import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';

export interface CorporateActionRecord {
  id: number;
  action_type: string;
  security_name: string;
  isin: string;
  symbol: string;
  exchange: string;
  ex_date: string;
  record_date: string | null;
  payment_date: string | null;
  old_units: string | null;
  new_units: string | null;
  cash_per_unit: string | null;
  source: string;
  source_reference: string;
  status: string;
  notes: string;
  quantity_factor: string | null;
}

export interface CorporateActionInboxItem {
  id: number;
  action: CorporateActionRecord;
  reconciliation: {
    id: number;
    status: string;
    matched_transaction_ids: number[];
    suggested_quantity: string | null;
    notes: string;
  };
  exposures: Array<{
    type: string;
    investment_name: string;
    investment_id: number | null;
    holding_weight: string | null;
    snapshot_date: string | null;
  }>;
  reviewed: boolean;
  dismissed: boolean;
  direct_exposure: boolean;
}

@Injectable({ providedIn: 'root' })
export class CorporateActionsService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiUrl}/api/corporate-actions`;

  getNotifications(status = 'ALL', page = 1, pageSize = 25): Observable<{
    count: number; page: number; page_size: number; results: CorporateActionInboxItem[];
  }> {
    let params = new HttpParams().set('page', page).set('page_size', pageSize);
    if (status !== 'ALL') params = params.set('status', status);
    return this.http.get<any>(`${this.baseUrl}/notifications/`, { params, withCredentials: true });
  }

  updateNotification(id: number, changes: { reviewed?: boolean; dismissed?: boolean }): Observable<any> {
    return this.http.patch(`${this.baseUrl}/notifications/${id}/`, changes, { withCredentials: true });
  }

  addTransaction(id: number, payload: { quantity: string; amount: string; price_per_unit: string; fees: string; transaction_date?: string; confirmed: boolean }): Observable<any> {
    return this.http.post(`${this.baseUrl}/reconciliations/${id}/add-transaction/`, payload, { withCredentials: true });
  }

  decideReconciliation(id: number, decision: 'ALREADY_RECORDED' | 'DISMISSED', notes = ''): Observable<any> {
    return this.http.post(`${this.baseUrl}/reconciliations/${id}/decision/`, { decision, notes }, { withCredentials: true });
  }
}
