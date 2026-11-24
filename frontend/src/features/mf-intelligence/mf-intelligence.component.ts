import { CommonModule, isPlatformBrowser } from '@angular/common';
import { ChangeDetectorRef, Component, ElementRef, OnDestroy, OnInit, PLATFORM_ID, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Chart, ChartConfiguration, ChartDataset, registerables } from 'chart.js';
import { Subscription } from 'rxjs';

import {
  MfCategorySummary,
  MfDashboard,
  MfFlowItem,
  MfFund,
  MfHoldingRow,
  MfHorizon,
  MfIntelligenceApiService,
  MfNumberMap,
  MfPlan,
  MfSchemeDetail,
  MfSyncRun,
} from '../../core/services/mf-intelligence-api.service';
import { RbacService } from '../../core/services/rbac.service';
import {
  LOWER_IS_BETTER,
  METRIC_LABELS,
  Quartile,
  SCORE_LABELS,
  Summary,
  average,
  clean,
  crore,
  escapeHtml,
  fmt,
  histogram,
  isNum,
  joinNames,
  niceEdges,
  pct,
  quartileOf,
  rankOf,
  signedPct,
  topBy,
  trendLabel,
} from './mf-intelligence.utils';

Chart.register(...registerables);

export type MfTab =
  | 'overview'
  | 'scorecard'
  | 'score'
  | 'performance'
  | 'calendar'
  | 'risk'
  | 'portfolio'
  | 'positioning'
  | 'universe'
  | 'activity';

interface AxisOption {
  key: string;
  label: string;
  get: (fund: MfFund) => number | null;
}

interface UniverseColumn {
  key: string;
  label: string;
  digits: number;
  get: (fund: MfFund) => number | null;
  higherIsBetter?: boolean;
}

const RETURN_PERIODS: Array<{ key: string; label: string }> = [
  { key: '1w', label: '1W' },
  { key: '1m', label: '1M' },
  { key: '3m', label: '3M' },
  { key: '6m', label: '6M' },
  { key: 'ytd', label: 'YTD' },
  { key: '1y', label: '1Y' },
  { key: '2y', label: '2Y' },
  { key: '3y', label: '3Y' },
  { key: '5y', label: '5Y' },
  { key: '10y', label: '10Y' },
];

const PALETTE = ['#FD7740', '#051233', '#CC9F53', '#3B82F6', '#16A34A', '#DC2626', '#7C3AED', '#0891B2', '#D97706', '#BE185D'];
const ORANGE = '#FD7740';
const GOLD = '#CC9F53';
const GREEN = '#16A34A';
const RED = '#DC2626';
const BLUE = '#3B82F6';
const STYLE_COLORS: Record<string, string> = { Cyclical: '#EF4444', Defensive: '#22C55E', Sensitive: '#3B82F6', Other: '#9CA3AF' };
const CAP_KEYS = ['Large Cap', 'Mid Cap', 'Small Cap', 'Unclassified'];
const STYLE_KEYS = ['Cyclical', 'Defensive', 'Sensitive', 'Other'];
const ROLLING_KEYS = ['rolling_return_p10', 'rolling_return_p25', 'rolling_return_p50', 'rolling_return_p75', 'rolling_return_p90'];
const RISK_KEYS = [
  'trailing_cagr', 'amfi_volatility', 'beta', 'alpha', 'correlation', 'sharpe_ratio', 'sortino_ratio',
  'information_ratio', 'upside_capture', 'downside_capture', 'trailing_max_drawdown', 'rolling_return_p50', 'rolling_return_sd', 'trailing_xirr',
];

@Component({
  selector: 'app-mf-intelligence',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './mf-intelligence.component.html',
  styleUrl: './mf-intelligence.component.scss',
})
export class MfIntelligenceComponent implements OnInit, OnDestroy {
  private readonly api = inject(MfIntelligenceApiService);
  private readonly rbac = inject(RbacService);
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly host = inject(ElementRef<HTMLElement>);
  private readonly isBrowser = isPlatformBrowser(inject(PLATFORM_ID));

  private readonly charts = new Map<string, Chart>();
  private dashboardRequest?: Subscription;
  private detailRequest?: Subscription;
  private syncPollTimer: ReturnType<typeof setTimeout> | null = null;
  private chartTimer: ReturnType<typeof setTimeout> | null = null;

  readonly tabs: Array<{ id: MfTab; label: string }> = [
    { id: 'overview', label: 'Overview' },
    { id: 'scorecard', label: 'Fund Scorecard' },
    { id: 'score', label: 'Quant Score' },
    { id: 'performance', label: 'Performance' },
    { id: 'calendar', label: 'Calendar Year' },
    { id: 'risk', label: 'Risk & Rolling' },
    { id: 'portfolio', label: 'Portfolio' },
    { id: 'positioning', label: 'Positioning' },
    { id: 'universe', label: 'Universe Table' },
    { id: 'activity', label: 'Manager Activity' },
  ];
  readonly returnPeriods = RETURN_PERIODS;
  readonly capKeys = CAP_KEYS;
  readonly styleKeys = STYLE_KEYS;
  readonly riskKeys = RISK_KEYS;
  readonly scoreLabels = SCORE_LABELS;
  readonly metricLabels = METRIC_LABELS;
  readonly growthWindows = ['1Y', '3Y', '5Y', 'Max'];
  readonly fmt = fmt;
  readonly pct = pct;
  readonly signedPct = signedPct;
  readonly crore = crore;

  tab: MfTab = 'overview';
  groups: Array<{ asset_class: string; categories: MfCategorySummary[] }> = [];
  categoriesLoading = true;
  categoriesError = '';
  categorySlug = '';
  plan: MfPlan = 'Regular';
  horizon: MfHorizon = '3Y';
  benchmarkSlug = '';

  dashboard: MfDashboard | null = null;
  loading = false;
  error = '';
  funds: MfFund[] = [];
  activeCode = '';
  fund: MfFund | null = null;
  detail: MfSchemeDetail | null = null;
  detailLoading = false;
  growthWindow = '5Y';

  performanceRows: MfFund[] = [];
  calendarRows: MfFund[] = [];
  scoreRows: MfFund[] = [];
  heatRows: MfFund[] = [];
  summaries: Partial<Record<MfTab, Summary>> = {};
  insights: Record<string, string> = {};

  universeColumns: UniverseColumn[] = [];
  universeSortKey = 'score';
  universeSortDesc = true;
  universeRows: MfFund[] = [];

  axisOptions: AxisOption[] = [];
  xAxis = 'risk';
  yAxis = 'cagr';
  sizeAxis = 'aum';
  colorAxis = 'score';

  lastSync: MfSyncRun | null = null;
  syncRunning = false;
  syncPanelOpen = false;
  syncScope: 'category' | 'all' = 'category';
  syncPlan: 'Regular' | 'Direct' | 'Both' = 'Regular';
  syncForce = false;
  syncMessage = '';

  ngOnInit(): void {
    this.loadCategories(true);
    this.refreshSyncStatus(false);
  }

  ngOnDestroy(): void {
    this.dashboardRequest?.unsubscribe();
    this.detailRequest?.unsubscribe();
    if (this.syncPollTimer) clearTimeout(this.syncPollTimer);
    if (this.chartTimer) clearTimeout(this.chartTimer);
    this.destroyCharts();
  }

  // ── Permissions / labels ────────────────────────────────────────────────
  get canSync(): boolean {
    const role = this.rbac.role();
    return role !== null && role !== 'VIEWER';
  }

  get selectedCategory(): MfCategorySummary | null {
    for (const group of this.groups) {
      const match = group.categories.find((category) => category.slug === this.categorySlug);
      if (match) return match;
    }
    return null;
  }

  get isEquity(): boolean {
    return !!this.dashboard?.flags.has_equity;
  }

  get hasFixedIncome(): boolean {
    return !!this.dashboard?.flags.has_fixed_income;
  }

  get benchmarkName(): string {
    return this.dashboard?.benchmark?.name ?? 'Benchmark';
  }

  label(fund: MfFund | null | undefined, max = 28): string {
    const name = fund?.short_name || fund?.name || '';
    return name.length > max ? `${name.slice(0, max - 1)}…` : name;
  }

  // ── Data loading ─────────────────────────────────────────────────────────
  loadCategories(selectDefault = false): void {
    this.categoriesLoading = true;
    this.api.getCategories().subscribe({
      next: (response) => {
        this.groups = response.groups;
        this.lastSync = response.last_sync;
        this.categoriesLoading = false;
        this.categoriesError = '';
        if (selectDefault || !this.selectedCategory) {
          const all = response.groups.flatMap((group) => group.categories);
          const preferred = all.find((category) => category.slug === this.categorySlug && category.has_data)
            ?? all.find((category) => category.has_data)
            ?? all[0];
          if (preferred) this.selectCategory(preferred.slug);
        }
        this.cdr.markForCheck();
      },
      error: () => {
        this.categoriesLoading = false;
        this.categoriesError = 'Could not load mutual fund categories.';
        this.cdr.markForCheck();
      },
    });
  }

  selectCategory(slug: string): void {
    if (this.categorySlug !== slug) {
      this.benchmarkSlug = '';
      this.activeCode = '';
    }
    this.categorySlug = slug;
    this.loadDashboard();
  }

  onPlanChange(plan: MfPlan): void {
    this.plan = plan;
    this.activeCode = '';
    this.loadDashboard();
  }

  onHorizonChange(): void {
    this.loadDashboard();
  }

  onBenchmarkChange(): void {
    this.loadDashboard();
  }

