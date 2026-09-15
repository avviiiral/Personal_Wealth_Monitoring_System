import { CommonModule } from '@angular/common';
import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HoldingReportRow, PortfolioApiService } from '../../core/services/portfolio-api.service';

interface HoldingGroup {
  key: string;
  asset_name: string;
  row: HoldingReportRow;
}

interface SubClassGroup {
  sub_class: string;
  holdings: HoldingGroup[];
  quantity: number;
  invested_value: number;
  current_value: number;
  pnl: number;
  xirr: number | null;
}

interface HoldingExportRow {
  family_name: string;
  portfolio: string;
  asset_class: string;
  sub_class: string;
  asset_name: string;
  underlying: string;
  isin: string;
  advisors: string;
  quantity: number;
  average_cost: number;
  invested_value: number;
  current_price: number;
  current_value: number;
  gain: number;
  pnl_percentage: number;
  xirr: number | null;
  sector: string;
  cap_type: string;
  amc_name: string;
}

const UNASSIGNED = 'Unassigned';

@Component({
  selector: 'app-holding-reports',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './holding-reports.component.html',
  styleUrl: './holding-reports.component.scss',
})
export class HoldingReportsComponent implements OnInit {
  private readonly portfolioApi = inject(PortfolioApiService);
  private readonly changeDetectorRef = inject(ChangeDetectorRef);

  holdingRows: HoldingReportRow[] = [];
  loading = true;
  error = '';

  selectedFamily = '';
  selectedAssetClass = '';
  expandedSubClass = '';
  expandedAssetName = '';
  downloading = false;

  ngOnInit(): void {
    this.loadHoldings();
  }

  loadHoldings(): void {
    this.loading = true;
    this.error = '';
    this.changeDetectorRef.detectChanges();

    this.portfolioApi.getHoldingReport().subscribe({
      next: (response) => {
        this.holdingRows = response.results ?? [];
        this.validateSelections();
        this.loading = false;
        this.changeDetectorRef.detectChanges();
      },
      error: (error) => {
        console.error('Holding report API error:', error);
        this.loading = false;
        this.error = error?.status === 401 || error?.status === 403
          ? 'Authentication failed. Please log in again.'
          : 'Unable to load holding report data.';
        this.changeDetectorRef.detectChanges();
      },
    });
  }

  refresh(): void {
    this.loadHoldings();
  }

  private clean(value: string | null | undefined, fallback = UNASSIGNED): string {
    const trimmed = value?.trim();
    return trimmed || fallback;
  }

  get familyOptions(): string[] {
    return Array.from(new Set(this.holdingRows.map((row) => this.clean(row.family_name))))
      .sort((a, b) => a.localeCompare(b));
  }

  get assetClassOptions(): string[] {
    return Array.from(new Set(this.filteredRows.map((row) => this.clean(row.asset_class))))
      .sort((a, b) => a.localeCompare(b));
  }

  private get filteredRows(): HoldingReportRow[] {
    return this.holdingRows.filter((row) => {
      const familyMatches = !this.selectedFamily || this.clean(row.family_name) === this.selectedFamily;
      const classMatches = !this.selectedAssetClass || this.clean(row.asset_class) === this.selectedAssetClass;
      return familyMatches && classMatches;
    });
  }

  get subClassGroups(): SubClassGroup[] {
    const groups = new Map<string, HoldingReportRow[]>();

    for (const row of this.filteredRows) {
      const subClass = this.clean(row.sub_class);
      if (!groups.has(subClass)) {
        groups.set(subClass, []);
      }
      groups.get(subClass)!.push(row);
    }

    return Array.from(groups.entries())
      .map(([sub_class, rows]) => {
        const holdings = rows
          .map((row) => ({
            key: `${row.owner_id}::${row.family_name}::${row.portfolio}::${row.asset_class}::${row.asset_id}`,
            asset_name: this.clean(row.asset_name),
            row,
          }))
          .sort((a, b) => a.asset_name.localeCompare(b.asset_name));

        const invested_value = rows.reduce((sum, row) => sum + this.toNumber(row.invested_value), 0);
        const current_value = rows.reduce((sum, row) => sum + this.toNumber(row.current_value), 0);
        const pnl = rows.reduce((sum, row) => sum + this.toNumber(row.gain), 0);

        return {
          sub_class,
          holdings,
          quantity: rows.reduce((sum, row) => sum + this.toNumber(row.quantity), 0),
          invested_value,
          current_value,
          pnl,
          xirr: null,
        };
      })
      .sort((a, b) => a.sub_class.localeCompare(b.sub_class));
  }

