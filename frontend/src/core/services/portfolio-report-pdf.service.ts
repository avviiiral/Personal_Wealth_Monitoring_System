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
  sub_class: string;
  quantity?: number;
  invested_value: number;
  current_value: number;
  pnl: number;
  xirr?: number | null;
}

export interface AssetDetailRow {
  asset_name: string;
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
  assets: AssetDetailRow[];
}

export interface PortfolioReviewReportData {
  familyName: string;
  totalWealth: number;
  totalInvested: number;
  totalPnl: number;
  xirrPercentage: number | null;
  investmentSummary: InvestmentSummaryRow[];
  advisorAllocation: AdvisorAllocationRow[];
  advisorPerformance: AdvisorPerformanceRow[];
  subClassSummaries: SubClassSummaryRow[];
  subClassDetails: SubClassDetail[];
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
  doc.text('Portfolio Review', MARGIN, 100);

  doc.setFont('helvetica', 'normal');
  doc.setFontSize(13);
  doc.setTextColor(...ORANGE);
  doc.text(`Family: ${data.familyName || 'All Families'}`, MARGIN, 130);

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

  const categoryTotals = new Map<string, number>();

  for (const row of data.investmentSummary) {
    categoryTotals.set(
      row.asset_category,
      (categoryTotals.get(row.asset_category) ?? 0) + row.current_value
    );
  }

  const totalValue = Array.from(categoryTotals.values()).reduce(
    (a, b) => a + b,
    0
  );

  const allocationRows = Array.from(categoryTotals.entries())
    .filter(([, value]) => value > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([category, value]) => [
      category,
      formatInr(value),
      totalValue ? `${((value / totalValue) * 100).toFixed(1)}%` : '-',
    ]);

  autoTable(doc, {
    startY: 52,
    margin: { left: PAGE_W / 2 + 4, right: MARGIN },
    head: [['Asset Allocation', 'Value', '%']],
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
        'Sub Class',
        'Invested Value',
        'Current Value',
        'Gain / Loss',
        'XIRR',
      ],
    ],
    body: data.subClassSummaries.map((row) => [
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
      1: { halign: 'right' },
      2: { halign: 'right' },
      3: { halign: 'right' },
      4: { halign: 'right' },
    },
    styles: { cellPadding: 3.2 },
    didParseCell: (hookData: CellHookData) => {
      if (hookData.section !== 'body' || hookData.column.index !== 3) {
        return;
      }

      const raw = data.subClassSummaries[hookData.row.index]?.pnl ?? 0;

      hookData.cell.styles.textColor =
        raw >= 0 ? [15, 122, 92] : [180, 35, 24];
    },
  });

  drawFooter(doc, 'Holdings by Sub Class');
}

function drawSchemeDetailPages(
  doc: jsPDF,
  data: PortfolioReviewReportData
): void {
  for (const subClassDetail of data.subClassDetails) {
    if (subClassDetail.assets.length === 0) {
      continue;
    }

    doc.addPage();

    drawSectionHeader(
      doc,
      subClassDetail.sub_class,
      `Every individual holding within ${subClassDetail.sub_class}`
    );

    const sortedAssets = [...subClassDetail.assets].sort(
      (a, b) => b.current_value - a.current_value
    );

    autoTable(doc, {
      startY: 46,
      margin: { left: MARGIN, right: MARGIN },
      head: [
        [
          'Scheme / Holding',
          'ISIN',
          'Advisor',
          'Quantity',
          'Invested Value',
          'Current Price',
          'Current Value',
          'Gain / Loss',
          'Gain %',
          'XIRR',
        ],
      ],
      body: sortedAssets.map((asset) => [
        asset.asset_name,
        asset.isin || '-',
        asset.advisors || '-',
        asset.quantity
          ? asset.quantity.toLocaleString('en-IN', {
              maximumFractionDigits: 3,
            })
          : '-',
        formatInr(asset.invested_value),
        asset.current_price
          ? asset.current_price.toLocaleString('en-IN', {
              maximumFractionDigits: 4,
            })
          : '-',
        formatInr(asset.current_value),
        formatInr(asset.pnl),
        formatPercent(asset.pnl_percentage),
        asset.xirr !== null ? formatPercent(asset.xirr) : '-',
      ]),
      theme: 'grid',
      headStyles: {
        fillColor: NAVY,
        textColor: WHITE,
        fontStyle: 'bold',
        fontSize: 8,
      },
      bodyStyles: { fontSize: 7.5, textColor: INK },
      alternateRowStyles: { fillColor: LIGHT_ROW },
      columnStyles: {
        3: { halign: 'right' },
        4: { halign: 'right' },
        5: { halign: 'right' },
        6: { halign: 'right' },
        7: { halign: 'right' },
        8: { halign: 'right' },
        9: { halign: 'right' },
      },
      styles: { cellPadding: 2.4 },
      didParseCell: (hookData: CellHookData) => {
        if (hookData.section !== 'body' || hookData.column.index !== 7) {
          return;
        }

        const raw = sortedAssets[hookData.row.index]?.pnl ?? 0;

        hookData.cell.styles.textColor =
          raw >= 0 ? [15, 122, 92] : [180, 35, 24];
      },
      didDrawPage: () => {
        drawFooter(doc, subClassDetail.sub_class);
      },
    });
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
    head: [['Top 10 by Allocation', 'Current Value', '%']],
    body: topByValue.map((asset) => [
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
    columnStyles: { 1: { halign: 'right' }, 2: { halign: 'right' } },
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
    head: [['Top 10 by XIRR', 'XIRR', 'Current Value']],
    body: topByXirr.map((asset) => [
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
    columnStyles: { 1: { halign: 'right' }, 2: { halign: 'right' } },
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
    drawExecutiveSummaryPage(doc, data);
    drawInvestmentSummaryPage(doc, data);
    drawAdvisorPage(doc, data);
    drawHoldingsPage(doc, data);
    drawTopExposuresPage(doc, data);
    drawSchemeDetailPages(doc, data);
    drawDisclaimerPage(doc);

    const filenameSafeDate = today.toISOString().slice(0, 10);
    const familyPart = data.familyName
      ? `-${data.familyName.replace(/[^a-z0-9]+/gi, '_')}`
      : '';

    doc.save(`Portfolio_Review${familyPart}_${filenameSafeDate}.pdf`);
  }
}
