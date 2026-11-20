import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { catchError, forkJoin, of } from 'rxjs';
import {
  IndiaMutualFundFeedResponse,
  IndianApiUsageResponse,
  PersonalMutualFundPortfolioResponse,
  MutualFundResearchReportResponse,
  MutualFundResearchUniverseOption,
  MutualFundAnalyticsService,
  SebiFundRankingRow,
  SebiUniverseRankingsResponse,
  SebiUniverseSummaryResponse,
} from '../../core/services/mutual-fund-analytics.service';

type Period = '1M' | '3M' | '6M' | '1Y' | '3Y' | '5Y' | 'YTD';

@Component({
  selector: 'app-mutual-fund-analytics',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './mutual-fund-analytics.component.html',
  styleUrl: './mutual-fund-analytics.component.scss',
})
export class MutualFundAnalyticsComponent implements OnInit, OnDestroy {
  private readonly analytics = inject(MutualFundAnalyticsService);

  report: MutualFundResearchReportResponse | null = null;
  researchUniverses: MutualFundResearchUniverseOption[] = [];
  selectedUniverseId: number | null = null;
  comparisonFundName = '';
  selectedSectorName = '';
  indiaFeed: IndiaMutualFundFeedResponse | null = null;
  apiUsage: IndianApiUsageResponse | null = null;
  personalPortfolio: PersonalMutualFundPortfolioResponse | null = null;
  sebiSummary: SebiUniverseSummaryResponse | null = null;
  sebiRankings: SebiUniverseRankingsResponse | null = null;
  sebiDashboard: any = null;
  activeSebiView = 'overview';
  selectedSebiPlan = '';
  selectedSebiCategory = 'All categories';
  selectedSebiFundName = '';
  overviewDashboard: any = null;
  sebiCategoryRiskChartSrc: string | null = null;
  sebiTopQ1ChartSrc: string | null = null;
  loading = false;
  refreshing = false;
  refreshMessage = '';
  error = '';
  activeTab = 'overview';
  selectedFundName = '';
  activePeriod: Period = '1Y';
  xAxis = 'large';
  yAxis = 'edge_score';
  bubbleAxis = 'aum';

  readonly tabs = [
    { id: 'my-portfolio', label: 'My Portfolio' },
    { id: 'overview', label: 'Overview' },
    { id: 'scorecard', label: 'Fund Scorecard' },
    { id: 'edge', label: 'EDGE Scores' },
    { id: 'calendar', label: 'Calendar Returns' },
    { id: 'performance', label: 'Performance' },
    { id: 'portfolio', label: 'Portfolio' },
    { id: 'positioning', label: 'Positioning' },
    { id: 'universe', label: 'Universe' },
    { id: 'attribution', label: 'Attribution' },
    { id: 'india', label: 'India Universe' },
    { id: 'sebi-universe', label: 'SEBI Analytics' },
  ];
  readonly periods: Period[] = ['1M', '3M', '6M', '1Y', '3Y', '5Y', 'YTD'];
  readonly axisOptions = [
    { key: 'large', label: 'Large Cap %' }, { key: 'mid', label: 'Mid Cap %' },
    { key: 'small', label: 'Small Cap %' }, { key: 'cash', label: 'Cash %' },
    { key: 'cyclical', label: 'Cyclical %' }, { key: 'defensive', label: 'Defensive %' },
    { key: 'sensitive', label: 'Sensitive %' }, { key: 'pe', label: 'P/E' },
    { key: 'pbv', label: 'P/B' }, { key: 'beta', label: 'Beta' },
    { key: 'sharpe_calc', label: 'Sharpe (3Y)' }, { key: 'sortino', label: 'Sortino' },
    { key: 'max_drawdown', label: 'Max Drawdown %' }, { key: 'calmar', label: 'Calmar' },
    { key: 'r3m', label: '3M Return %' }, { key: 'r6m', label: '6M Return %' },
    { key: 'r1y', label: '1Y Return %' }, { key: 'edge_score', label: 'EDGE Score' },
    { key: 'eps_cagr', label: 'EPS CAGR %' }, { key: 'fwd_peg', label: 'Forward PEG' },
    { key: 'quality', label: 'Quality' }, { key: 'valuation', label: 'Valuation' },
    { key: 'momentum', label: 'Momentum' }, { key: 'top10', label: 'Top-10 %' },
    { key: 'stocks', label: 'Stock Count' }, { key: 'aum', label: 'AUM' },
    { key: 'er', label: 'Expense Ratio %' }, { key: 'turnover', label: 'Turnover %' },
  ];

  ngOnInit(): void { this.load(); }

  load(): void {
    this.loading = true;
    this.error = '';
    const reportRequest = this.selectedUniverseId
      ? this.analytics.getResearchReport(this.selectedUniverseId)
      : this.analytics.getResearchReport();

    forkJoin({
      report: reportRequest,
      researchUniverses: this.analytics.getResearchUniverses().pipe(
        catchError(() => of({ available: false, count: 0, results: [], message: 'Unable to load research universes.' })),
      ),
      indiaFeed: this.analytics.getIndiaMarketFeed().pipe(catchError(() => of(null))),
      apiUsage: this.analytics.getApiUsage().pipe(catchError(() => of(null))),
      personalPortfolio: this.analytics.getPersonalPortfolio().pipe(catchError(() => of(null))),
      sebiSummary: this.analytics.getSebiUniverseSummary().pipe(catchError(() => of(null))),
      sebiRankings: this.analytics.getSebiUniverseRankings({ page_size: 100 }).pipe(catchError(() => of(null))),
    }).subscribe({
      next: value => {
        this.researchUniverses = value.researchUniverses?.results || [];
        let report = value.report;
        if (!report.available && this.selectedUniverseId) {
          this.selectedUniverseId = null;
          this.analytics.getResearchReport().subscribe({
            next: latest => this.finishLoad(value, latest),
            error: err => {
              this.error = err?.error?.detail || 'Unable to load the latest mutual-fund universe.';
              this.loading = false;
            },
          });
          return;
        }
        this.finishLoad(value, report);
      },
      error: err => {
        this.error = err?.error?.detail || 'Unable to load mutual-fund research data.';
        this.loading = false;
      },
    });
  }