  get holdingCount(): number {
    return this.filteredRows.length;
  }

  selectFamily(family: string): void {
    this.selectedFamily = this.selectedFamily === family ? '' : family;
    this.selectedAssetClass = '';
    this.resetExpansion();
  }

  selectAssetClass(assetClass: string): void {
    this.selectedAssetClass = this.selectedAssetClass === assetClass ? '' : assetClass;
    this.resetExpansion();
  }

  clearFamily(): void {
    this.selectedFamily = '';
    this.selectedAssetClass = '';
    this.resetExpansion();
  }

  clearAssetClass(): void {
    this.selectedAssetClass = '';
    this.resetExpansion();
  }

  isFamilySelected(family: string): boolean {
    return this.selectedFamily === family;
  }

  isAssetClassSelected(assetClass: string): boolean {
    return this.selectedAssetClass === assetClass;
  }

  toggleSubClass(subClass: string): void {
    this.expandedSubClass = this.expandedSubClass === subClass ? '' : subClass;
    this.expandedAssetName = '';
  }

  toggleAssetName(key: string): void {
    this.expandedAssetName = this.expandedAssetName === key ? '' : key;
  }

  getAssetKey(subClass: string, holding: HoldingGroup): string {
    return `${subClass}::${holding.key}`;
  }

  trackBySubClass(_index: number, group: SubClassGroup): string {
    return group.sub_class;
  }

  trackByHolding(_index: number, group: HoldingGroup): string {
    return group.key;
  }

  formatCurrency(value: number): string {
    return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(this.toNumber(value));
  }

  formatAbsoluteCurrency(value: number): string {
    return this.formatCurrency(Math.abs(this.toNumber(value)));
  }

  formatNumber(value: number): string {
    return new Intl.NumberFormat('en-IN', {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    }).format(this.toNumber(value));
  }

