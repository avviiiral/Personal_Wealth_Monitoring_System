import { CommonModule } from '@angular/common';
import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import {
  FamilyNode,
  HoldingReportRow,
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
    { id: 'market-cap', name: 'Market Cap', type: 'Equity Allocation Report', description: 'Equity PMS, Direct Equity and Equity Mutual Fund exposure grouped by market capitalization.', filters: 'Family' },\n    { id: 'holding-matrix', name: 'Holding Matrix', type: 'Equity Concentration Report', description: 'Equity PMS and Direct Equity holdings aggregated by security.', filters: 'Family' },
  ];

  transactions: Transaction[] = [];
  holdingRows: HoldingReportRow[] = [];
  portfolioTree: FamilyNode[] = [];
  watchListProducts: WatchListProduct[] = [];

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

  ngOnInit(): void {
    this.load();
  }

  async load(): Promise<void> {
    this.loading = true;
    this.error = '';
    try {
      await this.loadDataForReport(this.selectedReport, true);
      this.validateSelections();
    } catch (error) {
      console.error('Download page load failed:', error);
      this.error = 'Unable to load report data.';
    } finally {
      this.loading = false;
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
    const rows = this.filteredHoldingRows()
      .filter(row => !this.selectedSubClass || this.clean(row.sub_class) === this.selectedSubClass)
      .map(row => this.holdingExportRow(row));
    await this.exportWorkbook('Holdings', 'Sub Class Holdings', this.subClassHoldingColumns(), rows, 'sub_class_holdings');
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
    const rows: Record<string, unknown>[] = [];

    for (const row of filtered) {
      rows.push({
        name: level === 'asset-class' ? this.clean(row.asset_class)
          : level === 'sub-class' ? this.clean(row.sub_class)
          : this.clean(row.asset_name),
        family_name: this.clean(row.family_name),
        asset_class: this.clean(row.asset_class),
        sub_class: this.clean(row.sub_class),
        asset_name: this.clean(row.asset_name),
        underlying: row.underlying || '',
        invested_value: Number(row.invested_value || 0),
        current_value: Number(row.current_value || 0),
        gain: Number(row.gain || 0),
        xirr: level === 'asset-class' ? row.asset_class_xirr
          : level === 'sub-class' ? row.sub_class_xirr
          : row.asset_name_xirr,
      });
    }

    const title = level === 'asset-class' ? 'Asset Class XIRR'
      : level === 'sub-class' ? 'Sub Class XIRR'
      : 'Asset Name XIRR';
    await this.writeXirr(rows, title);
  }

  private async writeXirr(rows: Record<string, unknown>[], title: string): Promise<void> {
    await this.exportWorkbook('XIRR', title, [
      ['Family', 'family_name'], ['Asset Class', 'asset_class'], ['Sub Class', 'sub_class'], ['Asset Name / Group', 'name'],
      ['Asset Name', 'asset_name'], ['Underlying', 'underlying'], ['Invested Value', 'invested_value'],
      ['Current Value', 'current_value'], ['Gain', 'gain'], ['XIRR (%)', 'xirr'],
    ], rows, title.toLowerCase().replace(/[^a-z0-9]+/g, '_'));
  }

  private async downloadMarketCap(): Promise<void> {
    const selectedFamily = this.selectedFamily;
    const rows: Record<string, unknown>[] = [];
    const marketCapTotals = new Map<string, { current_value: number; direct_equity: number; equity_pms: number; equity_mf: number }>();

    // Direct Equity + Equity PMS. For PMS positions with uploaded underlyings,
    // use the underlying cap classification so the exposure is attributed to
    // the securities actually held inside the PMS.
    for (const row of this.holdingRows) {
      if (selectedFamily && this.clean(row.family_name) !== selectedFamily) continue;
      const assetClass = this.clean(row.asset_class);
      if (assetClass !== 'Direct Equity' && assetClass !== 'Equity PMS') continue;

      const baseValue = Number(row.current_value || 0);
      if (baseValue <= 0) continue;

      const entries = Object.entries(row.underlying_xirr ?? {});
      if (assetClass === 'Equity PMS' && entries.length) {
        for (const [underlying, data] of entries) {
          const pct = Number(data.holding_percentage || 0) / 100;
          if (pct <= 0) continue;
          const value = baseValue * pct;
          const cap = this.clean(this.findUnderlyingCapType(row, underlying));
          const item = marketCapTotals.get(cap) || { current_value: 0, direct_equity: 0, equity_pms: 0, equity_mf: 0 };
          item.current_value += value;
          item.equity_pms += value;
          marketCapTotals.set(cap, item);
        }
        continue;
      }

      const cap = this.clean(row.cap_type);
      const item = marketCapTotals.get(cap) || { current_value: 0, direct_equity: 0, equity_pms: 0, equity_mf: 0 };
      item.current_value += baseValue;
      if (assetClass === 'Direct Equity') item.direct_equity += baseValue;
      else item.equity_pms += baseValue;
      marketCapTotals.set(cap, item);
    }

    // Equity Mutual Funds are look-through classified from the same holding
    // report rows when their underlying snapshot is available.
    for (const row of this.holdingRows) {
      if (selectedFamily && this.clean(row.family_name) !== selectedFamily) continue;
      if (this.clean(row.asset_class) !== 'Equity Mutual Fund') continue;

      const baseValue = Number(row.current_value || 0);
      if (baseValue <= 0) continue;
      const entries = Object.entries(row.underlying_xirr ?? {});
      if (!entries.length) {
        const cap = this.clean(row.cap_type);
        const item = marketCapTotals.get(cap) || { current_value: 0, direct_equity: 0, equity_pms: 0, equity_mf: 0 };
        item.current_value += baseValue;
        item.equity_mf += baseValue;
        marketCapTotals.set(cap, item);
        continue;
      }

      for (const [underlying, data] of entries) {
        const pct = Number(data.holding_percentage || 0) / 100;
        if (pct <= 0) continue;
        const value = baseValue * pct;
        const cap = this.clean(this.findUnderlyingCapType(row, underlying));
        const item = marketCapTotals.get(cap) || { current_value: 0, direct_equity: 0, equity_pms: 0, equity_mf: 0 };
        item.current_value += value;
        item.equity_mf += value;
        marketCapTotals.set(cap, item);
      }
    }

    const total = Array.from(marketCapTotals.values()).reduce((sum, item) => sum + item.current_value, 0);
    marketCapTotals.forEach((item, cap_type) => {
      rows.push({
        cap_type,
        current_value: item.current_value,
        percentage: total ? (item.current_value / total) * 100 : 0,
        direct_equity: item.direct_equity,
        equity_pms: item.equity_pms,
        equity_mutual_fund: item.equity_mf,
      });
    });
    rows.sort((a, b) => Number(b['current_value']) - Number(a['current_value']));

    await this.exportWorkbook('Market Cap', 'Market Cap - Equity', [
      ['Market Cap', 'cap_type'],
      ['Current Value', 'current_value'],
      ['% of Equity', 'percentage'],
      ['Direct Equity', 'direct_equity'],
      ['Equity PMS', 'equity_pms'],
      ['Equity Mutual Fund', 'equity_mutual_fund'],
    ], rows, 'market_cap');
  }

  private async downloadHoldingMatrix(): Promise<void> {
    const selectedFamily = this.selectedFamily;
    const matrix = new Map<string, { current_value: number; direct_equity: number; equity_pms: number; portfolios: Set<string> }>();

    for (const row of this.holdingRows) {
      if (selectedFamily && this.clean(row.family_name) !== selectedFamily) continue;
      const assetClass = this.clean(row.asset_class);
      if (assetClass !== 'Direct Equity' && assetClass !== 'Equity PMS') continue;

      const baseValue = Number(row.current_value || 0);
      if (baseValue <= 0) continue;

      const entries = Object.entries(row.underlying_xirr ?? {});
      if (assetClass === 'Equity PMS' && entries.length) {
        for (const [underlying, data] of entries) {
          const pct = Number(data.holding_percentage || 0) / 100;
          if (pct <= 0) continue;
          const value = baseValue * pct;
          const key = this.clean(underlying);
          const item = matrix.get(key) || { current_value: 0, direct_equity: 0, equity_pms: 0, portfolios: new Set<string>() };
          item.current_value += value;
          item.equity_pms += value;
          if (row.portfolio) item.portfolios.add(row.portfolio);
          matrix.set(key, item);
        }
        continue;
      }

      const key = this.clean(row.asset_name);
      const item = matrix.get(key) || { current_value: 0, direct_equity: 0, equity_pms: 0, portfolios: new Set<string>() };
      item.current_value += baseValue;
      if (assetClass === 'Direct Equity') item.direct_equity += baseValue;
      else item.equity_pms += baseValue;
      if (row.portfolio) item.portfolios.add(row.portfolio);
      matrix.set(key, item);
    }

    const total = Array.from(matrix.values()).reduce((sum, item) => sum + item.current_value, 0);
    const rows = Array.from(matrix.entries()).map(([holding, item]) => ({
      holding,
      current_value: item.current_value,
      percentage: total ? (item.current_value / total) * 100 : 0,
      direct_equity: item.direct_equity,
      equity_pms: item.equity_pms,
      portfolio_count: item.portfolios.size,
      portfolios: Array.from(item.portfolios).sort().join(', '),
    })).sort((a, b) => Number(b['current_value']) - Number(a['current_value']));

    await this.exportWorkbook('Holding Matrix', 'Holding Matrix - Equity PMS + Direct Equity', [
      ['Holding', 'holding'],
      ['Current Value', 'current_value'],
      ['% of Equity', 'percentage'],
      ['Direct Equity', 'direct_equity'],
      ['Equity PMS', 'equity_pms'],
      ['Portfolio Count', 'portfolio_count'],
      ['Portfolios', 'portfolios'],
    ], rows, 'holding_matrix');
  }

  private findUnderlyingCapType(row: HoldingReportRow, underlying: string): string {
    const normalizedUnderlying = underlying.trim().toUpperCase();
    if (!normalizedUnderlying) return 'Unclassified';

    // Existing holding-report payload exposes one parent cap type. For
    // underlyings, use the same security master classification already
    // present in the dataset when the underlying is represented as an
    // asset row; otherwise leave it explicitly unclassified.
    const match = this.holdingRows.find(candidate =>
      this.clean(candidate.asset_name).trim().toUpperCase() === normalizedUnderlying
      && this.clean(candidate.cap_type) !== 'Unassigned'
      && this.clean(candidate.asset_class) === 'Direct Equity'
    );
    return match?.cap_type || 'Unclassified';
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

  private subClassHoldingColumns(): Array<[string, string]> {
    return [
      ['Family Name', 'family_name'], ['Portfolio', 'portfolio'], ['Asset Class', 'asset_class'], ['Sub Class', 'sub_class'],
      ['Quantity', 'quantity'], ['Average Cost', 'average_cost'], ['Invested Value', 'invested_value'],
      ['Current Price / NAV', 'current_price'], ['Current Value', 'current_value'], ['Gain', 'gain'],
      ['Gain %', 'gain_percentage'], ['XIRR (%)', 'xirr'], ['Sector', 'sector'], ['Cap Type', 'cap_type'], ['AMC', 'amc_name'],
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
    sheet.mergeCells(1, 1, 1, columns.length);
    sheet.getCell(1, 1).value = title;
    sheet.getCell(1, 1).font = { bold: true, size: 12 };
    const header = sheet.getRow(2);
    columns.forEach(([label, key], index) => {
      header.getCell(index + 1).value = label;
      header.getCell(index + 1).font = { bold: true };
    });
    sheet.columns = columns.map(([_, key]) => ({ key, width: Math.max(14, Math.min(32, key.length + 8)) }));
    rows.forEach(row => sheet.addRow(row));
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
