import { CommonModule } from '@angular/common';
import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import {
  FamilyNode,
  HoldingReportRow,
  MarketCapReportRow,
  PortfolioApiService,
  PortfolioAssetNode,
  Transaction,
} from '../../core/services/portfolio-api.service';
import { WatchListApiService, WatchListProduct } from '../../core/services/watch-list-api.service';

type ReportId =
  | 'portfolio-summary'
  | 'portfolio-detailed'
  | 'sub-class-holdings'
  | 'asset-name-transactions'
  | 'holding-report'
  | 'asset-class-xirr'
  | 'sub-class-xirr'
  | 'asset-name-xirr'
  | 'watch-list'
  | 'market-cap'
  | 'holding-matrix';

interface ReportDefinition {
  id: ReportId;
  name: string;
  type: string;
  description: string;
  filters: string;
}

@Component({
  selector: 'app-downloads',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './downloads.component.html',
  styleUrl: './downloads.component.scss',
})
export class DownloadsComponent implements OnInit {
  private readonly portfolioApi = inject(PortfolioApiService);
  private readonly watchListApi = inject(WatchListApiService);
  private readonly cdr = inject(ChangeDetectorRef);

  readonly reports: ReportDefinition[] = [
    { id: 'portfolio-summary', name: 'Portfolio Summary', type: 'Portfolio Summary', description: 'Sub Class level portfolio values, gain/loss and XIRR.', filters: 'Family' },
    { id: 'portfolio-detailed', name: 'Portfolio Detailed', type: 'Transaction Report', description: 'Transaction-level portfolio activity for a selected date range.', filters: 'Family + Date Range' },
    { id: 'sub-class-holdings', name: 'Sub Class Holdings', type: 'Holdings Report', description: 'Current holdings belonging to a selected Sub Class.', filters: 'Family + Asset Class + Sub Class' },
    { id: 'asset-name-transactions', name: 'Asset Name Transactions', type: 'Transaction Report', description: 'All transactions for one selected Asset Name.', filters: 'Family + Asset Class + Sub Class + Asset Name' },
    { id: 'holding-report', name: 'Holding Report', type: 'Holdings Report', description: 'Current holdings with quantity, invested value, current value, gain and XIRR.', filters: 'Family + Asset Class' },
    { id: 'asset-class-xirr', name: 'Asset Class XIRR', type: 'Performance / XIRR', description: 'Asset Class level XIRR performance.', filters: 'Family + Asset Class' },
    { id: 'sub-class-xirr', name: 'Sub Class XIRR', type: 'Performance / XIRR', description: 'Sub Class level XIRR performance.', filters: 'Family + Asset Class + Sub Class' },
    { id: 'asset-name-xirr', name: 'Asset Name XIRR', type: 'Performance / XIRR', description: 'Asset Name level XIRR performance, matching Portfolio.', filters: 'Family + Asset Class + Sub Class + Asset Name' },
    { id: 'watch-list', name: 'Watch List', type: 'Watch List Report', description: 'Currently watchlisted Mutual Funds and/or PMS products.', filters: 'Product Type' },
    { id: 'market-cap', name: 'Market Cap', type: 'Equity Allocation Report', description: 'Equity PMS, Direct Equity and Equity Mutual Fund exposure grouped by market capitalization.', filters: 'Family' },
    { id: 'holding-matrix', name: 'Holding Matrix', type: 'Equity Concentration Report', description: 'Equity PMS and Direct Equity holdings aggregated by security.', filters: 'Family' },
  ];

  transactions: Transaction[] = [];
  holdingRows: HoldingReportRow[] = [];
  portfolioTree: FamilyNode[] = [];
  watchListProducts: WatchListProduct[] = [];
  marketCapRows: MarketCapReportRow[] = [];

  selectedReport: ReportId = 'portfolio-summary';
  selectedFamily = '';
  selectedAssetClass = '';
  selectedSubClass = '';
  selectedAssetName = '';
  selectedWatchListType: 'ALL' | 'MUTUAL_FUND' | 'PMS' = 'ALL';
  fromDate = '';
  toDate = '';

  loading = true;
  reportLoading = false;
  downloading = false;
  error = '';
  success = '';

  private transactionsLoaded = false;
  private holdingsLoaded = false;
  private portfolioTreeLoaded = false;
  private marketCapLoaded = false;

  ngOnInit(): void {
    this.load();
  }

  async load(): Promise<void> {
    this.error = '';
    this.loading = false;
    this.reportLoading = true;
    this.cdr.detectChanges();

    try {
      await this.loadDataForReport(this.selectedReport, true);
      this.validateSelections();
    } catch (error) {
      console.error('Download page load failed:', error);
      this.error = 'Unable to load report data.';
    } finally {
      this.reportLoading = false;
      this.cdr.detectChanges();
    }
  }

