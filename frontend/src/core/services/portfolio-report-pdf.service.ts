import jsPDF from 'jspdf';
import autoTable, { type CellHookData } from 'jspdf-autotable';

/**
 * Portfolio Review PDF export.
 *
 * Phase 1: uses only data PWMS already computes (summary, XIRR,
 * investment summary by asset category/class, allocation/performance
 * by advisor, and the Portfolio page's sub-class holdings table).
 *
 * Deliberately does NOT attempt scheme-level XIRR, credit ratings,
 * sector look-through inside mutual funds, or scheme overlap
 * analysis - those require a market-data vendor feed PWMS doesn't
 * currently have, and fabricating those numbers would misrepresent
 * the portfolio. If/when that data exists, this is the place to add
 * those sections.
 */

const NAVY: [number, number, number] = [11, 24, 53];
const NAVY_SOFT: [number, number, number] = [23, 41, 79];
const ORANGE: [number, number, number] = [227, 111, 66];
const INK: [number, number, number] = [22, 35, 58];
const MUTED: [number, number, number] = [102, 112, 133];
const WHITE: [number, number, number] = [255, 255, 255];
const LIGHT_ROW: [number, number, number] = [247, 248, 250];

const PAGE_W = 297; // A4 landscape, mm
const PAGE_H = 210;
const MARGIN = 16;

export interface AdvisorAllocationRow {
  advisor: string;
  value: number;
  percentage: number;
}

export interface AdvisorPerformanceRow {
  advisor: string;
  invested_value: number;
  current_value: number;
  unrealized_pnl: number;
  pnl_percentage: number;
}

export interface InvestmentSummaryRow {
  asset_category: string;
  asset_class: string;
  current_value: number;
  percentage_of_total: number;
}

export interface SubClassSummaryRow {
  family_name: string;
  sub_class: string;
  quantity?: number;
  invested_value: number;
  current_value: number;
  pnl: number;
  xirr?: number | null;
}

export interface AssetDetailRow {
  family_name: string;
  asset_class: string;
  sub_class: string;
  asset_name: string;
  underlying: string;
  underlying_xirr: number | null;
  isin: string | null;
  advisors: string;
  quantity: number;
  average_cost: number;
  invested_value: number;
  current_price: number;
  current_value: number;
  pnl: number;
  pnl_percentage: number;
  xirr: number | null;
}

export interface SubClassDetail {
  sub_class: string;
  asset_class?: string;
  assets: AssetDetailRow[];
}

export interface PortfolioReviewReportData {
  familyName: string;
  totalWealth: number;
  totalInvested: number;
  totalPnl: number;
  xirrPercentage: number | null;
  investmentSummary: InvestmentSummaryRow[];
  standardAllocations: Record<string, number>;
  advisorAllocation: AdvisorAllocationRow[];
  advisorPerformance: AdvisorPerformanceRow[];
  subClassSummaries: SubClassSummaryRow[];
  subClassDetails: SubClassDetail[];
  /** Optional asset-class scope selected from the Dashboard report filter. */
  reportAssetClass?: string;
  reportLevel?: 'asset_class' | 'sub_class' | 'asset_name' | 'underlying';
  reportScope?: string;
}

function formatInr(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '-';
  }

  // NOT Intl's currency style: the rupee glyph (₹) isn't present in
  // jsPDF's built-in base14 fonts (helvetica/times), so
  // style: 'currency' silently drops it, leaving a bare, ambiguous
  // number. "Rs." is a plain-ASCII prefix every PDF viewer renders
  // correctly without embedding a custom font.
  const formatted = new Intl.NumberFormat('en-IN', {
    maximumFractionDigits: 0,
  }).format(Math.abs(value));

  return `${value < 0 ? '-Rs. ' : 'Rs. '}${formatted}`;
}

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '-';
  }

  return `${value.toFixed(1)}%`;
}

function formatDate(date: Date): string {
  return date
    .toLocaleDateString('en-GB', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    })
    .replace(/ /g, '-');
}

function drawFooter(doc: jsPDF, pageLabel: string) {
  doc.setFont('helvetica', 'normal');
  doc.setFontSize(8);
  doc.setTextColor(...MUTED);
  doc.text(pageLabel, MARGIN, PAGE_H - 8);

  const pageNumber = doc.getNumberOfPages();
  doc.text(String(pageNumber), PAGE_W - MARGIN, PAGE_H - 8, {
    align: 'right',
  });

  doc.setDrawColor(230, 230, 235);
  doc.line(MARGIN, PAGE_H - 12, PAGE_W - MARGIN, PAGE_H - 12);
}

function drawSectionHeader(
  doc: jsPDF,
  title: string,
  subtitle?: string
): number {
  doc.setFont('times', 'normal');
  doc.setFontSize(24);
  doc.setTextColor(...INK);
  doc.text(title, MARGIN, 26);

  doc.setDrawColor(...ORANGE);
  doc.setLineWidth(0.6);
  doc.line(MARGIN, 30, MARGIN + 24, 30);

  let cursorY = 30;

  if (subtitle) {
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(11);
    doc.setTextColor(...ORANGE);
    doc.text(subtitle, MARGIN, 40);
    cursorY = 40;
  }

  return cursorY + 8;
}

