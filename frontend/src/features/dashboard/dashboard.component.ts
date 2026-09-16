import { Component, effect, inject } from '@angular/core';
import { CommonModule } from '@angular/common';

import { DashboardComponent as BaseDashboardComponent } from './dashboard.component.base';
import { ThemeService } from '../../core/services/theme.service';

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
   *
   * This intentionally uses investmentSummaryGroups rather than the
   * backend Investment Summary API category names, because the visible
   * Dashboard table is the source of truth for the displayed hierarchy.
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
   * so retry rendering until both sources are ready. This keeps the
   * chart fully dynamic without hardcoding any Asset Categories.
   */
  override loadDashboard(): void {
    const request = ++this.allocationRenderRequest;

    super.loadDashboard();

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

  /**
   * XIRR Performance uses the same Investment Summary groups shown
   * immediately above it on the Dashboard.
   */
  override get xirrPerformanceCategories(): string[] {
    return this.investmentSummaryGroups
      .filter((group) =>
        group.asset_classes.some((subClass) => this.hasXirrForSubClass(subClass.asset_class)),
      )
      .map((group) => group.asset_category);
  }

  /**
   * XIRR rows for the selected Investment Summary Asset Category.
   * The displayed investment name is Asset Name, not Underlying.
   */
  override get selectedXirrRows(): Array<{
    underlying: string;
    xirr: number;
    assetClass: string;
  }> {
    const category = this.selectedXirrAssetCategory;

    if (!category || !this.portfolioTree) {
      return [];
    }

    const group = this.investmentSummaryGroups.find(
      (item) => item.asset_category === category,
    );

    if (!group) {
      return [];
    }

    const subClasses = new Set(
      group.asset_classes.map((item) => item.asset_class.trim()),
    );

    const rows: Array<{
      underlying: string;
      xirr: number;
      assetClass: string;
    }> = [];

    for (const family of this.portfolioTree.families ?? []) {
      if (this.selectedFamily && family.family_name !== this.selectedFamily) {
        continue;
      }

      for (const portfolio of family.portfolios ?? []) {
        for (const assetClass of portfolio.asset_classes ?? []) {
          for (const subClass of assetClass.sub_classes ?? []) {
            if (!subClasses.has((subClass.sub_class || '').trim())) {
              continue;
            }

            for (const asset of subClass.assets ?? []) {
              const xirr = Number(asset.xirr);

              if (!Number.isFinite(xirr)) {
                continue;
              }

              rows.push({
                underlying: asset.asset_name?.trim() || 'Unnamed Asset',
                xirr,
                assetClass: subClass.sub_class,
              });
            }
          }
        }
      }
    }

    return rows.sort((a, b) => b.xirr - a.xirr);
  }

  private hasXirrForSubClass(subClassName: string): boolean {
    const target = subClassName.trim();

    if (!target || !this.portfolioTree) {
      return false;
    }

    for (const family of this.portfolioTree.families ?? []) {
      if (this.selectedFamily && family.family_name !== this.selectedFamily) {
        continue;
      }

      for (const portfolio of family.portfolios ?? []) {
        for (const assetClass of portfolio.asset_classes ?? []) {
          for (const subClass of assetClass.sub_classes ?? []) {
            if ((subClass.sub_class || '').trim() !== target) {
              continue;
            }

            if (
              (subClass.assets ?? []).some((asset) => Number.isFinite(Number(asset.xirr)))
            ) {
              return true;
            }
          }
        }
      }
    }

    return false;
  }
}
