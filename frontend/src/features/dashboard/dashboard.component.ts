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

  override standardAllocations: Record<string, number> = {};
  override standardAllocationDraft: Record<string, number> = {};
  override standardAllocationEditing = false;
  override standardAllocationSaving = false;
  override standardAllocationError = '';

  private readonly allocationThemeEffect = effect(() => {
    this.allocationThemeService.mode();

    if (!this.loading && this.investmentSummary && this.portfolioTree) {
      setTimeout(() => {
        (this as any).renderAllocationChart();
      });
    }
  });

  override loadDashboard(): void {
    const request = ++this.allocationRenderRequest;

    super.loadDashboard();

    const renderWhenReady = (attempt: number): void => {
      if (request !== this.allocationRenderRequest) {
        return;
      }

      if (!this.loading && this.investmentSummary && this.portfolioTree) {
        this.refreshStandardAllocationTotalValue();
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

  private getInvestmentSummaryTotal(): number {
    const total = Number(
      this.summary?.total_current_value ??
      this.investmentSummary?.total_current_value ??
      0,
    );

    return Number.isFinite(total) && total > 0 ? total : 0;
  }

  private refreshStandardAllocationTotalValue(): void {
    const total = this.getInvestmentSummaryTotal();

    if (total > 0) {
      this.standardAllocationTotalValue = total;
      this.syncStandardAllocationAmounts();
    }
  }

  private syncStandardAllocationAmounts(): void {
    if (this.standardAllocationTotalValue <= 0) {
      return;
    }

    for (const category of Object.keys(this.standardAllocations)) {
      const percent = Number(this.standardAllocations[category]) || 0;

      this.standardAllocationAmounts[category] =
        Math.round((this.standardAllocationTotalValue * percent) * 100) / 10000;
    }
  }

  override startStandardAllocationEdit(): void {
    this.standardAllocationDraft = {};
    this.standardAllocationAmountDraft = {};

    for (const group of this.investmentSummaryGroups) {
      const category = group.asset_category;
      this.standardAllocationDraft[category] = this.getStandardAllocation(category);
      this.standardAllocationAmountDraft[category] = this.getStandardAllocationAmount(category);
    }

    this.standardAllocationEditing = true;
    this.standardAllocationError = '';
  }

  override cancelStandardAllocationEdit(): void {
    this.standardAllocationDraft = { ...this.standardAllocations };
    this.standardAllocationAmountDraft = { ...this.standardAllocationAmounts };
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
    const allocations: Record<string, { percent: number; amount: number }> = {};

    const baseTotal = this.getInvestmentSummaryTotal();

    for (const group of this.investmentSummaryGroups) {
      const category = group.asset_category;
      const percent = this.getStandardAllocation(category);
      allocations[category] = {
        percent,
        // Amount remains an API compatibility field; the UI is percentage-only.
        amount: baseTotal > 0 ? Math.round((baseTotal * percent) * 100) / 10000 : 0,
      };
    }

    const total = Math.round(
      Object.values(allocations).reduce((sum, value) => sum + value.percent, 0) * 100,
    ) / 100;

    if (total !== 100) {
      this.standardAllocationError = `Standard Allocation must total exactly 100%. Current total is ${total}%.`;
      return;
    }

    this.standardAllocationSaving = true;
    this.standardAllocationError = '';

    this.dashboardWealthApi
      .saveStandardAllocations(allocations, this.selectedFamilyMember || undefined)
      .subscribe({
        next: (data) => {
          const allocationResponse = data?.allocations ?? {};
          this.standardAllocations = this.normalizeAllocationMap(
            Object.fromEntries(
              Object.entries(allocationResponse).map(([category, value]) => [
                category,
                typeof value === 'object' && value !== null ? (value as any).percent : value,
              ]),
            ),
          );
          this.standardAllocationAmounts = this.normalizeAllocationMap(
            Object.fromEntries(
              Object.entries(allocationResponse).map(([category, value]) => [
                category,
                typeof value === 'object' && value !== null ? (value as any).amount : 0,
              ]),
            ),
          );
          this.standardAllocationTotalValue = this.getInvestmentSummaryTotal();
          this.syncStandardAllocationAmounts();
          this.standardAllocationDraft = { ...this.standardAllocations };
          this.standardAllocationAmountDraft = { ...this.standardAllocationAmounts };
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

    // Within +/- 2 percentage points of the Standard Allocation is Neutral.
    if (Math.abs(difference) <= 2) {
      return 'Neutral';
    }

    return difference < 0 ? 'Underweight' : 'Overweight';
  }

  override getAllocationCommentClass(group: { asset_category: string; percentage_of_total: number }): string {
    const comment = this.getAllocationComment(group);

    if (comment === 'Neutral') {
      return 'is-neutral';
    }

    return comment === 'Underweight' ? 'is-underweight' : 'is-overweight';
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

  /**
   * XIRR Performance categories are supplied by the backend-calculated
   * performance rows and the backend investment-summary hierarchy.
   */
  override get xirrPerformanceCategories(): string[] {
    return this.investmentSummaryGroups
      .filter((group) =>
        group.asset_classes.some((subClass) => this.hasXirrForSubClass(subClass.asset_class)),
      )
      .map((group) => group.asset_category);
  }

  override get selectedXirrRows(): Array<{
    underlying: string;
    xirr: number;
    assetClass: string;
  }> {
    const category = this.selectedXirrAssetCategory;

    if (!category) {
      return [];
    }

    const rowsByKey = new Map<string, {
      underlying: string;
      xirr: number;
      assetClass: string;
    }>();

    for (const row of this.dashboardPerformance) {
      if (row.asset_category !== category) {
        continue;
      }

      const xirr = Number(row.xirr_percentage);
      if (!Number.isFinite(xirr)) {
        continue;
      }

      const assetName = (row.asset_name || 'Unnamed Asset').trim() || 'Unnamed Asset';
      const assetClass = (row.asset_class || 'Unassigned').trim() || 'Unassigned';
      const key = `${assetClass}::${assetName}`;

      if (!rowsByKey.has(key)) {
        rowsByKey.set(key, {
          underlying: assetName,
          xirr,
          assetClass,
        });
      }
    }

    return Array.from(rowsByKey.values()).sort((a, b) => b.xirr - a.xirr);
  }

  private hasXirrForSubClass(subClassName: string): boolean {
    const target = subClassName.trim();

    if (!target) {
      return false;
    }

    return this.dashboardPerformance.some(
      (row) =>
        (row.asset_class || '').trim() === target &&
        Number.isFinite(Number(row.xirr_percentage)),
    );
  }

}
