import { Injectable } from '@angular/core';

import { WatchListProduct } from './watch-list-api.service';

@Injectable({ providedIn: 'root' })
export class WatchListStateService {
  private readonly storageKey = 'pwms.watch-list.pending-state';
  private readonly added = new Map<'MUTUAL_FUND' | 'PMS', Map<number, WatchListProduct>>();
  private readonly removed = new Map<'MUTUAL_FUND' | 'PMS', Set<number>>();

  constructor() {
    this.restore();
  }

  stageAdded(products: WatchListProduct[]): void {
    for (const product of products) {
      this.addedBucket(product.product_type).set(product.id, { ...product, is_watchlisted: true });
      this.removedBucket(product.product_type).delete(product.id);
    }
    this.persist();
  }

  stageRemoved(products: WatchListProduct[]): void {
    for (const product of products) {
      this.addedBucket(product.product_type).delete(product.id);
      this.removedBucket(product.product_type).add(product.id);
    }
    this.persist();
  }

  confirmAdded(productIds: number[], productType: 'MUTUAL_FUND' | 'PMS'): void {
    for (const id of productIds) this.addedBucket(productType).delete(id);
    this.persist();
  }

  confirmRemoved(productIds: number[], productType: 'MUTUAL_FUND' | 'PMS'): void {
    for (const id of productIds) this.removedBucket(productType).delete(id);
    this.persist();
  }

  rollbackAdded(products: WatchListProduct[]): void {
    for (const product of products) this.addedBucket(product.product_type).delete(product.id);
    this.persist();
  }

  rollbackRemoved(products: WatchListProduct[]): void {
    for (const product of products) this.removedBucket(product.product_type).delete(product.id);
    this.persist();
  }

  getAdded(productType: 'MUTUAL_FUND' | 'PMS'): WatchListProduct[] {
    return Array.from(this.addedBucket(productType).values());
  }

  isRemoved(productType: 'MUTUAL_FUND' | 'PMS', productId: number): boolean {
    return this.removedBucket(productType).has(productId);
  }

  filterVisible(products: WatchListProduct[]): WatchListProduct[] {
    return products.filter(product => !this.isRemoved(product.product_type, product.id));
  }

  private addedBucket(productType: 'MUTUAL_FUND' | 'PMS'): Map<number, WatchListProduct> {
    let bucket = this.added.get(productType);
    if (!bucket) {
      bucket = new Map<number, WatchListProduct>();
      this.added.set(productType, bucket);
    }
    return bucket;
  }

  private removedBucket(productType: 'MUTUAL_FUND' | 'PMS'): Set<number> {
    let bucket = this.removed.get(productType);
    if (!bucket) {
      bucket = new Set<number>();
      this.removed.set(productType, bucket);
    }
    return bucket;
  }

  private persist(): void {
    if (typeof localStorage === 'undefined') return;
    try {
      localStorage.setItem(this.storageKey, JSON.stringify({
        added: { MUTUAL_FUND: this.getAdded('MUTUAL_FUND'), PMS: this.getAdded('PMS') },
        removed: {
          MUTUAL_FUND: Array.from(this.removedBucket('MUTUAL_FUND')),
          PMS: Array.from(this.removedBucket('PMS')),
        },
      }));
    } catch (error) {
      console.warn('Failed to persist Watch List state:', error);
    }
  }

  private restore(): void {
    if (typeof localStorage === 'undefined') return;
    try {
      const raw = localStorage.getItem(this.storageKey);
      if (!raw) return;
      const payload = JSON.parse(raw);
      for (const productType of ['MUTUAL_FUND', 'PMS'] as const) {
        for (const product of Array.isArray(payload?.added?.[productType]) ? payload.added[productType] : []) {
          if (product?.id) this.addedBucket(productType).set(Number(product.id), product);
        }
        for (const id of Array.isArray(payload?.removed?.[productType]) ? payload.removed[productType] : []) {
          this.removedBucket(productType).add(Number(id));
        }
      }
    } catch (error) {
      console.warn('Failed to restore Watch List state:', error);
    }
  }
}
