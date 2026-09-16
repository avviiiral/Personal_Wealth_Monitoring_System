import { Component, effect, inject } from '@angular/core';
import { CommonModule } from '@angular/common';

import { DashboardComponent as BaseDashboardComponent } from './dashboard.component.base';
import { ThemeService } from '../../core/services/theme.service';
import { WealthApiService } from '../../core/services/wealth-api.service';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './dashboard.component.html',
  styleUrls: [
    './dashboard.component.scss',
    './dashboard-investment-summary-hierarchy.scss',
  ],
})
export class DashboardComponent extends BaseDashboardComponent {
  private allocationRenderRequest = 0;
  private readonly allocationThemeService = inject(ThemeService);
  private readonly dashboardWealthApi = inject(WealthApiService);

  private xirrAssetClassRows: Array<{
    asset_category: string;
    asset_class: string;
    xirr: number;
    current_value: number;
  }> = [];

  override standardAllocations: Record<string, number> = {};
  override standardAllocationDraft: Record<string, number> = {};
  override standardAllocationEditing = false;
  override standardAllocationSaving = false;
  override standardAllocationError = '';

  /**
   * Re-render the canvas-based Allocation chart whenever the theme
   * changes. Chart.js renders legend text inside the canvas, so the
   * legend color is fixed when the chart is created and does not react
   * to the HTML dark-theme class by itself.
   */
  private readonly allocationThemeEffect = effect(() => {
    this.allocationThemeService.mode();

    if (!this.loading && this.investmentSummary && this.portfolioTree) {
      setTimeout(() => {
        (this as any).renderAllocationChart();
      });
    }
  });

  /**
   * Dashboard Investment Summary hierarchy:
   *
   *   Asset Category = Portfolio Asset Class
   *   Sub Class      = Portfolio Sub Class
   *
   * Values are aggregated from the existing Portfolio Tree so the
   * Dashboard uses exactly the same classification hierarchy as the
   * Portfolio page without changing the backend investment-summary
   * calculation or the Allocation chart.
   */
  override get investmentSummaryGroups(): Array<{
    asset_category: string;
    current_value: number;
    percentage_of_total: number;
    asset_classes: Array<{
      asset_class: string;
      current_value: number;
      percentage_of_total: number;
      raw_asset_classes: string[];
    }>;
  }> {
    const groups = new Map<
      string,
      {
        asset_category: string;
        current_value: number;
        asset_classes: Map<
          string,
          {
            asset_class: string;
            current_value: number;
            percentage_of_total: number;
            raw_asset_classes: string[];
          }
        >;
      }
    >();

    for (const family of this.portfolioTree?.families ?? []) {
      if (this.selectedFamily && family.family_name !== this.selectedFamily) {
        continue;
      }

      for (const portfolio of family.portfolios ?? []) {
        for (const assetClass of portfolio.asset_classes ?? []) {
          const category = (assetClass.asset_class || 'Unassigned').trim() || 'Unassigned';

          let group = groups.get(category);

          if (!group) {
            group = {
              asset_category: category,
              current_value: 0,
              asset_classes: new Map(),
            };

            groups.set(category, group);
          }

          for (const subClass of assetClass.sub_classes ?? []) {
            const subClassName = (subClass.sub_class || 'Unassigned').trim() || 'Unassigned';

            let classRow = group.asset_classes.get(subClassName);

            if (!classRow) {
              classRow = {
                asset_class: subClassName,
                current_value: 0,
                percentage_of_total: 0,
                raw_asset_classes: [],
              };

              group.asset_classes.set(subClassName, classRow);
            }

            for (const asset of subClass.assets ?? []) {
              const currentValue = Number(asset.current_value);

              if (!Number.isFinite(currentValue)) {
                continue;
              }

              group.current_value += currentValue;
              classRow.current_value += currentValue;
            }
          }
        }
      }
    }

    const totalCurrentValue = Array.from(groups.values()).reduce(
      (total, group) => total + group.current_value,
      0,
    );

    return Array.from(groups.values()).map((group) => ({
      asset_category: group.asset_category,
      current_value: group.current_value,
      percentage_of_total: totalCurrentValue
        ? Math.round((group.current_value / totalCurrentValue) * 10000) / 100
        : 0,
      asset_classes: Array.from(group.asset_classes.values()).map((assetClass) => ({
        ...assetClass,
        percentage_of_total: totalCurrentValue
          ? Math.round((assetClass.current_value / totalCurrentValue) * 10000) / 100
          : 0,
      })),
    }));
  }