  private finishLoad(value: any, report: MutualFundResearchReportResponse): void {
    this.report = report;
    this.indiaFeed = value.indiaFeed;
    this.apiUsage = value.apiUsage;
    this.personalPortfolio = value.personalPortfolio;
    this.sebiSummary = value.sebiSummary;
    this.sebiRankings = value.sebiRankings;
    this.selectedUniverseId = report.id ?? null;
    const reportFunds = report.payload?.RAW?.funds || [];
    if (!reportFunds.some((fund: any) => fund.name === this.comparisonFundName)) {
      this.comparisonFundName = reportFunds[0]?.name || '';
    }
    if (!reportFunds.some((fund: any) => fund.name === this.selectedFundName)) {
      this.selectedFundName = reportFunds[0]?.name || '';
    }
    this.overviewDashboard = report.available ? this.buildOverviewDashboard() : null;

    if (report.available && this.activeTab === 'sebi-universe') {
      // The page previously fell back to the SEBI tab when no report existed.
      // Move to Overview when a report becomes available so the category dashboard is visible.
      this.activeTab = 'overview';
    } else if (!report.available && this.activeTab === 'overview') {
      this.activeTab = 'sebi-universe';
    }

    this.initializeSebiDashboard();
    this.loading = false;
  }

  onUniverseChange(reportId: number | string): void {
    const id = Number(reportId);
    if (!Number.isInteger(id) || id <= 0 || id === this.selectedUniverseId) return;
    this.loading = true;
    this.error = '';
    this.analytics.getResearchReport(id).subscribe({
      next: report => {
        if (!report.available) {
          this.error = report.message || 'The selected research universe is unavailable.';
          this.loading = false;
          return;
        }
        this.report = report;
        this.selectedUniverseId = report.id ?? id;
        const reportFunds = report.payload?.RAW?.funds || [];
        this.selectedFundName = reportFunds[0]?.name || '';
        this.comparisonFundName = this.selectedFundName;
        this.overviewDashboard = this.buildOverviewDashboard();
        this.activeTab = 'overview';
        this.loading = false;
      },
      error: err => {
        this.error = err?.error?.detail || 'Unable to load the selected research universe.';
        this.loading = false;
      },
    });
  }

  get comparisonFund(): any {
    return this.funds.find(fund => fund.name === this.comparisonFundName) || null;
  }

  get fundComparisonRows(): any[] {
    const fund = this.comparisonFund;
    if (!fund || !this.overviewDashboard) return [];
    const universeAverage = (key: string): number | null =>
      this.mean(this.funds.map(row => this.finiteNumber(row?.[key])));
    const fundLarge = this.finiteNumber(fund.large);
    const universeLarge = universeAverage('large');
    const fundCalmar = this.finiteNumber(fund.calmar);
    const rows = [
      { metric: 'P/E', value: this.finiteNumber(fund.pe), average: this.overviewDashboard.weightedPe, kind: 'number' },
      { metric: 'Large-cap allocation', value: fundLarge, average: universeLarge, kind: 'percent' },
      { metric: '3M return', value: this.fundReturn(fund, '3M'), average: this.overviewDashboard.average3M, kind: 'percent' },
      { metric: '3Y Sharpe', value: this.finiteNumber(fund.sharpe_calc), average: this.overviewDashboard.averageSharpe, kind: 'number' },
      { metric: 'Max drawdown', value: this.finiteNumber(fund.max_drawdown), average: this.overviewDashboard.averageMaxDrawdown, kind: 'percent' },
      { metric: 'Calmar ratio', value: fundCalmar, average: universeAverage('calmar'), kind: 'number' },
      { metric: 'EDGE score', value: this.finiteNumber(fund.edge_score), average: universeAverage('edge_score'), kind: 'number' },
    ];
    return rows.map(row => ({
      ...row,
      difference: row.value !== null && row.average !== null ? row.value - row.average : null,
    }));
  }

  onComparisonFundChange(name: string): void {
    this.comparisonFundName = name;
  }

  onSectorChange(sector: string): void {
    this.selectedSectorName = sector;
    this.overviewDashboard = this.buildOverviewDashboard();
  }

  refreshProviderData(): void {
    if (this.refreshing) return;
    this.refreshing = true;
    this.refreshMessage = '';
    this.analytics.refreshIndiaMarketFeed().subscribe({
      next: result => {
        this.refreshMessage = result.message;
        this.apiUsage = result.usage;
        this.refreshing = false;
        // Follow-up loads only read the PWMS database/cache; they do not call IndianAPI.
        this.load();
      },
      error: err => {
        this.refreshMessage = err?.error?.message || err?.error?.detail ||
          'Refresh failed. Cached IndianAPI and AMFI data remain available.';
        this.refreshing = false;
        this.analytics.getApiUsage().subscribe({
          next: usage => this.apiUsage = usage,
        });
      },
    });
  }


  get sebiPlanOptions(): string[] {
    const plans = (this.sebiSummary?.tree_summary || [])
      .map(row => String(row.Plan || '').trim()).filter(Boolean);
    return [...new Set(plans)].sort((a, b) => a.localeCompare(b));
  }

  get sebiCategoryOptions(): string[] {
    const names = (this.sebiSummary?.tree_summary || [])
      .filter(row => !this.selectedSebiPlan || row.Plan === this.selectedSebiPlan)
      .map(row => String(row.Level_3_Category || '').trim()).filter(Boolean);
    return [...new Set(names)].sort((a, b) => a.localeCompare(b));
  }