  private async loadDataForReport(report: ReportId, force = false): Promise<void> {
    if (report === 'watch-list') return;

    if (report === 'portfolio-summary') {
      if (this.portfolioTreeLoaded && !force) return;
      const treeResponse = await firstValueFrom(this.portfolioApi.getPortfolioTree());
      this.portfolioTree = treeResponse.families ?? [];
      this.portfolioTreeLoaded = true;
      return;
    }

    if (report === 'portfolio-detailed' || report === 'asset-name-transactions') {
      if (this.transactionsLoaded && !force) return;
      const response = await firstValueFrom(this.portfolioApi.getTransactions());
      this.transactions = response.results ?? [];
      this.setTransactionDateRange();
      this.transactionsLoaded = true;
      return;
    }

    if (report === 'market-cap') {
      if (this.marketCapLoaded && !force) return;
      const response = await firstValueFrom(this.portfolioApi.getEquityMarketCapReport());
      this.marketCapRows = response.results ?? [];
      this.marketCapLoaded = true;
      return;
    }

    if (this.holdingsLoaded && !force) return;
    const holdingResponse = await firstValueFrom(this.portfolioApi.getHoldingReport());
    this.holdingRows = holdingResponse.results ?? [];
    this.holdingsLoaded = true;
  }

  private setTransactionDateRange(): void {
    const dates = this.transactions.map(tx => tx.transaction_date).filter(Boolean).sort();
    this.fromDate = dates[0] ?? '';
    this.toDate = dates[dates.length - 1] ?? '';
  }

  get familyOptions(): string[] {
    return Array.from(new Set([
      ...this.transactions.map(tx => this.clean(tx.family_name)),
      ...this.holdingRows.map(row => this.clean(row.family_name)),
      ...this.portfolioTree.map(family => this.clean(family.family_name)),
    ])).sort((a, b) => a.localeCompare(b));
  }

  get assetClassOptions(): string[] {
    return Array.from(new Set(this.holdingRows
      .filter(row => !this.selectedFamily || this.clean(row.family_name) === this.selectedFamily)
      .map(row => this.clean(row.asset_class))))
      .sort((a, b) => a.localeCompare(b));
  }

  get subClassOptions(): string[] {
    return Array.from(new Set(this.holdingRows
      .filter(row => (!this.selectedFamily || this.clean(row.family_name) === this.selectedFamily)
        && (!this.selectedAssetClass || this.clean(row.asset_class) === this.selectedAssetClass))
      .map(row => this.clean(row.sub_class))))
      .sort((a, b) => a.localeCompare(b));
  }

  get assetNameOptions(): string[] {
    return Array.from(new Set(this.holdingRows
      .filter(row => (!this.selectedFamily || this.clean(row.family_name) === this.selectedFamily)
        && (!this.selectedAssetClass || this.clean(row.asset_class) === this.selectedAssetClass)
        && (!this.selectedSubClass || this.clean(row.sub_class) === this.selectedSubClass))
      .map(row => this.clean(row.asset_name))))
      .sort((a, b) => a.localeCompare(b));
  }

  get selectedDefinition(): ReportDefinition {
    return this.reports.find(report => report.id === this.selectedReport) ?? this.reports[0];
  }

  async selectReport(id: ReportId): Promise<void> {
    if (this.selectedReport === id) return;

    this.selectedReport = id;
    this.success = '';
    this.error = '';
    this.reportLoading = true;

    try {
      await this.loadDataForReport(id);
      this.validateSelections();
    } catch (error) {
      console.error('Download report data load failed:', error);
      this.error = 'Unable to load this report data.';
    } finally {
      this.reportLoading = false;
      this.cdr.detectChanges();
    }
  }

  clearFilters(): void {
    this.selectedFamily = '';
    this.selectedAssetClass = '';
    this.selectedSubClass = '';
    this.selectedAssetName = '';
    this.selectedWatchListType = 'ALL';
    const dates = this.transactions.map(tx => tx.transaction_date).filter(Boolean).sort();
    this.fromDate = dates[0] ?? '';
    this.toDate = dates[dates.length - 1] ?? '';
  }

  onFamilyChange(): void {
    this.selectedAssetClass = '';
    this.selectedSubClass = '';
    this.selectedAssetName = '';
  }

  onAssetClassChange(): void {
    this.selectedSubClass = '';
    this.selectedAssetName = '';
  }

  onSubClassChange(): void {
    this.selectedAssetName = '';
  }

  async download(): Promise<void> {
    if (this.downloading) return;
    this.error = '';
    this.success = '';
    this.downloading = true;

    try {
      await this.loadDataForReport(this.selectedReport);
      switch (this.selectedReport) {
        case 'portfolio-summary': await this.downloadPortfolioSummary(); break;
        case 'portfolio-detailed': await this.downloadPortfolioDetailed(); break;
        case 'sub-class-holdings': await this.downloadSubClassHoldings(); break;
        case 'asset-name-transactions': await this.downloadAssetNameTransactions(); break;
        case 'holding-report': await this.downloadHoldingReport(); break;
        case 'asset-class-xirr': await this.downloadXirr('asset-class'); break;
        case 'sub-class-xirr': await this.downloadXirr('sub-class'); break;
        case 'asset-name-xirr': await this.downloadXirr('asset-name'); break;
        case 'watch-list': await this.downloadWatchList(); break;
        case 'market-cap': await this.downloadMarketCap(); break;
        case 'holding-matrix': await this.downloadHoldingMatrix(); break;
      }
      this.success = `${this.selectedDefinition.name} downloaded successfully.`;
    } catch (error) {
      console.error('Report download failed:', error);
      this.error = 'Unable to prepare this report right now.';
    } finally {
      this.downloading = false;
    }
  }

