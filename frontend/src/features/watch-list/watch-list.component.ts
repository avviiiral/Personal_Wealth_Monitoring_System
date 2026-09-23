import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom, Subject } from 'rxjs';
import { debounceTime, distinctUntilChanged, finalize, takeUntil } from 'rxjs/operators';

import { WatchListApiService, WatchListProduct, WatchListResponse } from '../../core/services/watch-list-api.service';
import { WatchListStateService } from '../../core/services/watch-list-state.service';

type ProductTab = 'MUTUAL_FUND' | 'PMS';
type StatusTab = 'ALL' | 'OWNED' | 'UNIVERSAL' | 'WATCHLIST';
type PageItem = number | 'ellipsis';
type WatchListSortColumn = 'product' | 'provider' | 'status' | 'ownership' | '1M' | '3M' | '6M' | '1Y' | '3Y' | '5Y' | 'CAGR' | 'AUM';
type WatchListSortDirection = 'normal' | 'asc' | 'desc';
interface WatchListSortState { column: WatchListSortColumn | null; direction: WatchListSortDirection; }

@Component({
  selector: 'app-watch-list',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './watch-list.component.html',
  styleUrl: './watch-list.component.scss',
})
export class WatchListComponent implements OnInit, OnDestroy {
  private readonly api = inject(WatchListApiService);
  private readonly state = inject(WatchListStateService);
  private readonly cachePrefix = 'pwms.watch-list.';
  private autoRefreshAttempted = false;
  private readonly searchInput$ = new Subject<string>();
  private readonly destroy$ = new Subject<void>();
  private requestSequence = 0;

  products: WatchListProduct[] = [];
  loading = true;
  error = '';
  search = '';
  private readonly togglingIds = new Set<number>();
  readonly selectedIds = new Set<number>();
  provider = '';
  category = '';
  providers: string[] = [];
  categories: string[] = [];
  status: StatusTab = 'ALL';
  productTab: ProductTab = 'MUTUAL_FUND';
  private readonly sortState: WatchListSortState = { column: null, direction: 'normal' };
  count = 0;
  page = 1;
  readonly pageSize = 50;
  refreshing = false;
  movingToWatchList = false;
  removingFromWatchList = false;
  downloadModalOpen = false;
  downloadType: 'PMS' | 'MUTUAL_FUND' | 'ALL' = 'ALL';
  downloading = false;
  readonly benchmarkOptions: Array<'BSE 500 TRI' | 'Nifty 50'> = ['BSE 500 TRI', 'Nifty 50'];
  private readonly updatingBenchmarkIds = new Set<number>();
  benchmarkModalProduct: WatchListProduct | null = null;
  benchmarkData: any = null;
  benchmarkLoading = false;
  benchmarkPeriod: '1M' | '3M' | '6M' | '1Y' | '3Y' | '5Y' = '1Y';

  ngOnInit(): void {
    this.loadFilters();
    this.load();
    this.searchInput$.pipe(debounceTime(350), distinctUntilChanged(), takeUntil(this.destroy$)).subscribe(() => this.applyFilters());
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
    this.searchInput$.complete();
  }

  onSearchInput(): void { this.searchInput$.next(this.search); }

  get totalPages(): number { return Math.max(1, Math.ceil(this.count / this.pageSize)); }

  get pageItems(): PageItem[] {
    const total = this.totalPages;
    if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
    const items: PageItem[] = [1];
    const start = Math.max(2, this.page - 2);
    const end = Math.min(total - 1, this.page + 2);
    if (start > 2) items.push('ellipsis');
    for (let value = start; value <= end; value += 1) items.push(value);
    if (end < total - 1) items.push('ellipsis');
    items.push(total);
    return items;
  }

  ownershipFamilies(product: WatchListProduct): string[] {
    const families = new Set<string>();
    for (const item of product.ownership || []) {
      const family = String(item.family || '').trim();
      if (family) families.add(family);
    }
    return Array.from(families);
  }

  isSelected(productId: number): boolean { return this.selectedIds.has(productId); }

  toggleSelection(productId: number, event?: Event): void {
    event?.stopPropagation();
    if (this.selectedIds.has(productId)) this.selectedIds.delete(productId);
    else this.selectedIds.add(productId);
  }