  formatDecimal(value: number): string {
    return new Intl.NumberFormat('en-IN', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(this.toNumber(value));
  }

  formatPercentage(value: number | null): string {
    return value === null || value === undefined ? '-' : `${this.formatDecimal(value)}%`;
  }

  getPnlClass(value: number): string {
    return value > 0 ? 'positive' : value < 0 ? 'negative' : 'neutral';
  }

  async downloadHoldingReport(): Promise<void> {
    if (this.downloading) {
      return;
    }

    const rows = this.flattenFilteredHoldings();
    if (!rows.length) {
      this.error = 'There are no holdings available for the selected filters.';
      return;
    }

    this.downloading = true;

    try {
      const { default: ExcelJSLib } = await import('exceljs');
      const workbook = new ExcelJSLib.Workbook();
      workbook.creator = 'PWMS';
      workbook.created = new Date();

      const familyLabel = this.selectedFamily || 'All Families';
      const assetClassLabel = this.selectedAssetClass || 'All Asset Classes';
      const sheet = workbook.addWorksheet('Holdings', {
        views: [{ state: 'frozen', ySplit: 2 }],
      });

      const columns = [
        { header: 'Family Name', key: 'family_name', width: 24 },
        { header: 'Portfolio', key: 'portfolio', width: 24 },
        { header: 'Asset Class', key: 'asset_class', width: 18 },
        { header: 'Sub Class', key: 'sub_class', width: 22 },
        { header: 'Asset Name', key: 'asset_name', width: 30 },
        { header: 'Underlying', key: 'underlying', width: 28 },
        { header: 'ISIN', key: 'isin', width: 18 },
        { header: 'Advisor', key: 'advisors', width: 24 },
        { header: 'Quantity', key: 'quantity', width: 14, numFmt: '#,##,##0.00' },
        { header: 'Average Cost', key: 'average_cost', width: 16, numFmt: '"₹"#,##,##0.00' },
        { header: 'Invested Value', key: 'invested_value', width: 18, numFmt: '"₹"#,##,##0' },
        { header: 'Current Price / NAV', key: 'current_price', width: 18, numFmt: '"₹"#,##,##0.00' },
        { header: 'Current Value', key: 'current_value', width: 18, numFmt: '"₹"#,##,##0' },
        { header: 'Gain', key: 'gain', width: 18, numFmt: '"₹"#,##,##0' },
        { header: 'Gain %', key: 'pnl_percentage', width: 14, numFmt: '0.00"%"' },
        { header: 'XIRR (%)', key: 'xirr', width: 14, numFmt: '0.00"%"' },
        { header: 'Sector', key: 'sector', width: 24 },
        { header: 'Cap Type', key: 'cap_type', width: 16 },
        { header: 'AMC', key: 'amc_name', width: 24 },
      ];

      sheet.mergeCells(1, 1, 1, columns.length);
      const titleCell = sheet.getCell(1, 1);
      titleCell.value = `Holding Report — ${familyLabel} — ${assetClassLabel} (as of ${this.todayLabel()})`;
      titleCell.font = { bold: true, size: 12, color: { argb: 'FFFFFFFF' } };
      titleCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF111827' } };
      titleCell.alignment = { vertical: 'middle', horizontal: 'left' };
      sheet.getRow(1).height = 26;

      const headerRow = sheet.getRow(2);
      columns.forEach((column, index) => {
        const cell = headerRow.getCell(index + 1);
        cell.value = column.header;
        cell.font = { bold: true, color: { argb: 'FF101828' } };
        cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFF8FAFC' } };
      });

      sheet.columns = columns.map((column) => ({
        key: column.key,
        width: column.width,
        style: column.numFmt ? { numFmt: column.numFmt } : undefined,
      }));

      rows.forEach((rowData, index) => {
        const row = sheet.addRow(rowData);
        row.eachCell((cell) => {
          if (index % 2 === 1) {
            cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFF8FAFC' } };
          }
        });

        row.getCell('gain').font = {
          color: { argb: Number(rowData.gain) >= 0 ? 'FF16A34A' : 'FFDC2626' },
          bold: true,
        };
      });

      sheet.autoFilter = {
        from: { row: 2, column: 1 },
        to: { row: 2, column: columns.length },
      };

      const buffer = await workbook.xlsx.writeBuffer();
      const blob = new Blob([buffer], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `holding_report${this.selectedFamily ? '_' + this.slugify(this.selectedFamily) : ''}${this.selectedAssetClass ? '_' + this.slugify(this.selectedAssetClass) : ''}_${this.todayStamp()}.xlsx`;
      anchor.style.display = 'none';
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(url);
    } finally {
      this.downloading = false;
    }
  }

  private flattenFilteredHoldings(): HoldingExportRow[] {
    return this.filteredRows
      .map((row) => ({
        family_name: this.clean(row.family_name),
        portfolio: this.clean(row.portfolio),
        asset_class: this.clean(row.asset_class),
        sub_class: this.clean(row.sub_class),
        asset_name: this.clean(row.asset_name),
        underlying: this.clean(row.underlying, ''),
        isin: row.isin || '-',
        advisors: this.clean(row.advisors, ''),
        quantity: this.toNumber(row.quantity),
        average_cost: this.toNumber(row.average_cost),
        invested_value: this.toNumber(row.invested_value),
        current_price: this.toNumber(row.current_price),
        current_value: this.toNumber(row.current_value),
        gain: this.toNumber(row.gain),
        pnl_percentage: this.toNumber(row.gain_percentage),
        xirr: row.xirr,
        sector: row.sector || '-',
        cap_type: row.cap_type || '-',
        amc_name: row.amc_name || '-',
      }))
      .sort((a, b) =>
        a.family_name.localeCompare(b.family_name) ||
        a.portfolio.localeCompare(b.portfolio) ||
        a.asset_class.localeCompare(b.asset_class) ||
        a.sub_class.localeCompare(b.sub_class) ||
        a.asset_name.localeCompare(b.asset_name),
      );
  }

  private validateSelections(): void {
    if (this.selectedFamily && !this.familyOptions.includes(this.selectedFamily)) {
      this.selectedFamily = '';
      this.selectedAssetClass = '';
    }

    if (this.selectedAssetClass && !this.assetClassOptions.includes(this.selectedAssetClass)) {
      this.selectedAssetClass = '';
    }
  }

  private resetExpansion(): void {
    this.expandedSubClass = '';
    this.expandedAssetName = '';
  }

  private slugify(value: string): string {
    return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  }

  private todayStamp(): string {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  }

  private todayLabel(): string {
    return new Intl.DateTimeFormat('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    }).format(new Date());
  }

  private toNumber(value: number | null | undefined): number {
    if (value === null || value === undefined) {
      return 0;
    }
    const numberValue = Number(value);
    return Number.isFinite(numberValue) ? numberValue : 0;
  }
}