  get sebiFundOptions(): SebiFundRankingRow[] {
    const byName = new Map<string, SebiFundRankingRow>();
    [...this.sebiQ1Funds, ...(this.sebiRankings?.results || [])].forEach(row => {
      const name = String(row.Scheme_Name || '').trim();
      if (!name || (this.selectedSebiPlan && row.Plan !== this.selectedSebiPlan)) return;
      if (this.selectedSebiCategory !== 'All categories' &&
          row.Level_3_Category !== this.selectedSebiCategory) return;
      if (!byName.has(name)) byName.set(name, row);
    });
    return [...byName.values()];
  }

  get selectedSebiFund(): SebiFundRankingRow | null {
    return this.sebiFundOptions.find(row => row.Scheme_Name === this.selectedSebiFundName) ||
      this.sebiFundOptions[0] || null;
  }

  get selectedSebiBenchmark(): SebiCategorySummaryRow | null {
    const fund = this.selectedSebiFund;
    const category = fund?.Level_3_Category ||
      (this.selectedSebiCategory !== 'All categories' ? this.selectedSebiCategory : '');
    const plan = fund?.Plan || this.selectedSebiPlan;
    return category ? (this.sebiSummary?.tree_summary || []).find(row =>
      row.Level_3_Category === category && row.Plan === plan
    ) || null : null;
  }

  get sebiFundComparisonRows(): any[] {
    const fund = this.selectedSebiFund;
    const benchmark = this.selectedSebiBenchmark;
    if (!fund || !benchmark) return [];
    const metrics: any[] = [
      { metric: '1Y CAGR', value: fund.CAGR_1Y_pct, average: benchmark.Category_Avg_CAGR_1Y_pct },
      { metric: '3Y CAGR', value: fund.CAGR_3Y_pct, average: benchmark.Category_Avg_CAGR_3Y_pct },
      { metric: '5Y CAGR', value: fund.CAGR_5Y_pct, average: benchmark.Category_Avg_CAGR_5Y_pct },
      { metric: '3Y rolling CAGR', value: fund.Rolling_3Y_CAGR_Mean_pct, average: benchmark.Category_Avg_Rolling_3Y_CAGR_Mean_pct },
      { metric: 'Annualized volatility', value: fund.Annualized_Volatility_pct, average: benchmark.Category_Avg_Annualized_Volatility_pct },
      { metric: 'Sharpe ratio', value: fund.Sharpe_Ratio, average: benchmark.Category_Avg_Sharpe_Ratio, kind: 'number' },
      { metric: 'Maximum drawdown', value: fund.Max_Drawdown_pct, average: benchmark.Category_Avg_Max_Drawdown_pct },
    ];
    return metrics.map(row => {
      const value = this.finiteNumber(row.value);
      const average = this.finiteNumber(row.average);
      return { ...row, kind: row.kind || 'percent',
        difference: value !== null && average !== null ? value - average : null };
    });
  }

  get sebiVisibleTreeRows(): SebiCategorySummaryRow[] {
    return (this.sebiSummary?.tree_summary || []).filter(row =>
      (!this.selectedSebiPlan || row.Plan === this.selectedSebiPlan) &&
      (this.selectedSebiCategory === 'All categories' || row.Level_3_Category === this.selectedSebiCategory));
  }

  get sebiVisibleQ1Funds(): SebiFundRankingRow[] {
    return this.sebiQ1Funds.filter(row =>
      (!this.selectedSebiPlan || row.Plan === this.selectedSebiPlan) &&
      (this.selectedSebiCategory === 'All categories' || row.Level_3_Category === this.selectedSebiCategory));
  }

  get sebiVisibleRankings(): SebiFundRankingRow[] {
    return (this.sebiRankings?.results || []).filter(row =>
      (!this.selectedSebiPlan || row.Plan === this.selectedSebiPlan) &&
      (this.selectedSebiCategory === 'All categories' || row.Level_3_Category === this.selectedSebiCategory));
  }

  onSebiPlanChange(plan: string): void {
    this.selectedSebiPlan = plan;
    this.selectedSebiCategory = 'All categories';
    this.syncSelectedSebiFund();
    this.buildSebiDashboard();
  }

  onSebiCategoryChange(category: string): void {
    this.selectedSebiCategory = category;
    this.syncSelectedSebiFund();
    this.buildSebiDashboard();
  }

  onSebiFundChange(name: string): void { this.selectedSebiFundName = name; }

  private syncSelectedSebiFund(): void {
    if (!this.sebiFundOptions.some(row => row.Scheme_Name === this.selectedSebiFundName)) {
      this.selectedSebiFundName = this.sebiFundOptions[0]?.Scheme_Name || '';
    }
  }

  private initializeSebiDashboard(): void {
    const plans = this.sebiPlanOptions;
    if (!plans.includes(this.selectedSebiPlan)) {
      this.selectedSebiPlan = plans.find(plan => /direct/i.test(plan)) || plans[0] || '';
    }
    if (!this.sebiCategoryOptions.includes(this.selectedSebiCategory)) {
      this.selectedSebiCategory = 'All categories';
    }
    this.syncSelectedSebiFund();
    this.buildSebiDashboard();
  }