  loadDashboard(): void {
    if (!this.categorySlug) return;
    this.dashboardRequest?.unsubscribe();
    this.loading = true;
    this.error = '';
    this.dashboardRequest = this.api
      .getDashboard(this.categorySlug, { plan: this.plan, horizon: this.horizon, benchmark: this.benchmarkSlug || null })
      .subscribe({
        next: (dashboard) => this.applyDashboard(dashboard),
        error: () => {
          this.loading = false;
          this.dashboard = null;
          this.error = 'Could not load the category analytics.';
          this.cdr.markForCheck();
        },
      });
  }

  applyDashboard(dashboard: MfDashboard): void {
    this.loading = false;
    this.dashboard = dashboard;
    this.benchmarkSlug = dashboard.benchmark?.slug ?? '';
    this.funds = [...dashboard.funds].sort((a, b) => a.short_name.localeCompare(b.short_name));
    const keep = this.funds.find((fund) => fund.code === this.activeCode);
    const best = topBy(this.funds, (fund) => fund.score.total, 1)[0];
    this.activeCode = (keep ?? best ?? this.funds[0])?.code ?? '';
    this.buildAxisOptions();
    this.buildUniverseColumns();
    this.rebuild();
    this.loadDetail();
  }

  onFundChange(): void {
    this.rebuild();
    this.loadDetail();
  }

  selectFund(code: string): void {
    if (code === this.activeCode) return;
    this.activeCode = code;
    this.onFundChange();
  }

  loadDetail(): void {
    this.detailRequest?.unsubscribe();
    this.detail = null;
    if (!this.activeCode) {
      this.scheduleCharts();
      return;
    }
    this.detailLoading = true;
    this.detailRequest = this.api.getScheme(this.activeCode, this.benchmarkSlug || null).subscribe({
      next: (detail) => {
        this.detail = detail;
        this.detailLoading = false;
        this.insights['detail'] = this.detailInsight();
        this.scheduleCharts();
      },
      error: () => {
        this.detailLoading = false;
        this.scheduleCharts();
      },
    });
    this.scheduleCharts();
  }

  setTab(tab: MfTab): void {
    this.tab = tab;
    this.scheduleCharts();
  }

  setGrowthWindow(window: string): void {
    this.growthWindow = window;
    this.scheduleCharts();
  }

  // ── Accessors used by the template ─────────────────────────────────────
  fundReturn(fund: MfFund | null | undefined, key: string): number | null {
    return fund?.returns?.[key] ?? null;
  }

  calendarReturn(fund: MfFund | null | undefined, year: string): number | null {
    return fund?.calendar?.[year] ?? null;
  }

  fundMetric(fund: MfFund | null | undefined, key: string, horizon: MfHorizon = this.horizon): number | null {
    return fund?.metrics?.[horizon]?.[key] ?? null;
  }

  calmar(fund: MfFund | null | undefined, horizon: MfHorizon = this.horizon): number | null {
    return fund?.calmar?.[horizon] ?? null;
  }

  categoryReturn(key: string): number | null {
    return this.dashboard?.category_average.returns[key] ?? null;
  }

  categoryMetric(key: string, horizon: MfHorizon = this.horizon): number | null {
    return this.dashboard?.category_average.metrics[horizon]?.[key] ?? null;
  }

  categoryMedianMetric(key: string, horizon: MfHorizon = this.horizon): number | null {
    const value = this.dashboard?.category_median_all_plans?.[horizon]?.[key];
    return isNum(value) ? value : null;
  }

  categoryCalendarReturn(year: string): number | null {
    return this.dashboard?.category_average.calendar[year] ?? null;
  }

  benchmarkReturn(key: string): number | null {
    return this.dashboard?.benchmark?.returns[key] ?? null;
  }

  benchmarkCalendarReturn(year: string): number | null {
    return this.dashboard?.benchmark?.calendar[year] ?? null;
  }

  fundHolding(fund: MfFund | null | undefined, key: string): number | null {
    const value = (fund?.holdings as Record<string, unknown> | null | undefined)?.[key];
    return isNum(value) ? value : null;
  }

  categoryHolding(key: string): number | null {
    const value = this.dashboard?.category_average.holdings?.[key];
    return isNum(value) ? value : null;
  }

  mapValue(map: MfNumberMap | Record<string, number> | null | undefined, key: string): number | null {
    const value = map?.[key];
    return isNum(value) ? value : null;
  }

  values(getter: (fund: MfFund) => number | null): Array<number | null> {
    return this.funds.map(getter);
  }

  rank(fund: MfFund | null, getter: (fund: MfFund) => number | null, higherIsBetter = true): number | null {
    return fund ? rankOf(this.values(getter), getter(fund), higherIsBetter) : null;
  }

  quartile(fund: MfFund | null, getter: (fund: MfFund) => number | null, higherIsBetter = true): Quartile | null {
    return fund ? quartileOf(this.values(getter), getter(fund), higherIsBetter) : null;
  }

  // Ranks/quartiles are cached per metric for the loaded peer group so the
  // large tables do not re-rank the whole category for every cell.
  private rankCache = new Map<string, { count: number; byCode: Map<string, { rank: number | null; quartile: Quartile | null }> }>();

  private ranked(key: string, getter: (fund: MfFund) => number | null, higherIsBetter = true) {
    let entry = this.rankCache.get(key);
    if (!entry) {
      const values = this.funds.map(getter);
      const byCode = new Map<string, { rank: number | null; quartile: Quartile | null }>();
      for (const fund of this.funds) {
        const value = getter(fund);
        byCode.set(fund.code, { rank: rankOf(values, value, higherIsBetter), quartile: quartileOf(values, value, higherIsBetter) });
      }
      entry = { count: clean(values).length, byCode };
      this.rankCache.set(key, entry);
    }
    return entry;
  }

  returnRank(fund: MfFund, key: string): number | null {
    return this.ranked(`r:${key}`, (item) => this.fundReturn(item, key)).byCode.get(fund.code)?.rank ?? null;
  }

  returnQuartile(fund: MfFund | null, key: string): Quartile | null {
    return fund ? this.ranked(`r:${key}`, (item) => this.fundReturn(item, key)).byCode.get(fund.code)?.quartile ?? null : null;
  }

  countWith(key: string): number {
    return this.ranked(`r:${key}`, (item) => this.fundReturn(item, key)).count;
  }

  cyQuartile(fund: MfFund, year: string): Quartile | null {
    return this.ranked(`cy:${year}`, (item) => this.calendarReturn(item, year)).byCode.get(fund.code)?.quartile ?? null;
  }

  metricRank(fund: MfFund, key: string): number | null {
    const higherIsBetter = !['amfi_volatility', 'downside_capture', 'rolling_return_sd'].includes(key);
    return this.ranked(`m:${this.horizon}:${key}`, (item) => this.fundMetric(item, key), higherIsBetter).byCode.get(fund.code)?.rank ?? null;
  }

  heatTrend(fund: MfFund): { label: string; tone: string } {
    return trendLabel(this.returnQuartile(fund, '3m'), this.returnQuartile(fund, '6m'), this.returnQuartile(fund, '1y'));
  }

  positiveAlphaYears(fund: MfFund): { positive: number; total: number } {
    const years = this.dashboard?.calendar_years ?? [];
    let positive = 0;
    let total = 0;
    for (const year of years) {
      const value = this.calendarReturn(fund, year);
      const benchmark = this.benchmarkCalendarReturn(year);
      if (isNum(value) && isNum(benchmark)) {
        total++;
        if (value > benchmark) positive++;
      }
    }
    return { positive, total };
  }

  diff(value: number | null | undefined, reference: number | null | undefined): number | null {
    return isNum(value) && isNum(reference) ? value - reference : null;
  }

  tone(value: number | null | undefined, reference: number | null | undefined = 0, higherIsBetter = true): '' | 'good' | 'bad' {
    if (!isNum(value) || !isNum(reference) || value === reference) return '';
    return (value > reference) === higherIsBetter ? 'good' : 'bad';
  }

  metricTone(key: string, value: number | null, reference: number | null): string {
    return this.tone(value, reference, !LOWER_IS_BETTER.has(key));
  }

  topSectors(fund: MfFund | null, count = 12): Array<{ name: string; value: number; cat: number; style: string }> {
    const sectors = fund?.holdings?.sectors ?? {};
    const catSectors = this.dashboard?.category_average.holdings?.sectors ?? {};
    return Object.entries(sectors)
      .slice(0, count)
      .map(([name, value]) => ({ name, value, cat: catSectors[name] ?? 0, style: this.sectorStyle(name) }));
  }

  sectorStyle(sector: string): string {
    return this.dashboard?.sector_styles?.[sector] ?? 'Other';
  }

  styleColor(style: string): string {
    return STYLE_COLORS[style] ?? STYLE_COLORS['Other'];
  }

  barWidth(value: number | null | undefined, max: number): number {
    return isNum(value) && max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  }

  holdingRows(limit = 10): MfHoldingRow[] {
    return (this.detail?.holdings.rows ?? []).filter((row) => row.type !== 'cash').slice(0, limit);
  }

  fundActivity(status: 'NEW' | 'INCREASED' | 'DECREASED'): MfHoldingRow[] {
    const rows = (this.detail?.holdings.rows ?? []).filter((row) => row.status === status && row.type !== 'cash');
    if (status === 'NEW') return rows.slice(0, 12);
    const sorted = [...rows].sort((a, b) =>
      status === 'INCREASED' ? (b.change_pct ?? 0) - (a.change_pct ?? 0) : (a.change_pct ?? 0) - (b.change_pct ?? 0),
    );
    return sorted.slice(0, 10);
  }