function drawCoverPage(
  doc: jsPDF,
  data: PortfolioReviewReportData,
  today: Date
): void {
  doc.setFillColor(...NAVY);
  doc.rect(0, 0, PAGE_W, PAGE_H, 'F');

  // Small orange corner accent (top-right), evoking the source
  // deck's bracket mark without copying any firm's actual logo.
  doc.setDrawColor(...ORANGE);
  doc.setLineWidth(2.2);
  doc.line(PAGE_W - 28, 14, PAGE_W - 14, 14);
  doc.line(PAGE_W - 14, 14, PAGE_W - 14, 28);

  doc.setFont('helvetica', 'normal');
  doc.setFontSize(13);
  doc.setTextColor(...WHITE);
  doc.text('Personal Wealth Monitoring', MARGIN, 20);

  doc.setFont('times', 'normal');
  doc.setFontSize(42);
  const reportTitle = data.reportAssetClass ? data.reportAssetClass + ' Review' : 'Portfolio Review';
  doc.text(reportTitle, MARGIN, 100);

  doc.setFont('helvetica', 'normal');
  doc.setFontSize(13);
  doc.setTextColor(...ORANGE);
  const reportContext = data.reportAssetClass
    ? 'Asset Class: ' + data.reportAssetClass + ' | Family: ' + (data.familyName || 'All Families')
    : 'Family: ' + (data.familyName || 'All Families');
  doc.text(reportContext, MARGIN, 130);

  doc.setDrawColor(...WHITE);
  doc.setLineWidth(0.3);
  doc.line(PAGE_W - 110, PAGE_H - 40, PAGE_W - MARGIN, PAGE_H - 40);

  doc.setFont('helvetica', 'normal');
  doc.setFontSize(10);
  doc.setTextColor(...WHITE);
  doc.text(
    `Report Generated: ${formatDate(today)}`,
    PAGE_W - MARGIN,
    PAGE_H - 30,
    { align: 'right' }
  );

  doc.setFontSize(8);
  doc.setTextColor(180, 190, 205);
  doc.text(
    'This is a system-generated summary of holdings recorded in',
    PAGE_W - MARGIN,
    PAGE_H - 22,
    { align: 'right' }
  );
  doc.text(
    'Personal Wealth Monitoring - not investment advice.',
    PAGE_W - MARGIN,
    PAGE_H - 17,
    { align: 'right' }
  );
}

function getStandardAllocation(
  data: PortfolioReviewReportData,
  category: string
): number {
  const value = Number(data.standardAllocations?.[category]);
  return Number.isFinite(value) ? value : 0;
}

function getAllocationComment(
  percentage: number,
  standard: number
): string {
  const difference = percentage - standard;

  if (Math.abs(difference) <= 2) {
    return 'Neutral';
  }

  return difference > 2
    ? 'Invest Less in Other Asset Category'
    : 'Invest More in this Category';
}

function buildCategoryAllocationRows(data: PortfolioReviewReportData): Array<{
  category: string;
  value: number;
  percentage: number;
  standard: number;
  comment: string;
}> {
  const order: string[] = [];
  const totals = new Map<string, { value: number; percentage: number }>();

  for (const row of data.investmentSummary) {
    const category = (row.asset_category || 'Unassigned').trim() || 'Unassigned';
    if (!totals.has(category)) {
      totals.set(category, { value: 0, percentage: 0 });
      order.push(category);
    }

    const entry = totals.get(category)!;
    entry.value += Number(row.current_value) || 0;
    entry.percentage += Number(row.percentage_of_total) || 0;
  }

  return order
    .map((category) => {
      const entry = totals.get(category)!;
      const percentage = Math.round(entry.percentage * 100) / 100;
      const standard = getStandardAllocation(data, category);
      return {
        category,
        value: entry.value,
        percentage,
        standard,
        comment: getAllocationComment(percentage, standard),
      };
    })
    .filter((row) => row.value > 0);
}

