import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ChangeDetectorRef, Component, OnDestroy, OnInit, inject } from '@angular/core';
import { Subscription, timer } from 'rxjs';

import {
  PortfolioApiService,
  PortfolioAssetNode,
  FamilyNode,
  SubClassNode,
} from '../../core/services/portfolio-api.service';

import { ManualPriceService } from '../../core/services/manual-price.service';
import { InvestmentsApiService } from '../../core/services/investments-api.service';
import { ToastService } from '../../core/services/toast.service';
import { RbacService } from '../../core/services/rbac.service';

interface SubClassSummary {
  sub_class: string;
  current_value: number;
  invested_value: number;
  pnl: number;
  quantity: number;
  xirr: number | null;
  assets: PortfolioAssetNode[];
}

interface AssetGroup {
  asset_name: string;
  family_name: string;
  quantity: number;
  invested_value: number;
  current_value: number;
  pnl: number;
  xirr: number | null;
  assets: PortfolioAssetNode[];
}

type PortfolioSortColumn = 'sub_class' | 'asset_name' | 'family_name' | 'underlying' | 'family' | 'isin' | 'quantity' | 'invested_value' | 'invested' | 'current_price' | 'current_value' | 'pnl' | 'xirr';
type PortfolioSortDirection = 'normal' | 'asc' | 'desc';
interface PortfolioSortState { column: PortfolioSortColumn | null; direction: PortfolioSortDirection; }

@Component({
  selector: 'app-portfolio',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './portfolio.component.html',
  styleUrl: './portfolio.component.scss',
})
export class PortfolioComponent implements OnInit, OnDestroy {
  private readonly portfolioApi = inject(PortfolioApiService);
  private readonly manualPriceService = inject(ManualPriceService);
  private readonly investmentsApi = inject(InvestmentsApiService);
  private readonly toast = inject(ToastService);
  private readonly cdr = inject(ChangeDetectorRef);
  readonly rbac = inject(RbacService);
  private refreshSubscription: Subscription | null = null;
  families: FamilyNode[] = [];
  selectedFamily = '';
  selectedAssetClass = '';
  selectedAdvisor = '';
  expandedSubClass = '';
  expandedAsset = '';
  expandedQuantsAssetId: number | null = null;
  loading = true;
  error = '';
  private readonly subclassSort: PortfolioSortState = { column: null, direction: 'normal' };
  private readonly assetSort = new Map<string, PortfolioSortState>();
  private readonly underlyingSort = new Map<string, PortfolioSortState>();
  editingAssetId: number | null = null;
  manualPriceInput = '';
  savingManualPriceAssetId: number | null = null;
  manualPriceErrors: Record<number, string> = {};
  uploadingTransactions = false;

  ngOnInit(): void {
    this.loadPortfolio();
    this.refreshSubscription = timer(30000, 30000).subscribe(() => this.loadPortfolio(true));
  }
  ngOnDestroy(): void { this.refreshSubscription?.unsubscribe(); }
  loadPortfolio(silent = false): void {
    if (!silent) { this.loading = true; this.error = ''; }
    this.portfolioApi.getPortfolioTree().subscribe({
      next: (response) => { this.families = response.families ?? []; this.validateSelections(); this.loading = false; this.cdr.detectChanges(); },
      error: (error) => {
        console.error('Portfolio API error:', error); this.loading = false;
        this.error = error?.status === 401 || error?.status === 403 ? 'Authentication failed. Please log in again.' : 'Unable to load portfolio data.';
        this.cdr.detectChanges();
      },
    });
  }
  refresh(): void { this.loadPortfolio(); }