  flowFunds(item: MfFlowItem, kind: 'new' | 'exited'): string {
    const names = item[kind].map((entry) => entry.fund);
    return names.length > 4 ? `${names.slice(0, 4).join(', ')} +${names.length - 4} more` : names.join(', ');
  }

  maturityBuckets(): Array<{ label: string; value: number }> {
    const buckets = this.fund?.holdings?.fixed_income?.maturity_buckets ?? {};
    return ['< 1Y', '1-3Y', '3-5Y', '5-10Y', '10Y+'].map((label) => ({ label, value: buckets[label] ?? 0 }));
  }

  // ── Universe table ──────────────────────────────────────────────────────
  private buildUniverseColumns(): void {
    const h = this.horizon;
    const columns: UniverseColumn[] = [
      { key: 'score', label: 'Quant', digits: 1, get: (f) => f.score.total },
      ...RETURN_PERIODS.map((period) => ({ key: `r_${period.key}`, label: period.label, digits: 2, get: (f: MfFund) => this.fundReturn(f, period.key) })),
      ...(this.dashboard?.calendar_years ?? []).slice().reverse().map((year) => ({
        key: `cy_${year}`, label: `CY${year}`, digits: 1, get: (f: MfFund) => this.calendarReturn(f, year),
      })),
      { key: 'vol', label: `Vol ${h}`, digits: 2, get: (f) => this.fundMetric(f, 'amfi_volatility'), higherIsBetter: false },
      { key: 'sharpe', label: `Sharpe ${h}`, digits: 2, get: (f) => this.fundMetric(f, 'sharpe_ratio') },
      { key: 'sortino', label: `Sortino ${h}`, digits: 2, get: (f) => this.fundMetric(f, 'sortino_ratio') },
      { key: 'beta', label: 'Beta', digits: 2, get: (f) => this.fundMetric(f, 'beta'), higherIsBetter: false },
      { key: 'alpha', label: 'Alpha', digits: 2, get: (f) => this.fundMetric(f, 'alpha') },
      { key: 'mdd', label: `Max DD ${h}`, digits: 1, get: (f) => this.fundMetric(f, 'trailing_max_drawdown') },
      { key: 'calmar', label: 'Calmar', digits: 2, get: (f) => this.calmar(f) },
      { key: 'up', label: 'Up cap.', digits: 0, get: (f) => this.fundMetric(f, 'upside_capture') },
      { key: 'down', label: 'Down cap.', digits: 0, get: (f) => this.fundMetric(f, 'downside_capture'), higherIsBetter: false },
      { key: 'aum', label: 'AAUM ₹Cr', digits: 0, get: (f) => f.aum },
      { key: 'ter', label: 'TER', digits: 2, get: (f) => f.ter, higherIsBetter: false },
      { key: 'age', label: 'Age (y)', digits: 1, get: (f) => (isNum(f.age_months) ? f.age_months / 12 : null) },
    ];
    if (this.dashboard?.flags.has_holdings) {
      columns.push(
        { key: 'holdings', label: 'Holdings', digits: 0, get: (f) => this.fundHolding(f, 'total') },
        { key: 'top10', label: 'Top-10 %', digits: 1, get: (f) => this.fundHolding(f, 'top10_pct') },
        { key: 'cash', label: 'Cash %', digits: 1, get: (f) => this.fundHolding(f, 'cash_pct') },
        { key: 'equity', label: 'Equity %', digits: 1, get: (f) => this.fundHolding(f, 'equity_pct') },
      );
    }
    if (this.dashboard?.flags.has_equity) {
      columns.push(
        { key: 'large', label: 'Large %', digits: 1, get: (f) => this.mapValue(f.holdings?.cap, 'Large Cap') },
        { key: 'mid', label: 'Mid %', digits: 1, get: (f) => this.mapValue(f.holdings?.cap, 'Mid Cap') },
        { key: 'small', label: 'Small %', digits: 1, get: (f) => this.mapValue(f.holdings?.cap, 'Small Cap') },
        { key: 'unique', label: 'Unique', digits: 0, get: (f) => f.unique.count },
      );
    }
    if (this.dashboard?.flags.has_fixed_income) {
      columns.push(
        { key: 'sov', label: 'Sovereign %', digits: 1, get: (f) => f.holdings?.fixed_income?.sovereign_pct ?? null },
        { key: 'mat', label: 'Avg mat. (y)', digits: 2, get: (f) => f.holdings?.fixed_income?.avg_maturity_years ?? null },
      );
    }
    this.universeColumns = columns;
    if (!columns.some((column) => column.key === this.universeSortKey)) this.universeSortKey = 'score';
  }

  sortUniverse(key: string): void {
    if (this.universeSortKey === key) {
      this.universeSortDesc = !this.universeSortDesc;
    } else {
      this.universeSortKey = key;
      this.universeSortDesc = true;
    }
    this.sortUniverseRows();
  }

  private sortUniverseRows(): void {
    this.universeAverages = Object.fromEntries(this.universeColumns.map((item) => [item.key, average(this.funds.map(item.get))]));
    const column = this.universeColumns.find((item) => item.key === this.universeSortKey);
    if (!column) {
      this.universeRows = [...this.funds];
      return;
    }
    const direction = this.universeSortDesc ? -1 : 1;
    this.universeRows = [...this.funds].sort((a, b) => {
      const va = column.get(a);
      const vb = column.get(b);
      if (!isNum(va) && !isNum(vb)) return a.short_name.localeCompare(b.short_name);
      if (!isNum(va)) return 1;
      if (!isNum(vb)) return -1;
      return (va - vb) * direction;
    });
  }

  private universeAverages: Record<string, number | null> = {};

  universeCatValue(column: UniverseColumn): number | null {
    return this.universeAverages[column.key] ?? null;
  }

  cellTone(column: UniverseColumn, fund: MfFund): string {
    if (column.key === 'aum' || column.key === 'age' || column.key === 'holdings') return '';
    return this.tone(column.get(fund), this.universeCatValue(column), column.higherIsBetter !== false) === 'good' ? 'highlight' : '';
  }

  // ── Positioning axes ────────────────────────────────────────────────────
  private buildAxisOptions(): void {
    const options: AxisOption[] = [
      { key: 'cagr', label: `${this.horizon} CAGR %`, get: (f) => this.fundMetric(f, 'trailing_cagr') },
      { key: 'risk', label: `${this.horizon} Volatility %`, get: (f) => this.fundMetric(f, 'amfi_volatility') },
      { key: 'sharpe', label: `${this.horizon} Sharpe`, get: (f) => this.fundMetric(f, 'sharpe_ratio') },
      { key: 'sortino', label: `${this.horizon} Sortino`, get: (f) => this.fundMetric(f, 'sortino_ratio') },
      { key: 'beta', label: `${this.horizon} Beta`, get: (f) => this.fundMetric(f, 'beta') },
      { key: 'alpha', label: `${this.horizon} Alpha`, get: (f) => this.fundMetric(f, 'alpha') },
      { key: 'mdd', label: `${this.horizon} Max drawdown %`, get: (f) => this.fundMetric(f, 'trailing_max_drawdown') },
      { key: 'calmar', label: `${this.horizon} Calmar`, get: (f) => this.calmar(f) },
      { key: 'up', label: 'Upside capture %', get: (f) => this.fundMetric(f, 'upside_capture') },
      { key: 'down', label: 'Downside capture %', get: (f) => this.fundMetric(f, 'downside_capture') },
      { key: 'r3m', label: '3M return %', get: (f) => this.fundReturn(f, '3m') },
      { key: 'r6m', label: '6M return %', get: (f) => this.fundReturn(f, '6m') },
      { key: 'r1y', label: '1Y return %', get: (f) => this.fundReturn(f, '1y') },
      { key: 'r3y', label: '3Y CAGR (NAV) %', get: (f) => this.fundReturn(f, '3y') },
      { key: 'score', label: 'Quant Score', get: (f) => f.score.total },
      { key: 'aum', label: 'AAUM ₹ Cr', get: (f) => f.aum },
      { key: 'ter', label: 'TER %', get: (f) => f.ter },
      { key: 'age', label: 'Age (years)', get: (f) => (isNum(f.age_months) ? f.age_months / 12 : null) },
      { key: 'cash', label: 'Cash %', get: (f) => this.fundHolding(f, 'cash_pct') },
      { key: 'top10', label: 'Top-10 concentration %', get: (f) => this.fundHolding(f, 'top10_pct') },
      { key: 'holdings', label: 'Number of holdings', get: (f) => this.fundHolding(f, 'total') },
    ];
    if (this.dashboard?.flags.has_equity) {
      options.push(
        { key: 'large', label: 'Large cap % (of equity)', get: (f) => this.mapValue(f.holdings?.cap, 'Large Cap') },
        { key: 'mid', label: 'Mid cap % (of equity)', get: (f) => this.mapValue(f.holdings?.cap, 'Mid Cap') },
        { key: 'small', label: 'Small cap % (of equity)', get: (f) => this.mapValue(f.holdings?.cap, 'Small Cap') },
        { key: 'cyclical', label: 'Cyclical % (of equity)', get: (f) => this.mapValue(f.holdings?.style, 'Cyclical') },
        { key: 'defensive', label: 'Defensive % (of equity)', get: (f) => this.mapValue(f.holdings?.style, 'Defensive') },
        { key: 'unique', label: 'Unique ideas (count)', get: (f) => f.unique.count },
      );
    }
    if (this.dashboard?.flags.has_fixed_income) {
      options.push(
        { key: 'sov', label: 'Sovereign %', get: (f) => f.holdings?.fixed_income?.sovereign_pct ?? null },
        { key: 'maturity', label: 'Avg maturity (years)', get: (f) => f.holdings?.fixed_income?.avg_maturity_years ?? null },
      );
    }
    this.axisOptions = options;
    for (const [field, fallback] of [['xAxis', 'risk'], ['yAxis', 'cagr'], ['sizeAxis', 'aum'], ['colorAxis', 'score']] as const) {
      if (!options.some((option) => option.key === this[field])) this[field] = fallback;
    }
  }

