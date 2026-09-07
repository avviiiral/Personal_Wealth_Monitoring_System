import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import {
  ChangeDetectorRef,
  Component,
  ElementRef,
  HostListener,
  OnInit,
  inject,
} from '@angular/core';
import type ExcelJS from 'exceljs';

import {
  PortfolioApiService,
  FamilyNode,
  PortfolioAssetNode,
  Transaction,
} from '../../core/services/portfolio-api.service';

/* ==============================================================
   REPORTS TREE
   (Portfolio-page layout: Family / Asset Class are FILTERS, not
   table rows — same pattern as portfolio.component.ts's
   selection-area + subClassSummaries.)

   Sub Class            (Quantity / Invested / Current / Gain / XIRR)
      Asset Name         (Quantity / Invested / Current / Gain + Download)
         Underlying
            Transactions
   ============================================================== */

interface UnderlyingGroup {
  underlying: string;
  isin: string;
  transaction_count: number;
  transactions: Transaction[];
}

interface AssetNameGroup {
  asset_name: string;
  underlyings: UnderlyingGroup[];
  transaction_count: number;

  /* Financial values, joined in from the Portfolio tree by asset id -
     same fields/meaning as Portfolio's own AssetGroup. */
  quantity: number;
  invested_value: number;
  current_value: number;
  pnl: number;
}

interface SubClassGroup {
  sub_class: string;
  asset_names: AssetNameGroup[];
  transaction_count: number;

  /* Financial values, joined in from the Portfolio tree by asset id -
     same fields/meaning as Portfolio's own SubClassSummary. */
  quantity: number;
  invested_value: number;
  current_value: number;
  pnl: number;
  xirr: number | null;
}

/* Aggregate row used only by the Summary View download. */
interface SubClassSummaryRow {
  family_name: string;
  sub_class: string;
  quantity: number;
  invested_value: number;
  current_value: number;
  gain: number;
  xirr: number | null;
}

const UNASSIGNED = 'Unassigned';

@Component({
  selector: 'app-reports',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './reports.component.html',
  styleUrl: './reports.component.scss',
})
export class ReportsComponent implements OnInit {
  private readonly portfolioApi = inject(PortfolioApiService);
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly elementRef = inject(ElementRef);

  /* Raw transactions as returned by the API. Every grouping/filter
     below is derived from this list, the same way the Portfolio
     page derives everything from `families` (the portfolio tree). */
  transactions: Transaction[] = [];

  /* Portfolio tree — used for the Summary View download AND (now)
     to join Quantity / Invested Value / Current Value / Gain / XIRR
     onto the Sub Class and Asset Name rows here, by asset id. */
  private portfolioTree: FamilyNode[] = [];

  loading = true;
  error = '';

  /* ============================================================
     FILTERS
     Same pattern as portfolio.component.ts: Family Name / Asset
     Class selection buttons above the table.
     ============================================================ */
  selectedFamily = '';
  selectedAssetClass = '';

  /* ============================================================
     EXPAND / COLLAPSE STATE
     LEVEL 1: Sub Class
     LEVEL 2: Asset Name — reveals a Download action (for that
       Asset Name's full transaction history) right under its
       header, above the Underlying list.
     LEVEL 3 (final): Underlying — clicking one directly reveals
       its own Transactions below it.
     ============================================================ */
  expandedSubClass = '';
  expandedAssetName = '';
  expandedUnderlying = '';

  detailedRangeModalOpen = false;
  detailedRangeFrom = '';
  detailedRangeTo = '';
  detailedRangeFamily = '';
  detailedRangeError = '';

  summaryFamilyModalOpen = false;
  summaryFamily = '';

  downloadMenuOpen = false;

  ngOnInit(): void {
    this.loadReports();
  }