  get allVisibleSelected(): boolean {
    return this.products.length > 0 && this.products.every(product => this.selectedIds.has(product.id));
  }

  toggleSelectAll(event: Event): void {
    event.stopPropagation();
    if (this.allVisibleSelected) this.products.forEach(product => this.selectedIds.delete(product.id));
    else this.products.forEach(product => this.selectedIds.add(product.id));
  }

  get selectedCount(): number { return this.selectedIds.size; }

  get selectedAddCount(): number {
    return this.products.filter(product => this.selectedIds.has(product.id) && !product.is_watchlisted).length;
  }

  get selectedRemoveCount(): number {
    return this.products.filter(product => this.selectedIds.has(product.id) && product.is_watchlisted).length;
  }

  moveSelectedToWatchList(): void {
    const selectedProducts = this.products.filter(product => this.selectedIds.has(product.id) && !product.is_watchlisted);
    const productIds = selectedProducts.map(product => product.id);
    if (!productIds.length || this.movingToWatchList || this.removingFromWatchList) return;
    this.movingToWatchList = true;
    this.error = '';
    selectedProducts.forEach(product => { product.is_watchlisted = true; });
    this.state.stageAdded(selectedProducts);
    productIds.forEach(id => this.selectedIds.delete(id));
    this.api.bulkAddToWatchList(productIds).subscribe({
      next: () => {
        this.state.confirmAdded(productIds, this.productTab);
        this.movingToWatchList = false;
        this.cacheCurrentPage();
      },
      error: error => {
        console.error('Failed to move selected products to Watch List:', error);
        selectedProducts.forEach(product => { product.is_watchlisted = false; });
        this.state.rollbackAdded(selectedProducts);
        this.movingToWatchList = false;
        this.error = 'Unable to move the selected products to Watch List.';
      },
    });
  }
  removeSelectedFromWatchList(): void {
    const selectedProducts = this.products.filter(product => this.selectedIds.has(product.id) && product.is_watchlisted);
    const productIds = selectedProducts.map(product => product.id);
    if (!productIds.length || this.movingToWatchList || this.removingFromWatchList) return;
    this.removingFromWatchList = true;
    this.error = '';
    selectedProducts.forEach(product => { product.is_watchlisted = false; });
    this.state.stageRemoved(selectedProducts);
    productIds.forEach(id => this.selectedIds.delete(id));
    if (this.status === 'WATCHLIST') {
      this.products = this.products.filter(product => !productIds.includes(product.id));
      this.count = Math.max(0, this.count - productIds.length);
    }
    this.api.bulkRemoveFromWatchList(productIds).subscribe({
      next: () => {
        this.state.confirmRemoved(productIds, this.productTab);
        this.removingFromWatchList = false;
        this.cacheCurrentPage();
      },
      error: error => {
        console.error('Failed to remove selected products from Watch List:', error);
        selectedProducts.forEach(product => { product.is_watchlisted = true; });
        this.state.rollbackRemoved(selectedProducts);
        this.removingFromWatchList = false;
        this.error = 'Unable to remove the selected products from Watch List.';
        if (this.status === 'WATCHLIST') this.load();
      },
    });
  }
  loadFilters(): void {
    this.api.getFilters(this.productTab).subscribe({
      next: response => {
        this.providers = response.providers;
        this.categories = response.categories;
        if (this.provider && !this.providers.includes(this.provider)) this.provider = '';
        if (this.category && !this.categories.includes(this.category)) this.category = '';
      },
      error: error => console.error('Failed to load Watch List filter options:', error),
    });
  }

  private cacheKey(): string {
    return `${this.cachePrefix}${this.productTab}.${this.status}.${this.page}.${this.provider}.${this.category}.${this.search.trim()}`;
  }

  private restoreCachedPage(): void {
    try {
      const raw = localStorage.getItem(this.cacheKey());
      if (!raw) return;
      const cached = JSON.parse(raw) as WatchListResponse;
      if (!cached || !Array.isArray(cached.results)) return;
      this.products = this.sortProducts(cached.results);
      this.count = Number(cached.count) || cached.results.length;
      this.loading = false;
    } catch (error) { console.warn('Failed to restore Watch List cache:', error); }
  }