  axis(key: string): AxisOption | undefined {
    return this.axisOptions.find((option) => option.key === key) ?? this.axisOptions[0];
  }

  onAxisChange(): void {
    this.scheduleCharts();
  }

  // ── Derived tables + plain-language takeaways ──────────────────────────
  private rebuild(): void {
    this.fund = this.funds.find((fund) => fund.code === this.activeCode) ?? null;
    this.rankCache.clear();
    this.performanceRows = [...this.funds].sort((a, b) => this.compareDesc(this.fundReturn(a, '1y'), this.fundReturn(b, '1y'), this.fundReturn(a, '3m'), this.fundReturn(b, '3m')));
    const lastYear = this.dashboard?.calendar_years.slice(-1)[0] ?? '';
    this.calendarRows = [...this.funds].sort((a, b) => this.compareDesc(this.calendarReturn(a, lastYear), this.calendarReturn(b, lastYear), this.fundReturn(a, 'ytd'), this.fundReturn(b, 'ytd')));
    this.scoreRows = [...this.funds].sort((a, b) => this.compareDesc(a.score.total, b.score.total, null, null));
    const order: Record<string, number> = { Q1: 1, Q2: 2, Q3: 3, Q4: 4 };
    this.heatRows = [...this.funds].sort((a, b) => {
      const qa = order[this.returnQuartile(a, '1y') ?? ''] ?? 5;
      const qb = order[this.returnQuartile(b, '1y') ?? ''] ?? 5;
      return qa !== qb ? qa - qb : this.compareDesc(this.fundReturn(a, '1y'), this.fundReturn(b, '1y'), null, null);
    });
    this.sortUniverseRows();
    this.buildSummaries();
    this.scheduleCharts();
  }

  private compareDesc(a: number | null, b: number | null, tieA: number | null, tieB: number | null): number {
    if (isNum(a) && isNum(b) && a !== b) return b - a;
    if (isNum(a) !== isNum(b)) return isNum(a) ? -1 : 1;
    if (isNum(tieA) && isNum(tieB)) return tieB - tieA;
    return 0;
  }

  private names(funds: MfFund[]): string {
    return joinNames(funds.map((fund) => fund.short_name));
  }

  private buildSummaries(): void {
    const d = this.dashboard;
    const f = this.fund;
    if (!d) {
      this.summaries = {};
      return;
    }
    const h = this.horizon;
    const funds = this.funds;
    const withOneYear = funds.filter((fund) => isNum(this.fundReturn(fund, '1y')));
    const positive = withOneYear.filter((fund) => (this.fundReturn(fund, '1y') as number) > 0).length;
    const beatBenchmark = withOneYear.filter((fund) => isNum(this.benchmarkReturn('1y')) && (this.fundReturn(fund, '1y') as number) > (this.benchmarkReturn('1y') as number)).length;
    const topScore = topBy(funds, (fund) => fund.score.total, 3);
    const bestDrawdown = topBy(funds, (fund) => this.fundMetric(fund, 'trailing_max_drawdown'), 2);
    const catHoldings = d.category_average.holdings;
    const summaries: Partial<Record<MfTab, Summary>> = {};

    const parts: string[] = [];
    if (withOneYear.length) {
      parts.push(`<strong>${positive} of ${withOneYear.length}</strong> funds have a positive 1Y return`);
      if (d.benchmark && isNum(this.benchmarkReturn('1y'))) {
        parts.push(`<strong>${beatBenchmark}</strong> beat ${escapeHtml(d.benchmark.name)} (${signedPct(this.benchmarkReturn('1y'))})`);
      }
    }
    if (topScore.length) parts.push(`highest Quant Scores: ${this.names(topScore)}`);
    if (bestDrawdown.length) parts.push(`shallowest ${h} drawdowns: ${this.names(bestDrawdown)}`);
    if (catHoldings && this.isEquity) {
      parts.push(
        `average equity split is <strong>${fmt(catHoldings.cap['Large Cap'], 0)} / ${fmt(catHoldings.cap['Mid Cap'], 0)} / ${fmt(catHoldings.cap['Small Cap'], 0)}</strong> large / mid / small`,
      );
    }
    summaries.overview = {
      html: parts.length ? `${this.capitalise(parts.join('; '))}.` : 'No synced data for this category yet.',
      pills: [
        { label: `${funds.length} funds`, tone: '' },
        { label: `Cat avg 1Y ${signedPct(this.categoryReturn('1y'))}`, tone: (this.categoryReturn('1y') ?? 0) >= 0 ? 'good' : 'bad' },
        { label: `Cat avg ${h} Sharpe ${fmt(this.categoryMetric('sharpe_ratio'))}`, tone: '' },
        { label: `Cat avg TER ${pct(d.category_average.ter)}`, tone: '' },
      ],
    };

    if (f) {
      const sharpeRank = this.rank(f, (item) => this.fundMetric(item, 'sharpe_ratio'));
      const notes: string[] = [];
      const beta = this.fundMetric(f, 'beta');
      if (isNum(beta)) {
        notes.push(
          beta > 1.05
            ? `Beta <strong>${fmt(beta)}</strong>: amplifies market moves in both directions`
            : beta < 0.85
              ? `Beta <strong>${fmt(beta)}</strong>: defensively positioned, cushions market falls`
              : `Beta <strong>${fmt(beta)}</strong>: broadly tracks the market`,
        );
      }
      const up = this.fundMetric(f, 'upside_capture');
      const down = this.fundMetric(f, 'downside_capture');
      if (isNum(up) && isNum(down)) {
        notes.push(`captures <strong>${fmt(up, 0)}%</strong> of benchmark up-moves and <strong>${fmt(down, 0)}%</strong> of down-moves`);
      }
      const drawdown = this.fundMetric(f, 'trailing_max_drawdown');
      if (isNum(drawdown)) {
        notes.push(`${h} max drawdown <strong>${pct(drawdown, 1)}</strong> vs category ${pct(this.categoryMetric('trailing_max_drawdown'), 1)}`);
      }
      if (isNum(f.score.total)) {
        notes.push(`Quant Score <strong>${fmt(f.score.total, 1)}</strong> (rank #${f.score.rank ?? '—'} of ${clean(funds.map((item) => item.score.total)).length})`);
      }
      summaries.scorecard = {
        html: notes.length ? `${this.capitalise(notes.join('; '))}.` : 'Risk metrics are not available for this fund yet.',
        pills: [
          { label: `#${this.returnRank(f, '1y') ?? '—'} on 1Y`, tone: this.returnQuartile(f, '1y') === 'Q1' ? 'good' : '' },
          { label: `#${sharpeRank ?? '—'} on ${h} Sharpe`, tone: '' },
          { label: `TER ${pct(f.ter)}`, tone: this.tone(f.ter, d.category_average.ter, false) },
          { label: `AAUM ${crore(f.aum)}`, tone: '' },
        ],
      };

      const strengths = Object.entries(f.score.components).filter(([, value]) => isNum(value) && value >= 60).map(([key]) => SCORE_LABELS[key] ?? key);
      const weaknesses = Object.entries(f.score.components).filter(([, value]) => isNum(value) && value < 40).map(([key]) => SCORE_LABELS[key] ?? key);
      summaries.score = {
        html: isNum(f.score.total)
          ? `<strong>${escapeHtml(f.short_name)}</strong> ranks <strong>#${f.score.rank}</strong> with a Quant Score of <strong>${fmt(f.score.total, 1)}</strong> vs category average ${fmt(d.category_average.score.total, 1)}. Strong on: <strong>${escapeHtml(strengths.join(', ') || 'none')}</strong>. Weak on: <strong>${escapeHtml(weaknesses.join(', ') || 'none')}</strong>. Top 3 overall: ${this.names(topScore)}.`
          : `A Quant Score needs at least three of the five ${h} inputs; this fund does not have them yet (it may be younger than ${h}).`,
        pills: topScore.map((fund, index) => ({ label: `#${index + 1} ${this.label(fund, 22)}`, tone: index === 0 ? 'accent' : '' })),
      };

      const q3 = this.returnQuartile(f, '3m');
      const q6 = this.returnQuartile(f, '6m');
      const q1 = this.returnQuartile(f, '1y');
      const trend = trendLabel(q3, q6, q1);
      const consistent = funds.filter((fund) => this.heatTrend(fund).label.startsWith('★'));
      summaries.performance = {
        html: `<strong>${escapeHtml(f.short_name)}</strong> is ${q3 ?? '—'} / ${q6 ?? '—'} / ${q1 ?? '—'} on 3M / 6M / 1Y (${escapeHtml(trend.label)}). Funds in the top quartile on all three: ${consistent.length ? this.names(consistent) : '<strong>none</strong>'}. Category average 1Y ${signedPct(this.categoryReturn('1y'))}${d.benchmark ? ` vs ${escapeHtml(d.benchmark.name)} ${signedPct(this.benchmarkReturn('1y'))}` : ''}.`,
        pills: [
          { label: `#${this.returnRank(f, '3m') ?? '—'}/${this.countWith('3m')} on 3M`, tone: q3 === 'Q1' ? 'good' : q3 === 'Q4' ? 'bad' : '' },
          { label: `#${this.returnRank(f, '1y') ?? '—'}/${this.countWith('1y')} on 1Y`, tone: q1 === 'Q1' ? 'good' : q1 === 'Q4' ? 'bad' : '' },
          { label: `#${this.returnRank(f, '3y') ?? '—'}/${this.countWith('3y')} on 3Y`, tone: '' },
        ],
      };

      const alpha = this.positiveAlphaYears(f);
      const years = d.calendar_years;
      const best = years.reduce<string | null>((top, year) => (isNum(this.calendarReturn(f, year)) && (top === null || (this.calendarReturn(f, year) as number) > (this.calendarReturn(f, top) as number)) ? year : top), null);
      const worst = years.reduce<string | null>((low, year) => (isNum(this.calendarReturn(f, year)) && (low === null || (this.calendarReturn(f, year) as number) < (this.calendarReturn(f, low) as number)) ? year : low), null);
      summaries.calendar = {
        html: alpha.total
          ? `<strong>${escapeHtml(f.short_name)}</strong> beat ${escapeHtml(this.benchmarkName)} in <strong>${alpha.positive} of ${alpha.total}</strong> calendar years. Best year <strong>${best} (${pct(this.calendarReturn(f, best ?? ''), 1)})</strong>, weakest <strong>${worst} (${pct(this.calendarReturn(f, worst ?? ''), 1)})</strong>. YTD ${signedPct(this.fundReturn(f, 'ytd'))} vs benchmark ${signedPct(this.benchmarkReturn('ytd'))}.`
          : 'No complete calendar years with both fund and benchmark data yet.',
        pills: [
          { label: `Alpha in ${alpha.positive}/${alpha.total} years`, tone: alpha.total && alpha.positive > alpha.total / 2 ? 'good' : 'bad' },
          { label: `YTD ${signedPct(this.fundReturn(f, 'ytd'))}`, tone: this.tone(this.fundReturn(f, 'ytd'), this.benchmarkReturn('ytd')) },
        ],
      };

      const p25 = this.fundMetric(f, 'rolling_return_p25');
      const p75 = this.fundMetric(f, 'rolling_return_p75');
      summaries.risk = {
        html: `Over rolling ${h} windows, half of <strong>${escapeHtml(f.short_name)}</strong>'s outcomes fell between <strong>${pct(p25, 1)}</strong> and <strong>${pct(p75, 1)}</strong> a year (category average ${pct(this.categoryMetric('rolling_return_p25'), 1)} to ${pct(this.categoryMetric('rolling_return_p75'), 1)}). Volatility ${pct(this.fundMetric(f, 'amfi_volatility'), 1)}, Sortino ${fmt(this.fundMetric(f, 'sortino_ratio'))}, information ratio ${fmt(this.fundMetric(f, 'information_ratio'))}.`,
        pills: [
          { label: `Worst rolling ${h}: ${pct(this.fundMetric(f, 'min_rolling_return'), 1)}`, tone: 'bad' },
          { label: `Best rolling ${h}: ${pct(this.fundMetric(f, 'max_rolling_return'), 1)}`, tone: 'good' },
          { label: `SIP XIRR ${pct(this.fundMetric(f, 'trailing_xirr'), 1)}`, tone: '' },
        ],
      };

      const holdings = f.holdings;
      if (holdings) {
        const catSectors = catHoldings?.sectors ?? {};
        const named = (name: string) => name !== 'Unspecified';
        const active = Object.entries(holdings.sectors).filter(([name]) => named(name)).map(([name, value]) => ({ name, diff: value - (catSectors[name] ?? 0) }));
        const overweight = [...active].sort((a, b) => b.diff - a.diff).slice(0, 2);
        const underweight = Object.entries(catSectors)
          .filter(([name]) => named(name))
          .map(([name, value]) => ({ name, diff: (holdings.sectors[name] ?? 0) - value }))
          .sort((a, b) => a.diff - b.diff)
          .slice(0, 2);
        const bits = [
          `${holdings.total} holdings (${holdings.stocks} stocks) disclosed on ${holdings.as_of}`,
          `cash <strong>${pct(holdings.cash_pct, 1)}</strong> vs category ${pct(catHoldings?.['cash_pct'], 1)}`,
          `top-10 weight <strong>${pct(holdings.top10_pct, 1)}</strong>`,
        ];
        if (this.isEquity && overweight.length) {
          bits.push(`largest overweights ${joinNames(overweight.map((item) => `${item.name} (${signedPct(item.diff, 1)})`))}`);
          bits.push(`underweights ${joinNames(underweight.map((item) => `${item.name} (${signedPct(item.diff, 1)})`))}`);
        }
        if (holdings.fixed_income) {
          bits.push(`fixed income ${pct(holdings.fixed_income.weight_pct, 1)} with ${pct(holdings.fixed_income.sovereign_pct, 1)} sovereign and average maturity ${fmt(holdings.fixed_income.avg_maturity_years, 2)} years`);
        }
        summaries.portfolio = {
          html: `${this.capitalise(bits.join('; '))}.`,
          pills: [
            { label: `${f.unique.count} unique ideas`, tone: f.unique.count ? 'accent' : '' },
            { label: `${holdings.changes.new ?? 0} new · ${holdings.changes.exits ?? 0} exits`, tone: '' },
          ],
        };
      } else {
        summaries.portfolio = { html: 'No portfolio disclosure has been synced for this fund yet.', pills: [] };
      }
    }

    const x = this.axis(this.xAxis);
    const y = this.axis(this.yAxis);
    summaries.positioning = {
      html: `Plot any two of ${this.axisOptions.length} metrics; bubble size and colour add a third and fourth. The orange bubble is the selected fund. Top on ${escapeHtml(y?.label ?? '')}: ${this.names(topBy(funds, (fund) => y?.get(fund) ?? null, 3))}. Lowest on ${escapeHtml(x?.label ?? '')}: ${this.names(topBy(funds, (fund) => x?.get(fund) ?? null, 3, false))}.`,
      pills: [],
    };
    summaries.universe = {
      html: 'Click any column header to sort. <strong>Orange</strong> marks a value better than the category average for that column (lower is better for volatility, beta, downside capture and TER).',
      pills: [],
    };
    const buys = d.activity.net_buys.slice(0, 3);
    const sells = d.activity.net_sells.slice(0, 3);
    summaries.activity = {
      html: d.flags.has_holdings
        ? `Between the last two portfolio disclosures, the most widely bought stocks were ${joinNames(buys.map((item) => `${item.name} (${item.buyers} funds)`)) || '<strong>none</strong>'}; the most widely sold were ${joinNames(sells.map((item) => `${item.name} (${item.sellers} funds)`)) || '<strong>none</strong>'}. Weight changes include price drift, not only trading.`
        : 'Holdings have not been synced for this category yet.',
      pills: [
        { label: `${d.activity.new_entries.length} consensus new entries`, tone: 'good' },
        { label: `${d.activity.full_exits.length} consensus exits`, tone: 'bad' },
      ],
    };
    this.summaries = summaries;
  }

