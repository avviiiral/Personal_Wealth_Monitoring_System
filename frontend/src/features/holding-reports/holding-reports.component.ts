import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { PortfolioApiService, FamilyNode, PortfolioAssetNode } from '../../core/services/portfolio-api.service';

interface HoldingRow {
  family_name: string;
  portfolio: string;
  asset_class: string;
  sub_class: string;
  asset: PortfolioAssetNode;
}

interface HoldingGroup {
  key: string;
  asset_name: string;
  holdings: HoldingRow[];
  quantity: number;
  invested_value: number;
  current_value: number;
  pnl: number;
  xirr: number | null;
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

  portfolioTree: FamilyNode[] = [];
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

    this.portfolioApi.getPortfolioTree().subscribe({
      next: (response) => {
        this.portfolioTree = response.families ?? [];
        this.validateSelections();
        this.loading = false;
      },
      error: (error) => {
        console.error('Holding report API error:', error);
        this.loading = false;
        this.error = error?.status === 401 || error?.status === 403
          ? 'Authentication failed. Please log in again.'
          : 'Unable to load holding report data.';
      },
    });
  }

  refresh(): void {
    this.loadHoldings();
  }

  private clean(value: string | null | undefined): string {
    const trimmed = value?.trim();
    return trimmed || UNASSIGNED;
  }

  get familyOptions(): string[] {
    return Array.from(new Set(this.portfolioTree.map((family) => this.clean(family.family_name))))
      .sort((a, b) => a.localeCompare(b));
  }

  get assetClassOptions(): string[] {
    const classes = new Set<string>();

    for (const family of this.filteredFamilies) {
      for (const portfolio of family.portfolios) {
        for (const assetClass of portfolio.asset_classes) {
          classes.add(this.clean(assetClass.asset_class));
        }
      }
    }

    return Array.from(classes).sort((a, b) => a.localeCompare(b));
  }

  private get filteredFamilies(): FamilyNode[] {
    if (!this.selectedFamily) {
      return this.portfolioTree;
    }

    return this.portfolioTree.filter(
      (family) => this.clean(family.family_name) === this.selectedFamily,
    );
  }

  get subClassGroups(): SubClassGroup[] {
    const groups = new Map<string, HoldingRow[]>();

    for (const family of this.filteredFamilies) {
      for (const portfolio of family.portfolios) {
        for (const assetClass of portfolio.asset_classes) {
          const assetClassName = this.clean(assetClass.asset_class);

          if (this.selectedAssetClass && assetClassName !== this.selectedAssetClass) {
            continue;
          }

          for (const subClass of assetClass.sub_classes) {
            const subClassName = this.clean(subClass.sub_class);

            if (!groups.has(subClassName)) {
              groups.set(subClassName, []);
            }

            for (const asset of subClass.assets) {
              groups.get(subClassName)!.push({
                family_name: this.clean(family.family_name),
                portfolio: this.clean(portfolio.portfolio),
                asset_class: assetClassName,
                sub_class: subClassName,
                asset,
              });
            }
          }
        }
      }
    }

    return Array.from(groups.entries())
      .map(([sub_class, rows]) => {
        const holdings = this.buildHoldingGroups(rows);
        const invested_value = holdings.reduce((sum, group) => sum + group.invested_value, 0);
        const current_value = holdings.reduce((sum, group) => sum + group.current_value, 0);
        const pnl = holdings.reduce((sum, group) => sum + group.pnl, 0);

        return {
          sub_class,
          holdings,
          quantity: holdings.reduce((sum, group) => sum + group.quantity, 0),
          invested_value,
          current_value,
          pnl,
          xirr: this.weightedXirr(holdings),
        };
      })
      .sort((a, b) => a.sub_class.localeCompare(b.sub_class));
  }

  get holdingCount(): number {
    return this.flattenFilteredHoldings().length;
  }

  private buildHoldingGroups(rows: HoldingRow[]): HoldingGroup[] {
    const groups = new Map<string, HoldingRow[]>();

    for (const row of rows) {
      const key = `${row.family_name}::${row.portfolio}::${row.asset_class}::${row.sub_class}::${row.asset.id}`;
      if (!groups.has(key)) {
        groups.set(key, []);
      }
      groups.get(key)!.push(row);
    }

    return Array.from(groups.entries())
      .map(([key, holdingRows]) => {
        const asset = holdingRows[0].asset;
        return {
          key,
          asset_name: this.clean(asset.asset_name),
          holdings: holdingRows,
          quantity: this.toNumber(asset.quantity),
          invested_value: this.toNumber(asset.invested_value),
          current_value: this.toNumber(asset.current_value),
          pnl: this.toNumber(asset.pnl),
          xirr: asset.xirr,
        };
      })
      .sort((a, b) => a.asset_name.localeCompare(b.asset_name));
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
        cell.border = {
          top: { style: 'thin', color: { argb: 'FFE5E7EB' } },
          bottom: { style: 'thin', color: { argb: 'FFE5E7EB' } },
          left: { style: 'thin', color: { argb: 'FFE5E7EB' } },
          right: { style: 'thin', color: { argb: 'FFE5E7EB' } },
        };
      });

      sheet.columns = columns.map((column) => ({
        key: column.key,
        width: column.width,
        style: column.numFmt ? { numFmt: column.numFmt } : undefined,
      }));

      rows.forEach((rowData, index) => {
        const row = sheet.addRow(rowData);
        row.eachCell((cell) => {
          cell.border = {
            top: { style: 'thin', color: { argb: 'FFEEF0F3' } },
            bottom: { style: 'thin', color: { argb: 'FFEEF0F3' } },
            left: { style: 'thin', color: { argb: 'FFEEF0F3' } },
            right: { style: 'thin', color: { argb: 'FFEEF0F3' } },
          };
          if (index % 2 === 1) {
            cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFF8FAFC' } };
          }
        });

        const gainCell = row.getCell('gain');
        gainCell.font = {
          color: Number(rowData.gain) >= 0 ? 'FF16A34A' : 'FFDC2626',
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

  private flattenFilteredHoldings(): Record<string, unknown>[] {
    const rows: Record<string, unknown>[] = [];

    for (const family of this.filteredFamilies) {
      for (const portfolio of family.portfolios) {
        for (const assetClass of portfolio.asset_classes) {
          const assetClassName = this.clean(assetClass.asset_class);
          if (this.selectedAssetClass && assetClassName !== this.selectedAssetClass) {
            continue;
          }

          for (const subClass of assetClass.sub_classes) {
            for (const asset of subClass.assets) {
              rows.push({
                family_name: this.clean(family.family_name),
                portfolio: this.clean(portfolio.portfolio),
                asset_class: assetClassName,
                sub_class: this.clean(subClass.sub_class),
                asset_name: this.clean(asset.asset_name),
                underlying: this.clean(asset.underlying || asset.asset_name),
                isin: asset.isin || '-',
                advisors: asset.advisors || '-',
                quantity: this.toNumber(asset.quantity),
                average_cost: this.toNumber(asset.average_cost),
                invested_value: this.toNumber(asset.invested_value),
                current_price: this.toNumber(asset.current_price),
                current_value: this.toNumber(asset.current_value),
                gain: this.toNumber(asset.pnl),
                pnl_percentage: this.toNumber(asset.pnl_percentage),
                xirr: asset.xirr,
                sector: asset.sector || '-',
                cap_type: asset.cap_type || '-',
                amc_name: asset.amc_name || '-',
              });
            }
          }
        }
      }
    }

    return rows.sort(
      (a, b) => String(a.family_name).localeCompare(String(b.family_name)) ||
        String(a.portfolio).localeCompare(String(b.portfolio)) ||
        String(a.asset_class).localeCompare(String(b.asset_class)) ||
        String(a.sub_class).localeCompare(String(b.sub_class)) ||
        String(a.asset_name).localeCompare(String(b.asset_name)),
    );
  }

  private weightedXirr(groups: HoldingGroup[]): number | null {
    const valid = groups.filter((group) => group.xirr !== null && group.invested_value > 0);
    if (!valid.length) {
      return null;
    }

    const totalInvested = valid.reduce((sum, group) => sum + group.invested_value, 0);
    return totalInvested
      ? valid.reduce((sum, group) => sum + (group.xirr as number) * group.invested_value, 0) / totalInvested
      : null;
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