  loadReports(): void {
    this.loading = true;
    this.error = '';

    this.portfolioApi.getTransactions().subscribe({
      next: (response) => {
        this.transactions = response.results ?? [];

        this.portfolioApi.getPortfolioTree().subscribe({
          next: (treeResponse) => {
            this.portfolioTree = treeResponse.families ?? [];

            this.validateSelections();

            this.loading = false;
            this.cdr.detectChanges();
          },

          error: (error) => {
            console.error('Portfolio tree API error:', error);

            /* Transactions loaded fine, so the Reports tree itself
               still renders. Only the Summary download and the
               Quantity/Invested/Current/Gain/XIRR columns depend on
               portfolioTree, so we don't block the page on this -
               those columns just show as 0/blank until it's back. */
            this.portfolioTree = [];

            this.validateSelections();

            this.loading = false;
            this.cdr.detectChanges();
          },
        });
      },

      error: (error) => {
        console.error('Reports transactions API error:', error);

        this.loading = false;

        if (error?.status === 401 || error?.status === 403) {
          this.error = 'Authentication failed. Please log in again.';
        } else {
          this.error = 'Unable to load report data.';
        }

        this.cdr.detectChanges();
      },
    });
  }

  refresh(): void {
    this.loadReports();
  }

  /* ============================================================
     TREE BUILDING / FILTERS
     ============================================================ */

  private clean(value: string | null | undefined): string {
    const trimmed = value?.trim();
    return trimmed || UNASSIGNED;
  }

  private getAssetName(tx: Transaction): string {
    return this.clean(tx.asset_name);
  }

  private getUnderlyingName(tx: Transaction): string {
    return this.clean(tx.underlying || tx.asset_name);
  }

  /**
   * Distinct Family names, alphabetically sorted. Used both by the
   * top filter buttons (Portfolio-style) and by the download
   * modals' Family selects, exactly as before.
   */
  get familyOptions(): string[] {
    const names = new Set<string>();

    for (const tx of this.transactions) {
      names.add(this.clean(tx.family_name));
    }

    return Array.from(names).sort((a, b) => a.localeCompare(b));
  }

  /**
   * Distinct Asset Class values within the currently selected
   * Family (or all transactions if no Family is selected) —
   * same dependency pattern as Portfolio's assetClassOptions.
   */
  get assetClassOptions(): string[] {
    const classes = new Set<string>();

    for (const tx of this.transactionsFilteredByFamily) {
      classes.add(this.clean(tx.asset_class));
    }

    return Array.from(classes).sort((a, b) => a.localeCompare(b));
  }

  private get transactionsFilteredByFamily(): Transaction[] {
    if (!this.selectedFamily) {
      return this.transactions;
    }

    return this.transactions.filter((tx) => this.clean(tx.family_name) === this.selectedFamily);
  }

  get filteredTransactions(): Transaction[] {
    return this.transactionsFilteredByFamily.filter(
      (tx) => !this.selectedAssetClass || this.clean(tx.asset_class) === this.selectedAssetClass,
    );
  }

  /**
   * asset id -> PortfolioAssetNode, flattened from the Portfolio
   * tree. Used purely to join Quantity / Invested Value / Current
   * Value / Gain / XIRR onto the Sub Class and Asset Name rows
   * here, the same fields the Portfolio page itself renders.
   */
  private get assetLookup(): Map<number, PortfolioAssetNode> {
    const lookup = new Map<number, PortfolioAssetNode>();

    for (const family of this.portfolioTree) {
      for (const portfolio of family.portfolios) {
        for (const assetClass of portfolio.asset_classes) {
          for (const subClass of assetClass.sub_classes) {
            for (const asset of subClass.assets) {
              lookup.set(asset.id, asset);
            }
          }
        }
      }
    }

    return lookup;
  }

  /**
   * LEVEL 1: Sub Class summaries built from the filtered
   * transactions — same role as Portfolio's `subClassSummaries`
   * getter, including the same financial columns.
   */
  get subClassGroups(): SubClassGroup[] {
    return this.buildSubClassGroups(this.filteredTransactions);
  }