  private detailInsight(): string {
    const growth = this.detail?.growth;
    if (!growth || growth.fund.length < 2) return '';
    const fund = growth.fund[growth.fund.length - 1];
    const bench = growth.benchmark[growth.benchmark.length - 1];
    const since = growth.months[0];
    return isNum(bench)
      ? `₹100 invested in ${since} is worth <strong>₹${fmt(fund, 0)}</strong> today vs <strong>₹${fmt(bench, 0)}</strong> in ${escapeHtml(growth.benchmark_name ?? 'the benchmark')} (month-end NAVs).`
      : `₹100 invested in ${since} is worth <strong>₹${fmt(fund, 0)}</strong> today (month-end NAVs).`;
  }

  private capitalise(text: string): string {
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
  }

  // ── Sync ────────────────────────────────────────────────────────────────
  toggleSyncPanel(): void {
    this.syncPanelOpen = !this.syncPanelOpen;
    this.syncMessage = '';
    this.syncPlan = this.plan;
    this.syncScope = this.categorySlug ? 'category' : 'all';
  }

  startSync(): void {
    this.syncMessage = '';
    this.api
      .startSync({
        category: this.syncScope === 'category' ? this.categorySlug : null,
        plan: this.syncPlan,
        force: this.syncForce,
      })
      .subscribe({
        next: (response) => {
          this.syncRunning = true;
          this.lastSync = response.run;
          this.syncPanelOpen = false;
          this.syncMessage = response.status === 'already_running' ? 'A sync is already running.' : 'Sync started. Data refreshes automatically when it finishes.';
          this.pollSync();
          this.cdr.markForCheck();
        },
        error: (error) => {
          this.syncMessage = error?.status === 403 ? 'Your role cannot start a sync (Admin or above required).' : 'Could not start the sync.';
          this.cdr.markForCheck();
        },
      });
  }

  private pollSync(): void {
    if (this.syncPollTimer) clearTimeout(this.syncPollTimer);
    this.syncPollTimer = setTimeout(() => this.refreshSyncStatus(true), 5000);
  }

  refreshSyncStatus(reloadWhenDone: boolean): void {
    this.api.getSyncStatus().subscribe({
      next: (status) => {
        const wasRunning = this.syncRunning;
        this.syncRunning = status.running || status.run?.status === 'running';
        this.lastSync = status.run;
        if (this.syncRunning) {
          this.pollSync();
        } else if (wasRunning && reloadWhenDone) {
          this.syncMessage = `Sync ${status.run?.status ?? 'finished'}.`;
          this.loadCategories(false);
          this.loadDashboard();
        }
        this.cdr.markForCheck();
      },
      error: () => {
        if (this.syncRunning) this.pollSync();
      },
    });
  }