  private buildSebiDashboard(): void {
    const categoryRows = (this.sebiSummary?.tree_summary || [])
      .filter(row => !this.selectedSebiPlan || row.Plan === this.selectedSebiPlan)
      .filter(row => String(row.Level_3_Category || '').trim())
      .map(row => ({
        ...row,
        categoryName: String(row.Level_3_Category || ''),
        fundCount: this.finiteNumber(row.Fund_Count),
        cagr1Y: this.finiteNumber(row.Category_Avg_CAGR_1Y_pct),
        cagr3Y: this.finiteNumber(row.Category_Avg_CAGR_3Y_pct),
        cagr5Y: this.finiteNumber(row.Category_Avg_CAGR_5Y_pct),
        volatility: this.finiteNumber(row.Category_Avg_Annualized_Volatility_pct),
        sharpe: this.finiteNumber(row.Category_Avg_Sharpe_Ratio),
        maxDrawdown: this.finiteNumber(row.Category_Avg_Max_Drawdown_pct),
      }));
    const avg = (key: string): number | null =>
      this.mean(categoryRows.map(row => this.finiteNumber((row as any)[key])));
    const valid3Y = categoryRows.map(row => row.cagr3Y)
      .filter((value): value is number => value !== null).sort((a, b) => a - b);
    const median3Y = valid3Y.length
      ? (valid3Y.length % 2 ? valid3Y[Math.floor(valid3Y.length / 2)]
        : (valid3Y[valid3Y.length / 2 - 1] + valid3Y[valid3Y.length / 2]) / 2)
      : null;
    const strongestCategory = [...categoryRows].filter(row => row.cagr3Y !== null)
      .sort((a, b) => (b.cagr3Y || 0) - (a.cagr3Y || 0))[0] || null;
    const lowestRiskCategory = [...categoryRows].filter(row => row.volatility !== null)
      .sort((a, b) => (a.volatility || 0) - (b.volatility || 0))[0] || null;

    const points = categoryRows.filter(row => row.cagr3Y !== null && row.volatility !== null);
    const plot = { left: 70, right: 870, top: 24, bottom: 255 };
    const riskReturnPoints: any[] = [];
    let scatterXTicks: any[] = [];
    let scatterYTicks: any[] = [];
    if (points.length) {
      const xMin = Math.min(...points.map(row => row.volatility as number));
      const xMax = Math.max(...points.map(row => row.volatility as number));
      const yMin = Math.min(...points.map(row => row.cagr3Y as number));
      const yMax = Math.max(...points.map(row => row.cagr3Y as number));
      const xSpan = xMax === xMin ? 1 : xMax - xMin;
      const ySpan = yMax === yMin ? 1 : yMax - yMin;
      const labelNames = new Set([...points].sort((a, b) => (b.cagr3Y || 0) - (a.cagr3Y || 0))
        .slice(0, 8).map(row => row.categoryName));
      points.forEach(row => {
        const label = row.categoryName.length > 20 ? row.categoryName.slice(0, 18) + '…' : row.categoryName;
        riskReturnPoints.push({
          name: row.categoryName, label: labelNames.has(row.categoryName) ? label : '',
          cx: plot.left + ((row.volatility as number) - xMin) / xSpan * (plot.right - plot.left),
          cy: plot.bottom - ((row.cagr3Y as number) - yMin) / ySpan * (plot.bottom - plot.top),
          cagr3Y: row.cagr3Y, volatility: row.volatility,
        });
      });
      scatterXTicks = Array.from({ length: 5 }, (_item, i) => ({
        value: xMin + xSpan * i / 4, x: plot.left + (plot.right - plot.left) * i / 4,
      }));
      scatterYTicks = Array.from({ length: 5 }, (_item, i) => ({
        value: yMin + ySpan * i / 4, y: plot.bottom - (plot.bottom - plot.top) * i / 4,
      }));
    }

    const returnSource = [...categoryRows].filter(row =>
      row.cagr1Y !== null || row.cagr3Y !== null || row.cagr5Y !== null)
      .sort((a, b) => (b.cagr3Y || -Infinity) - (a.cagr3Y || -Infinity)).slice(0, 7);
    const maxReturn = Math.max(1, ...returnSource.flatMap(row =>
      [row.cagr1Y, row.cagr3Y, row.cagr5Y].map(value => Math.max(0, value || 0))));
    const returnComparisonRows = returnSource.map(row => ({
      category: row.categoryName, cagr1Y: row.cagr1Y, cagr3Y: row.cagr3Y, cagr5Y: row.cagr5Y,
      width1Y: Math.max(0, row.cagr1Y || 0) / maxReturn * 100,
      width3Y: Math.max(0, row.cagr3Y || 0) / maxReturn * 100,
      width5Y: Math.max(0, row.cagr5Y || 0) / maxReturn * 100,
    }));

    const q1Source = this.sebiVisibleQ1Funds.filter(row => this.finiteNumber(row.CAGR_3Y_pct) !== null)
      .sort((a, b) => this.numeric(b.CAGR_3Y_pct) - this.numeric(a.CAGR_3Y_pct)).slice(0, 10);
    const maxQ1 = Math.max(1, ...q1Source.flatMap(row => [
      Math.max(0, this.numeric(row.CAGR_3Y_pct)), Math.max(0, this.numeric(row.Category_Avg_CAGR_3Y_pct))]));
    const q1ComparisonRows = q1Source.map(row => ({
      name: row.Scheme_Name || 'Unnamed scheme',
      label: (row.Scheme_Name || 'Unnamed scheme').length > 30
        ? (row.Scheme_Name || '').slice(0, 28) + '…' : row.Scheme_Name,
      cagr3Y: this.finiteNumber(row.CAGR_3Y_pct),
      categoryAverage: this.finiteNumber(row.Category_Avg_CAGR_3Y_pct),
      fundWidth: Math.max(0, this.numeric(row.CAGR_3Y_pct)) / maxQ1 * 100,
      averageWidth: Math.max(0, this.numeric(row.Category_Avg_CAGR_3Y_pct)) / maxQ1 * 100,
    }));

    const coverageSource = [...categoryRows].filter(row => row.fundCount !== null)
      .sort((a, b) => (b.fundCount || 0) - (a.fundCount || 0)).slice(0, 10);
    const maxCoverage = Math.max(1, ...coverageSource.map(row => row.fundCount || 0));
    const coverageRows = coverageSource.map(row => ({
      category: row.categoryName, fundCount: row.fundCount,
      width: (row.fundCount || 0) / maxCoverage * 100,
    }));

    const riskSource = [...categoryRows].filter(row => row.volatility !== null || row.maxDrawdown !== null)
      .sort((a, b) => Math.abs(b.volatility || 0) - Math.abs(a.volatility || 0)).slice(0, 10);
    const maxRisk = Math.max(1, ...riskSource.flatMap(row =>
      [Math.abs(row.volatility || 0), Math.abs(row.maxDrawdown || 0)]));
    const riskRows = riskSource.map(row => ({
      category: row.categoryName, volatility: row.volatility, maxDrawdown: row.maxDrawdown,
      volatilityWidth: Math.abs(row.volatility || 0) / maxRisk * 100,
      drawdownWidth: Math.abs(row.maxDrawdown || 0) / maxRisk * 100,
    }));

    const sharpeSource = [...categoryRows].filter(row => row.sharpe !== null)
      .sort((a, b) => (b.sharpe || 0) - (a.sharpe || 0)).slice(0, 10);
    const maxSharpe = Math.max(1, ...sharpeSource.map(row => Math.max(0, row.sharpe || 0)));
    const sharpeRows = sharpeSource.map(row => ({
      category: row.categoryName, sharpe: row.sharpe,
      width: Math.max(0, row.sharpe || 0) / maxSharpe * 100,
    }));

    this.sebiDashboard = {
      categoryRows, categoryCount: categoryRows.length,
      average3Y: avg('cagr3Y'), median3Y, average1Y: avg('cagr1Y'), average5Y: avg('cagr5Y'),
      averageVolatility: avg('volatility'), averageSharpe: avg('sharpe'),
      averageMaxDrawdown: avg('maxDrawdown'), strongestCategory, lowestRiskCategory,
      riskReturnPoints, scatterPlot: plot, scatterXTicks, scatterYTicks,
      q1ComparisonRows, returnComparisonRows, coverageRows, riskRows, sharpeRows,
    };
  }

