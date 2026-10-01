import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ChangeDetectorRef, Component, inject, OnInit } from '@angular/core';

import { MISDataSheetRow, MISReport, MISReportService } from '../../core/services/mis-report.service';

type MISSheet = 'ips' | 'data' | 'fund-summary';

@Component({
  selector: 'app-mis-report',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './mis-report.component.html',
  styleUrl: './mis-report.component.scss',
})
export class MISReportComponent implements OnInit {
  private readonly service = inject(MISReportService);
  private readonly cdr = inject(ChangeDetectorRef);

  report: MISReport | null = null;
  activeSheet: MISSheet = 'ips';
  loading = true;
  downloading = false;
  error = '';
  fromDate = '';
  toDate = '';
  todayDate = '';
  displayUnit: 'amount' | 'lakhs' | 'crores' = 'lakhs';

  readonly displayUnits: Array<{ value: 'amount' | 'lakhs' | 'crores'; label: string }> = [
    { value: 'amount', label: 'Amount' },
    { value: 'lakhs', label: 'Lakhs' },
    { value: 'crores', label: 'Crores' },
  ];

  readonly sheets: Array<{ key: MISSheet; label: string }> = [
    { key: 'ips', label: 'IPS' },
    { key: 'data', label: 'Data Sheet' },
    { key: 'fund-summary', label: 'Fund Type-wise Summary' },
  ];

  ngOnInit(): void {
    const today = new Date();
    this.todayDate = this.toInputDate(today);
    this.toDate = this.todayDate;
    const start = new Date(today.getFullYear(), today.getMonth(), 1);
    this.fromDate = this.toInputDate(start);
    this.loadReport();
  }

  loadReport(): void {
    if (this.fromDate && this.toDate && this.fromDate > this.toDate) {
      this.error = 'From date cannot be after To date.';
      return;
    }
    this.loading = true;
    this.error = '';
    this.service.getReport(this.fromDate, this.toDate, this.displayUnit).subscribe({
      next: (report) => { this.report = report; this.loading = false; this.cdr.markForCheck(); },
      error: (error) => {
        this.loading = false;
        this.error = error?.status === 403
          ? 'You do not have access to an active family portfolio.'
          : 'Unable to load the MIS Report. Please try again.';
        this.cdr.markForCheck();
      },
    });
  }

  selectSheet(sheet: MISSheet): void { this.activeSheet = sheet; }

  downloadExcel(): void {
    if (this.downloading || !this.report) return;
    if (!this.fromDate || !this.toDate || this.fromDate > this.toDate) {
      this.error = 'Please select a valid date range.';
      return;
    }
    this.downloading = true;
    this.service.downloadReport(this.fromDate, this.toDate, this.displayUnit).subscribe({
      next: (blob) => {
        const filename = 'MIS_Report_' + this.safeFilename(this.report?.family_name ?? 'Family') + '_' + (this.report?.reporting_date ?? '') + '.xlsx';
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url; anchor.download = filename; anchor.click(); URL.revokeObjectURL(url);
        this.downloading = false; this.cdr.markForCheck();
      },
      error: () => {
        this.downloading = false;
        this.error = 'Unable to download the MIS Report Excel file. Please try again.';
        this.cdr.markForCheck();
      },
    });
  }

  get dataRows(): MISDataSheetRow[] { return this.report?.data_sheet ?? []; }

  get fundSummaryGrandTotal(): number {
    return (this.report?.fund_type_summary ?? []).reduce((total, group) => total + Number(group.subtotal || 0), 0);
  }

  ipsGrandTotal(family?: string): number {
    const rows = this.report?.ips ?? [];
    if (family) {
      return rows.reduce((total, row) => total + Number(row.family_values?.[family] || 0), 0);
    }
    return rows.reduce((total, row) => total + Number(row.grand_total || 0), 0);
  }

  formatNumber(value: number | null | undefined, digits = 2): string {
    if (value === null || value === undefined) return '—';
    return new Intl.NumberFormat('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
  }

  setDisplayUnit(unit: 'amount' | 'lakhs' | 'crores'): void {
    this.displayUnit = unit;
  }

  get displayUnitLabel(): string {
    return this.displayUnit === 'amount' ? '₹ Amount' : this.displayUnit === 'lakhs' ? '₹ Lakhs' : '₹ Crores';
  }

  formatDisplayAmount(value: number | null | undefined, digits = 2): string {
    if (value === null || value === undefined) return '—';
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) return '—';
    const divisor = this.displayUnit === 'amount' ? 1 : this.displayUnit === 'lakhs' ? 100000 : 10000000;
    return new Intl.NumberFormat('en-IN', {
      minimumFractionDigits: this.displayUnit === 'amount' ? 0 : digits,
      maximumFractionDigits: this.displayUnit === 'amount' ? 0 : digits,
    }).format(numericValue / divisor);
  }

  formatLakhs(value: number | null | undefined, digits = 2): string {
    return this.formatDisplayAmount(value, digits);
  }

  formatDate(value: string | null | undefined): string {
    if (!value) return '—';
    const parsed = new Date(value + 'T00:00:00');
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  }

  trackByDataRow(index: number, row: MISDataSheetRow): string {
    return row.family_name + '-' + row.asset_class + '-' + row.asset_name + '-' + index;
  }

  private toInputDate(value: Date): string {
    return value.toISOString().slice(0, 10);
  }

  private safeFilename(value: string): string {
    return value.replace(/[^a-zA-Z0-9._-]+/g, '_').replace(/^[_\\.]+|[_\\.]+$/g, '') || 'Family';
  }
}