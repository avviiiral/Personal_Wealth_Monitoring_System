import { CommonModule } from '@angular/common';
import { ChangeDetectorRef, Component, inject, OnInit } from '@angular/core';

import { MISHolding, MISReport, MISReportService } from '../../core/services/mis-report.service';

@Component({
  selector: 'app-mis-report',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './mis-report.component.html',
  styleUrl: './mis-report.component.scss',
})
export class MISReportComponent implements OnInit {
  private readonly service = inject(MISReportService);
  private readonly cdr = inject(ChangeDetectorRef);

  report: MISReport | null = null;
  loading = true;
  downloading = false;
  error = '';

  readonly groups = new Map<string, MISHolding[]>();
  selectedFamilyMember = '';

  ngOnInit(): void {
    this.loadReport();
  }

  loadReport(): void {
    this.loading = true;
    this.error = '';

    this.service.getReport().subscribe({
      next: (report) => {
        this.report = report;
        this.selectedFamilyMember = '';
        this.rebuildGroups();
        this.loading = false;
        this.cdr.markForCheck();
      },
      error: (error) => {
        this.loading = false;
        this.error = error?.status === 403
          ? 'You do not have access to an active family portfolio.'
          : 'Unable to load the MIS Report. Please try again.';
        this.cdr.markForCheck();
      },
    });
  }

  get familyMemberOptions(): string[] {
    const names = new Set<string>();

    for (const holding of this.report?.holdings ?? []) {
      const name = (holding.family_name || '').trim();
      if (name) {
        names.add(name);
      }
    }

    return Array.from(names).sort((a, b) => a.localeCompare(b));
  }

  get filteredHoldings(): MISHolding[] {
    const holdings = this.report?.holdings ?? [];

    if (!this.selectedFamilyMember) {
      return holdings;
    }

    return holdings.filter(
      (holding) => (holding.family_name || '').trim() === this.selectedFamilyMember,
    );
  }

  get filteredSummary(): MISReport['summary'] {
    const holdings = this.filteredHoldings;
    const totalInvested = holdings.reduce((sum, row) => sum + Number(row.invested_value || 0), 0);
    const totalCurrentValue = holdings.reduce((sum, row) => sum + Number(row.current_value || 0), 0);
    const totalPnl = totalCurrentValue - totalInvested;

    return {
      total_invested: totalInvested,
      total_current_value: totalCurrentValue,
      total_pnl: totalPnl,
      pnl_percentage: totalInvested ? (totalPnl / totalInvested) * 100 : 0,
      number_of_holdings: holdings.length,
    };
  }

  get filteredAssetClassSummary(): MISReport['asset_class_summary'] {
    const buckets = new Map<string, { invested_value: number; current_value: number }>();

    for (const row of this.filteredHoldings) {
      const name = row.asset_class || 'Unassigned';
      const bucket = buckets.get(name) ?? { invested_value: 0, current_value: 0 };
      bucket.invested_value += Number(row.invested_value || 0);
      bucket.current_value += Number(row.current_value || 0);
      buckets.set(name, bucket);
    }

    return Array.from(buckets.entries())
      .map(([asset_class, values]) => {
        const pnl = values.current_value - values.invested_value;
        return {
          asset_class,
          invested_value: values.invested_value,
          current_value: values.current_value,
          pnl,
          pnl_percentage: values.invested_value ? (pnl / values.invested_value) * 100 : 0,
        };
      })
      .sort((a, b) => a.asset_class.localeCompare(b.asset_class));
  }

  selectFamilyMember(family: string): void {
    this.selectedFamilyMember = this.selectedFamilyMember === family ? '' : family;
    this.rebuildGroups();
    this.cdr.markForCheck();
  }

  clearFamilyMember(): void {
    this.selectedFamilyMember = '';
    this.rebuildGroups();
    this.cdr.markForCheck();
  }

  isFamilyMemberSelected(family: string): boolean {
    return this.selectedFamilyMember === family;
  }

  rebuildGroups(): void {
    this.groups.clear();

    for (const holding of this.filteredHoldings) {
      // Match the Portfolio page hierarchy: Sub Class -> Asset Name,
      // with the uploaded Excel Family Name shown on every asset row.
      const groupKey = holding.sub_class || 'Unassigned';
      const existing = this.groups.get(groupKey) ?? [];
      existing.push(holding);
      this.groups.set(groupKey, existing);
    }
  }

  downloadExcel(): void {
    if (this.downloading) {
      return;
    }

    this.downloading = true;
    this.service.downloadReport().subscribe({
      next: (blob) => {
        const filename = 'MIS_Report_' + this.safeFilename(this.report?.family_name ?? 'Family') + '_' + (this.report?.reporting_date ?? '') + '.xlsx';
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename;
        anchor.click();
        URL.revokeObjectURL(url);
        this.downloading = false;
        this.cdr.markForCheck();
      },
      error: () => {
        this.downloading = false;
        this.error = 'Unable to download the MIS Report Excel file. Please try again.';
        this.cdr.markForCheck();
      },
    });
  }

  private safeFilename(value: string): string {
    return value.replace(/[^a-zA-Z0-9._-]+/g, '_').replace(/^[_\.]+|[_\.]+$/g, '') || 'Family';
  }

  formatCurrency(value: number | null | undefined): string {
    if (value === null || value === undefined) {
      return '—';
    }
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 2,
    }).format(value);
  }

  formatPercent(value: number | null | undefined): string {
    return value === null || value === undefined ? '—' : value.toFixed(2) + '%';
  }

  trackByHolding(_index: number, holding: MISHolding): string {
    return holding.asset_class + '-' + holding.sub_class + '-' + holding.asset_name + '-' + (holding.isin ?? holding.asset_id ?? _index);
  }
}
