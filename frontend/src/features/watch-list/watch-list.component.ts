import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom, Subject } from 'rxjs';
import { debounceTime, distinctUntilChanged, takeUntil } from 'rxjs/operators';

import { WatchListApiService, WatchListProduct, WatchListResponse } from '../../core/services/watch-list-api.service';
import { WatchListStateService } from '../../core/services/watch-list-state.service';

type ProductTab = 'MUTUAL_FUND' | 'PMS';
type StatusTab = 'ALL' | 'OWNED' | 'UNIVERSAL' | 'WATCHLIST';
type PageItem = number | 'ellipsis';

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
  ordering = 'name';
  count = 0;
  page = 1;
  readonly pageSize = 50;
  refreshing = false;
  movingToWatchList = false;
  removingFromWatchList = false;
  downloadModalOpen = false;
  downloadType: 'PMS' | 'MUTUAL_FUND' | 'ALL' = 'ALL';
  downloading = false;

  readonly orderings = [
    { value: 'name', label: 'Name' }, { value: '1M', label: '1M Return' },
    { value: '3M', label: '3M Return' }, { value: '6M', label: '6M Return' },
    { value: '1Y', label: '1Y Return' }, { value: '3Y', label: '3Y Return' },
    { value: '5Y', label: '5Y Return' }, { value: 'cagr', label: 'CAGR' },
    { value: 'aum', label: 'AUM' },
  ];

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
    return `${this.cachePrefix}${this.productTab}.${this.status}.${this.page}.${this.ordering}.${this.provider}.${this.category}.${this.search.trim()}`;
  }

  private restoreCachedPage(): void {
    try {
      const raw = localStorage.getItem(this.cacheKey());
      if (!raw) return;
      const cached = JSON.parse(raw) as WatchListResponse;
      if (!cached || !Array.isArray(cached.results)) return;
      this.products = cached.results;
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
    const requestedOrdering = this.ordering;
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
      ordering: requestedOrdering,
      page: requestedPage,
      page_size: this.pageSize,
    }).subscribe({
      next: response => {
        if (requestId !== this.requestSequence
          || requestedProductTab !== this.productTab
          || requestedStatus !== this.status
          || requestedPage !== this.page
          || requestedOrdering !== this.ordering
          || requestedProvider !== this.provider
          || requestedCategory !== this.category
          || requestedSearch !== this.search.trim()) return;

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
          this.products = [...optimisticResults, ...serverResults].slice(0, this.pageSize);
          this.count = response.count + optimisticResults.length;
        } else {
          this.products = serverResults;
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
        this.loading = false;
      },
    });
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
      sheet.getColumn(index + 1).width = column.width;
    });

    const headerRow = sheet.getRow(4);
    headerRow.height = 26;
    columnDefinitions.forEach((column, index) => {
      sheet.getCell(4, index + 1).value = column.header;
    });

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