  private async downloadPortfolioSummary(): Promise<void> {
    const rows: Record<string, unknown>[] = [];
    for (const family of this.portfolioTree) {
      if (this.selectedFamily && family.family_name !== this.selectedFamily) continue;
      for (const portfolio of family.portfolios) {
        for (const assetClass of portfolio.asset_classes) {
          if (this.selectedAssetClass && assetClass.asset_class !== this.selectedAssetClass) continue;
          for (const subClass of assetClass.sub_classes) {
            const assets = subClass.assets;
            rows.push({
              family_name: family.family_name,
              sub_class: subClass.sub_class || 'Unassigned',
              quantity: this.sum(assets, 'quantity'),
              invested_value: this.sum(assets, 'invested_value'),
              current_value: this.sum(assets, 'current_value'),
              gain: this.sum(assets, 'pnl'),
              xirr: this.firstNumber(assets.map(asset => asset.sub_class_xirr ?? asset.xirr)),
            });
          }
        }
      }
    }
    await this.exportWorkbook('Summary', 'Portfolio Summary', [
      ['Family', 'family_name'], ['Sub Class', 'sub_class'], ['Quantity', 'quantity'],
      ['Invested Amount', 'invested_value'], ['Current Value', 'current_value'], ['Gain/Loss', 'gain'], ['XIRR (%)', 'xirr'],
    ], rows, 'portfolio_summary');
  }

  private async downloadPortfolioDetailed(): Promise<void> {
    const rows = this.transactions
      .filter(tx => this.matchesTransaction(tx) && (!this.fromDate || tx.transaction_date >= this.fromDate) && (!this.toDate || tx.transaction_date <= this.toDate))
      .map(tx => ({
        family_name: this.clean(tx.family_name),
        sub_class: this.clean(tx.sub_class),
        asset_name: this.clean(tx.asset_name),
        underlying: this.clean(tx.underlying || tx.asset_name),
        isin: tx.isin || '-',
        transaction_date: tx.transaction_date,
        transaction_type: tx.transaction_type_display || tx.transaction_type,
        quantity: Number(tx.quantity || 0),
        price: Number(tx.price_per_unit || 0),
        amount: Number(tx.amount || 0),
      }));
    await this.exportWorkbook('Detailed', 'Portfolio Detailed', [
      ['Family', 'family_name'], ['Sub Class', 'sub_class'], ['Asset Name', 'asset_name'], ['Underlying', 'underlying'], ['ISIN', 'isin'],
      ['Transaction Date', 'transaction_date'], ['Transaction Type', 'transaction_type'], ['Quantity', 'quantity'],
      ['Price', 'price'], ['Amount', 'amount'],
    ], rows, 'portfolio_detailed');
  }

  private async downloadSubClassHoldings(): Promise<void> {
    const rows = this.buildSubClassHoldingExportRows(
      this.filteredHoldingRows()
        .filter(row => !this.selectedSubClass || this.clean(row.sub_class) === this.selectedSubClass),
    );
    await this.exportWorkbook('Holdings', 'Sub Class Holdings', this.subClassHoldingColumns(), rows, 'sub_class_holdings');
  }

  private buildSubClassHoldingExportRows(rows: HoldingReportRow[]): Record<string, unknown>[] {
    const groups = new Map<string, {
      family_name: string;
      sub_class: string;
      asset_classes: Set<string>;
      quantity: number;
      invested_value: number;
      current_value: number;
      gain: number;
      xirr_inputs: { invested_value: number; xirr: number | null }[];
    }>();

    for (const row of rows) {
      const family = this.clean(row.family_name);
      const subClass = this.clean(row.sub_class);
      const key = family + '::' + subClass;
      let group = groups.get(key);

      if (!group) {
        group = {
          family_name: family,
          sub_class: subClass,
          asset_classes: new Set<string>(),
          quantity: 0,
          invested_value: 0,
          current_value: 0,
          gain: 0,
          xirr_inputs: [],
        };
        groups.set(key, group);
      }

      const investedValue = Number(row.invested_value || 0);
      const currentValue = Number(row.current_value || 0);

      group.asset_classes.add(this.clean(row.asset_class));
      group.quantity += Number(row.quantity || 0);
      group.invested_value += investedValue;
      group.current_value += currentValue;
      group.gain += Number(row.gain || 0);

      group.xirr_inputs.push({
        invested_value: investedValue,
        xirr: row.asset_name_xirr,
      });
    }

    return Array.from(groups.values())
      .sort((a, b) =>
        a.family_name.localeCompare(b.family_name) ||
        a.sub_class.localeCompare(b.sub_class),
      )
      .map((group) => ({
        family_name: group.family_name,
        portfolio: 'All Portfolios',
        asset_class: group.asset_classes.size === 1
          ? Array.from(group.asset_classes)[0]
          : 'Multiple',
        sub_class: group.sub_class,
        quantity: group.quantity,
        average_cost: group.quantity ? group.invested_value / group.quantity : 0,
        invested_value: group.invested_value,
        current_price: group.quantity ? group.current_value / group.quantity : 0,
        current_value: group.current_value,
        gain: group.gain,
        gain_percentage: group.invested_value
          ? (group.gain / group.invested_value) * 100
          : 0,
        xirr: this.weightedXirr(group.xirr_inputs),
      }));
  }

