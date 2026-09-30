import { CommonModule } from '@angular/common';
import { Component, inject, OnInit } from '@angular/core';

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

  report: MISReport | null = null;
  loading = true;
  downloading = false;
  error = '';

  readonly groups = new Map<string, MISHolding[]>();

  ngOnInit(): void {
    this.loadReport();
  }

  loadReport(): void {
    this.loading = true;
    this.error = '';

    this.service.getReport().subscribe({
      next: (report) => {
        this.report = report;
        this.rebuildGroups();
        this.loading = false;
      },
      error: (error) => {
        this.loading = false;
        this.error = error?.status === 403
          ? 'You do not have access to an active family portfolio.'
          : 'Unable to load the MIS Report. Please try again.';
      },
    });
  }

  rebuildGroups(): void {
    this.groups.clear();
    for (const holding of this.report?.holdings ?? []) {
      const existing = this.groups.get(holding.asset_class) ?? [];
      existing.push(holding);
      this.groups.set(holding.asset_class, existing);
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
      },
      error: () => {
        this.downloading = false;
        this.error = 'Unable to download the MIS Report Excel file. Please try again.';
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