  get sebiQ1Funds(): SebiFundRankingRow[] {
    return this.sebiSummary?.top_q1_funds || [];
  }

  private replaceChartObjectUrl(kind: 'category' | 'q1', blob: Blob | null): void {
    const previous = kind === 'category'
      ? this.sebiCategoryRiskChartSrc
      : this.sebiTopQ1ChartSrc;
    if (previous) URL.revokeObjectURL(previous);

    const next = blob ? URL.createObjectURL(blob) : null;
    if (kind === 'category') {
      this.sebiCategoryRiskChartSrc = next;
    } else {
      this.sebiTopQ1ChartSrc = next;
    }
  }

  ngOnDestroy(): void {
    if (this.sebiCategoryRiskChartSrc) URL.revokeObjectURL(this.sebiCategoryRiskChartSrc);
    if (this.sebiTopQ1ChartSrc) URL.revokeObjectURL(this.sebiTopQ1ChartSrc);
  }

  get sebiWorkbookUrl(): string {
    return this.analytics.getSebiWorkbookUrl();
  }

  formatNumber(value: any, digits = 2): string {
    if (value === null || value === undefined || value === '') return '—';
    const number = Number(value);
    return Number.isFinite(number)
      ? new Intl.NumberFormat('en-IN', { maximumFractionDigits: digits }).format(number)
      : '—';
  }

  usagePercent(): number {
    if (!this.apiUsage?.monthly_budget) return 0;
    return Math.max(0, Math.min(100,
      this.apiUsage.requests_used / this.apiUsage.monthly_budget * 100));
  }

  formatMoney(value: any): string {
    if (value === null || value === undefined || value === '') return '—';
    const amount = Number(value);
    return Number.isFinite(amount)
      ? new Intl.NumberFormat('en-IN', {
        style: 'currency', currency: 'INR', maximumFractionDigits: 0,
      }).format(amount)
      : '—';
  }