  /**
   * Allocation chart uses the exact Asset Category and % of Total
   * Investment shown in the Dashboard Investment Summary table.
   */
  override get allocationByCategory(): Array<{
    category: string;
    value: number;
    percentage: number;
  }> {
    return this.investmentSummaryGroups
      .filter((group) => group.current_value > 0)
      .map((group) => ({
        category: group.asset_category,
        value: group.percentage_of_total,
        percentage: group.percentage_of_total,
      }));
  }

  /**
   * The base Dashboard loads Investment Summary and Portfolio Tree
   * independently. Allocation uses the Portfolio Tree-backed groups,
   * so retry rendering until both sources are ready.
   */
  override loadDashboard(): void {
    const request = ++this.allocationRenderRequest;

    super.loadDashboard();
    this.loadStandardAllocations();
    this.loadXirrByAssetClass();

    const renderWhenReady = (attempt: number): void => {
      if (request !== this.allocationRenderRequest) {
        return;
      }

      if (!this.loading && this.investmentSummary && this.portfolioTree) {
        (this as any).renderAllocationChart();
        return;
      }

      if (attempt >= 100) {
        return;
      }

      setTimeout(() => renderWhenReady(attempt + 1), 100);
    };

    setTimeout(() => renderWhenReady(0));
  }

  private loadStandardAllocations(): void {
    this.standardAllocationError = '';

    this.dashboardWealthApi.getStandardAllocations(this.selectedFamily || undefined).subscribe({
      next: (data) => {
        this.standardAllocations = this.normalizeAllocationMap(data?.allocations);
        this.standardAllocationDraft = { ...this.standardAllocations };
      },
      error: (error) => {
        console.error('STANDARD ALLOCATION API ERROR:', error);
        this.standardAllocationError = 'Unable to load Standard Allocation.';
        this.standardAllocations = {};
        this.standardAllocationDraft = {};
      },
    });
  }

  private loadXirrByAssetClass(): void {
    this.xirrAssetClassRows = [];

    this.dashboardWealthApi
      .getXirrByAssetClass(this.selectedFamily || undefined)
      .subscribe({
        next: (data) => {
          const rows = Array.isArray(data?.results) ? data.results : [];

          this.xirrAssetClassRows = rows
            .map((row: any) => ({
              asset_category: String(row?.asset_category ?? '').trim(),
              asset_class: String(row?.asset_class ?? '').trim(),
              xirr: Number(row?.xirr),
              current_value: Number(row?.current_value ?? 0),
            }))
            .filter(
              (row) =>
                !!row.asset_category &&
                !!row.asset_class &&
                Number.isFinite(row.xirr),
            );

          this.ensureValidXirrCategoryIndex();
        },
        error: (error) => {
          console.error('XIRR BY ASSET CLASS API ERROR:', error);
          this.xirrAssetClassRows = [];
          this.ensureValidXirrCategoryIndex();
        },
      });
  }

  override startStandardAllocationEdit(): void {
    this.standardAllocationDraft = {};

    for (const group of this.investmentSummaryGroups) {
      this.standardAllocationDraft[group.asset_category] = this.getStandardAllocation(group.asset_category);
    }

    this.standardAllocationEditing = true;
    this.standardAllocationError = '';
  }

  override cancelStandardAllocationEdit(): void {
    this.standardAllocationDraft = { ...this.standardAllocations };
    this.standardAllocationEditing = false;
    this.standardAllocationError = '';
  }

  override updateStandardAllocation(category: string, rawValue: string): void {
    const parsed = Number(rawValue);
    this.standardAllocationDraft[category] = Number.isFinite(parsed)
      ? Math.max(0, Math.min(100, parsed))
      : 0;
  }

