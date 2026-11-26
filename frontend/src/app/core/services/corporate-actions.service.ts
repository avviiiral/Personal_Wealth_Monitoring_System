import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';

type Num = string | number | null;

export interface CorporateActionRecord {
  id: number; action_type: string; action_type_display?: string; security_name: string; isin: string; symbol: string;
  exchange: string; ex_date: string; record_date: string | null; payment_date: string | null;
  old_units: Num; new_units: Num; cash_per_unit: Num;
  resulting_isin?: string; resulting_symbol?: string; resulting_security_name?: string;
  cost_allocation_ratio?: Num; cash_in_lieu_price?: Num;
  source: string; source_reference: string; status: string; notes: string; quantity_factor: Num;
  /** Terms still needed before quantities can be calculated (plain names). */
  missing_terms?: string[];
  /** Terms PWMS read from exchange announcements, with where each came from. */
  terms_from_announcements?: Record<string, {value: string; source: string}>;
  /** "TENDER", "OPEN_MARKET" or "" for a buyback. */
  buyback_method?: string;
}
export interface CorporateActionCandidate {
  id: number; name: string; symbol: string; isin: string; exchange: string;
}
export interface CorporateActionPlannedEntry {
  role: 'SOURCE' | 'RESULT'; transaction_type: string; transaction_date: string;
  quantity: string; price_per_unit: string; amount: string; label: string;
  asset_id: number | null; asset_label: string; underlying: string;
}
export interface CorporateActionMatchedTransaction {
  id: number; transaction_type: string; transaction_date: string; quantity: string; amount: string;
  source: string; portfolio: string;
}
export interface CorporateActionPosition {
  id: number; status: string; family_id: number | null; family_name: string; portfolio: string;
  asset_class: string; sub_class: string; asset_name: string; underlying: string; advisors: string;
  asset_ids: number[]; eligibility_cutoff: string | null;
  eligible_quantity: Num; quantity_delta: Num; resulting_quantity: Num; new_security_quantity: Num;
  fractional_entitlement: Num; cost_basis: Num; resulting_cost_basis: Num; allocated_cost: Num; cash_amount: Num;
  matched_transaction_ids: number[]; matched_transactions: CorporateActionMatchedTransaction[];
  created_transaction_ids: number[]; planned_entries: CorporateActionPlannedEntry[];
  explanation: string; eligibility_rule: string; cash_in_lieu_pending: boolean; reason: string;
  applied_at: string | null;
  /** Buyback / tender offer: the accepted share count is asked for when accepting. */
  needs_accepted_quantity?: boolean;
}
export interface CorporateActionSummary {
  status_counts: Record<string, number>;
  affected_portfolio_count: number; affected_family_members: string[];
  affected_asset_names: string[]; affected_asset_classes: string[];
  original_quantity: Num; proposed_adjustment: Num; resulting_quantity: Num; cash_amount: Num;
  already_recorded: number; manual_review: number;
}
export interface CorporateActionInboxItem {
  id: number;
  action: CorporateActionRecord;
  reconciliation: {
    id: number; status: string; status_display?: string; matched_transaction_ids: number[];
    suggested_quantity: Num; identity_match_method: string;
    candidate_asset_ids: number[]; candidate_assets: CorporateActionCandidate[];
    selected_asset_id: number | null; notes: string;
    proposal_fingerprint?: string; last_reconciled_at?: string | null;
    reviewed_at?: string | null; reviewed_by?: string | null;
    positions?: CorporateActionPosition[]; summary?: CorporateActionSummary;
    can_accept?: boolean; can_reject?: boolean;
  };
  exposures: Array<{type:string;investment_name:string;investment_id:number|null;quantity?:Num;holding_weight:Num;snapshot_date:string|null}>;
  reviewed: boolean; dismissed: boolean; direct_exposure: boolean;
}
/** Editable event terms used to complete a manual-review event. */
export type CorporateActionTermsPatch = Partial<Pick<CorporateActionRecord,
  'old_units' | 'new_units' | 'cash_per_unit' | 'record_date' | 'payment_date' | 'resulting_isin' |
  'resulting_symbol' | 'resulting_security_name' | 'cost_allocation_ratio' | 'cash_in_lieu_price'>>;

@Injectable({ providedIn: 'root' })
export class CorporateActionsService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiUrl}/api/corporate-actions`;

  getNotifications(status = 'ALL', page = 1, pageSize = 25): Observable<{count:number;page:number;page_size:number;results:CorporateActionInboxItem[]}> {
    let params = new HttpParams().set('page', page).set('page_size', pageSize);
    if (status !== 'ALL') params = params.set('status', status);
    return this.http.get<any>(`${this.baseUrl}/notifications/`, {params, withCredentials:true});
  }
  /** Official event verification only; it never books or changes a holding. */
  verifyEvent(id:number): Observable<any> {
    return this.http.patch(`${this.baseUrl}/${id}/`,{status:'CONFIRMED'},{withCredentials:true});
  }
  updateEventTerms(id:number, terms:CorporateActionTermsPatch): Observable<any> {
    return this.http.patch(`${this.baseUrl}/${id}/`,terms,{withCredentials:true});
  }
  updateNotification(id:number,changes:{reviewed?:boolean;dismissed?:boolean}):Observable<any>{
    return this.http.patch(`${this.baseUrl}/notifications/${id}/`,changes,{withCredentials:true});
  }
  decideReconciliation(id:number,decision:'ALREADY_RECORDED'|'DISMISSED',notes=''):Observable<any>{
    return this.http.post(`${this.baseUrl}/reconciliations/${id}/decision/`,{decision,notes},{withCredentials:true});
  }
  /**
   * Book the proposal shown to the user; refused (409) if the calculation changed meanwhile.
   * For a buyback, ``acceptedQuantities`` maps each holding (position id) to the shares accepted (0 = not tendered).
   */
  acceptReconciliation(id:number,proposalFingerprint:string,acceptedQuantities?:Record<number,number>,notes=''):Observable<any>{
    const payload:Record<string,unknown>={confirm:true,proposal_fingerprint:proposalFingerprint,notes};
    if(acceptedQuantities) payload['accepted_quantities']=acceptedQuantities;
    return this.http.post(`${this.baseUrl}/reconciliations/${id}/accept/`,payload,{withCredentials:true});
  }
  rejectReconciliation(id:number,reason:string):Observable<any>{
    return this.http.post(`${this.baseUrl}/reconciliations/${id}/reject/`,{reason},{withCredentials:true});
  }
  refreshReconciliation(id:number):Observable<any>{
    return this.http.post(`${this.baseUrl}/reconciliations/${id}/refresh/`,{}, {withCredentials:true});
  }
  decideIdentity(id:number,decision:'APPROVE'|'REJECT',assetId?:number,notes=''):Observable<any>{
    const payload: {decision:string;asset_id?:number;notes:string} = {decision,notes};
    if(decision==='APPROVE' && assetId!==undefined) payload.asset_id=assetId;
    return this.http.post(`${this.baseUrl}/reconciliations/${id}/identity-decision/`,payload,{withCredentials:true});
  }
}