  private buildSubClassGroups(transactions: Transaction[]): SubClassGroup[] {
    const lookup = this.assetLookup;

    const subClassMap = new Map<string, Map<string, Map<string, Transaction[]>>>();

    /* Distinct asset ids seen per Sub Class, and per Sub
       Class::Asset Name, so financial values are summed once per
       asset (not once per transaction). */
    const subClassAssetIds = new Map<string, Set<number>>();
    const assetNameAssetIds = new Map<string, Set<number>>();

    for (const tx of transactions) {
      const subClass = this.clean(tx.sub_class);
      const assetName = this.getAssetName(tx);
      const underlying = this.getUnderlyingName(tx);

      if (!subClassMap.has(subClass)) {
        subClassMap.set(subClass, new Map());
      }

      const assetNameMap = subClassMap.get(subClass)!;

      if (!assetNameMap.has(assetName)) {
        assetNameMap.set(assetName, new Map());
      }

      const underlyingMap = assetNameMap.get(assetName)!;

      if (!underlyingMap.has(underlying)) {
        underlyingMap.set(underlying, []);
      }

      underlyingMap.get(underlying)!.push(tx);

      if (!subClassAssetIds.has(subClass)) {
        subClassAssetIds.set(subClass, new Set());
      }
      subClassAssetIds.get(subClass)!.add(tx.asset);

      const assetNameKey = `${subClass}::${assetName}`;
      if (!assetNameAssetIds.has(assetNameKey)) {
        assetNameAssetIds.set(assetNameKey, new Set());
      }
      assetNameAssetIds.get(assetNameKey)!.add(tx.asset);
    }

    const nodesFor = (ids: Set<number> | undefined): PortfolioAssetNode[] => {
      if (!ids) {
        return [];
      }

      const nodes: PortfolioAssetNode[] = [];

      for (const id of ids) {
        const node = lookup.get(id);

        if (node) {
          nodes.push(node);
        }
      }

      return nodes;
    };

    const subClasses: SubClassGroup[] = Array.from(subClassMap.entries())
      .map(([sub_class, assetNameMap]) => {
        const asset_names: AssetNameGroup[] = Array.from(assetNameMap.entries())
          .map(([asset_name, underlyingMap]) => {
            const underlyings: UnderlyingGroup[] = Array.from(underlyingMap.entries())
              .map(([underlying, txs]) => ({
                underlying,
                isin: txs.find((tx) => tx.isin)?.isin ?? '-',
                transaction_count: txs.length,
                transactions: txs,
              }))
              .sort((a, b) => a.underlying.localeCompare(b.underlying));

            const transaction_count = underlyings.reduce(
              (total, group) => total + group.transaction_count,
              0,
            );

            const assetNameFinancials = this.aggregateAssetNodes(
              nodesFor(assetNameAssetIds.get(`${sub_class}::${asset_name}`)),
            );

            return {
              asset_name,
              underlyings,
              transaction_count,
              quantity: assetNameFinancials.quantity,
              invested_value: assetNameFinancials.invested_value,
              current_value: assetNameFinancials.current_value,
              pnl: assetNameFinancials.pnl,
            };
          })
          .sort((a, b) => a.asset_name.localeCompare(b.asset_name));

        const transaction_count = asset_names.reduce(
          (total, group) => total + group.transaction_count,
          0,
        );

        const subClassFinancials = this.aggregateAssetNodes(
          nodesFor(subClassAssetIds.get(sub_class)),
        );

        return {
          sub_class,
          asset_names,
          transaction_count,
          quantity: subClassFinancials.quantity,
          invested_value: subClassFinancials.invested_value,
          current_value: subClassFinancials.current_value,
          pnl: subClassFinancials.pnl,
          xirr: subClassFinancials.xirr,
        };
      })
      .sort((a, b) => a.sub_class.localeCompare(b.sub_class));

    return subClasses;
  }

  /**
   * Sums Quantity / Invested Value / Current Value / Gain across a
   * set of (already de-duplicated) Portfolio assets, and computes
   * their invested-value-weighted XIRR — same aggregation Portfolio
   * itself uses for its Sub Class rows.
   */
  private aggregateAssetNodes(nodes: PortfolioAssetNode[]): {
    quantity: number;
    invested_value: number;
    current_value: number;
    pnl: number;
    xirr: number | null;
  } {
    let quantity = 0;
    let invested_value = 0;
    let current_value = 0;
    let pnl = 0;

    for (const node of nodes) {
      quantity += this.toNumber(node.quantity);
      invested_value += this.toNumber(node.invested_value);
      current_value += this.toNumber(node.current_value);
      pnl += this.toNumber(node.pnl);
    }

    const xirr = this.weightedXirr(
      nodes.map((node) => ({
        invested_value: this.toNumber(node.invested_value),
        xirr: node.xirr,
      })),
    );

    return { quantity, invested_value, current_value, pnl, xirr };
  }