function drawAllocationAnalysisPage(
  doc: jsPDF,
  data: PortfolioReviewReportData
): void {
  const rows = buildCategoryAllocationRows(data);
  if (rows.length === 0) {
    return;
  }

  doc.addPage();
  drawSectionHeader(
    doc,
    'Allocation Analysis',
    'Current portfolio allocation and comparison with Standard Allocation'
  );

  const leftX = MARGIN;
  const rightX = PAGE_W / 2 + 4;
  const chartW = PAGE_W / 2 - MARGIN - 12;
  const barH = 6;
  const rowGap = 12;
  const maxValue = Math.max(...rows.map((row) => row.percentage), 1);

  const drawBarChart = (
    x: number,
    y: number,
    title: string,
    valueFor: (row: typeof rows[number]) => number,
    color: [number, number, number],
    valueSuffix: string
  ) => {
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(10);
    doc.setTextColor(...INK);
    doc.text(title, x, y);

    const labelW = 42;
    const barX = x + labelW;
    const valueX = x + chartW;
    const availableW = chartW - labelW - 14;
    let cursorY = y + 10;

    for (const row of rows) {
      const value = valueFor(row);
      const width = Math.max(0, Math.min(availableW, (value / maxValue) * availableW));

      doc.setFont('helvetica', 'normal');
      doc.setFontSize(7.5);
      doc.setTextColor(...INK);
      doc.text(row.category, x, cursorY + 4.2);

      doc.setFillColor(239, 241, 245);
      doc.roundedRect(barX, cursorY, availableW, barH, 1.2, 1.2, 'F');

      doc.setFillColor(...color);
      if (width > 0) {
        doc.roundedRect(barX, cursorY, width, barH, 1.2, 1.2, 'F');
      }

      doc.setFont('helvetica', 'bold');
      doc.setFontSize(7.5);
      doc.setTextColor(...INK);
      doc.text(value.toFixed(1) + valueSuffix, valueX, cursorY + 4.2, { align: 'right' });
      cursorY += rowGap;
    }
  };

  drawBarChart(leftX, 52, 'Current Allocation', (row) => row.percentage, NAVY, '%');

  const comparisonY = 52;
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(10);
  doc.setTextColor(...INK);
  doc.text('Current vs Standard Allocation', rightX, comparisonY);

  const labelW = 44;
  const barX = rightX + labelW;
  const valueX = rightX + chartW;
  const availableW = chartW - labelW - 14;
  let cursorY = comparisonY + 10;

  for (const row of rows) {
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(7.5);
    doc.setTextColor(...INK);
    doc.text(row.category, rightX, cursorY + 4.2);

    doc.setFillColor(239, 241, 245);
    doc.roundedRect(barX, cursorY, availableW, 4, 0.8, 0.8, 'F');
    doc.setFillColor(...NAVY);
    doc.roundedRect(barX, cursorY, Math.min(availableW, (row.percentage / 100) * availableW), 4, 0.8, 0.8, 'F');

    doc.setFillColor(239, 241, 245);
    doc.roundedRect(barX, cursorY + 5, availableW, 4, 0.8, 0.8, 'F');
    doc.setFillColor(...ORANGE);
    doc.roundedRect(barX, cursorY + 5, Math.min(availableW, (row.standard / 100) * availableW), 4, 0.8, 0.8, 'F');

    doc.setFont('helvetica', 'bold');
    doc.setFontSize(7.5);
    doc.setTextColor(...INK);
    doc.text(row.percentage.toFixed(1) + '% / ' + row.standard.toFixed(1) + '%', valueX, cursorY + 6.8, { align: 'right' });
    cursorY += rowGap + 3;
  }

  const legendY = Math.min(PAGE_H - 25, cursorY + 4);
  doc.setFillColor(...NAVY);
  doc.rect(rightX, legendY, 4, 4, 'F');
  doc.setFont('helvetica', 'normal');
  doc.setFontSize(7.5);
  doc.setTextColor(...MUTED);
  doc.text('Current', rightX + 7, legendY + 3.2);
  doc.setFillColor(...ORANGE);
  doc.rect(rightX + 36, legendY, 4, 4, 'F');
  doc.text('Standard', rightX + 43, legendY + 3.2);

  drawFooter(doc, 'Allocation Analysis');
}
function drawExecutiveSummaryPage(
  doc: jsPDF,
  data: PortfolioReviewReportData
): void {
  doc.addPage();

  drawSectionHeader(
    doc,
    'Executive Summary',
    `Overall Portfolio: ${formatInr(data.totalWealth)}` +
      (data.xirrPercentage !== null
        ? `  |  XIRR: ${formatPercent(data.xirrPercentage)} (Since Inception)`
        : '')
  );

  autoTable(doc, {
    startY: 52,
    margin: { left: MARGIN, right: PAGE_W / 2 + 4 },
    head: [['Portfolio Cashflows', '']],
    body: [
      ['Current Portfolio Value', formatInr(data.totalWealth)],
      ['Net Contribution (Invested)', formatInr(data.totalInvested)],
      ['Total Gain / Loss', formatInr(data.totalPnl)],
      [
        'Since Inception XIRR',
        data.xirrPercentage !== null
          ? formatPercent(data.xirrPercentage)
          : '-',
      ],
    ],
    theme: 'plain',
    headStyles: {
      fillColor: NAVY,
      textColor: WHITE,
      fontStyle: 'bold',
      fontSize: 10,
    },
    bodyStyles: { fontSize: 9.5, textColor: INK },
    alternateRowStyles: { fillColor: LIGHT_ROW },
    styles: { cellPadding: 3 },
  });

  const allocationRows = buildCategoryAllocationRows(data).map((row) => [
    row.category,
    formatInr(row.value),
    formatPercent(row.percentage),
    formatPercent(row.standard),
    row.comment,
  ]);

  autoTable(doc, {
    startY: 52,
    margin: { left: PAGE_W / 2 + 4, right: MARGIN },
    head: [['Asset Allocation', 'Value', '%', 'Standard', 'Comments']],
    body: allocationRows,
    theme: 'plain',
    headStyles: {
      fillColor: NAVY,
      textColor: WHITE,
      fontStyle: 'bold',
      fontSize: 10,
    },
    bodyStyles: { fontSize: 9.5, textColor: INK },
    alternateRowStyles: { fillColor: LIGHT_ROW },
    columnStyles: {
      1: { halign: 'right' },
      2: { halign: 'right' },
      3: { halign: 'right' },
    },
    styles: { cellPadding: 3 },
  });

  drawFooter(doc, 'Executive Summary');
}

function drawInvestmentSummaryPage(
  doc: jsPDF,
  data: PortfolioReviewReportData
): void {
  doc.addPage();

  drawSectionHeader(
    doc,
    'Asset Class Wise Allocation',
    'Current value and portfolio weight by asset category and asset class'
  );

  const sorted = [...data.investmentSummary].sort(
    (a, b) => b.current_value - a.current_value
  );

  autoTable(doc, {
    startY: 46,
    margin: { left: MARGIN, right: MARGIN },
    head: [['Asset Category', 'Asset Class', 'Current Value', 'Allocation %']],
    body: sorted
      .filter((row) => row.current_value > 0)
      .map((row) => [
        row.asset_category,
        row.asset_class,
        formatInr(row.current_value),
        formatPercent(row.percentage_of_total),
      ]),
    theme: 'grid',
    headStyles: {
      fillColor: NAVY,
      textColor: WHITE,
      fontStyle: 'bold',
      fontSize: 9.5,
    },
    bodyStyles: { fontSize: 9, textColor: INK },
    alternateRowStyles: { fillColor: LIGHT_ROW },
    columnStyles: {
      2: { halign: 'right' },
      3: { halign: 'right' },
    },
    styles: { cellPadding: 3.2 },
  });

  drawFooter(doc, 'Asset Class Wise Allocation');
}