  override getStandardAllocation(category: string): number {
    const value = Number(
      this.standardAllocationEditing
        ? this.standardAllocationDraft[category]
        : this.standardAllocations[category],
    );

    return Number.isFinite(value) ? value : 0;
  }

  override getStandardAllocationDraftTotal(): number {
    return Math.round(
      this.investmentSummaryGroups.reduce(
        (total, group) => total + this.getStandardAllocation(group.asset_category),
        0,
      ) * 100,
    ) / 100;
  }

  override getStandardAllocationTotalClass(): string {
    const total = this.getStandardAllocationDraftTotal();

    if (total === 100) {
      return 'is-valid';
    }

    return 'is-invalid';
  }

  override saveStandardAllocations(): void {
    const allocations: Record<string, number> = {};

    for (const group of this.investmentSummaryGroups) {
      allocations[group.asset_category] = this.getStandardAllocation(group.asset_category);
    }

    const total = Math.round(
      Object.values(allocations).reduce((sum, value) => sum + value, 0) * 100,
    ) / 100;

    if (total !== 100) {
      this.standardAllocationError = `Standard Allocation must total exactly 100%. Current total is ${total}%.`;
      return;
    }

    this.standardAllocationSaving = true;
    this.standardAllocationError = '';

    this.dashboardWealthApi
      .saveStandardAllocations(allocations, this.selectedFamily || undefined)
      .subscribe({
        next: (data) => {
          this.standardAllocations = this.normalizeAllocationMap(data?.allocations);
          this.standardAllocationDraft = { ...this.standardAllocations };
          this.standardAllocationEditing = false;
          this.standardAllocationSaving = false;
        },
        error: (error) => {
          console.error('STANDARD ALLOCATION SAVE ERROR:', error);
          this.standardAllocationSaving = false;
          this.standardAllocationError =
            error?.error?.detail || 'Unable to save Standard Allocation.';
        },
      });
  }

  override getAllocationComment(group: { asset_category: string; percentage_of_total: number }): string {
    const actual = Number(group.percentage_of_total);
    const standard = this.getStandardAllocation(group.asset_category);
    const difference = actual - standard;

    if (Math.abs(difference) <= 2) {
      return 'Neutral';
    }

    return difference > 2
      ? 'Invest Less in Other Asset Category'
      : 'Invest More in this Category';
  }

  override getAllocationCommentClass(group: { asset_category: string; percentage_of_total: number }): string {
    const comment = this.getAllocationComment(group);

    if (comment === 'Neutral') {
      return 'is-neutral';
    }

    return comment === 'Invest More in this Category' ? 'is-underweight' : 'is-overweight';
  }

  private normalizeAllocationMap(value: unknown): Record<string, number> {
    if (!value || typeof value !== 'object') {
      return {};
    }

    const result: Record<string, number> = {};

    for (const [category, rawValue] of Object.entries(value as Record<string, unknown>)) {
      const numberValue = Number(rawValue);

      if (Number.isFinite(numberValue)) {
        result[category] = numberValue;
      }
    }

    return result;
  }

  /** XIRR Performance is grouped by Asset Class, not Underlying. */
  override get xirrPerformanceCategories(): string[] {
    return Array.from(
      new Set(this.xirrAssetClassRows.map((row) => row.asset_category)),
    ).filter((category) => this.xirrAssetClassRows.some((row) => row.asset_category === category));
  }

  /** XIRR rows for the selected Asset Category; one row per Asset Class. */
  override get selectedXirrRows(): Array<{
    underlying: string;
    xirr: number;
    assetClass: string;
  }> {
    const category = this.selectedXirrAssetCategory;

    if (!category) {
      return [];
    }

    return this.xirrAssetClassRows
      .filter((row) => row.asset_category === category)
      .sort((a, b) => b.xirr - a.xirr)
      .map((row) => ({
        underlying: row.asset_class,
        xirr: row.xirr,
        assetClass: row.asset_class,
      }));
  }
}
