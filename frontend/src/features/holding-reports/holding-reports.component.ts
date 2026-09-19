import { CommonModule } from '@angular/common';
import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HoldingReportRow, PortfolioApiService } from '../../core/services/portfolio-api.service';

interface HoldingGroup { key: string; asset_name: string; row: HoldingReportRow; }
interface AssetClassGroup {
  asset_class: string;
  holdings: HoldingGroup[];
  quantity: number;
  invested_value: number;
  current_value: number;
  pnl: number;
  xirr: number | null;
  sub_classes: SubClassGroup[];
}
interface SubClassGroup {
  sub_class: string; holdings: HoldingGroup[]; quantity: number; invested_value: number;
  current_value: number; pnl: number; xirr: number | null;
}
interface HoldingExportRow {
  family_name: string; portfolio: string; asset_class: string; sub_class: string; asset_name: string;
  underlying: string; isin: string; advisors: string; quantity: number; average_cost: number;
  invested_value: number; current_price: number; current_value: number; gain: number;
  pnl_percentage: number; xirr: number | null; sector: string; cap_type: string; amc_name: string;
}

const UNASSIGNED = 'Unassigned';

@Component({
  selector: 'app-holding-reports', standalone: true, imports: [CommonModule, FormsModule],
  templateUrl: './holding-reports.component.html', styleUrl: './holding-reports.component.scss',
})
export class HoldingReportsComponent implements OnInit {
  private readonly portfolioApi = inject(PortfolioApiService);
  private readonly changeDetectorRef = inject(ChangeDetectorRef);
  holdingRows: HoldingReportRow[] = [];
  loading = true; error = '';
  selectedFamily = ''; selectedAssetClass = ''; expandedAssetClass = ''; expandedSubClass = ''; expandedAssetName = '';
  downloading = false;

  ngOnInit(): void { this.loadHoldings(); }
  loadHoldings(): void {
    this.loading = true; this.error = ''; this.changeDetectorRef.detectChanges();
    this.portfolioApi.getHoldingReport().subscribe({
      next: response => {
        this.holdingRows = (response.results ?? []).map(row => this.normalizeHoldingReportRow(row));
        this.validateSelections();
        this.loading = false;
        this.changeDetectorRef.detectChanges();
      },
      error: error => { console.error('Holding report API error:', error); this.loading = false; this.error = error?.status === 401 || error?.status === 403 ? 'Authentication failed. Please log in again.' : 'Unable to load holding report data.'; this.changeDetectorRef.detectChanges(); },
    });
  }
  refresh(): void { this.loadHoldings(); }
  private normalizeHoldingReportRow(row: HoldingReportRow): HoldingReportRow {
    const numeric = (value: number | null | undefined): number | null =>
      value === null || value === undefined || Number.isNaN(Number(value)) ? null : Number(value);

    return {
      ...row,
      asset_class_xirr: numeric(row.asset_class_xirr),
      sub_class_xirr: numeric(row.sub_class_xirr),
      asset_name_xirr: numeric(row.asset_name_xirr),
      xirr: numeric(row.xirr),
      quantity: Number(row.quantity ?? 0),
      average_cost: Number(row.average_cost ?? 0),
      invested_value: Number(row.invested_value ?? 0),
      current_price: Number(row.current_price ?? 0),
      current_value: Number(row.current_value ?? 0),
      gain: Number(row.gain ?? 0),
      gain_percentage: Number(row.gain_percentage ?? 0),
    };
  }

  private clean(value: string | null | undefined, fallback = UNASSIGNED): string { const trimmed = value?.trim(); return trimmed || fallback; }
  getUnderlyingAwareXirr(row: HoldingReportRow): number | null {
    return this.underlyingAwareXirr(row, row.asset_name_xirr);
  }