  // ── Charts ──────────────────────────────────────────────────────────────
  private scheduleCharts(): void {
    if (!this.isBrowser) return;
    if (this.chartTimer) clearTimeout(this.chartTimer);
    this.chartTimer = setTimeout(() => {
      this.chartTimer = null;
      this.cdr.detectChanges();
      this.renderCharts();
    }, 0);
  }

  private destroyCharts(): void {
    this.charts.forEach((chart) => chart.destroy());
    this.charts.clear();
  }

  private canvas(key: string): HTMLCanvasElement | null {
    return (this.host.nativeElement as HTMLElement).querySelector(`canvas[data-chart="${key}"]`);
  }

  private chart(key: string, config: ChartConfiguration): void {
    const element = this.canvas(key);
    if (!element) return;
    const options = (config.options ?? {}) as Record<string, any>;
    config.options = {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      ...options,
      plugins: {
        ...(options['plugins'] ?? {}),
        legend: { labels: { color: this.textColor(), font: { size: 10 }, boxWidth: 12 }, ...(options['plugins']?.legend ?? {}) },
      },
    } as any;
    this.charts.set(key, new Chart(element, config));
  }

  private isDark(): boolean {
    return typeof document !== 'undefined' && document.documentElement.classList.contains('dark-theme');
  }

  private textColor(): string {
    return this.isDark() ? '#cbd5e1' : '#475467';
  }

  private gridColor(): string {
    return this.isDark() ? '#2a2e38' : '#e5e7eb';
  }

  private navy(alpha = 1): string {
    return this.isDark() ? `rgba(148,163,184,${alpha})` : `rgba(5,18,51,${alpha})`;
  }

  private axisStyle(title?: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
    return {
      ticks: { color: this.textColor(), font: { size: 9 } },
      grid: { color: this.gridColor() },
      ...(title ? { title: { display: true, text: title, color: this.textColor(), font: { size: 10 } } } : {}),
      ...extra,
    };
  }

  private highlight(fund: MfFund, base: string): string {
    return fund.code === this.activeCode ? ORANGE : base;
  }

  private renderCharts(): void {
    this.destroyCharts();
    if (!this.dashboard || !this.funds.length) return;
    switch (this.tab) {
      case 'overview':
        this.renderOverview();
        break;
      case 'scorecard':
        this.renderScorecard();
        break;
      case 'score':
        this.renderScore();
        break;
      case 'performance':
        this.renderPerformance();
        break;
      case 'calendar':
        this.renderCalendar();
        break;
      case 'risk':
        this.renderRisk();
        break;
      case 'portfolio':
        this.renderPortfolio();
        break;
      case 'positioning':
        this.renderPositioning();
        break;
      case 'activity':
        this.renderActivity();
        break;
      default:
        break;
    }
  }

  private scatter(key: string, points: Array<{ fund: MfFund; x: number | null; y: number | null }>, xTitle: string, yTitle: string, extra: ChartDataset<'scatter'>[] = []): void {
    const valid = points.filter((point) => isNum(point.x) && isNum(point.y));
    this.chart(key, {
      type: 'scatter',
      data: {
        datasets: [
          {
            label: 'Funds',
            data: valid.map((point) => ({ x: point.x as number, y: point.y as number, name: point.fund.short_name }) as any),
            backgroundColor: valid.map((point) => this.highlight(point.fund, this.navy(0.5))),
            pointRadius: valid.map((point) => (point.fund.code === this.activeCode ? 8 : 5)),
          },
          ...extra,
        ],
      },
      options: {
        plugins: {
          legend: { display: extra.length > 0, labels: { filter: (item: any) => item.text !== 'Funds' } },
          tooltip: { callbacks: { label: (context: any) => `${context.raw.name}: ${fmt(context.raw.x)}, ${fmt(context.raw.y)}` } },
        },
        scales: { x: this.axisStyle(xTitle), y: this.axisStyle(yTitle) },
      } as any,
    });
  }

  private benchmarkPoint(x: number | null, y: number | null): ChartDataset<'scatter'>[] {
    if (!isNum(x) || !isNum(y)) return [];
    return [{
      label: this.benchmarkName,
      data: [{ x, y, name: this.benchmarkName } as any],
      backgroundColor: 'rgba(22,163,74,.9)',
      pointRadius: 9,
      pointStyle: 'triangle',
    }];
  }

  private renderOverview(): void {
    const periods = [
      { key: '3m', label: '3M' },
      { key: '6m', label: '6M' },
      { key: '1y', label: '1Y' },
    ];
    const all = periods.flatMap((period) => this.funds.map((fund) => this.fundReturn(fund, period.key)));
    const edges = niceEdges(all, 9);
    this.chart('ov-returns', {
      type: 'bar',
      data: {
        labels: edges.slice(0, -1).map((edge, index) => `${fmt(edge, 1)} to ${fmt(edges[index + 1], 1)}`),
        datasets: periods.map((period, index) => ({
          label: `${period.label} return`,
          data: histogram(this.funds.map((fund) => this.fundReturn(fund, period.key)), edges),
          backgroundColor: PALETTE[index] + 'B0',
        })),
      },
      options: { scales: { x: this.axisStyle('Return %'), y: this.axisStyle('# funds') } },
    });

    const scoreEdges = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100];
    this.chart('ov-score', {
      type: 'bar',
      data: {
        labels: scoreEdges.slice(0, -1).map((edge, index) => `${edge}-${scoreEdges[index + 1]}`),
        datasets: [{ label: 'Funds', data: histogram(this.funds.map((fund) => fund.score.total), scoreEdges), backgroundColor: ORANGE + 'C0' }],
      },
      options: { plugins: { legend: { display: false } }, scales: { x: this.axisStyle('Quant Score'), y: this.axisStyle('# funds') } },
    });

    const cat = this.dashboard!.category_average.holdings;
    if (this.isEquity) {
      const sorted = topBy(this.funds.filter((fund) => fund.holdings?.cap), (fund) => this.mapValue(fund.holdings?.cap, 'Large Cap'), 24);
      const colors = [this.navy(0.9), ORANGE, GOLD, '#9CA3AF'];
      this.chart('ov-mix', {
        type: 'bar',
        data: {
          labels: ['Category avg', ...sorted.map((fund) => this.label(fund, 22))],
          datasets: CAP_KEYS.map((key, index) => ({
            label: key,
            data: [this.mapValue(cat?.cap, key), ...sorted.map((fund) => this.mapValue(fund.holdings?.cap, key))],
            backgroundColor: colors[index],
          })),
        },
        options: { scales: { x: this.axisStyle(undefined, { stacked: true, ticks: { color: this.textColor(), font: { size: 8 }, maxRotation: 60 } }), y: this.axisStyle('% of equity', { stacked: true, max: 100 }) } },
      });
      const styleSorted = topBy(this.funds.filter((fund) => fund.holdings?.style), (fund) => this.mapValue(fund.holdings?.style, 'Cyclical'), 24);
      this.chart('ov-style', {
        type: 'bar',
        data: {
          labels: ['Category avg', ...styleSorted.map((fund) => this.label(fund, 22))],
          datasets: STYLE_KEYS.map((key) => ({
            label: key,
            data: [this.mapValue(cat?.style, key), ...styleSorted.map((fund) => this.mapValue(fund.holdings?.style, key))],
            backgroundColor: STYLE_COLORS[key] + 'C0',
          })),
        },
        options: { scales: { x: this.axisStyle(undefined, { stacked: true, ticks: { color: this.textColor(), font: { size: 8 }, maxRotation: 60 } }), y: this.axisStyle('% of equity', { stacked: true, max: 100 }) } },
      });
    } else {
      const withHoldings = topBy(this.funds.filter((fund) => fund.holdings), (fund) => fund.aum, 24);
      const parts = [
        { key: 'equity_pct', label: 'Equity', color: this.navy(0.85) },
        { key: 'debt_pct', label: 'Debt', color: ORANGE },
        { key: 'fund_unit_pct', label: 'Fund units', color: BLUE },
        { key: 'commodity_pct', label: 'Commodity', color: GOLD },
        { key: 'cash_pct', label: 'Cash', color: '#9CA3AF' },
      ];
      this.chart('ov-mix', {
        type: 'bar',
        data: {
          labels: ['Category avg', ...withHoldings.map((fund) => this.label(fund, 22))],
          datasets: parts.map((part) => ({
            label: part.label,
            data: [this.categoryHolding(part.key), ...withHoldings.map((fund) => this.fundHolding(fund, part.key))],
            backgroundColor: part.color,
          })),
        },
        options: { scales: { x: this.axisStyle(undefined, { stacked: true, ticks: { color: this.textColor(), font: { size: 8 }, maxRotation: 60 } }), y: this.axisStyle('% of portfolio', { stacked: true }) } },
      });
      if (this.hasFixedIncome) {
        const fi = withHoldings.filter((fund) => fund.holdings?.fixed_income);
        const fiParts = [
          { key: 'sovereign_pct', label: 'Sovereign', color: GREEN },
          { key: 'corporate_pct', label: 'Corporate / PSU', color: ORANGE },
          { key: 'cash_equivalent_pct', label: 'Cash & equivalents', color: '#9CA3AF' },
        ];
        this.chart('ov-style', {
          type: 'bar',
          data: {
            labels: ['Category avg', ...fi.map((fund) => this.label(fund, 22))],
            datasets: fiParts.map((part) => ({
              label: part.label,
              data: [this.mapValue(cat?.fixed_income, part.key), ...fi.map((fund) => this.mapValue(fund.holdings?.fixed_income as any, part.key))],
              backgroundColor: part.color + 'C0',
            })),
          },
          options: { scales: { x: this.axisStyle(undefined, { stacked: true, ticks: { color: this.textColor(), font: { size: 8 }, maxRotation: 60 } }), y: this.axisStyle('% of portfolio', { stacked: true }) } },
        });
      }
    }