function drawHoldingsPage(
  doc: jsPDF,
  data: PortfolioReviewReportData
): void {
  if (data.subClassSummaries.length === 0) {
    return;
  }

  doc.addPage();

  drawSectionHeader(
    doc,
    'Holdings by Sub Class',
    'Top-level roll-up of every holding, grouped by Sub Class'
  );

  autoTable(doc, {
    startY: 46,
    margin: { left: MARGIN, right: MARGIN },
    head: [
      [
        'Family Name',
        'Sub Class',
        'Invested Value',
        'Current Value',
        'Gain / Loss',
        'XIRR',
      ],
    ],
    body: data.subClassSummaries.map((row) => [
      row.family_name,
      row.sub_class,
      formatInr(row.invested_value),
      formatInr(row.current_value),
      formatInr(row.pnl),
      row.xirr !== null && row.xirr !== undefined
        ? formatPercent(row.xirr)
        : '-',
    ]),
    theme: 'grid',
    headStyles: {
      fillColor: NAVY,
      textColor: WHITE,
      fontStyle: 'bold',
      fontSize: 9.5,
    },
    bodyStyles: { fontSize: 9, textColor: INK },
    alternateRowStyles: { fillColor: LIGHT_ROW },
    columnStyles: {
      2: { halign: 'right' },
      3: { halign: 'right' },
      4: { halign: 'right' },
      5: { halign: 'right' },
    },
    styles: { cellPadding: 3.2 },
    didParseCell: (hookData: CellHookData) => {
      if (hookData.section !== 'body' || hookData.column.index !== 4) {
        return;
      }

      const raw = data.subClassSummaries[hookData.row.index]?.pnl ?? 0;

      hookData.cell.styles.textColor =
        raw >= 0 ? [15, 122, 92] : [180, 35, 24];
    },
  });

  drawFooter(doc, 'Holdings by Sub Class');
}

function normalized(value: string | null | undefined): string {
  return (value || '').trim().toLowerCase();
}

function isPmsDetail(detail: SubClassDetail): boolean {
  return normalized(detail.asset_class) === 'equity' && normalized(detail.sub_class).includes('pms');
}

function isDirectEquityDetail(detail: SubClassDetail): boolean {
  return normalized(detail.asset_class) === 'equity' && normalized(detail.sub_class) === 'direct equity';
}

function drawSchemeDetailPages(doc: jsPDF, data: PortfolioReviewReportData): void {
  for (const detail of data.subClassDetails) {
    if (!detail.assets.length) continue;
    doc.addPage();

    const directEquity = isDirectEquityDetail(detail);
    const pms = isPmsDetail(detail);
    const title = detail.asset_class ? detail.asset_class + ' - ' + detail.sub_class : detail.sub_class;
    drawSectionHeader(doc, title, 'Family-scoped holdings for the selected report hierarchy');
    const assets = [...detail.assets].sort((a, b) => b.current_value - a.current_value);

    if (directEquity) {
      autoTable(doc, {
        startY: 46, margin: { left: MARGIN, right: MARGIN },
        head: [['Family', 'Asset Name', 'Underlying', 'Invested Value', 'Current Value', 'Gain / Loss', 'XIRR']],
        body: assets.map(a => [a.family_name, a.asset_name, a.underlying || '-', formatInr(a.invested_value), formatInr(a.current_value), formatInr(a.pnl), formatPercent(a.underlying_xirr)]),
        theme: 'grid', headStyles: { fillColor: NAVY, textColor: WHITE, fontStyle: 'bold', fontSize: 8.5 },
        bodyStyles: { fontSize: 8, textColor: INK }, alternateRowStyles: { fillColor: LIGHT_ROW },
        columnStyles: { 3: { halign: 'right' }, 4: { halign: 'right' }, 5: { halign: 'right' }, 6: { halign: 'right' } },
        styles: { cellPadding: 2.7 },
      });
    } else if (pms) {
      // Equity PMS is a Sub Class. At Sub Class level the report must
      // present one row per Asset Name (per Family), not one row per
      // individual PMS security/holding record. Quantity, invested
      // value, current value and P&L are therefore aggregated at the
      // Asset Name level. XIRR is the existing Asset Name XIRR
      // (asset_name_xirr), carried into a.xirr by the dashboard builder.
      const grouped = new Map<string, {
        family_name: string;
        asset_name: string;
        quantity: number;
        invested_value: number;
        current_value: number;
        pnl: number;
        xirr: number | null;
      }>();

      for (const asset of assets) {
        const key = asset.family_name + '::' + asset.asset_name;
        const existing = grouped.get(key) ?? {
          family_name: asset.family_name,
          asset_name: asset.asset_name,
          quantity: 0,
          invested_value: 0,
          current_value: 0,
          pnl: 0,
          xirr: asset.xirr ?? null,
        };

        existing.quantity += Number(asset.quantity || 0);
        existing.invested_value += Number(asset.invested_value || 0);
        existing.current_value += Number(asset.current_value || 0);
        existing.pnl += Number(asset.pnl || 0);

        if (
          existing.xirr === null &&
          asset.xirr !== null &&
          Number.isFinite(Number(asset.xirr))
        ) {
          existing.xirr = Number(asset.xirr);
        }

        grouped.set(key, existing);
      }

      const assetNameRows = Array.from(grouped.values()).sort(
        (a, b) => b.current_value - a.current_value
      );

      autoTable(doc, {
        startY: 46, margin: { left: MARGIN, right: MARGIN },
        head: [['Family', 'Asset Name', 'Quantity', 'Invested Value', 'Current Value', 'Gain / Loss', 'XIRR']],
        body: assetNameRows.map(a => [
          a.family_name,
          a.asset_name,
          a.quantity ? a.quantity.toLocaleString('en-IN', { maximumFractionDigits: 3 }) : '-',
          formatInr(a.invested_value),
          formatInr(a.current_value),
          formatInr(a.pnl),
          formatPercent(a.xirr),
        ]),
        theme: 'grid', headStyles: { fillColor: NAVY, textColor: WHITE, fontStyle: 'bold', fontSize: 8.5 },
        bodyStyles: { fontSize: 8, textColor: INK }, alternateRowStyles: { fillColor: LIGHT_ROW },
        columnStyles: { 2: { halign: 'right' }, 3: { halign: 'right' }, 4: { halign: 'right' }, 5: { halign: 'right' }, 6: { halign: 'right' } },
        styles: { cellPadding: 2.7 },
      });
    } else {
      autoTable(doc, {
        startY: 46, margin: { left: MARGIN, right: MARGIN },
        head: [['Family', 'Asset Name', 'Underlying', 'ISIN', 'Invested Value', 'Current Value', 'Gain / Loss', 'XIRR']],
        body: assets.map(a => [a.family_name, a.asset_name, a.underlying || '-', a.isin || '-', formatInr(a.invested_value), formatInr(a.current_value), formatInr(a.pnl), formatPercent(a.xirr)]),
        theme: 'grid', headStyles: { fillColor: NAVY, textColor: WHITE, fontStyle: 'bold', fontSize: 8 },
        bodyStyles: { fontSize: 7.5, textColor: INK }, alternateRowStyles: { fillColor: LIGHT_ROW },
        columnStyles: { 4: { halign: 'right' }, 5: { halign: 'right' }, 6: { halign: 'right' }, 7: { halign: 'right' } },
        styles: { cellPadding: 2.4 },
      });
    }
    drawFooter(doc, title);
  }
}