  private underlyingAwareXirr(row: HoldingReportRow, fallback: number | null): number | null {
    const underlyingEntries = Object.values(row.underlying_xirr ?? {});
    const underlyingXirr = underlyingEntries.find(entry => entry.xirr !== null && entry.xirr !== undefined)?.xirr;
    return underlyingXirr === undefined ? fallback : Number(underlyingXirr);
  }
  get familyOptions(): string[] { return Array.from(new Set(this.holdingRows.map(row => this.clean(row.family_name)))).sort((a,b) => a.localeCompare(b)); }
  get assetClassOptions(): string[] { return Array.from(new Set(this.filteredRows.map(row => this.clean(row.asset_class)))).sort((a,b) => a.localeCompare(b)); }
  private get filteredRows(): HoldingReportRow[] { return this.holdingRows.filter(row => (!this.selectedFamily || this.clean(row.family_name) === this.selectedFamily) && (!this.selectedAssetClass || this.clean(row.asset_class) === this.selectedAssetClass)); }
  get assetClassGroups(): AssetClassGroup[] {
    const groups = new Map<string, HoldingReportRow[]>();
    for (const row of this.filteredRows) {
      const key = this.clean(row.asset_class);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(row);
    }
    return Array.from(groups.entries()).map(([asset_class, rows]) => {
      const subGroups = new Map<string, HoldingReportRow[]>();
      for (const row of rows) {
        const key = this.clean(row.sub_class);
        if (!subGroups.has(key)) subGroups.set(key, []);
        subGroups.get(key)!.push(row);
      }
      const sub_classes = Array.from(subGroups.entries())
        .map(([sub_class, subRows]) => this.buildSubClassGroup(sub_class, subRows))
        .sort((a, b) => a.sub_class.localeCompare(b.sub_class));
      return {
        asset_class,
        holdings: rows.map(row => this.toHoldingGroup(row)),
        quantity: rows.reduce((s, r) => s + this.toNumber(r.quantity), 0),
        invested_value: rows.reduce((s, r) => s + this.toNumber(r.invested_value), 0),
        current_value: rows.reduce((s, r) => s + this.toNumber(r.current_value), 0),
        pnl: rows.reduce((s, r) => s + this.toNumber(r.gain), 0),
        xirr: this.firstNumber(rows.map(r => this.underlyingAwareXirr(r, r.asset_class_xirr))),
        sub_classes,
      };
    }).sort((a, b) => a.asset_class.localeCompare(b.asset_class));
  }

  get subClassGroups(): SubClassGroup[] {
    return this.assetClassGroups.flatMap(group => group.sub_classes);
  }

  private buildSubClassGroup(sub_class: string, rows: HoldingReportRow[]): SubClassGroup {
    const holdings = rows.map(row => this.toHoldingGroup(row)).sort((a, b) => a.asset_name.localeCompare(b.asset_name));
    return {
      sub_class,
      holdings,
      quantity: rows.reduce((s, r) => s + this.toNumber(r.quantity), 0),
      invested_value: rows.reduce((s, r) => s + this.toNumber(r.invested_value), 0),
      current_value: rows.reduce((s, r) => s + this.toNumber(r.current_value), 0),
      pnl: rows.reduce((s, r) => s + this.toNumber(r.gain), 0),
      xirr: this.firstNumber(rows.map(r => this.underlyingAwareXirr(r, r.sub_class_xirr))),
    };
  }

  private toHoldingGroup(row: HoldingReportRow): HoldingGroup {
    return {
      key: row.family_name + '::' + row.portfolio + '::' + row.asset_class + '::' + row.sub_class + '::' + row.asset_id,
      asset_name: this.clean(row.asset_name),
      row,
    };
  }

  trackByAssetClass(_index: number, group: AssetClassGroup): string { return group.asset_class; }