  private weightedXirr(
    inputs: { invested_value: number; xirr: number | null }[],
  ): number | null {
    const valid = inputs.filter(
      item => item.xirr !== null && item.xirr !== undefined && item.invested_value > 0,
    );

    if (!valid.length) {
      return null;
    }

    const totalInvested = valid.reduce((sum, item) => sum + item.invested_value, 0);

    if (!totalInvested) {
      return null;
    }

    return valid.reduce(
      (sum, item) => sum + (item.xirr as number) * item.invested_value,
      0,
    ) / totalInvested;
  }

  private async downloadAssetNameTransactions(): Promise<void> {
    const rows = this.transactions
      .filter(tx => this.matchesTransaction(tx) && (!this.selectedAssetName || this.clean(tx.asset_name) === this.selectedAssetName))
      .map(tx => ({
        family_name: this.clean(tx.family_name),
        asset_name: this.clean(tx.asset_name),
        underlying: this.clean(tx.underlying || tx.asset_name),
        transaction_date: tx.transaction_date,
        transaction_type: tx.transaction_type_display || tx.transaction_type,
        isin: tx.isin || '-',
        quantity: Number(tx.quantity || 0),
        price: Number(tx.price_per_unit || 0),
        amount: Number(tx.amount || 0),
      }));
    await this.exportWorkbook('Transactions', 'Asset Name Transactions', [
      ['Family', 'family_name'], ['Asset Name', 'asset_name'], ['Underlying', 'underlying'], ['Transaction Date', 'transaction_date'],
      ['Transaction Type', 'transaction_type'], ['ISIN', 'isin'], ['Quantity', 'quantity'], ['Price', 'price'], ['Amount', 'amount'],
    ], rows, 'asset_name_transactions');
  }

  private async downloadHoldingReport(): Promise<void> {
    const rows = this.filteredHoldingRows().flatMap(row => this.holdingExportRows(row));
    await this.exportWorkbook('Holdings', 'Holding Report', this.holdingColumns(), rows, 'holding_report');
  }

  private holdingExportRows(row: HoldingReportRow): Record<string, unknown>[] {
    const underlyingEntries = Object.entries(row.underlying_xirr ?? {});

    if (!underlyingEntries.length) {
      return [this.holdingExportRow(row)];
    }

    return underlyingEntries.map(([underlying, data]) => {
      const percentage = Number(data.holding_percentage || 0) / 100;
      return {
        family_name: this.clean(row.family_name),
        portfolio: row.portfolio,
        asset_class: row.asset_class,
        sub_class: row.sub_class,
        asset_name: row.asset_name,
        underlying,
        isin: row.isin || '-',
        advisors: row.advisors || '-',
        quantity: Number(row.quantity || 0) * percentage,
        average_cost: Number(row.average_cost || 0),
        invested_value: Number(row.invested_value || 0) * percentage,
        current_price: Number(row.current_price || 0),
        current_value: Number(row.current_value || 0) * percentage,
        gain: Number(row.gain || 0) * percentage,
        gain_percentage: Number(row.gain_percentage || 0),
        xirr: data.xirr,
        sector: row.sector || '-',
        cap_type: row.cap_type || '-',
        amc_name: row.amc_name || '-',
      };
    });
  }

