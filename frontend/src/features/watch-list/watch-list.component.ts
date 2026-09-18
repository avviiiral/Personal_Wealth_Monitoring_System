import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subject } from 'rxjs';
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