  private cachePage(response: WatchListResponse): void {
    try { localStorage.setItem(this.cacheKey(), JSON.stringify(response)); }
    catch (error) { console.warn('Failed to cache Watch List page:', error); }
  }

  private cacheCurrentPage(): void {
    this.cachePage({
      count: this.count,
      next: null,
      previous: null,
      results: this.products,
    });
  }

  private shouldBootstrapUniverse(response: WatchListResponse): boolean {
    return response.count === 0 && !this.autoRefreshAttempted && !this.refreshing && this.page === 1
      && this.status === 'ALL' && !this.search.trim() && !this.provider && !this.category;
  }

  load(): void {
    const requestId = ++this.requestSequence;
    const requestedProductTab = this.productTab;
    const requestedStatus = this.status;
    const requestedPage = this.page;
    const requestedProvider = this.provider;
    const requestedCategory = this.category;
    const requestedSearch = this.search.trim();

    this.restoreCachedPage();
    this.loading = this.products.length === 0;
    this.error = '';
    this.api.getProducts({
      product_type: requestedProductTab,
      status: requestedStatus === 'ALL' ? undefined : requestedStatus,
      search: requestedSearch || undefined,
      provider: requestedProvider || undefined,
      category: requestedCategory || undefined,
      page: requestedPage,
      page_size: this.pageSize,
    }).pipe(
      finalize(() => {
        if (requestId === this.requestSequence) this.loading = false;
      }),
    ).subscribe({
      next: response => {
        if (requestId !== this.requestSequence
          || requestedProductTab !== this.productTab
          || requestedStatus !== this.status
          || requestedPage !== this.page
          || requestedProvider !== this.provider
          || requestedCategory !== this.category
          || requestedSearch !== this.search.trim()) return;

        // A completed response for the active request always ends the
        // loading state before any response-specific handling.
        this.loading = false;

        if (this.shouldBootstrapUniverse(response)) {
          this.autoRefreshAttempted = true;
          this.refreshUniverse(true);
          return;
        }
        const serverResults = this.state.filterVisible(response.results);
        if (requestedStatus === 'WATCHLIST' && requestedPage === 1) {
          const optimistic = this.state.getAdded(requestedProductTab);
          const serverIds = new Set(serverResults.map(product => product.id));
          const optimisticResults = optimistic.filter(product => !serverIds.has(product.id));
          this.products = this.sortProducts([...optimisticResults, ...serverResults].slice(0, this.pageSize));
          this.count = response.count + optimisticResults.length;
        } else {
          this.products = this.sortProducts(serverResults);
          this.count = Math.max(0, response.count - (response.results.length - serverResults.length));
        }
        this.cachePage({ ...response, results: this.products, count: this.count });
        const visibleIds = new Set(this.products.map(product => product.id));
        this.selectedIds.forEach(id => { if (!visibleIds.has(id)) this.selectedIds.delete(id); });
        if (this.page > this.totalPages) { this.page = this.totalPages; this.load(); return; }
        this.loading = false;
      },
      error: error => {
        if (requestId !== this.requestSequence) return;
        console.error('Failed to load Watch List:', error);
        this.error = 'Unable to load Watch List right now.';
      },
    });
  }

  cycleSort(column: WatchListSortColumn): void {
    if (this.sortState.column !== column) {
      this.sortState.column = column;
      this.sortState.direction = 'desc';
    } else if (this.sortState.direction === 'desc') {
      this.sortState.direction = 'asc';
    } else {
      this.sortState.column = null;
      this.sortState.direction = 'normal';
    }
    this.products = this.sortProducts(this.products);
    this.cacheCurrentPage();
  }

  getSortIndicator(column: WatchListSortColumn): string {
    if (this.sortState.column !== column || this.sortState.direction === 'normal') return '';
    return this.sortState.direction === 'desc' ? '↓' : '↑';
  }

  private sortProducts(products: WatchListProduct[]): WatchListProduct[] {
    const { column, direction } = this.sortState;
    if (!column || direction === 'normal') return [...products];

    const sorted = [...products];
    sorted.sort((left, right) => {
      const comparison = this.compareSortValues(
        this.getSortValue(left, column),
        this.getSortValue(right, column),
        this.isNumericSortColumn(column),
      );
      if (comparison === 0) return left.name.localeCompare(right.name, undefined, { numeric: true, sensitivity: 'base' });
      return direction === 'asc' ? comparison : -comparison;
    });
    return sorted;
  }