  /* ============================================================
     FILTER SELECTION (mirrors portfolio.component.ts)
     ============================================================ */

  selectFamily(family: string): void {
    this.selectedFamily = this.selectedFamily === family ? '' : family;

    this.selectedAssetClass = '';
    this.resetExpansion();
  }

  selectAssetClass(assetClass: string): void {
    this.selectedAssetClass = this.selectedAssetClass === assetClass ? '' : assetClass;

    this.resetExpansion();
  }

  clearFamily(): void {
    this.selectedFamily = '';
    this.selectedAssetClass = '';
    this.resetExpansion();
  }

  clearAssetClass(): void {
    this.selectedAssetClass = '';
    this.resetExpansion();
  }

  isFamilySelected(family: string): boolean {
    return this.selectedFamily === family;
  }

  isAssetClassSelected(assetClass: string): boolean {
    return this.selectedAssetClass === assetClass;
  }

  private resetExpansion(): void {
    this.expandedSubClass = '';
    this.expandedAssetName = '';
    this.expandedUnderlying = '';
  }

  private validateSelections(): void {
    if (this.selectedFamily && !this.familyOptions.includes(this.selectedFamily)) {
      this.selectedFamily = '';
      this.selectedAssetClass = '';
    }

    if (this.selectedAssetClass && !this.assetClassOptions.includes(this.selectedAssetClass)) {
      this.selectedAssetClass = '';
    }

    if (
      this.expandedSubClass &&
      !this.subClassGroups.some((group) => group.sub_class === this.expandedSubClass)
    ) {
      this.resetExpansion();
    }
  }

  /* ============================================================
     EXPAND / COLLAPSE
     ============================================================ */

  toggleSubClass(subClass: string): void {
    if (this.expandedSubClass === subClass) {
      this.expandedSubClass = '';
      this.expandedAssetName = '';
      this.expandedUnderlying = '';
      return;
    }

    this.expandedSubClass = subClass;
    this.expandedAssetName = '';
    this.expandedUnderlying = '';
  }

  toggleAssetName(key: string): void {
    if (this.expandedAssetName === key) {
      this.expandedAssetName = '';
      this.expandedUnderlying = '';
      return;
    }

    this.expandedAssetName = key;
    this.expandedUnderlying = '';
  }

  /**
   * LEVEL 3 (final): Underlying.
   *
   * Clicking one directly reveals its own Transactions below it -
   * same click behavior the very first Reports tree had.
   */
  toggleUnderlying(key: string): void {
    this.expandedUnderlying = this.expandedUnderlying === key ? '' : key;
  }

  getAssetNameKey(subClass: string, assetName: string): string {
    return `${subClass}::${assetName}`;
  }

  getUnderlyingKey(subClass: string, assetName: string, underlying: string): string {
    return `${subClass}::${assetName}::${underlying}`;
  }

  trackBySubClass(_index: number, group: SubClassGroup): string {
    return group.sub_class;
  }

  trackByAssetName(_index: number, group: AssetNameGroup): string {
    return group.asset_name;
  }

  trackByUnderlying(_index: number, group: UnderlyingGroup): string {
    return group.underlying;
  }

  trackByTransaction(_index: number, tx: Transaction): number {
    return tx.id;
  }

  /* ============================================================
     PER-ASSET-NAME DOWNLOAD
     Sits directly under the Asset Name header (above its
     Underlying list), and downloads every transaction across all
     of that Asset Name's Underlyings. Reuses the same
     styled-workbook exporter the top Download menu already uses.
     ============================================================ */