function drawTopExposuresPage(
  doc: jsPDF,
  data: PortfolioReviewReportData
): void {
  const allAssets = data.subClassDetails.flatMap((s) => s.assets);

  if (allAssets.length === 0) {
    return;
  }

  doc.addPage();

  drawSectionHeader(
    doc,
    'Top Holdings',
    'Largest positions by current value, and best/worst by XIRR'
  );

  const totalValue = allAssets.reduce((sum, a) => sum + a.current_value, 0);

  const topByValue = [...allAssets]
    .sort((a, b) => b.current_value - a.current_value)
    .slice(0, 10);

  autoTable(doc, {
    startY: 46,
    margin: { left: MARGIN, right: PAGE_W / 2 + 4 },
    head: [['Family Name', 'Top 10 by Allocation', 'Current Value', '%']],
    body: topByValue.map((asset) => [
      asset.family_name,
      asset.asset_name,
      formatInr(asset.current_value),
      totalValue
        ? `${((asset.current_value / totalValue) * 100).toFixed(1)}%`
        : '-',
    ]),
    theme: 'grid',
    headStyles: {
      fillColor: NAVY,
      textColor: WHITE,
      fontStyle: 'bold',
      fontSize: 8.5,
    },
    bodyStyles: { fontSize: 8, textColor: INK },
    alternateRowStyles: { fillColor: LIGHT_ROW },
    columnStyles: { 2: { halign: 'right' }, 3: { halign: 'right' } },
    styles: { cellPadding: 2.6 },
  });

  const withXirr = allAssets.filter(
    (a) => a.xirr !== null && a.xirr !== undefined
  );

  const topByXirr = [...withXirr]
    .sort((a, b) => (b.xirr ?? 0) - (a.xirr ?? 0))
    .slice(0, 10);

  autoTable(doc, {
    startY: 46,
    margin: { left: PAGE_W / 2 + 4, right: MARGIN },
    head: [['Family Name', 'Top 10 by XIRR', 'XIRR', 'Current Value']],
    body: topByXirr.map((asset) => [
      asset.family_name,
      asset.asset_name,
      formatPercent(asset.xirr),
      formatInr(asset.current_value),
    ]),
    theme: 'grid',
    headStyles: {
      fillColor: NAVY,
      textColor: WHITE,
      fontStyle: 'bold',
      fontSize: 8.5,
    },
    bodyStyles: { fontSize: 8, textColor: INK },
    alternateRowStyles: { fillColor: LIGHT_ROW },
    columnStyles: { 2: { halign: 'right' }, 3: { halign: 'right' } },
    styles: { cellPadding: 2.6 },
  });

  if (withXirr.length === 0) {
    doc.setFont('helvetica', 'italic');
    doc.setFontSize(8);
    doc.setTextColor(...MUTED);
    doc.text(
      'No holdings currently have a computed XIRR.',
      PAGE_W / 2 + 4,
      52
    );
  }

  drawFooter(doc, 'Top Holdings');
}