  private getSortValue(product: WatchListProduct, column: WatchListSortColumn): unknown {
    switch (column) {
      case 'product': return product.name;
      case 'provider': return product.provider || '';
      case 'status': return product.status || '';
      case 'ownership': return this.ownershipFamilies(product).join(', ');
      case '1M':
      case '3M':
      case '6M':
      case '1Y':
      case '3Y':
      case '5Y':
      case 'CAGR': return this.metric(product, column);
      case 'AUM': return product.mutual_fund?.aum ?? product.pms?.aum ?? null;
    }
  }

  private compareSortValues(left: unknown, right: unknown, numeric: boolean): number {
    if (numeric) {
      const leftNumber = this.toNullableNumber(left);
      const rightNumber = this.toNullableNumber(right);

      // Keep missing numeric values at the bottom for both ascending and descending sorts.
      if (leftNumber === null && rightNumber === null) return 0;
      if (leftNumber === null) return 1;
      if (rightNumber === null) return -1;

      if (leftNumber === rightNumber) return 0;
      return leftNumber < rightNumber ? -1 : 1;
    }

    if (left == null || left === '') return right == null || right === '' ? 0 : 1;
    if (right == null || right === '') return -1;
    return String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: 'base' });
  }

  private isNumericSortColumn(column: WatchListSortColumn): boolean {
    return ['1M', '3M', '6M', '1Y', '3Y', '5Y', 'CAGR', 'AUM'].includes(column);
  }

  private toNullableNumber(value: unknown): number | null {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  applyFilters(): void { this.page = 1; this.selectedIds.clear(); this.load(); }

  goToPage(page: number): void {
    if (page < 1 || page > this.totalPages || page === this.page || this.loading) return;
    this.selectedIds.clear();
    this.page = page;
    this.load();
  }

  setProductTab(tab: ProductTab): void {
    if (this.productTab !== tab) {
      this.productTab = tab;
      this.provider = '';
      this.category = '';
      this.selectedIds.clear();
      this.page = 1;
      this.loadFilters();
      this.load();
    }
  }

  setStatus(status: StatusTab): void {
    if (this.status !== status) {
      this.status = status;
      this.selectedIds.clear();
      this.page = 1;
      this.load();
    }
  }

  isToggling(productId: number): boolean { return this.togglingIds.has(productId); }

  isUpdatingBenchmark(productId: number): boolean { return this.updatingBenchmarkIds.has(productId); }

  updateBenchmark(product: WatchListProduct, benchmark: 'BSE 500 TRI' | 'Nifty 50'): void {
    const previous = product.benchmark;
    if (previous === benchmark || this.updatingBenchmarkIds.has(product.id)) return;
    this.updatingBenchmarkIds.add(product.id);
    this.error = '';
    this.api.updateBenchmark(product.id, benchmark).subscribe({
      next: response => {
        product.benchmark = response.benchmark as 'BSE 500 TRI' | 'Nifty 50';
        this.updatingBenchmarkIds.delete(product.id);
        this.cacheCurrentPage();
      },
      error: error => {
        console.error('Failed to update Watch List benchmark:', error);
        product.benchmark = previous;
        this.updatingBenchmarkIds.delete(product.id);
        this.error = 'Unable to update the benchmark right now.';
      },
    });
  }

  openBenchmarkComparison(product: WatchListProduct): void {
    if (!product.benchmark) return;
    this.benchmarkModalProduct = product;
    this.benchmarkData = null;
    this.benchmarkLoading = true;
    this.benchmarkPeriod = '1Y';
    this.api.getBenchmarkPerformance(product.id, this.benchmarkPeriod).subscribe({
      next: data => {
        this.benchmarkData = data;
        this.benchmarkLoading = false;
      },
      error: error => {
        console.error('Failed to load benchmark performance:', error);
        this.benchmarkLoading = false;
        this.error = 'Unable to load benchmark performance right now.';
      },
    });
  }

  closeBenchmarkComparison(): void {
    this.benchmarkModalProduct = null;
    this.benchmarkData = null;
    this.benchmarkLoading = false;
  }

  changeBenchmarkChartPeriod(period: '1M' | '3M' | '6M' | '1Y' | '3Y' | '5Y'): void {
    if (!this.benchmarkModalProduct || this.benchmarkLoading) return;
    this.benchmarkPeriod = period;
    this.benchmarkLoading = true;
    this.api.getBenchmarkPerformance(this.benchmarkModalProduct.id, period).subscribe({
      next: data => {
        this.benchmarkData = data;
        this.benchmarkLoading = false;
      },
      error: error => {
        console.error('Failed to load benchmark chart:', error);
        this.benchmarkLoading = false;
        this.error = 'Unable to load benchmark chart right now.';
      },
    });
  }

  benchmarkMetric(period: string, key: 'fund_metrics' | 'benchmark_metrics' | 'differences'): number | null {
    const value = this.benchmarkData?.[key]?.[period];
    return value === null || value === undefined ? null : Number(value);
  }

  benchmarkComparison(period: string): string {
    return this.benchmarkData?.comparison?.[period] || 'Unavailable';
  }

  benchmarkPolyline(series: Array<{ date: string; value: number }> | undefined): string {
    if (!series?.length) return '';
    const normalized = series
      .map(point => Number(point.value))
      .filter(value => Number.isFinite(value) && value > 0);
    if (!normalized.length) return '';

    const base = normalized[0];
    const normalizedSeries = series.map(point => ({
      value: Number(point.value) / base * 100,
    }));
    const fund = this.benchmarkData?.chart?.fund || [];
    const benchmark = this.benchmarkData?.chart?.benchmark || [];
    const combined = [...fund, ...benchmark]
      .map((point: { value: number }) => Number(point.value))
      .filter(value => Number.isFinite(value) && value > 0);
    const fundBase = fund[0]?.value ? Number(fund[0].value) : null;
    const benchmarkBase = benchmark[0]?.value ? Number(benchmark[0].value) : null;
    const normalizedAll = [
      ...(fundBase ? fund.map((point: { value: number }) => Number(point.value) / fundBase * 100) : []),
      ...(benchmarkBase ? benchmark.map((point: { value: number }) => Number(point.value) / benchmarkBase * 100) : []),
    ];
    const min = normalizedAll.length ? Math.min(...normalizedAll) : Math.min(...combined);
    const max = normalizedAll.length ? Math.max(...normalizedAll) : Math.max(...combined);
    const range = max - min || 1;
    const lastIndex = Math.max(series.length - 1, 1);
    return normalizedSeries.map((point, index) => {
      const x = (index / lastIndex) * 100;
      const y = 96 - ((point.value - min) / range) * 88;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(' ');
  }

  toggleWatch(product: WatchListProduct, event: Event): void {
    event.stopPropagation();
    if (this.togglingIds.has(product.id)) return;
    this.togglingIds.add(product.id);
    const previous = product.is_watchlisted;
    product.is_watchlisted = !previous;
    if (product.is_watchlisted) this.state.stageAdded([product]);
    else this.state.stageRemoved([product]);
    this.api.toggleWatch(product.id).subscribe({
      next: response => {
        product.is_watchlisted = response.is_watchlisted;
        if (response.is_watchlisted) this.state.confirmAdded([product.id], product.product_type);
        else this.state.confirmRemoved([product.id], product.product_type);
        this.togglingIds.delete(product.id);
        this.selectedIds.delete(product.id);
        if (this.status === 'WATCHLIST' && !response.is_watchlisted) {
          this.products = this.products.filter(item => item.id !== product.id);
          this.count = Math.max(0, this.count - 1);
        }
      },
      error: error => {
        console.error('Failed to update Watch List entry:', error);
        product.is_watchlisted = previous;
        if (previous) this.state.rollbackRemoved([product]);
        else this.state.rollbackAdded([product]);
        this.togglingIds.delete(product.id);
      },
    });
  }

  openDownloadModal(): void { this.downloadModalOpen = true; }
  closeDownloadModal(): void { if (!this.downloading) this.downloadModalOpen = false; }

  async confirmWatchListDownload(): Promise<void> {
    if (this.downloading) return;
    this.downloading = true;
    this.error = '';
    try {
      const productTypes: ProductTab[] = this.downloadType === 'ALL' ? ['PMS', 'MUTUAL_FUND'] : [this.downloadType];
      const allProducts: WatchListProduct[] = [];
      for (const productType of productTypes) {
        let page = 1;
        while (true) {
          const response = await firstValueFrom(this.api.getProducts({ product_type: productType, status: 'WATCHLIST', ordering: 'name', page, page_size: 100 }));
          allProducts.push(...this.state.filterVisible(response.results));
          if (!response.next || response.results.length === 0) break;
          page += 1;
        }
      }
      await this.exportWatchListWorkbook(allProducts);
      this.downloadModalOpen = false;
    } catch (error) {
      console.error('Failed to download Watch List:', error);
      this.error = 'Unable to download the Watch List right now.';
    } finally { this.downloading = false; }
  }

  private async exportWatchListWorkbook(products: WatchListProduct[]): Promise<void> {
    const { default: ExcelJSLib } = await import('exceljs');
    const workbook = new ExcelJSLib.Workbook();
    workbook.creator = 'Personal Wealth Monitoring System';
    workbook.subject = 'Watch List';
    workbook.title = 'Watch List';
    workbook.created = new Date();

    const sheet = workbook.addWorksheet('Watch List', {
      views: [{ state: 'frozen', ySplit: 4, showGridLines: false }],
      properties: { defaultRowHeight: 21 },
    });

    const exportTypeLabel = this.downloadType === 'ALL'
      ? 'Mutual Funds & PMS'
      : this.downloadType === 'MUTUAL_FUND' ? 'Mutual Funds' : 'PMS';

    const columnDefinitions = [
      { header: 'Type', key: 'type', width: 16 }, { header: 'Product', key: 'product', width: 48 },
      { header: 'Provider', key: 'provider', width: 28 }, { header: 'Category', key: 'category', width: 24 },
      { header: 'Identifier', key: 'identifier', width: 24 }, { header: 'Status', key: 'status', width: 14 },
      { header: '1M', key: '1M', width: 12 }, { header: '3M', key: '3M', width: 12 }, { header: '6M', key: '6M', width: 12 },
      { header: '1Y', key: '1Y', width: 12 }, { header: '3Y', key: '3Y', width: 12 }, { header: '5Y', key: '5Y', width: 12 },
      { header: 'CAGR', key: 'CAGR', width: 12 }, { header: 'AUM', key: 'AUM', width: 18 },
    ];
    columnDefinitions.forEach((column, index) => {
      const excelColumn = sheet.getColumn(index + 1);
      excelColumn.width = column.width;
      excelColumn.key = column.key;
    });

    sheet.mergeCells('A1:N1');
    const title = sheet.getCell('A1');
    title.value = 'Watch List Report';
    title.font = { name: 'Aptos Display', size: 18, bold: true, color: { argb: 'FFFFFFFF' } };
    title.alignment = { vertical: 'middle' };
    title.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF1F2937' } };
    sheet.getRow(1).height = 32;

    sheet.mergeCells('A2:N2');
    const subtitle = sheet.getCell('A2');
    subtitle.value = exportTypeLabel + ' • Watchlisted products • Generated ' + this.todayStamp();
    subtitle.font = { name: 'Aptos', size: 10, italic: true, color: { argb: 'FF6B7280' } };
    subtitle.alignment = { vertical: 'middle' };
    sheet.getRow(2).height = 22;

    sheet.mergeCells('A3:N3');
    const summary = sheet.getCell('A3');
    summary.value = 'Total watchlisted products: ' + products.length;
    summary.font = { name: 'Aptos', size: 10, bold: true, color: { argb: 'FF374151' } };
    summary.alignment = { vertical: 'middle' };
    sheet.getRow(3).height = 22;

    const headerRow = sheet.insertRow(4, columnDefinitions.map(column => column.header));
    headerRow.height = 26;
    headerRow.eachCell(cell => {
      cell.font = { name: 'Aptos', size: 10, bold: true, color: { argb: 'FFFFFFFF' } };
      cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF374151' } };
      cell.alignment = { vertical: 'middle', horizontal: 'center', wrapText: true };
      cell.border = {
        top: { style: 'thin', color: { argb: 'FFD1D5DB' } },
        bottom: { style: 'thin', color: { argb: 'FFD1D5DB' } },
      };
    });

    products.forEach((product, index) => {
      const row = sheet.addRow({
        type: product.product_type === 'MUTUAL_FUND' ? 'Mutual Fund' : 'PMS',
        product: product.name,
        provider: product.provider || '',
        category: product.category || '',
        identifier: product.isin || product.external_identifier || '',
        status: product.status,
        '1M': this.metric(product, '1M'), '3M': this.metric(product, '3M'), '6M': this.metric(product, '6M'),
        '1Y': this.metric(product, '1Y'), '3Y': this.metric(product, '3Y'), '5Y': this.metric(product, '5Y'),
        CAGR: this.metric(product, 'CAGR'),
        AUM: product.mutual_fund?.aum ?? product.pms?.aum ?? null,
      });

      row.eachCell(cell => {
        cell.font = { name: 'Aptos', size: 10, color: { argb: 'FF111827' } };
        cell.alignment = { vertical: 'middle' };
        cell.border = { bottom: { style: 'hair', color: { argb: 'FFE5E7EB' } } };
      });
      row.height = 21;
      if (index % 2 === 1) row.eachCell(cell => {
        cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFF9FAFB' } };
      });

      [7, 8, 9, 10, 11, 12, 13].forEach(column => {
        const cell = row.getCell(column);
        if (typeof cell.value === 'number') {
          cell.numFmt = '0.00"%"';
          cell.alignment = { vertical: 'middle', horizontal: 'right' };
        }
      });

      const aum = row.getCell(14);
      if (typeof aum.value === 'number') {
        aum.numFmt = '#,##0.00';
        aum.alignment = { vertical: 'middle', horizontal: 'right' };
      }
      row.getCell(1).alignment = { vertical: 'middle', horizontal: 'center' };
      row.getCell(6).alignment = { vertical: 'middle', horizontal: 'center' };
    });

    sheet.autoFilter = { from: 'A4', to: 'N4' };
    sheet.getColumn(2).alignment = { vertical: 'middle', wrapText: true };
    sheet.getColumn(3).alignment = { vertical: 'middle', wrapText: true };
    sheet.getColumn(4).alignment = { vertical: 'middle', wrapText: true };
    sheet.getColumn(5).alignment = { vertical: 'middle', wrapText: true };

    const lastRow = Math.max(4, products.length + 4);
    const noteCell = sheet.getCell('A' + (lastRow + 1));
    noteCell.value = 'Note: Returns are shown as provided by the Watch List data source. AUM is shown in the source currency.';
    sheet.mergeCells('A' + (lastRow + 1) + ':N' + (lastRow + 1));
    noteCell.font = { name: 'Aptos', size: 9, italic: true, color: { argb: 'FF6B7280' } };
    noteCell.alignment = { vertical: 'middle' };
    sheet.getRow(lastRow + 1).height = 20;

    const buffer = await workbook.xlsx.writeBuffer();
    this.triggerDownload(
      new Blob([buffer], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }),
      'watch_list_' + this.downloadType.toLowerCase() + '_' + this.todayStamp() + '.xlsx',
    );
  }
  private todayStamp(): string { return new Date().toISOString().slice(0, 10); }
  private triggerDownload(blob: Blob, filename: string): void {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click(); URL.revokeObjectURL(url);
  }

  refreshUniverse(auto = false): void {
    if (this.refreshing) return;
    this.refreshing = true;
    if (!auto) this.error = '';
    this.api.refresh().subscribe({
      next: () => {
        this.refreshing = false;
        this.page = 1;
        this.selectedIds.clear();
        this.loadFilters();
        this.load();
      },
      error: error => {
        console.error('Watch List refresh failed:', error);
        this.refreshing = false;
        this.error = 'Universe refresh failed. Existing data was not changed.';
        this.loading = false;
      },
    });
  }

  metric(product: WatchListProduct, key: string): number | null { return product.metrics?.[key] ?? null; }
  formatPercent(value: number | null): string { return value === null || value === undefined ? '—' : `${Number(value).toFixed(2)}%`; }
  formatAmount(value: number | null): string {
    if (value === null || value === undefined) return '—';
    return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 }).format(Number(value));
  }
}