  private async downloadXirr(level: 'asset-class' | 'sub-class' | 'asset-name'): Promise<void> {
    const filtered = this.filteredHoldingRows();

    if (level === 'asset-class') {
      const groups = new Map<string, {
        family_name: string;
        asset_class: string;
        invested_value: number;
        current_value: number;
        gain: number;
        xirr_inputs: { invested_value: number; xirr: number | null }[];
      }>();

      for (const row of filtered) {
        const family = this.clean(row.family_name);
        const assetClass = this.clean(row.asset_class);
        const key = family + '::' + assetClass;
        let group = groups.get(key);

        if (!group) {
          group = {
            family_name: family,
            asset_class: assetClass,
            invested_value: 0,
            current_value: 0,
            gain: 0,
            xirr_inputs: [],
          };
          groups.set(key, group);
        }

        const investedValue = Number(row.invested_value || 0);
        group.invested_value += investedValue;
        group.current_value += Number(row.current_value || 0);
        group.gain += Number(row.gain || 0);
        group.xirr_inputs.push({
          invested_value: investedValue,
          xirr: row.asset_class_xirr,
        });
      }

      const rows = Array.from(groups.values())
        .sort((a, b) =>
          a.family_name.localeCompare(b.family_name) ||
          a.asset_class.localeCompare(b.asset_class),
        )
        .map(group => ({
          name: group.asset_class,
          family_name: group.family_name,
          asset_class: group.asset_class,
          sub_class: 'All Sub Classes',
          asset_name: 'All Assets',
          underlying: '',
          invested_value: group.invested_value,
          current_value: group.current_value,
          gain: group.gain,
          xirr: this.weightedXirr(group.xirr_inputs),
        }));

      await this.writeXirr(rows, 'Asset Class XIRR');
      return;
    }

    if (level === 'sub-class') {
      const groups = new Map<string, {
        family_name: string;
        asset_class: string;
        sub_class: string;
        invested_value: number;
        current_value: number;
        gain: number;
        xirr_inputs: { invested_value: number; xirr: number | null }[];
      }>();

      for (const row of filtered) {
        const family = this.clean(row.family_name);
        const assetClass = this.clean(row.asset_class);
        const subClass = this.clean(row.sub_class);
        const key = family + '::' + assetClass + '::' + subClass;
        let group = groups.get(key);

        if (!group) {
          group = {
            family_name: family,
            asset_class: assetClass,
            sub_class: subClass,
            invested_value: 0,
            current_value: 0,
            gain: 0,
            xirr_inputs: [],
          };
          groups.set(key, group);
        }

        const investedValue = Number(row.invested_value || 0);
        group.invested_value += investedValue;
        group.current_value += Number(row.current_value || 0);
        group.gain += Number(row.gain || 0);
        group.xirr_inputs.push({
          invested_value: investedValue,
          xirr: row.sub_class_xirr,
        });
      }

      const rows = Array.from(groups.values())
        .sort((a, b) =>
          a.family_name.localeCompare(b.family_name) ||
          a.asset_class.localeCompare(b.asset_class) ||
          a.sub_class.localeCompare(b.sub_class),
        )
        .map(group => ({
          name: group.sub_class,
          family_name: group.family_name,
          asset_class: group.asset_class,
          sub_class: group.sub_class,
          asset_name: 'All Assets',
          underlying: '',
          invested_value: group.invested_value,
          current_value: group.current_value,
          gain: group.gain,
          xirr: this.weightedXirr(group.xirr_inputs),
        }));

      await this.writeXirr(rows, 'Sub Class XIRR');
      return;
    }

    const groups = new Map<string, {
      family_name: string;
      asset_class: string;
      sub_class: string;
      asset_name: string;
      invested_value: number;
      current_value: number;
      gain: number;
      xirr_inputs: { invested_value: number; xirr: number | null }[];
    }>();

    for (const row of filtered) {
      const family = this.clean(row.family_name);
      const assetClass = this.clean(row.asset_class);
      const subClass = this.clean(row.sub_class);
      const assetName = this.clean(row.asset_name);
      const key = family + '::' + assetClass + '::' + subClass + '::' + assetName;
      let group = groups.get(key);

      if (!group) {
        group = {
          family_name: family,
          asset_class: assetClass,
          sub_class: subClass,
          asset_name: assetName,
          invested_value: 0,
          current_value: 0,
          gain: 0,
          xirr_inputs: [],
        };
        groups.set(key, group);
      }

      const investedValue = Number(row.invested_value || 0);
      group.invested_value += investedValue;
      group.current_value += Number(row.current_value || 0);
      group.gain += Number(row.gain || 0);
      group.xirr_inputs.push({
        invested_value: investedValue,
        xirr: row.asset_name_xirr,
      });
    }

    const rows = Array.from(groups.values())
      .sort((a, b) =>
        a.family_name.localeCompare(b.family_name) ||
        a.asset_class.localeCompare(b.asset_class) ||
        a.sub_class.localeCompare(b.sub_class) ||
        a.asset_name.localeCompare(b.asset_name),
      )
      .map(group => ({
        name: group.asset_name,
        family_name: group.family_name,
        asset_class: group.asset_class,
        sub_class: group.sub_class,
        asset_name: group.asset_name,
        underlying: '',
        invested_value: group.invested_value,
        current_value: group.current_value,
        gain: group.gain,
        xirr: this.weightedXirr(group.xirr_inputs),
      }));

    await this.writeXirr(rows, 'Asset Name XIRR');
  }

  private async writeXirr(rows: Record<string, unknown>[], title: string): Promise<void> {
    await this.exportWorkbook('XIRR', title, [
      ['Family', 'family_name'], ['Asset Class', 'asset_class'], ['Sub Class', 'sub_class'], ['Asset Name / Group', 'name'],
      ['Asset Name', 'asset_name'], ['Underlying', 'underlying'], ['Invested Value', 'invested_value'],
      ['Current Value', 'current_value'], ['Gain', 'gain'], ['XIRR (%)', 'xirr'],
    ], rows, title.toLowerCase().replace(/[^a-z0-9]+/g, '_'));
  }