function drawAdvisorPage(
  doc: jsPDF,
  data: PortfolioReviewReportData
): void {
  if (
    data.advisorAllocation.length === 0 &&
    data.advisorPerformance.length === 0
  ) {
    return;
  }

  doc.addPage();

  drawSectionHeader(
    doc,
    'Advisor Comparison',
    'Allocation and performance attributed to each advisor'
  );

  const performanceByAdvisor = new Map(
    data.advisorPerformance.map((row) => [row.advisor, row])
  );

  const rows = data.advisorAllocation.map((allocationRow) => {
    const perf = performanceByAdvisor.get(allocationRow.advisor);

    return [
      allocationRow.advisor,
      formatInr(allocationRow.value),
      formatPercent(allocationRow.percentage),
      perf ? formatInr(perf.invested_value) : '-',
      perf ? formatInr(perf.unrealized_pnl) : '-',
      perf ? formatPercent(perf.pnl_percentage) : '-',
    ];
  });

  autoTable(doc, {
    startY: 46,
    margin: { left: MARGIN, right: MARGIN },
    head: [
      [
        'Advisor',
        'Current Value',
        'Allocation %',
        'Invested Value',
        'Unrealized P&L',
        'P&L %',
      ],
    ],
    body: rows,
    theme: 'grid',
    headStyles: {
      fillColor: NAVY,
      textColor: WHITE,
      fontStyle: 'bold',
      fontSize: 9.5,
    },
    bodyStyles: { fontSize: 9, textColor: INK },
    alternateRowStyles: { fillColor: LIGHT_ROW },
    columnStyles: {
      1: { halign: 'right' },
      2: { halign: 'right' },
      3: { halign: 'right' },
      4: { halign: 'right' },
      5: { halign: 'right' },
    },
    styles: { cellPadding: 3.2 },
  });

  doc.setFont('helvetica', 'italic');
  doc.setFontSize(8);
  doc.setTextColor(...MUTED);
  doc.text(
    'Mutual fund holdings currently have no advisor attribution and are',
    MARGIN,
    (doc as any).lastAutoTable.finalY + 8
  );
  doc.text(
    'grouped under "Unassigned" above.',
    MARGIN,
    (doc as any).lastAutoTable.finalY + 13
  );

  drawFooter(doc, 'Advisor Comparison');
}

function drawMetricCard(doc: jsPDF, x: number, y: number, width: number, title: string, value: string, subtitle: string): void {
  doc.setDrawColor(226, 230, 236);
  doc.setFillColor(250, 251, 253);
  doc.roundedRect(x, y, width, 28, 2, 2, 'FD');
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(8);
  doc.setTextColor(...MUTED);
  doc.text(title.toUpperCase(), x + 6, y + 7);
  doc.setFontSize(15);
  doc.setTextColor(...INK);
  doc.text(value, x + 6, y + 17);
  doc.setFont('helvetica', 'normal');
  doc.setFontSize(7);
  doc.setTextColor(...MUTED);
  doc.text(subtitle, x + 6, y + 23);
}

function drawScopedHierarchyPage(doc: jsPDF, data: PortfolioReviewReportData): void {
  const assets = data.subClassDetails.flatMap(detail => detail.assets);
  if (!assets.length) return;
  const scope = data.reportScope || data.reportAssetClass || 'Selected Scope';
  const levelLabel = data.reportLevel === 'asset_class' ? 'Asset Class' : data.reportLevel === 'sub_class' ? 'Sub Class' : data.reportLevel === 'asset_name' ? 'Asset Name' : 'Underlying';
  doc.addPage();
  drawSectionHeader(doc, scope, levelLabel + ' report | Family: ' + (data.familyName || 'All Families'));
  const current = assets.reduce((s, a) => s + Number(a.current_value || 0), 0);
  const invested = assets.reduce((s, a) => s + Number(a.invested_value || 0), 0);
  const pnl = assets.reduce((s, a) => s + Number(a.pnl || 0), 0);
  const valid = assets.filter(a => a.xirr !== null && Number.isFinite(Number(a.xirr)) && Number(a.invested_value || 0) > 0);
  const base = valid.reduce((s, a) => s + Number(a.invested_value || 0), 0);
  const weightedXirr = base ? valid.reduce((s, a) => s + Number(a.xirr) * Number(a.invested_value || 0), 0) / base : null;
  const cardW = (PAGE_W - MARGIN * 2 - 18) / 4;
  drawMetricCard(doc, MARGIN, 48, cardW, 'Current Value', formatInr(current), assets.length + ' rows');
  drawMetricCard(doc, MARGIN + cardW + 6, 48, cardW, 'Invested Value', formatInr(invested), 'Capital deployed');
  drawMetricCard(doc, MARGIN + (cardW + 6) * 2, 48, cardW, 'Gain / Loss', formatInr(pnl), 'Return: ' + (invested ? ((pnl / invested) * 100).toFixed(1) : '0.0') + '%');
  drawMetricCard(doc, MARGIN + (cardW + 6) * 3, 48, cardW, 'XIRR', weightedXirr === null ? '-' : formatPercent(weightedXirr), 'Weighted by invested value');

  const grouped = new Map<string, number>();
  for (const asset of assets) {
    const key = data.reportLevel === 'asset_class' ? asset.sub_class : data.reportLevel === 'sub_class' ? asset.asset_name : data.reportLevel === 'asset_name' ? asset.underlying : asset.family_name;
    grouped.set(key || '-', (grouped.get(key || '-') || 0) + Number(asset.current_value || 0));
  }
  const rows = Array.from(grouped.entries()).sort((a, b) => b[1] - a[1]).slice(0, 10);
  const max = Math.max(...rows.map(([, value]) => value), 1);
  let y = 92;
  doc.setFont('helvetica', 'bold'); doc.setFontSize(10); doc.setTextColor(...INK);
  doc.text(data.reportLevel === 'asset_class' ? 'Value by Sub Class' : data.reportLevel === 'sub_class' ? 'Value by Asset Name' : data.reportLevel === 'asset_name' ? 'Value by Underlying' : 'Value by Family', MARGIN, y);
  y += 9;
  for (const [name, value] of rows) {
    doc.setFont('helvetica', 'normal'); doc.setFontSize(8); doc.setTextColor(...INK); doc.text(name, MARGIN, y + 4);
    doc.setFillColor(241, 243, 247); doc.roundedRect(MARGIN + 52, y, 132, 5, 1, 1, 'F');
    doc.setFillColor(...NAVY); doc.roundedRect(MARGIN + 52, y, Math.min(132, value / max * 132), 5, 1, 1, 'F');
    doc.setFont('helvetica', 'bold'); doc.setFontSize(7.5); doc.text(formatInr(value), MARGIN + 190, y + 4);
    y += 10;
  }
  drawFooter(doc, levelLabel + ': ' + scope);
}