  get raw(): any { return this.report?.payload?.RAW || null; }
  get p2p(): any { return this.report?.payload?.P2P || {}; }
  get attr(): any { return this.report?.payload?.ATTR || {}; }
  get funds(): any[] { return this.raw?.funds || []; }
  get category(): any { return this.raw?.cat || {}; }
  get selectedFund(): any {
    return this.funds.find(f => f.name === this.selectedFundName) || this.funds[0] || null;
  }
  get selectedAttribution(): any {
    return this.selectedFund ? this.attr?.per_fund?.[this.selectedFund.name] || null : null;
  }
  get edgeRanked(): any[] {
    return [...this.funds].sort((a, b) => this.numeric(b.edge_score) - this.numeric(a.edge_score));
  }
  get rankedFunds(): any[] {
    return [...this.funds].filter(f => this.fundReturn(f, this.activePeriod) !== null)
      .sort((a, b) => this.fundReturn(b, this.activePeriod)! - this.fundReturn(a, this.activePeriod)!);
  }
  get positioningFunds(): any[] {
    return this.funds.filter(f => this.axisValue(f, this.xAxis) !== null && this.axisValue(f, this.yAxis) !== null)
      .sort((a, b) => this.numeric(b[this.yAxis]) - this.numeric(a[this.yAxis]));
  }
  get sectorRows(): Array<{ name: string; value: number }> {
    return Object.entries(this.selectedFund?.sectors || {})
      .map(([name, value]) => ({ name, value: this.numeric(value) }))
      .sort((a, b) => b.value - a.value);
  }
  get selectedFundTrend(): any[] {
    const fund = this.selectedFund;
    if (!fund) return [];
    return (fund.ts_large || []).map((large: any, index: number) => ({
      month: this.raw?.months?.[index] || String(index + 1),
      large,
      mid: fund.ts_mid?.[index] ?? null,
      small: fund.ts_small?.[index] ?? null,
      cash: fund.ts_cash?.[index] ?? null,
      pe: fund.pe_ts?.[index] ?? null,
      aum: fund.aum_ts?.[index] ?? null,
    }));
  }
  get topSectorRows(): Array<{ sector: string; total_contrib: number; fund_count: number; stocks: number; avg_pr: number }> {
    return [...(this.attr?.category_sector || [])].sort((a, b) => this.numeric(b.total_contrib) - this.numeric(a.total_contrib)).slice(0, 12);
  }
  private finiteNumber(value: any): number | null {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  private mean(values: Array<number | null>): number | null {
    const valid = values.filter((value): value is number => value !== null && Number.isFinite(value));
    return valid.length ? valid.reduce((sum, value) => sum + value, 0) / valid.length : null;
  }

  private buildOverviewDashboard(): any {
    const funds = this.funds || [];
    const fundCount = funds.length;

    // AUM-weighted P/E across funds with both a valid P/E and positive AUM.
    const weightedPeRows = funds
      .map(fund => ({ pe: this.finiteNumber(fund?.pe), aum: this.finiteNumber(fund?.aum) }))
      .filter((row): row is { pe: number; aum: number } => row.pe !== null && row.aum !== null && row.aum > 0);
    const totalPeWeight = weightedPeRows.reduce((sum, row) => sum + (row.aum || 0), 0);
    const weightedPe = totalPeWeight > 0
      ? weightedPeRows.reduce((sum, row) => sum + (row.pe || 0) * (row.aum || 0), 0) / totalPeWeight
      : null;

    // Rebase the equal-weighted Large/Mid/Small average to 100; cash is excluded.
    const capRows = funds.map(fund => ({
      large: this.finiteNumber(fund?.large),
      mid: this.finiteNumber(fund?.mid),
      small: this.finiteNumber(fund?.small),
    })).filter(row => row.large !== null && row.mid !== null && row.small !== null);
    const capAverages = {
      large: this.mean(capRows.map(row => row.large)),
      mid: this.mean(capRows.map(row => row.mid)),
      small: this.mean(capRows.map(row => row.small)),
    };
    const capTotal = (capAverages.large || 0) + (capAverages.mid || 0) + (capAverages.small || 0);
    const marketCapSplit: any[] = capTotal > 0 ? [
      { key: 'large', label: 'Large Cap', value: (capAverages.large || 0) / capTotal * 100, x: 0, width: 0, color: 'var(--chart-series-1)' },
      { key: 'mid', label: 'Mid Cap', value: (capAverages.mid || 0) / capTotal * 100, x: 0, width: 0, color: 'var(--chart-series-2)' },
      { key: 'small', label: 'Small Cap', value: (capAverages.small || 0) / capTotal * 100, x: 0, width: 0, color: 'var(--chart-series-3)' },
    ] : [];
    let capOffset = 34;
    const capChartWidth = 980;
    marketCapSplit.forEach(segment => {
      segment.x = capOffset;
      segment.width = capChartWidth * segment.value / 100;
      capOffset += segment.width;
    });

    const styleFields = [
      { key: 'cyclical', label: 'Cyclical', color: 'var(--chart-series-1)' },
      { key: 'defensive', label: 'Defensive', color: 'var(--chart-series-2)' },
      { key: 'sensitive', label: 'Sensitive', color: 'var(--chart-series-3)' },
    ];
    const styleComposition = styleFields.map(item => ({
      ...item,
      value: this.mean(funds.map(fund => this.finiteNumber(fund?.[item.key]))),
      width: 0,
    }));
    const styleMax = Math.max(0, ...styleComposition.map(item => item.value || 0));
    styleComposition.forEach(item => item.width = styleMax > 0 ? (item.value || 0) / styleMax * 100 : 0);

    const periodValues: Record<string, number[]> = {
      '3M': funds.map(fund => this.fundReturn(fund, '3M')).filter((value): value is number => value !== null),
      '6M': funds.map(fund => this.fundReturn(fund, '6M')).filter((value): value is number => value !== null),
      '1Y': funds.map(fund => this.fundReturn(fund, '1Y')).filter((value): value is number => value !== null),
    };
    const allReturns = Object.values(periodValues).flat();
    let returnDistribution: any[] = [];
    if (allReturns.length) {
      const minimum = Math.min(...allReturns);
      const maximum = Math.max(...allReturns);
      const span = Math.max(10, maximum - minimum);
      let step = span > 100 ? 20 : span > 55 ? 10 : 5;
      while (Math.ceil(span / step) > 14) step *= 2;
      const start = Math.floor(minimum / step) * step;
      const end = Math.max(start + step, Math.ceil(maximum / step) * step);
      const binCount = Math.max(1, Math.ceil((end - start) / step));
      const labels = Array.from({ length: binCount }, (_item, index) => {
        const lower = start + index * step;
        const upper = lower + step;
        return lower.toFixed(0) + ' to ' + upper.toFixed(0) + '%';
      });
      const counts: Record<string, number[]> = {
        '3M': Array(binCount).fill(0),
        '6M': Array(binCount).fill(0),
        '1Y': Array(binCount).fill(0),
      };
      Object.entries(periodValues).forEach(([period, values]) => {
        values.forEach(value => {
          const index = Math.max(0, Math.min(binCount - 1, Math.floor((value - start) / step)));
          counts[period][index] += 1;
        });
      });
      const maximumCount = Math.max(1, ...Object.values(counts).flat());
      const plot = { left: 62, baseY: 238, height: 184, width: 982 };
      const groupWidth = plot.width / binCount;
      const barGap = 3;
      const barWidth = Math.min(19, Math.max(7, (groupWidth - 18) / 3));
      const barTotalWidth = barWidth * 3 + barGap * 2;
      returnDistribution = labels.map((label, index) => {
        const groupLeft = plot.left + index * groupWidth;
        const firstX = groupLeft + (groupWidth - barTotalWidth) / 2;
        const bars = (['3M', '6M', '1Y'] as const).map((period, seriesIndex) => {
          const count = counts[period][index];
          const height = count / maximumCount * plot.height;
          return {
            period,
            count,
            x: firstX + seriesIndex * (barWidth + barGap),
            y: plot.baseY - height,
            width: barWidth,
            height,
            color: seriesIndex === 0 ? 'var(--chart-series-1)' :
              seriesIndex === 1 ? 'var(--chart-series-2)' : 'var(--chart-series-3)',
          };
        });
        return { label, centerX: groupLeft + groupWidth / 2, bars };
      });
    }

    const topAumFunds: any[] = funds
      .map(fund => ({ name: fund?.short || fund?.name || 'Unnamed fund', fullName: fund?.name || 'Unnamed fund', aum: this.finiteNumber(fund?.aum) }))
      .filter(row => row.aum !== null && row.aum >= 0)
      .sort((a, b) => (b.aum || 0) - (a.aum || 0))
      .slice(0, 12);
    const maxAum = Math.max(1, ...topAumFunds.map(row => row.aum || 0));
    const aumPlot = { left: 44, baseY: 235, height: 172, width: 980 };
    const aumGroupWidth = aumPlot.width / Math.max(1, topAumFunds.length);
    topAumFunds.forEach((row, index) => {
      row.x = aumPlot.left + index * aumGroupWidth + aumGroupWidth * 0.17;
      row.width = aumGroupWidth * 0.66;
      row.height = (row.aum || 0) / maxAum * aumPlot.height;
      row.y = aumPlot.baseY - row.height;
      row.centerX = row.x + row.width / 2;
      row.label = row.name.length > 15 ? row.name.slice(0, 14) + '…' : row.name;
    });

    const drawdownPoints: any[] = funds.map(fund => ({
      name: fund?.short || fund?.name || 'Unnamed fund',
      drawdown: this.finiteNumber(fund?.max_drawdown),
      calmar: this.finiteNumber(fund?.calmar),
    })).filter(row => row.drawdown !== null && row.calmar !== null);
    const scatterPlot = { left: 76, right: 1034, top: 22, bottom: 292 };
    let scatterXTicks: any[] = [];
    let scatterYTicks: any[] = [];
    if (drawdownPoints.length) {
      let xMin = Math.min(...drawdownPoints.map(row => row.drawdown as number));
      let xMax = Math.max(...drawdownPoints.map(row => row.drawdown as number));
      let yMin = Math.min(0, ...drawdownPoints.map(row => row.calmar as number));
      let yMax = Math.max(...drawdownPoints.map(row => row.calmar as number));
      if (xMin === xMax) { xMin -= 1; xMax += 1; }
      if (yMin === yMax) { yMin -= 1; yMax += 1; }
      const xSpan = xMax - xMin;
      const ySpan = yMax - yMin;
      drawdownPoints.forEach(row => {
        row.cx = scatterPlot.left + ((row.drawdown as number) - xMin) / xSpan *
          (scatterPlot.right - scatterPlot.left);
        row.cy = scatterPlot.bottom - ((row.calmar as number) - yMin) / ySpan *
          (scatterPlot.bottom - scatterPlot.top);
      });
      scatterXTicks = Array.from({ length: 5 }, (_item, index) => {
        const value = xMin + xSpan * index / 4;
        return { value, x: scatterPlot.left + (scatterPlot.right - scatterPlot.left) * index / 4 };
      });
      scatterYTicks = Array.from({ length: 5 }, (_item, index) => {
        const value = yMin + ySpan * index / 4;
        return { value, y: scatterPlot.bottom - (scatterPlot.bottom - scatterPlot.top) * index / 4 };
      });
    }

    const sectorFunds = funds.map(fund => {
      const entries = Object.entries(fund?.sectors || {})
        .map(([sector, rawValue]) => ({ sector, value: this.finiteNumber(rawValue) }))
        .filter((row): row is { sector: string; value: number } => row.value !== null);
      return { fund, entries };
    }).filter(row => row.entries.length > 0);
    const sectorTotals = new Map<string, number>();
    const sectorCounts = new Map<string, number>();
    sectorFunds.forEach(row => row.entries.forEach(entry => {
      sectorTotals.set(entry.sector, (sectorTotals.get(entry.sector) || 0) + entry.value);
      sectorCounts.set(entry.sector, (sectorCounts.get(entry.sector) || 0) + 1);
    }));
    const sectorOptions = [...sectorTotals.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([sector]) => sector);
    if (!sectorOptions.includes(this.selectedSectorName)) {
      this.selectedSectorName = sectorOptions[0] || '';
    }
    const selectedSector = this.selectedSectorName;
    const sectorAverage = selectedSector && sectorCounts.get(selectedSector)
      ? (sectorTotals.get(selectedSector) || 0) / (sectorCounts.get(selectedSector) || 1)
      : null;
    const sectorExposureRows: any[] = sectorFunds
      .map(row => {
        const exposure = row.entries.find(entry => entry.sector === selectedSector);
        if (!exposure) return null;
        return {
          name: row.fund?.short || row.fund?.name || 'Unnamed fund',
          fullName: row.fund?.name || 'Unnamed fund',
          manager: row.fund?.manager || '—',
          sector: selectedSector,
          allocation: exposure.value,
          vsCategory: sectorAverage === null ? null : exposure.value - sectorAverage,
          return1Y: this.fundReturn(row.fund, '1Y'),
          edgeScore: this.finiteNumber(row.fund?.edge_score),
        };
      })
      .filter((row): row is NonNullable<typeof row> => row !== null)
      .sort((a, b) => b.allocation - a.allocation);
    const maxSectorAllocation = Math.max(1, ...sectorExposureRows.map(row => row.allocation));
    sectorExposureRows.forEach(row => row.barWidth = row.allocation / maxSectorAllocation * 100);

    const average3M = this.finiteNumber(this.categoryPeriodReturn('3M')) ??
      this.mean(periodValues['3M']);
    const averageSharpe = this.mean(funds.map(fund => this.finiteNumber(fund?.sharpe_calc))) ??
      this.finiteNumber(this.category?.sharpe_calc);
    const averageMaxDrawdown = this.mean(funds.map(fund => this.finiteNumber(fund?.max_drawdown))) ??
      this.finiteNumber(this.category?.max_drawdown);

    return {
      fundCount,
      weightedPe,
      weightedPeCoverage: weightedPeRows.length,
      largeCapRebased: marketCapSplit.length ? marketCapSplit[0].value : null,
      capCoverage: capRows.length,
      marketCapSplit,
      styleComposition,
      hasStyleValues: styleComposition.some(item => item.value !== null),
      styleCoverage: funds.filter(fund =>
        ['cyclical', 'defensive', 'sensitive'].some(key => this.finiteNumber(fund?.[key]) !== null)
      ).length,
      average3M,
      returnDistribution,
      returnCoverage: {
        '3M': periodValues['3M'].length,
        '6M': periodValues['6M'].length,
        '1Y': periodValues['1Y'].length,
      },
      averageSharpe,
      sharpeCoverage: funds.filter(fund => this.finiteNumber(fund?.sharpe_calc) !== null).length,
      averageMaxDrawdown,
      drawdownCoverage: funds.filter(fund => this.finiteNumber(fund?.max_drawdown) !== null).length,
      topAumFunds,
      drawdownPoints,
      scatterPlot,
      scatterXTicks,
      scatterYTicks,
      sectorExposureRows,
      sectorOptions,
      selectedSector,
      sectorCoverage: sectorExposureRows.length,
      sectorUniverseCoverage: sectorFunds.length,
      sectorMaxAllocation: maxSectorAllocation,
    };
  }

  formatPercentagePoints(value: any): string {
    if (value === null || value === undefined || value === '') return '—';
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return (number > 0 ? '+' : '') + number.toFixed(2) + ' pp';
  }

  get selectedReturnRanks(): Record<string, number | null> {
    const result: Record<string, number | null> = {};
    for (const p of this.periods) {
      const values = this.funds.map(f => this.fundReturn(f, p)).filter((v): v is number => v !== null).sort((a, b) => b - a);
      const value = this.selectedFund ? this.fundReturn(this.selectedFund, p) : null;
      result[p] = value === null ? null : values.findIndex(v => v === value) + 1;
    }
    return result;
  }

  indiaFundReturn(fund: any, period: string): number | null {
    const value = fund?.returns?.[period];
    return value === null || value === undefined || value === '' ? null : this.numeric(value);
  }

  fundReturn(fund: any, period: string): number | null {
    const key = ({ '1M': 'r1m', '3M': 'r3m', '6M': 'r6m', '1Y': 'r1y', '3Y': 'r3y', '5Y': 'r5y', 'YTD': 'ytd' } as Record<string, string>)[period];
    const p2pFund = this.p2p?.funds?.[fund?.name];
    if (!p2pFund) return null;
    const value = p2pFund[key];
    return value === null || value === undefined || value === '' ? null : this.numeric(value);
  }

  axisValue(fund: any, key: string): number | null {
    const value = fund?.[key];
    return value === null || value === undefined || value === '' ? null : this.numeric(value);
  }

  quartile(fund: any, period: string): string {
    const value = this.fundReturn(fund, period);
    if (value === null) return '—';
    const values = this.funds.map(f => this.fundReturn(f, period)).filter((v): v is number => v !== null).sort((a, b) => b - a);
    const rank = values.findIndex(v => v === value) + 1;
    if (!rank || !values.length) return '—';
    return rank <= Math.ceil(values.length / 4) ? 'Q1' :
      rank <= Math.ceil(values.length / 2) ? 'Q2' :
      rank <= Math.ceil(values.length * 3 / 4) ? 'Q3' : 'Q4';
  }

  numeric(value: any): number {
    const n = Number(value);
    return Number.isFinite(n) ? n : 0;
  }
  format(value: any, digits = 2): string {
    if (value === null || value === undefined || value === '') return '—';
    const n = Number(value);
    return Number.isFinite(n) ? n.toFixed(digits) : '—';
  }
  formatPct(value: any): string {
    if (value === null || value === undefined || value === '') return '—';
    const n = Number(value);
    return Number.isFinite(n) ? `${n > 0 ? '+' : ''}${n.toFixed(2)}%` : '—';
  }
  formatAum(value: any): string {
    if (value === null || value === undefined || value === '') return '—';
    return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(Number(value));
  }
  axisLabel(key: string): string {
    return this.axisOptions.find(o => o.key === key)?.label || key;
  }
  benchmarkValue(period: string): any {
    return this.p2p?.bm?.vals?.[period.toLowerCase()] ?? null;
  }
  categoryPeriodReturn(period: string): any {
    const key = ({ '1M': 'r1m', '3M': 'r3m', '6M': 'r6m', '1Y': 'r1y', '3Y': 'r3y', '5Y': 'r5y', 'YTD': 'ytd' } as Record<string, string>)[period];
    return this.p2p?.cat?.[key] ?? null;
  }
  benchmarkCytd(name: string): any {
    return this.raw?.benchmarks?.cytd_bench?.[name] ?? null;
  }
  barWidth(value: any, max: any): number {
    const v = Math.abs(this.numeric(value));
    const m = Math.max(Math.abs(this.numeric(max)), 0.0001);
    return Math.max(2, Math.min(100, v / m * 100));
  }
  trackByName(_index: number, item: any): string { return item?.name || item?.fund_name || item?.company || item?.sector || String(_index); }
}