  private equityReportType(row: HoldingReportRow): 'Direct Equity' | 'Equity PMS' | 'Equity Mutual Fund' | null {
    const assetClass = this.clean(row.asset_class).toUpperCase();
    const subClass = this.clean(row.sub_class).toUpperCase();
    const isEquity = assetClass.includes('EQUITY') || subClass.includes('EQUITY');

    if (subClass.includes('DIRECT EQUITY') || assetClass.includes('DIRECT EQUITY')) return 'Direct Equity';
    if (subClass.includes('PMS') || assetClass.includes('PMS')) return isEquity ? 'Equity PMS' : null;

    const isMutualFund = subClass.includes('MUTUAL FUND') || subClass === 'MF'
      || assetClass.includes('MUTUAL FUND') || assetClass === 'MF';
    if (isMutualFund && isEquity) return 'Equity Mutual Fund';

    return null;
  }

  private async downloadMarketCap(): Promise<void> {
    const rows: Record<string, unknown>[] = this.marketCapRows
      .filter(row => !this.selectedFamily || this.clean(row.family_name) === this.selectedFamily)
      .map(row => ({
        family_name: this.clean(row.family_name),
        asset_name: this.clean(row.asset_name),
        small_cap: row.small_cap,
        mid_cap: row.mid_cap,
        large_cap: row.large_cap,
        unclassified: row.unclassified,
      }));

    await this.exportWorkbook('Market Cap', 'Market Cap - Equity', [
      ['Family Name', 'family_name'],
      ['Asset Name', 'asset_name'],
      ['Small Cap', 'small_cap'],
      ['Mid Cap', 'mid_cap'],
      ['Large Cap', 'large_cap'],
      ['Unclassified', 'unclassified'],
    ], rows, 'market_cap');
  }

  private async downloadHoldingMatrix(): Promise<void> {
    type MatrixRow = Record<string, unknown> & { asset_name: string; [key: string]: unknown };

    const matrix = new Map<string, Map<string, number>>();
    const underlyingTotals = new Map<string, number>();
    const rowAssetNames = new Set<string>();
    const underlyings = new Set<string>();

    const addCell = (assetName: string, underlying: string, percentage: number): void => {
      const cleanAsset = this.clean(assetName);
      const cleanUnderlying = this.clean(underlying);
      if (!Number.isFinite(percentage) || percentage <= 0) return;

      rowAssetNames.add(cleanAsset);
      underlyings.add(cleanUnderlying);

      const row = matrix.get(cleanAsset) ?? new Map<string, number>();
      row.set(cleanUnderlying, (row.get(cleanUnderlying) ?? 0) + percentage);
      matrix.set(cleanAsset, row);

      underlyingTotals.set(
        cleanUnderlying,
        (underlyingTotals.get(cleanUnderlying) ?? 0) + percentage,
      );
    };

    for (const row of this.holdingRows) {
      if (this.selectedFamily && this.clean(row.family_name) !== this.selectedFamily) continue;

      const type = this.equityReportType(row);
      if (type !== 'Direct Equity' && type !== 'Equity PMS') continue;

      const currentValue = Number(row.current_value || 0);
      if (currentValue <= 0) continue;

      if (type === 'Equity PMS') {
        const entries = Object.entries(row.underlying_xirr || {});
        if (entries.length) {
          for (const [underlying, data] of entries) {
            addCell(
              row.asset_name,
              underlying,
              currentValue * Number(data.holding_percentage || 0) / 100,
            );
          }
        } else {
          // Preserve the existing fallback for PMS holdings without an
          // uploaded underlying breakdown.
          addCell(row.asset_name, row.asset_name, currentValue);
        }
      } else {
        // Direct equity is itself the underlying and therefore represents
        // 100% of that Asset Name's underlying exposure.
        addCell(row.asset_name, row.asset_name, 100);
      }
    }

    const sortedUnderlyings = Array.from(underlyings).sort((a, b) => a.localeCompare(b));
    const rows: MatrixRow[] = Array.from(rowAssetNames)
      .map(assetName => {
        const row = matrix.get(assetName) ?? new Map<string, number>();
        const output: MatrixRow = { asset_name: assetName };

        for (const underlying of sortedUnderlyings) {
          const rawShare = row.get(underlying) ?? 0;
          const underlyingTotal = underlyingTotals.get(underlying) ?? 0;
          output[underlying] = underlyingTotal > 0
            ? (rawShare / underlyingTotal) * 100
            : 0;
        }

        return output;
      })
      .sort((a, b) => a.asset_name.localeCompare(b.asset_name));

    const columns: Array<[string, string]> = [
      ['Asset Name', 'asset_name'],
      ...sortedUnderlyings.map(underlying => [underlying, underlying] as [string, string]),
    ];

    await this.exportWorkbook(
      'Holding Matrix',
      'Holding Matrix - Asset Name vs Underlying',
      columns,
      rows,
      'holding_matrix',
    );
  }