function drawScopedOverviewPage(doc: jsPDF, data: PortfolioReviewReportData): void {
  const assets = data.subClassDetails.flatMap(detail => detail.assets);
  if (!assets.length) return;

  doc.addPage();
  const scope = data.reportAssetClass || 'Selected Asset Class';
  drawSectionHeader(doc, scope, 'Detailed report generated from the Portfolio hierarchy');

  const totalCurrent = assets.reduce((sum, asset) => sum + Number(asset.current_value || 0), 0);
  const totalInvested = assets.reduce((sum, asset) => sum + Number(asset.invested_value || 0), 0);
  const totalPnl = assets.reduce((sum, asset) => sum + Number(asset.pnl || 0), 0);
  const weightedXirrAssets = assets.filter(asset => asset.xirr !== null && Number.isFinite(Number(asset.xirr)) && Number(asset.invested_value || 0) > 0);
  const weightedXirrBase = weightedXirrAssets.reduce((sum, asset) => sum + Number(asset.invested_value || 0), 0);
  const weightedXirr = weightedXirrBase
    ? weightedXirrAssets.reduce((sum, asset) => sum + Number(asset.xirr) * Number(asset.invested_value || 0), 0) / weightedXirrBase
    : null;

  const cardW = (PAGE_W - MARGIN * 2 - 18) / 4;
  drawMetricCard(doc, MARGIN, 48, cardW, 'Current Value', formatInr(totalCurrent), assets.length + ' holdings');
  drawMetricCard(doc, MARGIN + cardW + 6, 48, cardW, 'Invested Value', formatInr(totalInvested), 'Capital deployed');
  drawMetricCard(doc, MARGIN + (cardW + 6) * 2, 48, cardW, 'Gain / Loss', formatInr(totalPnl), 'Return: ' + (totalInvested ? ((totalPnl / totalInvested) * 100).toFixed(1) : '0.0') + '%');
  drawMetricCard(doc, MARGIN + (cardW + 6) * 3, 48, cardW, 'XIRR', weightedXirr === null ? '-' : formatPercent(weightedXirr), 'Invested-value weighted');

  const typeRows = [...data.subClassSummaries].sort((a, b) => Number(b.current_value || 0) - Number(a.current_value || 0));
  const maxTypeValue = Math.max(...typeRows.map(row => Number(row.current_value || 0)), 1);
  let y = 92;
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(10);
  doc.setTextColor(...INK);
  doc.text('Current Value by Type', MARGIN, y);
  y += 9;
  for (const row of typeRows.slice(0, 10)) {
    const value = Number(row.current_value || 0);
    const width = Math.min(132, (value / maxTypeValue) * 132);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(8);
    doc.setTextColor(...INK);
    doc.text(row.sub_class, MARGIN, y + 4);
    doc.setFillColor(241, 243, 247);
    doc.roundedRect(MARGIN + 50, y, 132, 5, 1, 1, 'F');
    doc.setFillColor(...NAVY);
    doc.roundedRect(MARGIN + 50, y, width, 5, 1, 1, 'F');
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(7.5);
    doc.text(formatInr(value) + ' (' + (totalCurrent ? ((value / totalCurrent) * 100).toFixed(1) : '0.0') + '%)', MARGIN + 190, y + 4);
    y += 10;
  }

  const topAssets = [...assets].sort((a, b) => Number(b.current_value || 0) - Number(a.current_value || 0)).slice(0, 8);
  autoTable(doc, {
    startY: 92,
    margin: { left: PAGE_W / 2 + 2, right: MARGIN },
    head: [['Top Holding', 'Current Value', 'Gain', 'XIRR']],
    body: topAssets.map(asset => [asset.asset_name, formatInr(asset.current_value), formatInr(asset.pnl), asset.xirr === null ? '-' : formatPercent(asset.xirr)]),
    theme: 'grid',
    headStyles: { fillColor: NAVY, textColor: WHITE, fontStyle: 'bold', fontSize: 8.5 },
    bodyStyles: { fontSize: 8, textColor: INK },
    alternateRowStyles: { fillColor: LIGHT_ROW },
    columnStyles: { 1: { halign: 'right' }, 2: { halign: 'right' }, 3: { halign: 'right' } },
    styles: { cellPadding: 2.5 },
  });

  drawFooter(doc, scope + ' Overview');
}