    const aumTop = topBy(this.funds, (fund) => fund.aum, 12);
    this.chart('ov-aum', {
      type: 'bar',
      data: {
        labels: aumTop.map((fund) => this.label(fund, 24)),
        datasets: [{ label: 'AAUM ₹ Cr', data: aumTop.map((fund) => fund.aum), backgroundColor: aumTop.map((fund) => this.highlight(fund, this.navy(0.7))) }],
      },
      options: { indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: this.axisStyle('₹ Cr'), y: this.axisStyle() } },
    });

    this.scatter(
      'ov-mdd-calmar',
      this.funds.map((fund) => ({ fund, x: this.fundMetric(fund, 'trailing_max_drawdown'), y: this.calmar(fund) })),
      `${this.horizon} max drawdown %`,
      `${this.horizon} Calmar ratio`,
    );
  }

  private renderScorecard(): void {
    const f = this.fund;
    if (!f) return;
    const cat = this.dashboard!.category_average;
    if (this.isEquity && f.holdings?.cap) {
      this.chart('sc-cap', {
        type: 'bar',
        data: {
          labels: CAP_KEYS,
          datasets: [
            { label: this.label(f, 24), data: CAP_KEYS.map((key) => this.mapValue(f.holdings?.cap, key)), backgroundColor: ORANGE + 'C0' },
            { label: 'Category avg', data: CAP_KEYS.map((key) => this.mapValue(cat.holdings?.cap, key)), backgroundColor: this.navy(0.45) },
          ],
        },
        options: { scales: { x: this.axisStyle(), y: this.axisStyle('% of equity') } },
      });
      this.chart('sc-style', {
        type: 'bar',
        data: {
          labels: STYLE_KEYS,
          datasets: [
            { label: this.label(f, 24), data: STYLE_KEYS.map((key) => this.mapValue(f.holdings?.style, key)), backgroundColor: STYLE_KEYS.map((key) => STYLE_COLORS[key] + 'C0') },
            { label: 'Category avg', data: STYLE_KEYS.map((key) => this.mapValue(cat.holdings?.style, key)), backgroundColor: this.navy(0.35) },
          ],
        },
        options: { scales: { x: this.axisStyle(), y: this.axisStyle('% of equity') } },
      });
    } else if (f.holdings) {
      const parts = ['equity_pct', 'debt_pct', 'fund_unit_pct', 'commodity_pct', 'cash_pct'];
      const labels = ['Equity', 'Debt', 'Fund units', 'Commodity', 'Cash'];
      this.chart('sc-cap', {
        type: 'bar',
        data: {
          labels,
          datasets: [
            { label: this.label(f, 24), data: parts.map((key) => this.fundHolding(f, key)), backgroundColor: ORANGE + 'C0' },
            { label: 'Category avg', data: parts.map((key) => this.categoryHolding(key)), backgroundColor: this.navy(0.45) },
          ],
        },
        options: { scales: { x: this.axisStyle(), y: this.axisStyle('% of portfolio') } },
      });
    }
    const components = Object.keys(SCORE_LABELS);
    this.chart('sc-score', {
      type: 'radar',
      data: {
        labels: components.map((key) => SCORE_LABELS[key]),
        datasets: [
          { label: this.label(f, 24), data: components.map((key) => f.score.components[key] ?? 0), borderColor: ORANGE, backgroundColor: 'rgba(253,119,64,.15)', pointBackgroundColor: ORANGE },
          { label: 'Category avg', data: components.map((key) => cat.score.components[key] ?? 0), borderColor: this.navy(), backgroundColor: this.navy(0.08), pointBackgroundColor: this.navy() },
        ],
      },
      options: { scales: { r: { min: 0, max: 100, ticks: { display: false }, grid: { color: this.gridColor() }, angleLines: { color: this.gridColor() }, pointLabels: { color: this.textColor(), font: { size: 9 } } } } } as any,
    });
    this.renderGrowth('sc-growth');
    const aum = this.detail?.aum_history ?? [];
    if (aum.length) {
      this.chart('sc-aum', {
        type: 'line',
        data: {
          labels: aum.map((item) => item.date.slice(0, 7)),
          datasets: [{ label: 'Quarterly average AUM (₹ Cr)', data: aum.map((item) => item.aum), borderColor: this.navy(), backgroundColor: this.navy(0.06), fill: true, tension: 0.3, pointRadius: 2 }],
        },
        options: { plugins: { legend: { display: false } }, scales: { x: this.axisStyle(), y: this.axisStyle('₹ Cr') } },
      });
    }
  }

  private renderGrowth(key: string): void {
    const growth = this.detail?.growth;
    if (!growth || growth.fund.length < 2) return;
    const months = { '1Y': 13, '3Y': 37, '5Y': 61 }[this.growthWindow] ?? growth.months.length;
    const start = Math.max(0, growth.months.length - months);
    const rebase = (series: number[]) => {
      const slice = series.slice(start);
      const base = slice[0];
      return isNum(base) && base > 0 ? slice.map((value) => (value / base) * 100) : slice;
    };
    const datasets: ChartDataset<'line'>[] = [
      { label: this.label(this.fund, 26), data: rebase(growth.fund), borderColor: ORANGE, backgroundColor: 'rgba(253,119,64,.08)', fill: true, tension: 0.25, pointRadius: 0, borderWidth: 2 },
    ];
    if (growth.benchmark.length) {
      datasets.push({ label: growth.benchmark_name ?? 'Benchmark', data: rebase(growth.benchmark), borderColor: GREEN, borderDash: [5, 4], tension: 0.25, pointRadius: 0, borderWidth: 1.5, fill: false });
    }
    this.chart(key, {
      type: 'line',
      data: { labels: growth.months.slice(start), datasets },
      options: { interaction: { mode: 'index', intersect: false }, scales: { x: this.axisStyle(undefined, { ticks: { color: this.textColor(), font: { size: 8 }, maxTicksLimit: 12 } }), y: this.axisStyle('Value of ₹100') } } as any,
    });
  }

  private renderScore(): void {
    const f = this.fund;
    const cat = this.dashboard!.category_average;
    const components = Object.keys(SCORE_LABELS);
    if (f) {
      this.chart('qs-radar', {
        type: 'radar',
        data: {
          labels: components.map((key) => SCORE_LABELS[key]),
          datasets: [
            { label: this.label(f, 24), data: components.map((key) => f.score.components[key] ?? 0), borderColor: ORANGE, backgroundColor: 'rgba(253,119,64,.15)', borderWidth: 2 },
            { label: 'Category avg', data: components.map((key) => cat.score.components[key] ?? 0), borderColor: this.navy(), backgroundColor: this.navy(0.08), borderWidth: 2 },
          ],
        },
        options: { scales: { r: { min: 0, max: 100, grid: { color: this.gridColor() }, angleLines: { color: this.gridColor() }, ticks: { backdropColor: 'transparent', color: this.textColor(), font: { size: 8 } }, pointLabels: { color: this.textColor(), font: { size: 10 } } } } } as any,
      });
    }
    this.scatter(
      'qs-return-risk',
      this.funds.map((fund) => ({ fund, x: fund.score.components['returns'] ?? null, y: fund.score.components['risk_adjusted'] ?? null })),
      'Returns percentile',
      'Risk-adjusted percentile',
    );
    this.scatter(
      'qs-consistency-downside',
      this.funds.map((fund) => ({ fund, x: fund.score.components['consistency'] ?? null, y: fund.score.components['downside'] ?? null })),
      'Consistency percentile',
      'Downside protection percentile',
    );
  }

  private renderPerformance(): void {
    const ranked = this.performanceRows.filter((fund) => isNum(this.fundReturn(fund, '1y')));
    const datasets: any[] = [
      {
        label: '1Y return %',
        data: ranked.map((fund) => this.fundReturn(fund, '1y')),
        backgroundColor: ranked.map((fund) => (fund.code === this.activeCode ? ORANGE : (this.fundReturn(fund, '1y') as number) >= 0 ? 'rgba(22,163,74,.65)' : 'rgba(220,38,38,.55)')),
      },
      { type: 'line', label: 'Category avg', data: ranked.map(() => this.categoryReturn('1y')), borderColor: this.navy(), borderDash: [4, 3], borderWidth: 1.5, pointRadius: 0 },
    ];
    if (isNum(this.benchmarkReturn('1y'))) {
      datasets.push({ type: 'line', label: this.benchmarkName, data: ranked.map(() => this.benchmarkReturn('1y')), borderColor: GREEN, borderDash: [6, 3], borderWidth: 1.5, pointRadius: 0 });
    }
    this.chart('pf-rank', {
      type: 'bar',
      data: { labels: ranked.map((fund) => this.label(fund, 20)), datasets },
      options: { scales: { x: this.axisStyle(undefined, { ticks: { color: this.textColor(), font: { size: 7 }, maxRotation: 70 } }), y: this.axisStyle('1Y return %') } },
    });
    this.scatter(
      'pf-3m-6m',
      this.funds.map((fund) => ({ fund, x: this.fundReturn(fund, '3m'), y: this.fundReturn(fund, '6m') })),
      '3M return %',
      '6M return %',
      this.benchmarkPoint(this.benchmarkReturn('3m'), this.benchmarkReturn('6m')),
    );
    this.scatter(
      'pf-1y-mdd',
      this.funds.map((fund) => ({ fund, x: this.fundMetric(fund, 'trailing_max_drawdown'), y: this.fundReturn(fund, '1y') })),
      `${this.horizon} max drawdown %`,
      '1Y return %',
    );
  }

  private renderCalendar(): void {
    const f = this.fund;
    if (!f) return;
    const years = this.dashboard!.calendar_years;
    const labels = [...years.map((year) => `CY${year}`), 'YTD'];
    this.chart('cy-grouped', {
      type: 'bar',
      data: {
        labels,
        datasets: [
          { label: this.label(f, 24), data: [...years.map((year) => this.calendarReturn(f, year)), this.fundReturn(f, 'ytd')], backgroundColor: ORANGE },
          { label: 'Category avg', data: [...years.map((year) => this.categoryCalendarReturn(year)), this.categoryReturn('ytd')], backgroundColor: this.navy(0.5) },
          { label: this.benchmarkName, data: [...years.map((year) => this.benchmarkCalendarReturn(year)), this.benchmarkReturn('ytd')], backgroundColor: 'rgba(22,163,74,.6)' },
        ],
      },
      options: { scales: { x: this.axisStyle(), y: this.axisStyle('Return %') } },
    });
    const alpha = [...years.map((year) => this.diff(this.calendarReturn(f, year), this.benchmarkCalendarReturn(year))), this.diff(this.fundReturn(f, 'ytd'), this.benchmarkReturn('ytd'))];
    this.chart('cy-alpha', {
      type: 'bar',
      data: {
        labels,
        datasets: [{ label: `Excess return vs ${this.benchmarkName}`, data: alpha, backgroundColor: alpha.map((value) => (!isNum(value) ? '#9CA3AF' : value >= 0 ? 'rgba(22,163,74,.75)' : 'rgba(220,38,38,.65)')) }],
      },
      options: { plugins: { legend: { display: false } }, scales: { x: this.axisStyle(), y: this.axisStyle('Percentage points') } },
    });
  }

  private renderRisk(): void {
    const f = this.fund;
    if (f) {
      this.chart('rk-rolling', {
        type: 'bar',
        data: {
          labels: ['P10', 'P25', 'Median', 'P75', 'P90'],
          datasets: [
            { label: this.label(f, 24), data: ROLLING_KEYS.map((key) => this.fundMetric(f, key)), backgroundColor: ORANGE + 'C0' },
            { label: 'Category avg', data: ROLLING_KEYS.map((key) => this.categoryMetric(key)), backgroundColor: this.navy(0.45) },
            { label: 'Category median (all plans)', data: ROLLING_KEYS.map((key) => this.categoryMedianMetric(key)), backgroundColor: GOLD + 'A0' },
          ],
        },
        options: { scales: { x: this.axisStyle(`Rolling ${this.horizon} return percentile`), y: this.axisStyle('% a year') } },
      });
    }
    this.scatter(
      'rk-vol-return',
      this.funds.map((fund) => ({ fund, x: this.fundMetric(fund, 'amfi_volatility'), y: this.fundMetric(fund, 'trailing_cagr') })),
      `${this.horizon} volatility %`,
      `${this.horizon} CAGR %`,
    );
    this.scatter(
      'rk-capture',
      this.funds.map((fund) => ({ fund, x: this.fundMetric(fund, 'downside_capture'), y: this.fundMetric(fund, 'upside_capture') })),
      'Downside capture % (lower is better)',
      'Upside capture % (higher is better)',
    );
  }

  private renderPortfolio(): void {
    const f = this.fund;
    if (!f?.holdings) return;
    const parts = ['equity_pct', 'debt_pct', 'fund_unit_pct', 'commodity_pct', 'cash_pct'];
    this.chart('pt-alloc', {
      type: 'bar',
      data: {
        labels: ['Equity', 'Debt', 'Fund units', 'Commodity', 'Cash'],
        datasets: [
          { label: this.label(f, 24), data: parts.map((key) => this.fundHolding(f, key)), backgroundColor: ORANGE + 'C0' },
          { label: 'Category avg', data: parts.map((key) => this.categoryHolding(key)), backgroundColor: this.navy(0.45) },
        ],
      },
      options: { scales: { x: this.axisStyle(), y: this.axisStyle('% of portfolio') } },
    });
    const byCount = this.funds.filter((fund) => fund.holdings).sort((a, b) => (a.holdings!.total ?? 0) - (b.holdings!.total ?? 0)).slice(0, 30);
    this.chart('pt-concentration', {
      type: 'bar',
      data: {
        labels: byCount.map((fund) => this.label(fund, 20)),
        datasets: [
          { label: 'Holdings', data: byCount.map((fund) => fund.holdings!.total), backgroundColor: byCount.map((fund) => this.highlight(fund, this.navy(0.65))), yAxisID: 'y' },
          { type: 'line', label: 'Top-10 %', data: byCount.map((fund) => fund.holdings!.top10_pct), borderColor: GOLD, backgroundColor: GOLD, pointRadius: 3, yAxisID: 'y2' } as any,
        ],
      },
      options: {
        scales: {
          x: this.axisStyle(undefined, { ticks: { color: this.textColor(), font: { size: 7 }, maxRotation: 70 } }),
          y: this.axisStyle('# holdings', { position: 'left' }),
          y2: this.axisStyle('Top-10 %', { position: 'right', grid: { drawOnChartArea: false } }),
        },
      } as any,
    });
    if (f.holdings.fixed_income) {
      const buckets = this.maturityBuckets();
      this.chart('pt-maturity', {
        type: 'bar',
        data: { labels: buckets.map((item) => item.label), datasets: [{ label: '% of portfolio', data: buckets.map((item) => item.value), backgroundColor: ORANGE + 'C0' }] },
        options: { plugins: { legend: { display: false } }, scales: { x: this.axisStyle('Residual maturity'), y: this.axisStyle('% of portfolio') } },
      });
    }
  }

  private renderPositioning(): void {
    const x = this.axis(this.xAxis);
    const y = this.axis(this.yAxis);
    const size = this.axis(this.sizeAxis);
    const color = this.axis(this.colorAxis);
    if (!x || !y || !size || !color) return;
    const valid = this.funds.filter((fund) => isNum(x.get(fund)) && isNum(y.get(fund)));
    const sizes = clean(valid.map((fund) => size.get(fund)));
    const colors = clean(valid.map((fund) => color.get(fund)));
    const [sMin, sMax] = [Math.min(...sizes), Math.max(...sizes)];
    const [cMin, cMax] = [Math.min(...colors), Math.max(...colors)];
    const radius = (value: number | null) => (!isNum(value) || sMax === sMin ? 7 : 4 + ((value - sMin) / (sMax - sMin)) * 18);
    const shade = (value: number | null) => {
      const t = !isNum(value) || cMax === cMin ? 0.5 : (value - cMin) / (cMax - cMin);
      const dark = this.isDark() ? [148, 163, 184] : [5, 18, 51];
      const mix = dark.map((channel, index) => Math.round(channel + ([204, 159, 83][index] - channel) * t));
      return `rgba(${mix[0]},${mix[1]},${mix[2]},.7)`;
    };
    this.chart('ps-bubble', {
      type: 'bubble',
      data: {
        datasets: [{
          label: 'Funds',
          data: valid.map((fund) => ({ x: x.get(fund) as number, y: y.get(fund) as number, r: radius(size.get(fund)), name: fund.short_name }) as any),
          backgroundColor: valid.map((fund) => (fund.code === this.activeCode ? 'rgba(253,119,64,.95)' : shade(color.get(fund)))),
          borderColor: valid.map((fund) => (fund.code === this.activeCode ? ORANGE : 'transparent')),
          borderWidth: valid.map((fund) => (fund.code === this.activeCode ? 2 : 0)),
        }],
      },
      options: {
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (context: any) => `${context.raw.name} · ${x.label}: ${fmt(context.raw.x)} · ${y.label}: ${fmt(context.raw.y)}` } },
        },
        scales: { x: this.axisStyle(x.label), y: this.axisStyle(y.label) },
      } as any,
    });
    const rankedX = topBy(this.funds, x.get, 40);
    this.chart('ps-rank', {
      type: 'bar',
      data: {
        labels: rankedX.map((fund) => this.label(fund, 24)),
        datasets: [{ label: x.label, data: rankedX.map((fund) => x.get(fund)), backgroundColor: rankedX.map((fund) => this.highlight(fund, this.navy(0.55))) }],
      },
      options: { indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: this.axisStyle(x.label), y: this.axisStyle(undefined, { ticks: { color: this.textColor(), font: { size: 8 } } }) } },
    });
    this.scatter(
      'ps-sharpe-1y',
      this.funds.map((fund) => ({ fund, x: this.fundMetric(fund, 'sharpe_ratio'), y: this.fundReturn(fund, '1y') })),
      `${this.horizon} Sharpe ratio`,
      '1Y return %',
    );
  }

  private renderActivity(): void {
    const sectors = this.dashboard!.activity.sector_change;
    const shown = [...sectors.slice(0, 8), ...sectors.slice(-8).filter((item) => !sectors.slice(0, 8).includes(item))];
    if (!shown.length) return;
    this.chart('ac-sectors', {
      type: 'bar',
      data: {
        labels: shown.map((item) => item.sector),
        datasets: [{ label: 'Sum of weight changes (pp)', data: shown.map((item) => item.total_change_pct), backgroundColor: shown.map((item) => (item.total_change_pct >= 0 ? 'rgba(22,163,74,.7)' : 'rgba(220,38,38,.6)')) }],
      },
      options: { indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: this.axisStyle('Percentage points across all funds'), y: this.axisStyle(undefined, { ticks: { color: this.textColor(), font: { size: 9 } } }) } },
    });
  }
}
