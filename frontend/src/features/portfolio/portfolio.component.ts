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
  quantity: number;
  invested_value: number;
  current_value: number;
  pnl: number;
  assets: PortfolioAssetNode[];
}

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

  /*
   * Keyed by asset.id (not a compound key like expandedAsset above,
   * since Quant Details lives at the final, already-unique
   * underlying row level, one row per asset id).
   */
  expandedQuantsAssetId: number | null = null;

  loading = true;
  error = '';

  /**
   * Asset currently being edited.
   */
  editingAssetId: number | null = null;

  /**
   * Temporary manual price entered by the user.
   */
  manualPriceInput = '';

  /**
   * Asset currently being saved.
   */
  savingManualPriceAssetId: number | null = null;

  /**
   * Per-asset error messages.
   */
  manualPriceErrors: Record<number, string> = {};

  /**
   * Transaction workbook upload state.
   */
  uploadingTransactions = false;

  ngOnInit(): void {
    this.loadPortfolio();

    this.refreshSubscription = timer(30000, 30000).subscribe(() => {
      this.loadPortfolio(true);
    });
  }

  ngOnDestroy(): void {
    this.refreshSubscription?.unsubscribe();
  }

  loadPortfolio(silent = false): void {
    if (!silent) {
      this.loading = true;
      this.error = '';
    }

    this.portfolioApi.getPortfolioTree().subscribe({
      next: (response) => {
        this.families = response.families ?? [];

        this.validateSelections();

        this.loading = false;

        this.cdr.detectChanges();
      },

      error: (error) => {
        console.error('Portfolio API error:', error);

        this.loading = false;

        if (error?.status === 401 || error?.status === 403) {
          this.error = 'Authentication failed. Please log in again.';
        } else {
          this.error = 'Unable to load portfolio data.';
        }

        this.cdr.detectChanges();
      },
    });
  }

  refresh(): void {
    this.loadPortfolio();
  }

  /**
   * Triggered by the hidden <input type="file"> in the template.
   * Uploads the selected workbook to the existing
   * /api/investments/import-transactions/ endpoint, then
   * refreshes the tree so newly-imported transactions show up
   * immediately.
   */
  onTransactionFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];

    // Allow re-selecting the same file consecutively.
    input.value = '';

    if (!file) {
      return;
    }

    this.uploadingTransactions = true;

    this.investmentsApi.importTransactions(file).subscribe({
      next: (response) => {
        this.uploadingTransactions = false;

        const data = response.data;

        if (data) {
          this.toast.success(
            `Imported ${data.total_imported} transaction(s)` +
              (data.skipped_duplicates
                ? ` (${data.skipped_duplicates} duplicate(s) skipped).`
                : '.'),
          );
        } else {
          this.toast.success(response.message || 'Transactions imported.');
        }

        this.loadPortfolio(true);

        this.cdr.detectChanges();
      },

      error: (error) => {
        this.uploadingTransactions = false;

        const message =
          error?.error?.message || error?.error?.error || 'Unable to import the transaction file.';

        this.toast.error(message, 6000);

        this.cdr.detectChanges();
      },
    });
  }

  get familyOptions(): string[] {
    return this.families
      .map((family) => family.family_name)
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b));
  }

  get assetClassOptions(): string[] {
    const classes = new Set<string>();

    for (const family of this.filteredFamilies) {
      for (const portfolio of family.portfolios) {
        for (const assetClass of portfolio.asset_classes) {
          if (assetClass.asset_class) {
            classes.add(assetClass.asset_class);
          }
        }
      }
    }

    return Array.from(classes).sort((a, b) => a.localeCompare(b));
  }

  get advisorOptions(): string[] {
    const advisors = new Set<string>();

    for (const family of this.families) {
      if (this.selectedFamily && family.family_name !== this.selectedFamily) {
        continue;
      }

      for (const portfolio of family.portfolios) {
        for (const assetClass of portfolio.asset_classes) {
          if (this.selectedAssetClass && assetClass.asset_class !== this.selectedAssetClass) {
            continue;
          }

          for (const subClass of assetClass.sub_classes) {
            for (const asset of subClass.assets) {
              const advisor = asset.advisors?.trim();

              if (advisor) {
                advisors.add(advisor);
              }
            }
          }
        }
      }
    }

    return Array.from(advisors).sort((a, b) => a.localeCompare(b));
  }

  get filteredFamilies(): FamilyNode[] {
    if (!this.selectedFamily) {
      return this.families;
    }

    return this.families.filter((family) => family.family_name === this.selectedFamily);
  }

  get subClassSummaries(): SubClassSummary[] {
    const summaryMap = new Map<string, SubClassSummary>();

    for (const family of this.filteredFamilies) {
      for (const portfolio of family.portfolios) {
        for (const assetClass of portfolio.asset_classes) {
          if (this.selectedAssetClass && assetClass.asset_class !== this.selectedAssetClass) {
            continue;
          }

          for (const subClass of assetClass.sub_classes) {
            const filteredAssets = subClass.assets.filter((asset) => {
              if (!this.selectedAdvisor) {
                return true;
              }

              return asset.advisors?.trim() === this.selectedAdvisor;
            });

            if (!filteredAssets.length) {
              continue;
            }

            const key = subClass.sub_class || 'Unassigned';

            let summary = summaryMap.get(key);

            if (!summary) {
              summary = {
                sub_class: key,
                current_value: 0,
                invested_value: 0,
                pnl: 0,
                quantity: 0,
                xirr: null,
                assets: [],
              };

              summaryMap.set(key, summary);
            }

            summary.current_value += this.getAssetsCurrentValue(filteredAssets);
            summary.invested_value += this.getAssetsInvestedValue(filteredAssets);
            summary.pnl += this.getAssetsPnl(filteredAssets);
            summary.quantity += this.getAssetsQuantity(filteredAssets);
            summary.assets.push(...filteredAssets);
          }
        }
      }
    }

    return Array.from(summaryMap.values())
      .map((summary) => ({
        ...summary,
        xirr: this.calculateXirr(summary.assets),
      }))
      .sort((a, b) => a.sub_class.localeCompare(b.sub_class));
  }

  getSubClassAssets(subClass: string): PortfolioAssetNode[] {
    return this.subClassSummaries.find((summary) => summary.sub_class === subClass)?.assets ?? [];
  }

  /**
   * LEVEL 1:
   * Sub Class expansion.
   */
  toggleSubClass(subClass: string): void {
    if (this.expandedSubClass === subClass) {
      this.expandedSubClass = '';
      this.expandedAsset = '';
      this.expandedQuantsAssetId = null;
      return;
    }

    this.expandedSubClass = subClass;
    this.expandedAsset = '';
  }

  /**
   * Returns the Asset Name groups displayed under a Sub Class.
   *
   * Multiple backend rows having the same asset_name are grouped
   * together at Level 2.
   */
  getAssetGroups(assets: PortfolioAssetNode[]): AssetGroup[] {
    const groups = new Map<string, PortfolioAssetNode[]>();

    for (const asset of assets) {
      const assetName = asset.asset_name?.trim() || 'Unnamed Asset';

      if (!groups.has(assetName)) {
        groups.set(assetName, []);
      }

      groups.get(assetName)!.push(asset);
    }

    return Array.from(groups.entries())
      .map(([asset_name, groupedAssets]) => ({
        asset_name,
        quantity: this.getAssetsQuantity(groupedAssets),
        invested_value: this.getAssetsInvestedValue(groupedAssets),
        current_value: this.getAssetsCurrentValue(groupedAssets),
        pnl: this.getAssetsPnl(groupedAssets),
        assets: groupedAssets,
      }))
      .sort((a, b) => a.asset_name.localeCompare(b.asset_name));
  }

  /**
   * LEVEL 2:
   * Asset Name expansion.
   *
   * This opens the FINAL Underlying/details section.
   */
  toggleAsset(assetKey: string): void {
    this.expandedAsset = this.expandedAsset === assetKey ? '' : assetKey;
  }

  /**
   * Expand / collapse the Quant Details row for one underlying
   * asset — Sector / Cap Type / AMC / Credit Rating / P/E / P/B /
   * ROE / YTM / Modified Duration / Average Maturity, sourced from
   * SecurityMaster via the Portfolio Tree API. Independent of
   * expandedSubClass/expandedAsset so opening quant details doesn't
   * collapse the underlying table it lives inside.
   */
  toggleQuantDetails(asset: PortfolioAssetNode): void {
    this.expandedQuantsAssetId = this.expandedQuantsAssetId === asset.id ? null : asset.id;
  }

  isQuantDetailsExpanded(asset: PortfolioAssetNode): boolean {
    return this.expandedQuantsAssetId === asset.id;
  }

  /**
   * True when at least one SecurityMaster-sourced field is present
   * for this asset, so the "Quant Details" toggle is only shown
   * when there's actually something to show — never a button that
   * opens onto an all-dashes row.
   */
  hasQuantDetails(asset: PortfolioAssetNode): boolean {
    return (
      asset.sector != null ||
      asset.cap_type != null ||
      asset.amc_name != null ||
      asset.credit_rating != null ||
      asset.pe_ratio != null ||
      asset.pb_ratio != null ||
      asset.roe != null ||
      asset.ytm != null ||
      asset.modified_duration != null ||
      asset.average_maturity != null
    );
  }

  /**
   * Stable key for an Asset Name group.
   */
  getAssetKey(subClass: string, assetName: string): string {
    return `${subClass}::${assetName}`;
  }

  /**
   * FINAL LEVEL:
   * Underlying name.
   *
   * If Underlying is empty/null, Asset Name itself is used.
   */
  getUnderlyingName(asset: PortfolioAssetNode): string {
    return asset.underlying?.trim() || asset.asset_name;
  }

  /**
   * Existing invested_value is used directly.
   */
  getUnderlyingInvested(asset: PortfolioAssetNode): number {
    return this.toNumber(asset.invested_value);
  }

  selectFamily(family: string): void {
    this.selectedFamily = this.selectedFamily === family ? '' : family;

    this.selectedAssetClass = '';
    this.selectedAdvisor = '';
    this.expandedSubClass = '';
    this.expandedAsset = '';
    this.expandedQuantsAssetId = null;
  }

  selectAssetClass(assetClass: string): void {
    this.selectedAssetClass = this.selectedAssetClass === assetClass ? '' : assetClass;

    this.selectedAdvisor = '';
    this.expandedSubClass = '';
    this.expandedAsset = '';
    this.expandedQuantsAssetId = null;
  }

  selectAdvisor(advisor: string): void {
    this.selectedAdvisor = this.selectedAdvisor === advisor ? '' : advisor;

    this.expandedSubClass = '';
    this.expandedAsset = '';
    this.expandedQuantsAssetId = null;
  }

  clearFamily(): void {
    this.selectedFamily = '';
    this.selectedAssetClass = '';
    this.selectedAdvisor = '';
    this.expandedSubClass = '';
    this.expandedAsset = '';
    this.expandedQuantsAssetId = null;
  }

  clearAssetClass(): void {
    this.selectedAssetClass = '';
    this.selectedAdvisor = '';
    this.expandedSubClass = '';
    this.expandedAsset = '';
    this.expandedQuantsAssetId = null;
  }

  clearAdvisor(): void {
    this.selectedAdvisor = '';
    this.expandedSubClass = '';
    this.expandedAsset = '';
    this.expandedQuantsAssetId = null;
  }

  isFamilySelected(family: string): boolean {
    return this.selectedFamily === family;
  }

  isAssetClassSelected(assetClass: string): boolean {
    return this.selectedAssetClass === assetClass;
  }

  isAdvisorSelected(advisor: string): boolean {
    return this.selectedAdvisor === advisor;
  }

  /**
   * trackBy functions.
   *
   * subClassSummaries / getAssetGroups() are derived from getters
   * that rebuild new array/object instances on every change
   * detection cycle (including the one triggered by clicking
   * "Edit" itself, and the 30s auto-refresh timer). Without a
   * stable trackBy, Angular's default identity check sees "new"
   * items every cycle and destroys/recreates the row DOM - which
   * is what makes the manual-price Edit button appear unresponsive.
   * These trackBy functions key rows by a stable value so Angular
   * reuses the existing DOM instead of tearing it down.
   */
  trackBySubClass(_index: number, summary: SubClassSummary): string {
    return summary.sub_class;
  }

  trackByAssetGroup(_index: number, group: AssetGroup): string {
    return group.asset_name;
  }

  trackByAssetId(_index: number, asset: PortfolioAssetNode): number {
    return asset.id;
  }

  onManualPriceEdit(event: MouseEvent, asset: PortfolioAssetNode): void {
    event.preventDefault();
    event.stopPropagation();

    if (!this.rbac.canEditPrices()) {
      this.toast.error('You do not have permission to edit prices.');
      return;
    }

    console.log('[Portfolio] Edit price clicked:', asset.id, asset.asset_name);

    this.startEditingPrice(asset);
  }

  /**
   * Start editing an asset's current price.
   */
  startEditingPrice(asset: PortfolioAssetNode): void {
    console.log('[Portfolio] Starting price edit:', asset.id, asset.asset_name);

    this.editingAssetId = asset.id;

    this.manualPriceInput =
      asset.current_price !== null && asset.current_price !== undefined
        ? String(asset.current_price)
        : '';

    this.manualPriceErrors[asset.id] = '';

    this.cdr.detectChanges();

    console.log('[Portfolio] Edit state:', {
      editingAssetId: this.editingAssetId,
      manualPriceInput: this.manualPriceInput,
    });
  }

  /**
   * Cancel manual price editing.
   */
  cancelEditingPrice(asset: PortfolioAssetNode): void {
    this.editingAssetId = null;
    this.manualPriceInput = '';

    this.manualPriceErrors[asset.id] = '';
  }

  /**
   * Save a manually entered current price.
   */
  saveManualPrice(asset: PortfolioAssetNode): void {
    const price = Number(this.manualPriceInput);

    if (!Number.isFinite(price) || price <= 0) {
      this.manualPriceErrors[asset.id] = 'Enter a valid price greater than 0.';
      return;
    }

    this.manualPriceErrors[asset.id] = '';

    this.savingManualPriceAssetId = asset.id;

    this.manualPriceService.updatePrice(asset.id, price).subscribe({
      next: (response) => {
        this.savingManualPriceAssetId = null;

        if (!response.success) {
          this.manualPriceErrors[asset.id] = response.message || 'Unable to update price.';

          this.cdr.detectChanges();
          return;
        }

        this.editingAssetId = null;
        this.manualPriceInput = '';

        /*
         * Reload the portfolio tree so that:
         *
         * Current Value
         * P&L
         * P&L %
         * XIRR
         *
         * are recalculated from the updated price.
         */
        this.loadPortfolio(true);
      },

      error: (error) => {
        console.error('Manual price update failed:', error);

        this.savingManualPriceAssetId = null;

        this.manualPriceErrors[asset.id] =
          error?.error?.message || 'Unable to update manual price.';

        this.cdr.detectChanges();
      },
    });
  }

  /**
   * Check whether an asset is currently being edited.
   */
  isEditingPrice(asset: PortfolioAssetNode): boolean {
    return this.editingAssetId === asset.id;
  }
  /**
   * Check whether an asset's current price was manually entered
   * (as opposed to fetched from Yahoo Finance / AMFI).
   */
  isManualPrice(asset: PortfolioAssetNode): boolean {
    return asset.price_source === 'MANUAL';
  }

  /**
   * Check whether ANY underlying asset inside a Sub Class (Level 1
   * row) has a manually entered current price.
   *
   * Used to highlight the Level 1 row's Current Value cell in
   * yellow, the same way Level 2 and Level 3 are highlighted.
   */
  subClassHasManualPrice(summary: SubClassSummary): boolean {
    return summary.assets.some((asset) => this.isManualPrice(asset));
  }

  /**
   * Check whether ANY underlying asset inside an Asset Name group
   * (Level 2 row) has a manually entered current price.
   *
   * Used to highlight the Level 2 row's Current Value cell in
   * yellow, the same way the individual underlying price cell is
   * highlighted at Level 3.
   */
  assetGroupHasManualPrice(assetGroup: AssetGroup): boolean {
    return assetGroup.assets.some((asset) => this.isManualPrice(asset));
  }

  /**
   * Check whether an asset price is currently being saved.
   */
  isSavingManualPrice(asset: PortfolioAssetNode): boolean {
    return this.savingManualPriceAssetId === asset.id;
  }

  /**
   * Get the manual-price error for an asset.
   */
  getManualPriceError(asset: PortfolioAssetNode): string {
    return this.manualPriceErrors[asset.id] || '';
  }

  formatAbsoluteCurrency(value: number): string {
    return this.formatCurrency(Math.abs(this.toNumber(value)));
  }

  formatCurrency(value: number): string {
    return new Intl.NumberFormat('en-IN', {
      maximumFractionDigits: 0,
    }).format(this.toNumber(value));
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
    if (value === null || value === undefined) {
      return '-';
    }

    return `${this.formatDecimal(value)}%`;
  }

  getPnlClass(value: number): string {
    if (value > 0) {
      return 'positive';
    }

    if (value < 0) {
      return 'negative';
    }

    return 'neutral';
  }

  hasPriceDate(asset: PortfolioAssetNode): boolean {
    return !!this.getPriceDate(asset);
  }

  getPriceDate(asset: PortfolioAssetNode): string | null {
    const extendedAsset = asset as PortfolioAssetNode & {
      price_date?: string | null;
      updated_at?: string | null;
    };

    return extendedAsset.price_date ?? extendedAsset.updated_at ?? null;
  }

  formatPriceDate(dateValue: string | null): string {
    if (!dateValue) {
      return '';
    }

    const parsedDate = new Date(dateValue);

    if (Number.isNaN(parsedDate.getTime())) {
      return '';
    }

    return new Intl.DateTimeFormat('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    }).format(parsedDate);
  }

  private getAssetsCurrentValue(assets: PortfolioAssetNode[]): number {
    return assets.reduce((total, asset) => total + this.toNumber(asset.current_value), 0);
  }

  private getAssetsInvestedValue(assets: PortfolioAssetNode[]): number {
    return assets.reduce((total, asset) => total + this.toNumber(asset.invested_value), 0);
  }

  private getAssetsPnl(assets: PortfolioAssetNode[]): number {
    return assets.reduce((total, asset) => total + this.toNumber(asset.pnl), 0);
  }

  private getAssetsQuantity(assets: PortfolioAssetNode[]): number {
    return assets.reduce((total, asset) => total + this.toNumber(asset.quantity), 0);
  }

  private getSubClassCurrentValue(subClass: SubClassNode): number {
    return this.getAssetsCurrentValue(subClass.assets);
  }

  private getSubClassPnl(subClass: SubClassNode): number {
    return this.getAssetsPnl(subClass.assets);
  }

  private getSubClassQuantity(subClass: SubClassNode): number {
    return this.getAssetsQuantity(subClass.assets);
  }

  private calculateXirr(assets: PortfolioAssetNode[]): number | null {
    const validAssets = assets.filter(
      (asset) =>
        asset.xirr !== null && asset.xirr !== undefined && this.toNumber(asset.invested_value) > 0,
    );

    if (!validAssets.length) {
      return null;
    }

    let weightedXirr = 0;
    let totalInvested = 0;

    for (const asset of validAssets) {
      const invested = this.toNumber(asset.invested_value);

      const xirr = this.toNumber(asset.xirr);

      weightedXirr += xirr * invested;
      totalInvested += invested;
    }

    return totalInvested ? weightedXirr / totalInvested : null;
  }

  private toNumber(value: number | null | undefined): number {
    if (value === null || value === undefined) {
      return 0;
    }

    const numberValue = Number(value);

    return Number.isFinite(numberValue) ? numberValue : 0;
  }

  private validateSelections(): void {
    if (this.selectedFamily && !this.familyOptions.includes(this.selectedFamily)) {
      this.selectedFamily = '';
    }

    if (this.selectedAssetClass && !this.assetClassOptions.includes(this.selectedAssetClass)) {
      this.selectedAssetClass = '';
    }

    if (this.selectedAdvisor && !this.advisorOptions.includes(this.selectedAdvisor)) {
      this.selectedAdvisor = '';
    }

    if (
      this.expandedSubClass &&
      !this.subClassSummaries.some((summary) => summary.sub_class === this.expandedSubClass)
    ) {
      this.expandedSubClass = '';
      this.expandedAsset = '';
      this.expandedQuantsAssetId = null;
    }
  }
}
