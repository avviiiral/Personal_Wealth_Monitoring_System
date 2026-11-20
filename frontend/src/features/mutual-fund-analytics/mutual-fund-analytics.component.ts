import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { catchError, forkJoin, of } from 'rxjs';
import {
  IndiaMutualFundFeedResponse,
  IndianApiUsageResponse,
  PersonalMutualFundPortfolioResponse,
  MutualFundResearchReportResponse,
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
  indiaFeed: IndiaMutualFundFeedResponse | null = null;
  apiUsage: IndianApiUsageResponse | null = null;
  personalPortfolio: PersonalMutualFundPortfolioResponse | null = null;
  sebiSummary: SebiUniverseSummaryResponse | null = null;
  sebiRankings: SebiUniverseRankingsResponse | null = null;
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
    forkJoin({
      report: this.analytics.getResearchReport(),
      indiaFeed: this.analytics.getIndiaMarketFeed().pipe(catchError(() => of(null))),
      apiUsage: this.analytics.getApiUsage().pipe(catchError(() => of(null))),
      personalPortfolio: this.analytics.getPersonalPortfolio().pipe(catchError(() => of(null))),
      sebiSummary: this.analytics.getSebiUniverseSummary().pipe(catchError(() => of(null))),
      sebiRankings: this.analytics.getSebiUniverseRankings({ page_size: 100 }).pipe(catchError(() => of(null))),
    }).subscribe({
      next: value => {
        this.report = value.report;
        this.indiaFeed = value.indiaFeed;
        this.apiUsage = value.apiUsage;
        this.personalPortfolio = value.personalPortfolio;
        this.sebiSummary = value.sebiSummary;
        this.sebiRankings = value.sebiRankings;
        this.overviewDashboard = value.report.available ? this.buildOverviewDashboard() : null;
        if (value.sebiSummary?.available) {
          // The chart files are created by the analytics build, not by GET requests.
          forkJoin({
            categoryRiskChart: this.analytics.getSebiCategoryRiskChart().pipe(catchError(() => of(null))),
            topQ1Chart: this.analytics.getSebiTopQ1Chart().pipe(catchError(() => of(null))),
          }).subscribe(charts => {
            this.replaceChartObjectUrl('category', charts.categoryRiskChart);
            this.replaceChartObjectUrl('q1', charts.topQ1Chart);
          });
        } else {
          // Avoid expected 404 requests before the first successful analytics build.
          this.replaceChartObjectUrl('category', null);
          this.replaceChartObjectUrl('q1', null);
        }
        if (value.report.available && !this.selectedFundName) {
          this.selectedFundName = value.report.payload?.RAW?.funds?.[0]?.name || '';
        }
        if (!value.report.available && this.activeTab === 'overview') {
          this.activeTab = 'sebi-universe';
        }
        this.loading = false;
      },
      error: err => {
        this.error = err?.error?.detail || 'Unable to load mutual-fund research data.';
        this.loading = false;
      },
    });
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
    sectorFunds.forEach(row => row.entries.forEach(entry => {
      sectorTotals.set(entry.sector, (sectorTotals.get(entry.sector) || 0) + entry.value);
    }));
    const sectorAverages = new Map<string, number>();
    sectorTotals.forEach((total, sector) => {
      sectorAverages.set(sector, total / sectorFunds.length);
    });
    const sectorExposureRows: any[] = sectorFunds.map(row => {
      const topSector = [...row.entries].sort((a, b) => b.value - a.value)[0];
      const categoryAverage = sectorAverages.get(topSector.sector) ?? null;
      return {
        name: row.fund?.short || row.fund?.name || 'Unnamed fund',
        fullName: row.fund?.name || 'Unnamed fund',
        manager: row.fund?.manager || '—',
        sector: topSector.sector,
        allocation: topSector.value,
        vsCategory: categoryAverage === null ? null : topSector.value - categoryAverage,
        return1Y: this.fundReturn(row.fund, '1Y'),
        edgeScore: this.finiteNumber(row.fund?.edge_score),
      };
    }).sort((a, b) => b.allocation - a.allocation);
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
      sectorCoverage: sectorFunds.length,
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
