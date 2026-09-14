import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';

import { DashboardComponent as BaseDashboardComponent } from './dashboard.component.base';

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
   * Keep the existing XIRR Performance selector based on the
   * Investment Summary API categories. The Investment Summary table
   * itself is intentionally driven by the Portfolio Tree hierarchy.
   */
  override get xirrPerformanceCategories(): string[] {
    const categories = this.investmentSummary?.results?.map(
      (row: any) => row.asset_category,
    ) ?? [];

    return Array.from(new Set(categories.filter(Boolean)));
  }
}
