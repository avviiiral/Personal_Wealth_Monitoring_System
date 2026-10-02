import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';

export interface MISIPSRow {
  asset_class: string;
  family_values: Record<string, number>;
  grand_total: number;
  prior_family_values: Record<string, number>;
  prior_total: number;
  difference: number;
}

export interface MISDataSheetRow {
  asset_name: string;
  family_name: string;
  asset_class: string;
  sub_class: string;
  advisor: string;
  qty_units: number;
  rate: number;
  total_cost: number;
  opening_units: number;
  opening_nav: number | null;
  opening_amount: number;
  transaction_units: number;
  transaction_nav: number | null;
  transaction_amount: number;
  closing_units: number;
  closing_nav: number | null;
  closing_amount: number;
}

export interface MISTaxReportRow extends MISDataSheetRow {
  realized_pnl: number;
  unrealized_pnl: number;
  realized_tax: number;
  unrealized_tax: number;
}

export interface MISFundSummaryRow {
  fund_type: string;
  rows: Array<{
    fund_name: string;
    total: number;
  }>;
  subtotal: number;
}

export interface MISNoteItem {
  name: string;
  opening_rate: number;
  closing_rate: number;
  change: number;
  percent_change: number | null;
}

export interface MISNoteSection {
  section: string;
  section_number: number;
  title: string;
  unit_label: string;
  change_label: string;
  items: MISNoteItem[];
  note?: string | null;
}


export interface MISEditableColumn {
  id: string;
  label: string;
  type: string;
}

export interface MISEditableRow {
  id: string;
  cells: Record<string, string | number | null>;
}

export interface MISEditableSection {
  id: string;
  section_number: number;
  title: string;
  note?: string | null;
  columns: MISEditableColumn[];
  rows: MISEditableRow[];
}

export interface MISEditableNotes {
  title: string;
  opening_label: string;
  closing_label: string;
  sections: MISEditableSection[];
}

export interface MISNotesHistoryEntry {
  id: number;
  user: string;
  date_time: string;
  changes: Array<Record<string, unknown>>;
}

export interface MISNotes {
  title: string;
  opening_label: string;
  closing_label: string;
  sections: MISNoteSection[];
  editable: MISEditableNotes;
}

export interface MISReport {
  family_name: string;
  reporting_date: string;
  opening_date: string;
  prior_month_date: string;
  period_start: string;
  family_names: string[];
  ips: MISIPSRow[];
  data_sheet: MISDataSheetRow[];
  tax_report: MISTaxReportRow[];
  fund_type_summary: MISFundSummaryRow[];
  notes: MISNotes;
  summary: {
    total_current_value: number;
    number_of_rows: number;
  };
}

@Injectable({ providedIn: 'root' })
export class MISReportService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = environment.apiUrl;

  getReport(fromDate?: string, toDate?: string, displayUnit = 'lakhs'): Observable<MISReport> {
    const params: Record<string, string> = {};
    if (fromDate) params['from_date'] = fromDate;
    if (toDate) params['to_date'] = toDate;
    params['display_unit'] = displayUnit;
    return this.http.get<MISReport>(this.baseUrl + '/api/portfolio/mis-report/', {
      withCredentials: true,
      params,
    });
  }

  getNotes(fromDate?: string, toDate?: string): Observable<{ notes: MISNotes }> {
    const params: Record<string, string> = {};
    if (fromDate) params['from_date'] = fromDate;
    if (toDate) params['to_date'] = toDate;
    return this.http.get<{ notes: MISNotes }>(this.baseUrl + '/api/portfolio/mis-report/notes/', {
      withCredentials: true,
      params,
    });
  }

  saveNotes(notes: MISEditableNotes, fromDate?: string, toDate?: string, autoFill = true): Observable<{ notes: MISNotes; changed: boolean }> {
    const params: Record<string, string> = {};
    if (fromDate) params['from_date'] = fromDate;
    if (toDate) params['to_date'] = toDate;
    const csrfToken = this.readCsrfToken();
    const headers = csrfToken ? new HttpHeaders({ 'X-CSRFToken': csrfToken, 'Content-Type': 'application/json' }) : undefined;
    return this.http.put<{ notes: MISNotes; changed: boolean }>(this.baseUrl + '/api/portfolio/mis-report/notes/', {
      notes,
      auto_fill: autoFill,
    }, { withCredentials: true, headers, params });
  }

  getNotesHistory(): Observable<{ count: number; results: MISNotesHistoryEntry[] }> {
    return this.http.get<{ count: number; results: MISNotesHistoryEntry[] }>(this.baseUrl + '/api/portfolio/mis-report/notes/history/', {
      withCredentials: true,
    });
  }

  downloadReport(fromDate?: string, toDate?: string, displayUnit = 'lakhs'): Observable<Blob> {
    const params: Record<string, string> = {};
    if (fromDate) params['from_date'] = fromDate;
    if (toDate) params['to_date'] = toDate;
    params['display_unit'] = displayUnit;
    return this.http.get(this.baseUrl + '/api/portfolio/mis-report/download/', {
      responseType: 'blob',
      withCredentials: true,
      params,
    });
  }
}