function drawScopedPerformancePage(doc: jsPDF, data: PortfolioReviewReportData): void {
  const rows = [...data.subClassSummaries];
  if (!rows.length) return;

  doc.addPage();
  const scope = data.reportAssetClass || 'Selected Asset Class';
  drawSectionHeader(doc, 'Performance & Contribution', 'P&L and XIRR by type within ' + scope);

  const maxPnl = Math.max(...rows.map(row => Math.abs(Number(row.pnl || 0))), 1);
  let leftY = 55;
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(10);
  doc.setTextColor(...INK);
  doc.text('P&L Contribution', MARGIN, leftY);
  leftY += 10;
  for (const row of [...rows].sort((a, b) => Number(b.pnl || 0) - Number(a.pnl || 0)).slice(0, 10)) {
    const pnl = Number(row.pnl || 0);
    const width = Math.min(116, (Math.abs(pnl) / maxPnl) * 116);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(8);
    doc.setTextColor(...INK);
    doc.text(row.sub_class, MARGIN, leftY + 4);
    doc.setFillColor(241, 243, 247);
    doc.roundedRect(MARGIN + 50, leftY, 116, 5, 1, 1, 'F');
    doc.setFillColor(...(pnl >= 0 ? [15, 122, 92] : [180, 35, 24]) as [number, number, number]);
    doc.roundedRect(MARGIN + 50, leftY, width, 5, 1, 1, 'F');
    doc.setFont('helvetica', 'bold');
    doc.text(formatInr(pnl), MARGIN + 172, leftY + 4);
    leftY += 11;
  }

  const xirrRows = rows.filter(row => row.xirr !== null && row.xirr !== undefined && Number.isFinite(Number(row.xirr))).sort((a, b) => Number(b.xirr) - Number(a.xirr));
  const maxXirr = Math.max(...xirrRows.map(row => Math.abs(Number(row.xirr || 0))), 1);
  let rightY = 55;
  const rightX = PAGE_W / 2 + 4;
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(10);
  doc.setTextColor(...INK);
  doc.text('XIRR by Type', rightX, rightY);
  rightY += 10;
  for (const row of xirrRows.slice(0, 10)) {
    const xirr = Number(row.xirr || 0);
    const width = Math.min(116, (Math.abs(xirr) / maxXirr) * 116);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(8);
    doc.setTextColor(...INK);
    doc.text(row.sub_class, rightX, rightY + 4);
    doc.setFillColor(241, 243, 247);
    doc.roundedRect(rightX + 48, rightY, 116, 5, 1, 1, 'F');
    doc.setFillColor(...(xirr >= 0 ? [15, 122, 92] : [180, 35, 24]) as [number, number, number]);
    doc.roundedRect(rightX + 48, rightY, width, 5, 1, 1, 'F');
    doc.setFont('helvetica', 'bold');
    doc.text(formatPercent(xirr), rightX + 170, rightY + 4);
    rightY += 11;
  }

  drawFooter(doc, 'Performance & Contribution');
}

function drawDisclaimerPage(doc: jsPDF): void {
  doc.addPage();

  doc.setFillColor(...NAVY_SOFT);
  doc.rect(0, 0, PAGE_W, PAGE_H, 'F');

  doc.setFont('times', 'normal');
  doc.setFontSize(20);
  doc.setTextColor(...WHITE);
  doc.text('Disclaimer', MARGIN, 26);

  doc.setDrawColor(...ORANGE);
  doc.setLineWidth(0.6);
  doc.line(MARGIN, 30, MARGIN + 20, 30);

  const paragraphs = [
    'This report is generated automatically from data you have ' +
      'recorded in Personal Wealth Monitoring (PWMS) and reflects ' +
      'your own portfolio records as of the generation date shown ' +
      'on the cover page.',
    'Values, allocations, and returns shown here depend entirely on ' +
      'the accuracy and completeness of the transactions and prices ' +
      'you have entered or imported. PWMS does not independently ' +
      'verify these figures against any exchange, registrar, or ' +
      'custodian record.',
    'Nothing in this report constitutes investment advice, a ' +
      'recommendation, or an offer to buy or sell any security. Past ' +
      'performance and computed returns (including XIRR) are not ' +
      'indicative of future results.',
    'This report is intended solely for your personal use and ' +
      'reference and should not be relied upon as a substitute for ' +
      'advice from a qualified, registered investment adviser.',
  ];

  let y = 50;

  doc.setFont('helvetica', 'normal');
  doc.setFontSize(10);
  doc.setTextColor(215, 220, 230);

  for (const paragraph of paragraphs) {
    const lines = doc.splitTextToSize(paragraph, PAGE_W - MARGIN * 2 - 20);
    doc.text(lines, MARGIN, y);
    y += lines.length * 5.5 + 8;
  }
}

export class PortfolioReportPdfService {
  generate(data: PortfolioReviewReportData): void {
    const doc = new jsPDF({
      orientation: 'landscape',
      unit: 'mm',
      format: 'a4',
    });

    const today = new Date();

    drawCoverPage(doc, data, today);

    if (data.reportAssetClass) {
      if (data.subClassDetails.length === 0) {
        throw new Error('No holdings found for the selected asset class.');
      }

      drawScopedHierarchyPage(doc, data);
      drawScopedOverviewPage(doc, data);
      drawScopedPerformancePage(doc, data);
      drawTopExposuresPage(doc, data);
      drawSchemeDetailPages(doc, data);
      drawDisclaimerPage(doc);
    } else {
      drawExecutiveSummaryPage(doc, data);
      drawAllocationAnalysisPage(doc, data);
      drawInvestmentSummaryPage(doc, data);
      drawAdvisorPage(doc, data);
      drawHoldingsPage(doc, data);
      drawTopExposuresPage(doc, data);
      drawSchemeDetailPages(doc, data);
      drawDisclaimerPage(doc);
    }

    const filenameSafeDate = today.toISOString().slice(0, 10);
    const familyPart = data.familyName
      ? '-' + data.familyName.replace(/[^a-z0-9]+/gi, '_')
      : '';
    const assetClassPart = data.reportAssetClass
      ? '-' + data.reportAssetClass.replace(/[^a-z0-9]+/gi, '_')
      : '';

    doc.save('Portfolio_Review' + assetClassPart + familyPart + '_' + filenameSafeDate + '.pdf');
  }
}