  async downloadAssetNameTransactions(
    event: MouseEvent,
    assetGroup: AssetNameGroup,
  ): Promise<void> {
    event.stopPropagation();

    const allTransactions: Transaction[] = [];

    for (const underlyingGroup of assetGroup.underlyings) {
      allTransactions.push(...underlyingGroup.transactions);
    }

    const rows = allTransactions
      .slice()
      .sort((a, b) => b.transaction_date.localeCompare(a.transaction_date))
      .map((tx) => ({
        underlying: this.getUnderlyingName(tx),
        transaction_date: new Date(tx.transaction_date),
        transaction_type: tx.transaction_type_display || tx.transaction_type,
        isin: tx.isin || '-',
        quantity: this.toNumber(tx.quantity),
        price_per_unit: this.toNumber(tx.price_per_unit),
        amount: this.toNumber(tx.amount),
      }));

    await this.exportWorkbook({
      sheetName: 'Transactions',
      title: `${assetGroup.asset_name} — Transactions (as of ${this.todayLabel()})`,
      columns: [
        { header: 'Underlying', key: 'underlying', width: 24 },
        { header: 'Transaction Date', key: 'transaction_date', width: 18, numFmt: 'dd-mmm-yyyy' },
        { header: 'Type', key: 'transaction_type', width: 16 },
        { header: 'ISIN', key: 'isin', width: 16 },
        { header: 'Quantity', key: 'quantity', width: 14, numFmt: '#,##,##0.00' },
        { header: 'Price', key: 'price_per_unit', width: 16, numFmt: '"₹"#,##,##0.00' },
        { header: 'Amount', key: 'amount', width: 18, numFmt: '"₹"#,##,##0' },
      ],
      rows,
      filename: `${this.slugify(assetGroup.asset_name)}_transactions_${this.todayStamp()}.xlsx`,
    });
  }

  /* ============================================================
     DOWNLOAD MENU (top of page — unchanged)
     ============================================================ */