  get holdingCount(): number { return this.filteredRows.length; }
  selectFamily(family: string): void { this.selectedFamily = this.selectedFamily === family ? '' : family; this.selectedAssetClass = ''; this.resetExpansion(); }
  selectAssetClass(assetClass: string): void { this.selectedAssetClass = this.selectedAssetClass === assetClass ? '' : assetClass; this.resetExpansion(); }
  clearFamily(): void { this.selectedFamily = ''; this.selectedAssetClass = ''; this.resetExpansion(); }
  clearAssetClass(): void { this.selectedAssetClass = ''; this.resetExpansion(); }
  isFamilySelected(family: string): boolean { return this.selectedFamily === family; }
  isAssetClassSelected(assetClass: string): boolean { return this.selectedAssetClass === assetClass; }
  toggleAssetClass(assetClass: string): void { this.expandedAssetClass = this.expandedAssetClass === assetClass ? '' : assetClass; this.expandedSubClass = ''; this.expandedAssetName = ''; }
  toggleSubClass(assetClass: string, subClass: string): void { const key = assetClass + '::' + subClass; this.expandedSubClass = this.expandedSubClass === key ? '' : key; this.expandedAssetName = ''; }
  isSubClassExpanded(assetClass: string, subClass: string): boolean { return this.expandedSubClass === assetClass + '::' + subClass; }
  toggleAssetName(key: string): void { this.expandedAssetName = this.expandedAssetName === key ? '' : key; }
  getAssetKey(subClass: string, holding: HoldingGroup): string { return `${subClass}::${holding.key}`; }
  trackBySubClass(_index: number, group: SubClassGroup): string { return group.sub_class; }
  trackByHolding(_index: number, group: HoldingGroup): string { return group.key; }
  formatCurrency(value: number): string { return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(this.toNumber(value)); }
  formatAbsoluteCurrency(value: number): string { return this.formatCurrency(Math.abs(this.toNumber(value))); }
  formatNumber(value: number): string { return new Intl.NumberFormat('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(this.toNumber(value)); }
  formatDecimal(value: number): string { return new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(this.toNumber(value)); }
  formatPercentage(value: number | null): string { return value === null || value === undefined ? '-' : `${this.formatDecimal(value)}%`; }
  getPnlClass(value: number): string { return value > 0 ? 'positive' : value < 0 ? 'negative' : 'neutral'; }

  async downloadAssetClassSubClassXirrReport(group: AssetClassGroup, event?: Event): Promise<void> {
    event?.stopPropagation();
    const rows = group.sub_classes.map(subClass => this.toSubClassXirrExportRow(subClass));
    await this.downloadRows(
      rows,
      this.subClassXirrColumns(),
      'holding_report_' + this.slugify(group.asset_class) + '_sub_class_xirr' + this.fileSuffix() + '_' + this.todayStamp() + '.xlsx',
      'Sub Class XIRR Report — ' + group.asset_class + ' (as of ' + this.todayLabel() + ')'
    );
  }

  async downloadAssetClassReport(group: AssetClassGroup, event?: Event): Promise<void> {
    event?.stopPropagation();
    const rows = group.holdings.map(h => this.toExportRow(h.row, group.xirr, true));
    await this.downloadRows(rows, this.fullColumns(), 'holding_report_asset_class_' + this.slugify(group.asset_class) + this.fileSuffix() + '_' + this.todayStamp() + '.xlsx', 'Asset Class Holding Report — ' + group.asset_class + ' (XIRR ' + this.formatPercentage(group.xirr) + ')');
  }

  async downloadAllAssetClassXirrReport(event?: Event): Promise<void> {
    event?.stopPropagation();
    const rows = this.assetClassGroups.map(group => this.toAssetClassXirrExportRow(group));
    await this.downloadRows(rows, this.subClassXirrColumns(), 'holding_report_asset_class_xirr' + this.fileSuffix() + '_' + this.todayStamp() + '.xlsx', 'Asset Class XIRR Report — ' + this.reportScope() + ' (as of ' + this.todayLabel() + ')');
  }

  async downloadHoldingReport(): Promise<void> {
    await this.downloadRows(this.flattenFilteredHoldings(), this.fullColumns(), `holding_report${this.fileSuffix()}_${this.todayStamp()}.xlsx`, `Holding Report — ${this.reportScope()} (as of ${this.todayLabel()})`);
  }
  async downloadAllSubClassXirrReport(event?: Event): Promise<void> {
    event?.stopPropagation();
    const rows = this.subClassGroups.map(group => this.toSubClassXirrExportRow(group));
    await this.downloadRows(rows, this.subClassXirrColumns(), `holding_report_sub_class_xirr${this.fileSuffix()}_${this.todayStamp()}.xlsx`, `Sub Class XIRR Report — ${this.reportScope()} (as of ${this.todayLabel()})`);
  }
  async downloadSubClassReport(group: SubClassGroup, event?: Event): Promise<void> {
    event?.stopPropagation();
    // Portfolio-style Sub Class report: one row per Asset Name, with that
    // Asset Name's own XIRR (not the Sub Class XIRR or underlying XIRR).
    const rows = this.toAssetNameXirrRows(group);
    await this.downloadRows(
      rows,
      this.subClassAssetNameColumns(),
      `holding_report_sub_class_${this.slugify(group.sub_class)}_asset_name_xirr_${this.todayStamp()}.xlsx`,
      `Sub Class — Asset Name XIRR Report — ${group.sub_class} (as of ${this.todayLabel()})`
    );
  }
  async downloadAssetNameReport(group: SubClassGroup, holding: HoldingGroup, event?: Event): Promise<void> {
    event?.stopPropagation();
    const assetRows = group.holdings.filter(item => item.asset_name === holding.asset_name).map(item => this.toExportRow(item.row, holding.row.asset_name_xirr, true));
    await this.downloadRows(assetRows, this.assetNameColumns(), `holding_report_asset_${this.slugify(holding.asset_name)}_${this.todayStamp()}.xlsx`, `Asset Name Holding Report — ${holding.asset_name} (as of ${this.todayLabel()})`);
  }
  private async downloadRows(rows: HoldingExportRow[], columns: Array<{header:string; key:string; width:number; numFmt?:string}>, filename: string, title: string): Promise<void> {
    if (this.downloading || !rows.length) return;
    this.downloading = true;
    try {
      const { default: ExcelJSLib } = await import('exceljs');
      const workbook = new ExcelJSLib.Workbook(); workbook.creator = 'PWMS'; workbook.created = new Date();
      const sheet = workbook.addWorksheet('Holdings', { views: [{ state: 'frozen', ySplit: 2 }] });
      sheet.mergeCells(1,1,1,columns.length); const titleCell = sheet.getCell(1,1); titleCell.value = title; titleCell.font = { bold:true, size:12, color:{argb:'FFFFFFFF'} }; titleCell.fill = { type:'pattern', pattern:'solid', fgColor:{argb:'FF111827'} }; titleCell.alignment = { vertical:'middle', horizontal:'left' }; sheet.getRow(1).height = 26;
      const headerRow = sheet.getRow(2); columns.forEach((column,index) => { const cell=headerRow.getCell(index+1); cell.value=column.header; cell.font={bold:true,color:{argb:'FF101828'}}; cell.fill={type:'pattern',pattern:'solid',fgColor:{argb:'FFF8FAFC'}}; });
      sheet.columns = columns.map(column => ({ key:column.key, width:column.width, style:column.numFmt ? {numFmt:column.numFmt} : undefined }));
      rows.forEach((rowData,index) => { const row=sheet.addRow(rowData); row.eachCell(cell => { if(index%2===1) cell.fill={type:'pattern',pattern:'solid',fgColor:{argb:'FFF8FAFC'}}; }); if (rowData.gain !== undefined) row.getCell('gain').font={color:{argb:Number(rowData.gain)>=0?'FF16A34A':'FFDC2626'},bold:true}; });
      sheet.autoFilter={from:{row:2,column:1},to:{row:2,column:columns.length}};
      const buffer=await workbook.xlsx.writeBuffer(); const blob=new Blob([buffer],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}); const url=URL.createObjectURL(blob); const anchor=document.createElement('a'); anchor.href=url; anchor.download=filename; anchor.style.display='none'; document.body.appendChild(anchor); anchor.click(); document.body.removeChild(anchor); URL.revokeObjectURL(url);
    } finally { this.downloading=false; }
  }
  private fullColumns() { return [
    {header:'Family Name',key:'family_name',width:24},{header:'Portfolio',key:'portfolio',width:24},{header:'Asset Class',key:'asset_class',width:18},{header:'Sub Class',key:'sub_class',width:22},{header:'Asset Name',key:'asset_name',width:30},{header:'Underlying',key:'underlying',width:28},{header:'ISIN',key:'isin',width:18},{header:'Advisor',key:'advisors',width:24},{header:'Quantity',key:'quantity',width:14,numFmt:'#,##0.00'},{header:'Average Cost',key:'average_cost',width:16,numFmt:'"₹"#,##0.00'},{header:'Invested Value',key:'invested_value',width:18,numFmt:'"₹"#,##0'},{header:'Current Price / NAV',key:'current_price',width:18,numFmt:'"₹"#,##0.00'},{header:'Current Value',key:'current_value',width:18,numFmt:'"₹"#,##0'},{header:'Gain',key:'gain',width:18,numFmt:'"₹"#,##0'},{header:'Gain %',key:'pnl_percentage',width:14,numFmt:'0.00"%"'},{header:'XIRR (%)',key:'xirr',width:14,numFmt:'0.00"%"'},{header:'Sector',key:'sector',width:24},{header:'Cap Type',key:'cap_type',width:16},{header:'AMC',key:'amc_name',width:24}
  ]; }
  private subClassColumns() { return [
    {header:'Family Name',key:'family_name',width:24},{header:'Portfolio',key:'portfolio',width:24},{header:'Asset Class',key:'asset_class',width:18},{header:'Sub Class',key:'sub_class',width:22},{header:'Underlying',key:'underlying',width:28},{header:'ISIN',key:'isin',width:18},{header:'Advisor',key:'advisors',width:24},{header:'Quantity',key:'quantity',width:14,numFmt:'#,##0.00'},{header:'Average Cost',key:'average_cost',width:16,numFmt:'"₹"#,##0.00'},{header:'Invested Value',key:'invested_value',width:18,numFmt:'"₹"#,##0'},{header:'Current Price / NAV',key:'current_price',width:18,numFmt:'"₹"#,##0.00'},{header:'Current Value',key:'current_value',width:18,numFmt:'"₹"#,##0'},{header:'Gain',key:'gain',width:18,numFmt:'"₹"#,##0'},{header:'Gain %',key:'pnl_percentage',width:14,numFmt:'0.00"%"'},{header:'XIRR (%)',key:'xirr',width:14,numFmt:'0.00"%"'}
  ]; }
  private subClassXirrColumns() { return [
    {header:'Family Name',key:'family_name',width:24},{header:'Portfolio',key:'portfolio',width:24},{header:'Asset Class',key:'asset_class',width:18},{header:'Sub Class',key:'sub_class',width:22},{header:'Quantity',key:'quantity',width:14,numFmt:'#,##0.00'},{header:'Invested Value',key:'invested_value',width:18,numFmt:'"₹"#,##0'},{header:'Current Value',key:'current_value',width:18,numFmt:'"₹"#,##0'},{header:'Gain',key:'gain',width:18,numFmt:'"₹"#,##0'},{header:'Gain %',key:'pnl_percentage',width:14,numFmt:'0.00"%"'},{header:'XIRR (%)',key:'xirr',width:14,numFmt:'0.00"%"'}
  ]; }
  private assetNameColumns() { return [
    {header:'Family Name',key:'family_name',width:24},{header:'Portfolio',key:'portfolio',width:24},{header:'Asset Class',key:'asset_class',width:18},{header:'Sub Class',key:'sub_class',width:22},{header:'Asset Name',key:'asset_name',width:30},{header:'Underlying',key:'underlying',width:28},{header:'ISIN',key:'isin',width:18},{header:'Advisor',key:'advisors',width:24},{header:'Quantity',key:'quantity',width:14,numFmt:'#,##0.00'},{header:'Average Cost',key:'average_cost',width:16,numFmt:'"₹"#,##0.00'},{header:'Invested Value',key:'invested_value',width:18,numFmt:'"₹"#,##0'},{header:'Current Price / NAV',key:'current_price',width:18,numFmt:'"₹"#,##0.00'},{header:'Current Value',key:'current_value',width:18,numFmt:'"₹"#,##0'},{header:'Gain',key:'gain',width:18,numFmt:'"₹"#,##0'},{header:'Gain %',key:'pnl_percentage',width:14,numFmt:'0.00"%"'},{header:'XIRR (%)',key:'xirr',width:14,numFmt:'0.00"%"'}
  ]; }
    private subClassAssetNameColumns() { return [
    {header:'Family Name',key:'family_name',width:24},{header:'Portfolio',key:'portfolio',width:24},{header:'Asset Class',key:'asset_class',width:18},{header:'Sub Class',key:'sub_class',width:22},{header:'Asset Name',key:'asset_name',width:30},{header:'Underlying',key:'underlying',width:28},{header:'ISIN',key:'isin',width:18},{header:'Advisor',key:'advisors',width:24},{header:'Quantity',key:'quantity',width:14,numFmt:'#,##0.00'},{header:'Average Cost',key:'average_cost',width:16,numFmt:'"₹"#,##0.00'},{header:'Invested Value',key:'invested_value',width:18,numFmt:'"₹"#,##0'},{header:'Current Value',key:'current_value',width:18,numFmt:'"₹"#,##0'},{header:'Gain',key:'gain',width:18,numFmt:'"₹"#,##0'},{header:'Gain %',key:'pnl_percentage',width:14,numFmt:'0.00"%"'},{header:'XIRR (%)',key:'xirr',width:14,numFmt:'0.00"%"'}
  ]; }
  private flattenFilteredHoldings(): HoldingExportRow[] { return this.filteredRows.map(row => this.toExportRow(row,row.xirr,true)).sort((a,b) => a.family_name.localeCompare(b.family_name)||a.portfolio.localeCompare(b.portfolio)||a.asset_class.localeCompare(b.asset_class)||a.sub_class.localeCompare(b.sub_class)||a.asset_name.localeCompare(b.asset_name)); }
  private toAssetNameXirrRows(group: SubClassGroup): HoldingExportRow[] {
    const assetGroups = new Map<string, HoldingGroup[]>();
    for (const holding of group.holdings) {
      const key = holding.asset_name;
      if (!assetGroups.has(key)) assetGroups.set(key, []);
      assetGroups.get(key)!.push(holding);
    }

    return Array.from(assetGroups.entries()).map(([asset_name, holdings]) => {
      const first = holdings[0].row;
      const quantity = holdings.reduce((sum, item) => sum + this.toNumber(item.row.quantity), 0);
      const invested_value = holdings.reduce((sum, item) => sum + this.toNumber(item.row.invested_value), 0);
      const current_value = holdings.reduce((sum, item) => sum + this.toNumber(item.row.current_value), 0);
      const gain = holdings.reduce((sum, item) => sum + this.toNumber(item.row.gain), 0);
      const assetNameXirr = this.firstNumber(holdings.map(item => item.row.asset_name_xirr));

      return {
        family_name: this.clean(first.family_name),
        portfolio: this.clean(first.portfolio),
        asset_class: this.clean(first.asset_class),
        sub_class: this.clean(first.sub_class),
        asset_name,
        underlying: this.clean(first.underlying, ''),
        isin: first.isin || '-',
        advisors: this.clean(first.advisors, ''),
        quantity,
        average_cost: quantity ? invested_value / quantity : 0,
        invested_value,
        current_price: holdings.length === 1 ? this.toNumber(first.current_price) : 0,
        current_value,
        gain,
        pnl_percentage: invested_value ? (gain / invested_value) * 100 : 0,
        xirr: assetNameXirr,
        sector: first.sector || '-',
        cap_type: first.cap_type || '-',
        amc_name: first.amc_name || '-',
      };
    }).sort((a, b) => a.asset_name.localeCompare(b.asset_name));
  }

  private toSubClassXirrExportRow(group: SubClassGroup): HoldingExportRow {
    return {family_name:group.holdings[0]?.row.family_name ? this.clean(group.holdings[0].row.family_name) : '',portfolio:group.holdings[0]?.row.portfolio ? this.clean(group.holdings[0].row.portfolio) : '',asset_class:group.holdings[0]?.row.asset_class ? this.clean(group.holdings[0].row.asset_class) : '',sub_class:group.sub_class,asset_name:'',underlying:'',isin:'-',advisors:'',quantity:this.toNumber(group.quantity),average_cost:group.quantity ? group.invested_value / group.quantity : 0,invested_value:this.toNumber(group.invested_value),current_price:0,current_value:this.toNumber(group.current_value),gain:this.toNumber(group.pnl),pnl_percentage:group.invested_value ? (group.pnl / group.invested_value) * 100 : 0,xirr:group.xirr,sector:'-',cap_type:'-',amc_name:'-'};
  }
  private toAssetClassXirrExportRow(group: AssetClassGroup): HoldingExportRow {
    const first = group.holdings[0]?.row;
    return {
      family_name: first ? this.clean(first.family_name) : '',
      portfolio: first ? this.clean(first.portfolio) : '',
      asset_class: group.asset_class,
      sub_class: '',
      asset_name: '',
      underlying: '',
      isin: '-',
      advisors: '',
      quantity: group.quantity,
      average_cost: group.quantity ? group.invested_value / group.quantity : 0,
      invested_value: group.invested_value,
      current_price: 0,
      current_value: group.current_value,
      gain: group.pnl,
      pnl_percentage: group.invested_value ? (group.pnl / group.invested_value) * 100 : 0,
      xirr: group.xirr,
      sector: '-',
      cap_type: '-',
      amc_name: '-',
    };
  }

  private toExportRow(row: HoldingReportRow, xirr: number | null, includeAssetName: boolean): HoldingExportRow {
    return {family_name:this.clean(row.family_name),portfolio:this.clean(row.portfolio),asset_class:this.clean(row.asset_class),sub_class:this.clean(row.sub_class),asset_name:includeAssetName?this.clean(row.asset_name):'',underlying:this.clean(row.underlying,''),isin:row.isin||'-',advisors:this.clean(row.advisors,''),quantity:this.toNumber(row.quantity),average_cost:this.toNumber(row.average_cost),invested_value:this.toNumber(row.invested_value),current_price:this.toNumber(row.current_price),current_value:this.toNumber(row.current_value),gain:this.toNumber(row.gain),pnl_percentage:this.toNumber(row.gain_percentage),xirr,sector:row.sector||'-',cap_type:row.cap_type||'-',amc_name:row.amc_name||'-'};
  }
  private reportScope(): string { return `${this.selectedFamily || 'All Families'} — ${this.selectedAssetClass || 'All Asset Classes'}`; }
  private fileSuffix(): string { return `${this.selectedFamily?'_'+this.slugify(this.selectedFamily):''}${this.selectedAssetClass?'_'+this.slugify(this.selectedAssetClass):''}`; }
  private slugify(value:string):string { return value.trim().toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^_+|_+$/g,''); }
  private todayStamp(): string { const now=new Date(); return `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`; }
  private todayLabel(): string { return new Intl.DateTimeFormat('en-IN',{day:'2-digit',month:'short',year:'numeric'}).format(new Date()); }
  private firstNumber(values: Array<number|null|undefined>): number|null { const value=values.find(item => item !== null && item !== undefined); return value === undefined ? null : Number(value); }
  private toNumber(value:number|null|undefined):number { if(value===null||value===undefined)return 0; const n=Number(value); return Number.isFinite(n)?n:0; }
  private validateSelections(): void { if(this.selectedFamily&&!this.familyOptions.includes(this.selectedFamily)){this.selectedFamily='';this.selectedAssetClass='';} if(this.selectedAssetClass&&!this.assetClassOptions.includes(this.selectedAssetClass))this.selectedAssetClass=''; }
  private resetExpansion(): void { this.expandedAssetClass=''; this.expandedSubClass=''; this.expandedAssetName=''; }
}