  private async downloadWatchList(): Promise<void> {
    const types: Array<'MUTUAL_FUND' | 'PMS'> = this.selectedWatchListType === 'ALL'
      ? ['MUTUAL_FUND', 'PMS'] : [this.selectedWatchListType];
    const products: WatchListProduct[] = [];

    for (const type of types) {
      let page = 1;
      while (true) {
        const response = await firstValueFrom(this.watchListApi.getProducts({
          product_type: type,
          status: 'WATCHLIST',
          ordering: 'name',
          page,
          page_size: 100,
        }));
        products.push(...response.results);
        if (!response.next || !response.results.length) break;
        page += 1;
      }
    }

    const rows = products.map(product => ({
      type: product.product_type === 'MUTUAL_FUND' ? 'Mutual Fund' : 'PMS',
      product: product.name,
      provider: product.provider || '-',
      category: product.category || '-',
      identifier: product.isin || product.external_identifier || '-',
      status: product.status,
      one_month: product.metrics?.['1M'] ?? null,
      three_month: product.metrics?.['3M'] ?? null,
      six_month: product.metrics?.['6M'] ?? null,
      one_year: product.metrics?.['1Y'] ?? null,
      three_year: product.metrics?.['3Y'] ?? null,
      five_year: product.metrics?.['5Y'] ?? null,
      cagr: product.metrics?.['CAGR'] ?? null,
      aum: product.mutual_fund?.aum ?? product.pms?.aum ?? null,
    }));

    await this.exportWorkbook('Watch List', 'Watch List', [
      ['Type', 'type'], ['Product', 'product'], ['Provider', 'provider'], ['Category', 'category'],
      ['Identifier', 'identifier'], ['Status', 'status'], ['1M', 'one_month'], ['3M', 'three_month'],
      ['6M', 'six_month'], ['1Y', 'one_year'], ['3Y', 'three_year'], ['5Y', 'five_year'], ['CAGR', 'cagr'], ['AUM', 'aum'],
    ], rows, 'watch_list');
  }

  private filteredHoldingRows(): HoldingReportRow[] {
    return this.holdingRows.filter(row =>
      (!this.selectedFamily || this.clean(row.family_name) === this.selectedFamily) &&
      (!this.selectedAssetClass || this.clean(row.asset_class) === this.selectedAssetClass) &&
      (!this.selectedSubClass || this.clean(row.sub_class) === this.selectedSubClass) &&
      (!this.selectedAssetName || this.clean(row.asset_name) === this.selectedAssetName)
    );
  }

  private matchesTransaction(tx: Transaction): boolean {
    return (!this.selectedFamily || this.clean(tx.family_name) === this.selectedFamily)
      && (!this.selectedAssetClass || this.clean(tx.asset_class) === this.selectedAssetClass)
      && (!this.selectedSubClass || this.clean(tx.sub_class) === this.selectedSubClass);
  }

  private holdingExportRow(row: HoldingReportRow): Record<string, unknown> {
    return {
      family_name: this.clean(row.family_name), portfolio: row.portfolio, asset_class: row.asset_class,
      sub_class: row.sub_class, asset_name: row.asset_name, underlying: row.underlying || row.asset_name,
      isin: row.isin || '-', advisors: row.advisors || '-', quantity: Number(row.quantity || 0),
      average_cost: Number(row.average_cost || 0), invested_value: Number(row.invested_value || 0),
      current_price: Number(row.current_price || 0), current_value: Number(row.current_value || 0),
      gain: Number(row.gain || 0), gain_percentage: Number(row.gain_percentage || 0),
      xirr: row.asset_name_xirr, sector: row.sector || '-', cap_type: row.cap_type || '-', amc_name: row.amc_name || '-',
    };
  }

  private subClassHoldingExportRow(row: HoldingReportRow): Record<string, unknown> {
    return {
      ...this.holdingExportRow(row),
      xirr: row.sub_class_xirr,
    };
  }

  private subClassHoldingColumns(): Array<[string, string]> {
    return [
      ['Family Name', 'family_name'], ['Portfolio', 'portfolio'], ['Asset Class', 'asset_class'], ['Sub Class', 'sub_class'],
      ['Quantity', 'quantity'], ['Average Cost', 'average_cost'], ['Invested Value', 'invested_value'],
      ['Current Price / NAV', 'current_price'], ['Current Value', 'current_value'], ['Gain', 'gain'],
      ['Gain %', 'gain_percentage'], ['XIRR (%)', 'xirr'],
    ];
  }

  private holdingColumns(): Array<[string, string]> {
    return [
      ['Family Name', 'family_name'], ['Portfolio', 'portfolio'], ['Asset Class', 'asset_class'], ['Sub Class', 'sub_class'],
      ['Asset Name', 'asset_name'], ['Underlying', 'underlying'], ['ISIN', 'isin'], ['Advisor', 'advisors'],
      ['Quantity', 'quantity'], ['Average Cost', 'average_cost'], ['Invested Value', 'invested_value'],
      ['Current Price / NAV', 'current_price'], ['Current Value', 'current_value'], ['Gain', 'gain'],
      ['Gain %', 'gain_percentage'], ['XIRR (%)', 'xirr'], ['Sector', 'sector'], ['Cap Type', 'cap_type'], ['AMC', 'amc_name'],
    ];
  }