  toggleDownloadMenu(): void {
    this.downloadMenuOpen = !this.downloadMenuOpen;
  }

  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent): void {
    if (!this.downloadMenuOpen) {
      return;
    }

    if (!this.elementRef.nativeElement.contains(event.target)) {
      this.downloadMenuOpen = false;
    }
  }

  /**
   * Opens the Family prompt for the Summary View download.
   */
  openSummaryFamilyModal(): void {
    this.downloadMenuOpen = false;
    this.summaryFamily = '';
    this.summaryFamilyModalOpen = true;
  }

  cancelSummaryFamilyModal(): void {
    this.summaryFamilyModalOpen = false;
  }

  async confirmSummaryDownload(): Promise<void> {
    this.summaryFamilyModalOpen = false;

    const rows = this.buildSummaryRows(this.summaryFamily);

    const familyLabel = this.summaryFamily || 'All Families';

    const familySlug = this.summaryFamily ? `_${this.slugify(this.summaryFamily)}` : '';

    await this.exportWorkbook({
      sheetName: 'Summary',
      title: `Portfolio Summary — ${familyLabel} (as of ${this.todayLabel()})`,
      columns: [
        { header: 'Family', key: 'family_name', width: 24 },
        { header: 'Sub Class', key: 'sub_class', width: 22 },
        { header: 'Quantity', key: 'quantity', width: 16, numFmt: '#,##,##0.00' },
        { header: 'Invested Amount', key: 'invested_value', width: 20, numFmt: '"₹"#,##,##0' },
        { header: 'Current Value', key: 'current_value', width: 20, numFmt: '"₹"#,##,##0' },
        { header: 'Gain/Loss', key: 'gain', width: 20, numFmt: '"₹"#,##,##0' },
        { header: 'XIRR (%)', key: 'xirr', width: 14, numFmt: '0.00"%"' },
      ],
      rows: rows.map((row) => ({
        family_name: row.family_name,
        sub_class: row.sub_class,
        quantity: row.quantity,
        invested_value: row.invested_value,
        current_value: row.current_value,
        gain: row.gain,
        xirr: row.xirr,
      })),
      gainKey: 'gain',
      filename: `portfolio_summary${familySlug}_${this.todayStamp()}.xlsx`,
    });
  }

  /**
   * Opens the date-range prompt for the Detailed View download.
   * Prefills From/To with the earliest/latest transaction dates
   * actually present, so the default range covers everything and
   * the user only needs to narrow it if they want a subset.
   */
  openDetailedRangeModal(): void {
    this.downloadMenuOpen = false;
    this.detailedRangeError = '';
    this.detailedRangeFamily = '';

    const dates = this.allTransactionDates();

    this.detailedRangeFrom = dates.length ? dates[0] : '';
    this.detailedRangeTo = dates.length ? dates[dates.length - 1] : '';

    this.detailedRangeModalOpen = true;
  }

  cancelDetailedRangeModal(): void {
    this.detailedRangeModalOpen = false;
    this.detailedRangeError = '';
  }

  async confirmDetailedRangeDownload(): Promise<void> {
    if (
      this.detailedRangeFrom &&
      this.detailedRangeTo &&
      this.detailedRangeFrom > this.detailedRangeTo
    ) {
      this.detailedRangeError = 'From date must be on or before To date.';
      return;
    }

    this.detailedRangeError = '';
    this.detailedRangeModalOpen = false;

    const rows = this.buildDetailedRows(
      this.detailedRangeFrom,
      this.detailedRangeTo,
      this.detailedRangeFamily,
    );

    const rangeLabel = this.formatRangeLabel(this.detailedRangeFrom, this.detailedRangeTo);

    const familyLabel = this.detailedRangeFamily || 'All Families';

    const familySlug = this.detailedRangeFamily ? `_${this.slugify(this.detailedRangeFamily)}` : '';

    await this.exportWorkbook({
      sheetName: 'Detailed',
      title: `Portfolio Detailed — ${familyLabel} (${rangeLabel})`,
      columns: [
        { header: 'Family', key: 'family_name', width: 24 },
        { header: 'Sub Class', key: 'sub_class', width: 20 },
        { header: 'Underlying', key: 'underlying', width: 28 },
        { header: 'ISIN', key: 'isin', width: 16 },
        { header: 'Transaction Date', key: 'transaction_date', width: 18, numFmt: 'dd-mmm-yyyy' },
        { header: 'Transaction Type', key: 'transaction_type', width: 18 },
        { header: 'Quantity', key: 'quantity', width: 14, numFmt: '#,##,##0.00' },
        { header: 'Price', key: 'price_per_unit', width: 16, numFmt: '"₹"#,##,##0.00' },
        { header: 'Amount', key: 'amount', width: 18, numFmt: '"₹"#,##,##0' },
      ],
      rows,
      filename: `portfolio_detailed${familySlug}_${this.detailedRangeFrom || 'start'}_to_${this.detailedRangeTo || 'end'}.xlsx`,
    });
  }

  /**
   * Lowercases and replaces anything that isn't alphanumeric with
   * an underscore, for building a safe filename segment from a
   * Family name (or, now, an Asset Name).
   */
  private slugify(value: string): string {
    return value
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '');
  }

  /**
   * Every transaction's ISO date string ("YYYY-MM-DD"), sorted
   * ascending. Used only to prefill the date-range modal's default
   * From/To with the actual earliest/latest dates in the data.
   */
  private allTransactionDates(): string[] {
    return this.transactions
      .map((tx) => tx.transaction_date)
      .filter((date): date is string => !!date)
      .sort();
  }

  /**
   * Flattens all transactions into rows for the Detailed download,
   * filtered to the given date range (inclusive; an empty bound
   * means unbounded on that side) and optionally to one Family (an
   * empty value means all Families), and sorted by Transaction Date
   * across the whole sheet (most recent first) - so the export is
   * indexed by date rather than clustered by Underlying. Family/Sub
   * Class/Underlying are still included as columns on every row so
   * the hierarchy stays identifiable per the Detailed View's
   * requirements.
   */
  private buildDetailedRows(from: string, to: string, family?: string): Record<string, unknown>[] {
    const filtered = this.transactions.filter((tx) => {
      if (!tx.transaction_date) {
        return false;
      }

      if (family && this.clean(tx.family_name) !== family) {
        return false;
      }

      if (from && tx.transaction_date < from) {
        return false;
      }

      if (to && tx.transaction_date > to) {
        return false;
      }

      return true;
    });

    filtered.sort((a, b) => b.transaction_date.localeCompare(a.transaction_date));

    return filtered.map((tx) => ({
      family_name: this.clean(tx.family_name),
      sub_class: this.clean(tx.sub_class),
      underlying: this.getUnderlyingName(tx),
      isin: tx.isin || '-',
      transaction_date: new Date(tx.transaction_date),
      transaction_type: tx.transaction_type_display || tx.transaction_type,
      quantity: this.toNumber(tx.quantity),
      price_per_unit: this.toNumber(tx.price_per_unit),
      amount: this.toNumber(tx.amount),
    }));
  }

  private formatRangeLabel(from: string, to: string): string {
    if (!from && !to) {
      return 'All dates';
    }

    const formatOne = (value: string) =>
      new Intl.DateTimeFormat('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }).format(
        new Date(value),
      );

    if (from && to) {
      return `${formatOne(from)} – ${formatOne(to)}`;
    }

    return from ? `From ${formatOne(from)}` : `Up to ${formatOne(to)}`;
  }

  /**
   * Family -> Sub Class aggregation for the Summary View download.
   *
   * Reuses the same portfolio tree (and the same per-asset
   * quantity/invested_value/current_value/pnl/xirr fields) the
   * Portfolio page's own subClassSummaries getter aggregates from -
   * this only adds Family as an additional grouping key, since the
   * Summary View must be Family -> Sub Class, not just Sub Class.
   *
   * An optional family filters the result to that one Family; an
   * empty value returns all Families.
   */
  private buildSummaryRows(family?: string): SubClassSummaryRow[] {
    const rowMap = new Map<
      string,
      SubClassSummaryRow & { assets: { invested_value: number; xirr: number | null }[] }
    >();

    for (const familyNode of this.portfolioTree) {
      if (family && familyNode.family_name !== family) {
        continue;
      }

      for (const portfolio of familyNode.portfolios) {
        for (const assetClass of portfolio.asset_classes) {
          for (const subClass of assetClass.sub_classes) {
            const key = `${familyNode.family_name}::${subClass.sub_class}`;

            let row = rowMap.get(key);

            if (!row) {
              row = {
                family_name: familyNode.family_name,
                sub_class: subClass.sub_class,
                quantity: 0,
                invested_value: 0,
                current_value: 0,
                gain: 0,
                xirr: null,
                assets: [],
              };

              rowMap.set(key, row);
            }

            for (const asset of subClass.assets) {
              row.quantity += this.toNumber(asset.quantity);
              row.invested_value += this.toNumber(asset.invested_value);
              row.current_value += this.toNumber(asset.current_value);
              row.gain += this.toNumber(asset.pnl);

              row.assets.push({
                invested_value: this.toNumber(asset.invested_value),
                xirr: asset.xirr,
              });
            }
          }
        }
      }
    }

    return Array.from(rowMap.values())
      .map((row) => ({
        family_name: row.family_name,
        sub_class: row.sub_class,
        quantity: row.quantity,
        invested_value: row.invested_value,
        current_value: row.current_value,
        gain: row.gain,
        xirr: this.weightedXirr(row.assets),
      }))
      .sort(
        (a, b) =>
          a.family_name.localeCompare(b.family_name) || a.sub_class.localeCompare(b.sub_class),
      );
  }

  private weightedXirr(assets: { invested_value: number; xirr: number | null }[]): number | null {
    const validAssets = assets.filter(
      (asset) => asset.xirr !== null && asset.xirr !== undefined && asset.invested_value > 0,
    );

    if (!validAssets.length) {
      return null;
    }

    let weightedXirr = 0;
    let totalInvested = 0;

    for (const asset of validAssets) {
      weightedXirr += (asset.xirr as number) * asset.invested_value;
      totalInvested += asset.invested_value;
    }

    return totalInvested ? weightedXirr / totalInvested : null;
  }

  /* ============================================================
     EXCEL EXPORT
     ============================================================ */

  /**
   * Builds a single-sheet, styled .xlsx workbook and downloads it.
   *
   * Styling is purely cosmetic (title band, header fill/borders,
   * frozen header row, autofilter, column number formats, banded
   * rows, green/red Gain-Loss coloring) - the underlying values are
   * exactly the rows passed in, which the callers build from the
   * same tree/aggregation used to render the page.
   */
  private async exportWorkbook(config: {
    sheetName: string;
    title: string;
    columns: { header: string; key: string; width: number; numFmt?: string }[];
    rows: Record<string, unknown>[];
    gainKey?: string;
    filename: string;
  }): Promise<void> {
    const { default: ExcelJSLib } = await import('exceljs');

    const workbook = new ExcelJSLib.Workbook();
    workbook.creator = 'PWMS';
    workbook.created = new Date();

    const sheet = workbook.addWorksheet(config.sheetName, {
      views: [{ state: 'frozen', ySplit: 2 }],
    });

    const columnCount = config.columns.length;

    /* Title band */
    sheet.mergeCells(1, 1, 1, columnCount);
    const titleCell = sheet.getCell(1, 1);
    titleCell.value = config.title;
    titleCell.font = { bold: true, size: 12, color: { argb: 'FFFFFFFF' } };
    titleCell.fill = {
      type: 'pattern',
      pattern: 'solid',
      fgColor: { argb: 'FF111827' },
    };
    titleCell.alignment = { vertical: 'middle', horizontal: 'left' };
    sheet.getRow(1).height = 26;

    /* Header row */
    const headerRow = sheet.getRow(2);
    config.columns.forEach((col, index) => {
      const cell = headerRow.getCell(index + 1);
      cell.value = col.header;
      cell.font = { bold: true, color: { argb: 'FF101828' } };
      cell.fill = {
        type: 'pattern',
        pattern: 'solid',
        fgColor: { argb: 'FFF8FAFC' },
      };
      cell.border = {
        top: { style: 'thin', color: { argb: 'FFE5E7EB' } },
        bottom: { style: 'thin', color: { argb: 'FFE5E7EB' } },
        left: { style: 'thin', color: { argb: 'FFE5E7EB' } },
        right: { style: 'thin', color: { argb: 'FFE5E7EB' } },
      };
      cell.alignment = { vertical: 'middle' };
    });
    headerRow.height = 20;

    sheet.columns = config.columns.map((col) => ({
      key: col.key,
      width: col.width,
      style: col.numFmt ? { numFmt: col.numFmt } : undefined,
    }));

    /* Data rows */
    config.rows.forEach((rowData, rowIndex) => {
      const row = sheet.addRow(rowData);
      const isBanded = rowIndex % 2 === 1;

      row.eachCell((cell) => {
        cell.border = {
          top: { style: 'thin', color: { argb: 'FFEEF0F3' } },
          bottom: { style: 'thin', color: { argb: 'FFEEF0F3' } },
          left: { style: 'thin', color: { argb: 'FFEEF0F3' } },
          right: { style: 'thin', color: { argb: 'FFEEF0F3' } },
        };

        if (isBanded) {
          cell.fill = {
            type: 'pattern',
            pattern: 'solid',
            fgColor: { argb: 'FFF8FAFC' },
          };
        }
      });

      if (config.gainKey) {
        const gainValue = Number(rowData[config.gainKey]);
        const gainCell = row.getCell(config.gainKey);

        gainCell.font = {
          color: { argb: gainValue >= 0 ? 'FF16A34A' : 'FFDC2626' },
          bold: true,
        };
      }
    });

    sheet.autoFilter = {
      from: { row: 2, column: 1 },
      to: { row: 2, column: columnCount },
    };

    const buffer = await workbook.xlsx.writeBuffer();

    const blob = new Blob([buffer], {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });

    this.triggerDownload(blob, config.filename);
  }

  private triggerDownload(blob: Blob, filename: string): void {
    const url = URL.createObjectURL(blob);

    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.style.display = 'none';

    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);

    URL.revokeObjectURL(url);
  }

  private todayStamp(): string {
    const now = new Date();

    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');

    return `${year}-${month}-${day}`;
  }

  private todayLabel(): string {
    return new Intl.DateTimeFormat('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    }).format(new Date());
  }

  /* ============================================================
     DISPLAY FORMATTING
     (same helpers/format conventions as portfolio.component.ts)
     ============================================================ */

  formatCurrency(value: number): string {
    return new Intl.NumberFormat('en-IN', {
      maximumFractionDigits: 0,
    }).format(this.toNumber(value));
  }

  formatAbsoluteCurrency(value: number): string {
    return this.formatCurrency(Math.abs(this.toNumber(value)));
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

  /**
   * Same green/red/gray classification Portfolio uses for its
   * Gain and XIRR cells.
   */
  getPnlClass(value: number): string {
    if (value > 0) {
      return 'positive';
    }

    if (value < 0) {
      return 'negative';
    }

    return 'neutral';
  }

  private toNumber(value: number | null | undefined): number {
    if (value === null || value === undefined) {
      return 0;
    }

    const numberValue = Number(value);

    return Number.isFinite(numberValue) ? numberValue : 0;
  }
}
