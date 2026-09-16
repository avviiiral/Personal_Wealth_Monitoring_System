import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { WatchListApiService, WatchListProduct, WatchListResponse } from '../../core/services/watch-list-api.service';

type ProductTab = 'MUTUAL_FUND' | 'PMS';
type StatusTab = 'ALL' | 'OWNED' | 'UNIVERSAL';
type PageItem = number | 'ellipsis';

@Component({
  selector: 'app-watch-list',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './watch-list.component.html',
  styleUrl: './watch-list.component.scss',
})
export class WatchListComponent implements OnInit {
  private readonly api = inject(WatchListApiService);
  private readonly cachePrefix = 'pwms.watch-list.';
  private autoRefreshAttempted = false;

  products: WatchListProduct[] = [];
  loading = true;
  error = '';
  search = '';
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
  }

  get totalPages(): number {
    return Math.max(1, Math.ceil(this.count / this.pageSize));
  }

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
    } catch (error) {
      console.warn('Failed to restore Watch List cache:', error);
    }
  }

  private cachePage(response: WatchListResponse): void {
    try {
      localStorage.setItem(this.cacheKey(), JSON.stringify(response));
    } catch (error) {
      // A full/disabled browser storage should never block Watch List loading.
      console.warn('Failed to cache Watch List page:', error);
    }
  }

  private shouldBootstrapUniverse(response: WatchListResponse): boolean {
    return response.count === 0
      && !this.autoRefreshAttempted
      && !this.refreshing
      && this.page === 1
      && this.status === 'ALL'
      && !this.search.trim()
      && !this.provider
      && !this.category;
  }

  load(): void {
    this.restoreCachedPage();
    this.loading = this.products.length === 0;
    this.error = '';

    this.api.getProducts({
      product_type: this.productTab,
      status: this.status === 'ALL' ? undefined : this.status,
      search: this.search.trim() || undefined,
      provider: this.provider || undefined,
      category: this.category || undefined,
      ordering: this.ordering,
      page: this.page,
      page_size: this.pageSize,
    }).subscribe({
      next: response => {
        if (this.shouldBootstrapUniverse(response)) {
          this.autoRefreshAttempted = true;
          this.refreshUniverse(true);
          return;
        }

        this.products = response.results;
        this.count = response.count;
        this.cachePage(response);
        if (this.page > this.totalPages) {
          this.page = this.totalPages;
          this.load();
          return;
        }
        this.loading = false;
      },
      error: error => {
        console.error('Failed to load Watch List:', error);
        this.error = 'Unable to load Watch List right now.';
        this.loading = false;
      },
    });
  }

  applyFilters(): void {
    this.page = 1;
    this.load();
  }

  goToPage(page: number): void {
    if (page < 1 || page > this.totalPages || page === this.page || this.loading) return;
    this.page = page;
    this.load();
  }

  setProductTab(tab: ProductTab): void {
    if (this.productTab !== tab) {
      this.productTab = tab;
      this.provider = '';
      this.category = '';
      this.page = 1;
      this.loadFilters();
      this.load();
    }
  }

  setStatus(status: StatusTab): void {
    if (this.status !== status) {
      this.status = status;
      this.page = 1;
      this.load();
    }
  }

  refreshUniverse(auto = false): void {
    if (this.refreshing) return;
    this.refreshing = true;
    if (!auto) this.error = '';
    this.api.refresh().subscribe({
      next: () => {
        this.refreshing = false;
        this.page = 1;
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