  private async exportWorkbook(
    sheetName: string,
    title: string,
    columns: Array<[string, string]>,
    rows: Record<string, unknown>[],
    filename: string,
  ): Promise<void> {
    if (!rows.length) throw new Error('No data matches the selected filters.');

    const { default: ExcelJS } = await import('exceljs');
    const workbook = new ExcelJS.Workbook();
    workbook.creator = 'PWMS';
    workbook.created = new Date();
    const sheet = workbook.addWorksheet(sheetName);
    const isMarketCap = sheetName === 'Market Cap';

    sheet.mergeCells(1, 1, 1, columns.length);
    const titleCell = sheet.getCell(1, 1);
    titleCell.value = title;
    titleCell.font = { bold: true, size: 16, color: { argb: 'FFFFFF' } };
    titleCell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: '1F4E78' } };
    titleCell.alignment = { vertical: 'middle', horizontal: 'left' };
    titleCell.border = { bottom: { style: 'medium' } };
    sheet.getRow(1).height = 28;

    const header = sheet.getRow(2);
    columns.forEach(([label, key], index) => {
      const cell = header.getCell(index + 1);
      cell.value = label;
      cell.font = { bold: true, size: 11, color: { argb: 'FFFFFF' } };
      cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: '4472C4' } };
      cell.alignment = { vertical: 'middle', horizontal: 'center' };
      cell.border = {
        top: { style: 'thin' },
        bottom: { style: 'thin' },
      };
    });
    header.height = 22;

    sheet.columns = columns.map(([label, key], index) => ({
      key,
      width: isMarketCap
        ? (index === 0 ? 34 : 18)
        : Math.max(14, Math.min(32, Math.max(label.length, key.length) + 8)),
    }));

    rows.forEach(row => sheet.addRow(row));

    const lastRow = sheet.rowCount;
    for (let rowNumber = 3; rowNumber <= lastRow; rowNumber++) {
      const row = sheet.getRow(rowNumber);
      const firstValue = String(row.getCell(1).value ?? '');
      const isSummaryRow = ['% of Equity', 'Current Value', 'total'].includes(firstValue);

      row.height = 20;
      row.eachCell({ includeEmpty: true }, (cell, columnNumber) => {
        cell.border = {
          bottom: { style: 'hair' },
        };
        cell.alignment = {
          vertical: 'middle',
          horizontal: 'center',
        };

        if (columnNumber > 1 && typeof cell.value === 'number') {
          cell.numFmt = isMarketCap
            ? '0.00"%"'
            : '#,##0.00';

          if (cell.value > 0) {
            cell.font = { color: { argb: '008000' } };
          } else if (cell.value < 0) {
            cell.font = { color: { argb: 'C00000' } };
          }
        }
      });

      if (isSummaryRow) {
        row.height = 22;
        row.eachCell({ includeEmpty: true }, cell => {
          cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: firstValue === 'total' ? 'D9EAF7' : 'EAF2F8' } };
          if (typeof cell.value === 'number') {
            cell.font = {
              bold: true,
              color: cell.value > 0 ? { argb: '008000' } : cell.value < 0 ? { argb: 'C00000' } : undefined,
            };
          } else {
            cell.font = { bold: true };
          }
        });
        row.eachCell({ includeEmpty: true }, cell => {
          cell.border = {
            top: { style: 'thin' },
            bottom: { style: 'thin' },
          };
        });
      }
    }

    if (isMarketCap) {
      // Market-cap data rows are percentage points (0-100), so use a
      // literal percent sign rather than Excel's fractional percentage format.
      const firstDataRow = 3;
      const lastDataRow = lastRow;
      for (let rowNumber = firstDataRow; rowNumber <= lastDataRow; rowNumber++) {
        if ((rowNumber - firstDataRow) % 2 === 0) {
          sheet.getRow(rowNumber).eachCell({ includeEmpty: true }, cell => {
            cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'F7F9FC' } };
          });
        }
      }
    }

    sheet.views = [{ state: 'frozen', ySplit: 2 }];
    sheet.autoFilter = { from: { row: 2, column: 1 }, to: { row: 2, column: columns.length } };
    const buffer = await workbook.xlsx.writeBuffer();
    const blob = new Blob([buffer], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${filename}_${this.todayStamp()}.xlsx`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  }

  private clean(value: string | null | undefined): string {
    const trimmed = value?.trim();
    return trimmed || 'Unassigned';
  }

  private sum(assets: PortfolioAssetNode[], key: keyof PortfolioAssetNode): number {
    return assets.reduce((total, asset) => total + Number(asset[key] ?? 0), 0);
  }

  private first(values: string[]): string {
    return values.find(Boolean) || 'Unassigned';
  }

  private firstNumber(values: Array<number | null | undefined>): number | null {
    const value = values.find(item => item !== null && item !== undefined && Number.isFinite(Number(item)));
    return value === undefined ? null : Number(value);
  }

  private todayStamp(): string {
    return new Date().toISOString().slice(0, 10);
  }

  private validateSelections(): void {
    if (this.selectedFamily && !this.familyOptions.includes(this.selectedFamily)) this.selectedFamily = '';
    if (this.selectedAssetClass && !this.assetClassOptions.includes(this.selectedAssetClass)) this.selectedAssetClass = '';
    if (this.selectedSubClass && !this.subClassOptions.includes(this.selectedSubClass)) this.selectedSubClass = '';
    if (this.selectedAssetName && !this.assetNameOptions.includes(this.selectedAssetName)) this.selectedAssetName = '';
  }
}