  cycleSort(level: 'subclass' | 'asset' | 'underlying', column: PortfolioSortColumn, parentKey = ''): void {
    const state = this.getSortState(level, parentKey);
    if (state.column !== column) { state.column = column; state.direction = 'desc'; }
    else if (state.direction === 'desc') state.direction = 'asc';
    else { state.column = null; state.direction = 'normal'; }
    this.cdr.detectChanges();
  }
  getSortIndicator(level: 'subclass' | 'asset' | 'underlying', column: PortfolioSortColumn, parentKey = ''): string {
    const state = this.getSortState(level, parentKey);
    if (state.column !== column || state.direction === 'normal') return '';
    return state.direction === 'desc' ? '↓' : '↑';
  }
  private getSortState(level: 'subclass' | 'asset' | 'underlying', parentKey: string): PortfolioSortState {
    if (level === 'subclass') return this.subclassSort;
    const store = level === 'asset' ? this.assetSort : this.underlyingSort;
    let state = store.get(parentKey);
    if (!state) { state = { column: null, direction: 'normal' }; store.set(parentKey, state); }
    return state;
  }
  private sortRows<T>(rows: T[], level: 'subclass' | 'asset' | 'underlying', parentKey: string, valueGetter: (row: T, column: PortfolioSortColumn) => unknown, defaultCompare: (left: T, right: T) => number): T[] {
    const state = this.getSortState(level, parentKey);
    if (!state.column || state.direction === 'normal') return rows;
    const sorted = [...rows]; const column = state.column;
    sorted.sort((left, right) => {
      const comparison = this.compareSortValues(valueGetter(left, column), valueGetter(right, column));
      if (comparison === 0) return defaultCompare(left, right);
      return state.direction === 'asc' ? comparison : -comparison;
    });
    return sorted;
  }
  private compareSortValues(left: unknown, right: unknown): number {
    const leftNumber = this.toNullableNumber(left); const rightNumber = this.toNullableNumber(right);
    if (leftNumber !== null && rightNumber !== null) { if (leftNumber === rightNumber) return 0; return leftNumber < rightNumber ? -1 : 1; }
    if (left == null || left === '') return right == null || right === '' ? 0 : 1;
    if (right == null || right === '') return -1;
    return String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: 'base' });
  }
  private toNullableNumber(value: unknown): number | null {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value); return Number.isFinite(number) ? number : null;
  }
  onTransactionFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement; const file = input.files?.[0]; input.value = '';
    if (!file) return;
    this.uploadingTransactions = true;
    this.investmentsApi.importTransactions(file).subscribe({
      next: (response) => {
        this.uploadingTransactions = false; const data = response.data;
        if (data) this.toast.success(`Imported ${data.total_imported} transaction(s)` + (data.skipped_duplicates ? ` (${data.skipped_duplicates} duplicate(s) skipped).` : '.'));
        else this.toast.success(response.message || 'Transactions imported.');
        this.loadPortfolio(true); this.cdr.detectChanges();
      },
      error: (error) => {
        this.uploadingTransactions = false;
        const message = error?.error?.message || error?.error?.error || 'Unable to import the transaction file.';
        this.toast.error(message, 6000); this.cdr.detectChanges();
      },
    });
  }
  get familyOptions(): string[] { return this.families.map((family) => family.family_name).filter(Boolean).sort((a, b) => a.localeCompare(b)); }
  get assetClassOptions(): string[] {
    const classes = new Set<string>();
    for (const family of this.filteredFamilies) for (const portfolio of family.portfolios) for (const assetClass of portfolio.asset_classes) if (assetClass.asset_class) classes.add(assetClass.asset_class);
    return Array.from(classes).sort((a, b) => a.localeCompare(b));
  }
  get advisorOptions(): string[] {
    const advisors = new Set<string>();
    for (const family of this.families) {
      if (this.selectedFamily && family.family_name !== this.selectedFamily) continue;
      for (const portfolio of family.portfolios) for (const assetClass of portfolio.asset_classes) {
        if (this.selectedAssetClass && assetClass.asset_class !== this.selectedAssetClass) continue;
        for (const subClass of assetClass.sub_classes) for (const asset of subClass.assets) {
          const advisor = asset.advisors?.trim(); if (advisor) advisors.add(advisor);
        }
      }
    }
    return Array.from(advisors).sort((a, b) => a.localeCompare(b));
  }
  get filteredFamilies(): FamilyNode[] { return this.selectedFamily ? this.families.filter((family) => family.family_name === this.selectedFamily) : this.families; }

  get subClassSummaries(): SubClassSummary[] {
    const summaryMap = new Map<string, SubClassSummary>();
    for (const family of this.filteredFamilies) for (const portfolio of family.portfolios) for (const assetClass of portfolio.asset_classes) {
      if (this.selectedAssetClass && assetClass.asset_class !== this.selectedAssetClass) continue;
      for (const subClass of assetClass.sub_classes) {
        const filteredAssets = subClass.assets.filter((asset) => !this.selectedAdvisor || asset.advisors?.trim() === this.selectedAdvisor);
        if (!filteredAssets.length) continue;
        const key = subClass.sub_class || 'Unassigned';
        let summary = summaryMap.get(key);
        if (!summary) {
          summary = { sub_class: key, current_value: 0, invested_value: 0, pnl: 0, quantity: 0, xirr: null, assets: [] };
          summaryMap.set(key, summary);
        }
        summary.current_value += this.getAssetsCurrentValue(filteredAssets);
        summary.invested_value += this.getAssetsInvestedValue(filteredAssets);
        summary.pnl += this.getAssetsPnl(filteredAssets);
        summary.quantity += this.getAssetsQuantity(filteredAssets);
        summary.assets.push(...filteredAssets);
      }
    }
    const summaries = Array.from(summaryMap.values()).map((summary) => ({ ...summary, xirr: this.getSubClassXirr(summary.assets) }));
    return this.sortRows(summaries, 'subclass', '', (summary, column) => {
      switch (column) {
        case 'sub_class': return summary.sub_class;
        case 'quantity': return summary.quantity;
        case 'invested_value': return summary.invested_value;
        case 'current_value': return summary.current_value;
        case 'pnl': return summary.pnl;
        case 'xirr': return summary.xirr;
        default: return summary.sub_class;
      }
    }, (left, right) => left.sub_class.localeCompare(right.sub_class));
  }

  private getSortableValue(summary: SubClassSummary, column: PortfolioSortColumn): number | null {
    switch (column) {
      case 'quantity': return this.toNullableNumber(summary.quantity);
      case 'invested_value': return this.toNullableNumber(summary.invested_value);
      case 'current_value': return this.toNullableNumber(summary.current_value);
      case 'pnl': return this.toNullableNumber(summary.pnl);
      case 'xirr': return this.toNullableNumber(summary.xirr);
      default: return null;
    }
  }
  getSubClassAssets(subClass: string): PortfolioAssetNode[] { return this.subClassSummaries.find((summary) => summary.sub_class === subClass)?.assets ?? []; }
  toggleSubClass(subClass: string): void {
    if (this.expandedSubClass === subClass) { this.expandedSubClass = ''; this.expandedAsset = ''; this.expandedQuantsAssetId = null; return; }
    this.expandedSubClass = subClass; this.expandedAsset = '';
  }
  getAssetGroups(assets: PortfolioAssetNode[], parentKey = ''): AssetGroup[] {
    const groups = new Map<string, PortfolioAssetNode[]>();
    for (const asset of assets) {
      const assetName = asset.asset_name?.trim() || 'Unnamed Asset';
      if (!groups.has(assetName)) groups.set(assetName, []);
      groups.get(assetName)!.push(asset);
    }
    const groupsList: AssetGroup[] = Array.from(groups.entries()).map(([asset_name, groupedAssets]) => {
      const familyNames = Array.from(new Set(groupedAssets.map((asset) => asset.family_name?.trim()).filter((family): family is string => Boolean(family)))).sort((a, b) => a.localeCompare(b));
      return {
        asset_name,
        family_name: familyNames.length ? familyNames.join(', ') : '-',
        quantity: this.getAssetsQuantity(groupedAssets),
        invested_value: this.getAssetsInvestedValue(groupedAssets),
        current_value: this.getAssetsCurrentValue(groupedAssets),
        pnl: this.getAssetsPnl(groupedAssets),
        xirr: this.getAssetNameXirr(groupedAssets),
        assets: groupedAssets,
      };
    });
    return this.sortRows(groupsList, 'asset', parentKey, (group, column) => {
      switch (column) {
        case 'asset_name': return group.asset_name;
        case 'family_name': return group.family_name;
        case 'quantity': return group.quantity;
        case 'invested_value': return group.invested_value;
        case 'current_value': return group.current_value;
        case 'pnl': return group.pnl;
        case 'xirr': return group.xirr;
        default: return group.asset_name;
      }
    }, (left, right) => left.asset_name.localeCompare(right.asset_name));
  }
  getSortedUnderlyingAssets(assets: PortfolioAssetNode[]): PortfolioAssetNode[] {
    const parentKey = this.expandedAsset || 'empty';
    return this.sortRows(assets, 'underlying', parentKey, (asset, column) => {
      switch (column) {
        case 'underlying': return this.getUnderlyingName(asset);
        case 'family': return asset.family_name || '';
        case 'isin': return asset.isin || '';
        case 'quantity': return asset.quantity;
        case 'invested': return asset.invested_value;
        case 'current_price': return asset.current_price;
        case 'current_value': return asset.current_value;
        case 'pnl': return asset.pnl;
        case 'xirr': return asset.xirr;
        default: return this.getUnderlyingName(asset);
      }
    }, (left, right) => this.getUnderlyingName(left).localeCompare(this.getUnderlyingName(right)));
  }
  toggleAsset(assetKey: string): void { this.expandedAsset = this.expandedAsset === assetKey ? '' : assetKey; }
  toggleQuantDetails(asset: PortfolioAssetNode): void { this.expandedQuantsAssetId = this.expandedQuantsAssetId === asset.id ? null : asset.id; }
  isQuantDetailsExpanded(asset: PortfolioAssetNode): boolean { return this.expandedQuantsAssetId === asset.id; }
  hasQuantDetails(asset: PortfolioAssetNode): boolean { return asset.sector != null || asset.cap_type != null || asset.amc_name != null || asset.credit_rating != null || asset.pe_ratio != null || asset.pb_ratio != null || asset.roe != null || asset.ytm != null || asset.modified_duration != null || asset.average_maturity != null; }
  getAssetKey(subClass: string, assetName: string): string { return `${subClass}::${assetName}`; }
  getUnderlyingName(asset: PortfolioAssetNode): string { return asset.underlying?.trim() || asset.asset_name; }
  getUnderlyingInvested(asset: PortfolioAssetNode): number { return this.toNumber(asset.invested_value); }
  selectFamily(family: string): void { this.selectedFamily = this.selectedFamily === family ? '' : family; this.selectedAssetClass = ''; this.selectedAdvisor = ''; this.expandedSubClass = ''; this.expandedAsset = ''; this.expandedQuantsAssetId = null; }
  selectAssetClass(assetClass: string): void { this.selectedAssetClass = this.selectedAssetClass === assetClass ? '' : assetClass; this.selectedAdvisor = ''; this.expandedSubClass = ''; this.expandedAsset = ''; this.expandedQuantsAssetId = null; }
  selectAdvisor(advisor: string): void { this.selectedAdvisor = this.selectedAdvisor === advisor ? '' : advisor; this.expandedSubClass = ''; this.expandedAsset = ''; this.expandedQuantsAssetId = null; }
  clearFamily(): void { this.selectedFamily = ''; this.selectedAssetClass = ''; this.selectedAdvisor = ''; this.expandedSubClass = ''; this.expandedAsset = ''; this.expandedQuantsAssetId = null; }
  clearAssetClass(): void { this.selectedAssetClass = ''; this.selectedAdvisor = ''; this.expandedSubClass = ''; this.expandedAsset = ''; this.expandedQuantsAssetId = null; }
  clearAdvisor(): void { this.selectedAdvisor = ''; this.expandedSubClass = ''; this.expandedAsset = ''; this.expandedQuantsAssetId = null; }
  isFamilySelected(family: string): boolean { return this.selectedFamily === family; }
  isAssetClassSelected(assetClass: string): boolean { return this.selectedAssetClass === assetClass; }
  isAdvisorSelected(advisor: string): boolean { return this.selectedAdvisor === advisor; }
  trackBySubClass(_index: number, summary: SubClassSummary): string { return summary.sub_class; }
  trackByAssetGroup(_index: number, group: AssetGroup): string { return group.asset_name; }
  trackByAssetId(_index: number, asset: PortfolioAssetNode): number { return asset.id; }
  onManualPriceEdit(event: MouseEvent, asset: PortfolioAssetNode): void { event.preventDefault(); event.stopPropagation(); if (!this.rbac.canEditPrices()) { this.toast.error('You do not have permission to edit prices.'); return; } this.startEditingPrice(asset); }
  startEditingPrice(asset: PortfolioAssetNode): void { this.editingAssetId = asset.id; this.manualPriceInput = asset.current_price !== null && asset.current_price !== undefined ? String(asset.current_price) : ''; this.manualPriceErrors[asset.id] = ''; this.cdr.detectChanges(); }
  cancelEditingPrice(asset: PortfolioAssetNode): void { this.editingAssetId = null; this.manualPriceInput = ''; this.manualPriceErrors[asset.id] = ''; }
  saveManualPrice(asset: PortfolioAssetNode): void {
    const price = Number(this.manualPriceInput);
    if (!Number.isFinite(price) || price <= 0) { this.manualPriceErrors[asset.id] = 'Enter a valid price greater than 0.'; return; }
    this.manualPriceErrors[asset.id] = ''; this.savingManualPriceAssetId = asset.id;
    this.manualPriceService.updatePrice(asset.id, price).subscribe({
      next: (response) => { this.savingManualPriceAssetId = null; if (!response.success) { this.manualPriceErrors[asset.id] = response.message || 'Unable to update price.'; this.cdr.detectChanges(); return; } this.editingAssetId = null; this.manualPriceInput = ''; this.loadPortfolio(true); },
      error: (error) => { console.error('Manual price update failed:', error); this.savingManualPriceAssetId = null; this.manualPriceErrors[asset.id] = error?.error?.message || 'Unable to update manual price.'; this.cdr.detectChanges(); },
    });
  }
  isEditingPrice(asset: PortfolioAssetNode): boolean { return this.editingAssetId === asset.id; }
  isManualPrice(asset: PortfolioAssetNode): boolean { return asset.price_source === 'MANUAL'; }
  subClassHasManualPrice(summary: SubClassSummary): boolean { return summary.assets.some((asset) => this.isManualPrice(asset)); }
  assetGroupHasManualPrice(assetGroup: AssetGroup): boolean { return assetGroup.assets.some((asset) => this.isManualPrice(asset)); }
  isSavingManualPrice(asset: PortfolioAssetNode): boolean { return this.savingManualPriceAssetId === asset.id; }
  getManualPriceError(asset: PortfolioAssetNode): string { return this.manualPriceErrors[asset.id] || ''; }
  formatAbsoluteCurrency(value: number): string { return this.formatCurrency(Math.abs(this.toNumber(value))); }
  formatCurrency(value: number): string { return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(this.toNumber(value)); }
  formatNumber(value: number): string { return new Intl.NumberFormat('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(this.toNumber(value)); }
  formatDecimal(value: number): string { return new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(this.toNumber(value)); }
  formatPercentage(value: number | null): string { if (value === null || value === undefined) return '-'; return `${this.formatDecimal(value)}%`; }
  getPnlClass(value: number): string { if (value > 0) return 'positive'; if (value < 0) return 'negative'; return 'neutral'; }
  hasPriceDate(asset: PortfolioAssetNode): boolean { return !!this.getPriceDate(asset); }
  getPriceDate(asset: PortfolioAssetNode): string | null { const extendedAsset = asset as PortfolioAssetNode & { price_date?: string | null; updated_at?: string | null }; return extendedAsset.price_date ?? extendedAsset.updated_at ?? null; }
  formatPriceDate(dateValue: string | null): string { if (!dateValue) return ''; const parsedDate = new Date(dateValue); if (Number.isNaN(parsedDate.getTime())) return ''; return new Intl.DateTimeFormat('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }).format(parsedDate); }
  private getAssetsCurrentValue(assets: PortfolioAssetNode[]): number { return assets.reduce((total, asset) => total + this.toNumber(asset.current_value), 0); }
  private getAssetsInvestedValue(assets: PortfolioAssetNode[]): number { return assets.reduce((total, asset) => total + this.toNumber(asset.invested_value), 0); }
  private getAssetsPnl(assets: PortfolioAssetNode[]): number { return assets.reduce((total, asset) => total + this.toNumber(asset.pnl), 0); }
  private getAssetsQuantity(assets: PortfolioAssetNode[]): number { return assets.reduce((total, asset) => total + this.toNumber(asset.quantity), 0); }
  private getSubClassCurrentValue(subClass: SubClassNode): number { return this.getAssetsCurrentValue(subClass.assets); }
  private getSubClassPnl(subClass: SubClassNode): number { return this.getAssetsPnl(subClass.assets); }
  private getSubClassQuantity(subClass: SubClassNode): number { return this.getAssetsQuantity(subClass.assets); }

  /** Sub Class XIRR is supplied by the backend as one XIRR over all cash flows belonging to the Sub Class. */
  private getSubClassXirr(assets: PortfolioAssetNode[]): number | null {
    const values = assets
      .map((asset) => (asset as PortfolioAssetNode & { sub_class_xirr?: number | null }).sub_class_xirr)
      .filter((value): value is number => value !== null && value !== undefined);
    return values.length ? values[0] : null;
  }

  private getAssetNameXirr(assets: PortfolioAssetNode[]): number | null {
    const values = assets
      .map((asset) => (asset as PortfolioAssetNode & { asset_name_xirr?: number | null }).asset_name_xirr)
      .filter((value): value is number => value !== null && value !== undefined);
    return values.length ? values[0] : null;
  }

  private calculateXirr(assets: PortfolioAssetNode[]): number | null {
    const validAssets = assets.filter((asset) => asset.xirr !== null && asset.xirr !== undefined && this.toNumber(asset.invested_value) > 0);
    if (!validAssets.length) return null;
    let weightedXirr = 0; let totalInvested = 0;
    for (const asset of validAssets) { const invested = this.toNumber(asset.invested_value); const xirr = this.toNumber(asset.xirr); weightedXirr += xirr * invested; totalInvested += invested; }
    return totalInvested ? weightedXirr / totalInvested : null;
  }
  private toNumber(value: number | null | undefined): number { if (value === null || value === undefined) return 0; const numberValue = Number(value); return Number.isFinite(numberValue) ? numberValue : 0; }
  private validateSelections(): void {
    if (this.selectedFamily && !this.familyOptions.includes(this.selectedFamily)) this.selectedFamily = '';
    if (this.selectedAssetClass && !this.assetClassOptions.includes(this.selectedAssetClass)) this.selectedAssetClass = '';
    if (this.selectedAdvisor && !this.advisorOptions.includes(this.selectedAdvisor)) this.selectedAdvisor = '';
    if (this.expandedSubClass && !this.subClassSummaries.some((summary) => summary.sub_class === this.expandedSubClass)) { this.expandedSubClass = ''; this.expandedAsset = ''; this.expandedQuantsAssetId = null; }
  }
}
