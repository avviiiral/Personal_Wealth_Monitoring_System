import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ChangeDetectorRef, Component, inject, OnInit } from '@angular/core';

import {
  MISDataSheetRow,
  MISTaxReportRow,
  MISReport,
  MISReportService,
  MISEditableNotes,
  MISEditableSection,
  MISEditableColumn,
  MISEditableRow,
} from '../../core/services/mis-report.service';

type MISSheet = 'ips' | 'data' | 'tax' | 'fund-summary' | 'notes';

@Component({
  selector: 'app-mis-report',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './mis-report.component.html',
  styleUrl: './mis-report.component.scss',
})
export class MISReportComponent implements OnInit {
  private readonly service = inject(MISReportService);
  private readonly cdr = inject(ChangeDetectorRef);

  report: MISReport | null = null;
  activeSheet: MISSheet = 'ips';
  loading = true;
  downloading = false;
  savingNotes = false;
  editingNotes = false;
  autoFillNewRows = true;
  error = '';
  notesError = '';
  fromDate = '';
  toDate = '';
  todayDate = '';
  displayUnit: 'amount' | 'lakhs' | 'crores' = 'lakhs';
  editableNotes: MISEditableNotes | null = null;

  readonly sheets: Array<{ key: MISSheet; label: string }> = [
    { key: 'ips', label: 'IPS' },
    { key: 'data', label: 'Data Sheet' },
    { key: 'tax', label: 'Tax Report' },
    { key: 'fund-summary', label: 'Fund Type-wise Summary' },
    { key: 'notes', label: 'Notes' },
  ];

  ngOnInit(): void {
    const today = new Date();
    this.todayDate = this.toInputDate(today);
    this.toDate = this.todayDate;
    const start = new Date(today.getFullYear(), today.getMonth(), 1);
    this.fromDate = this.toInputDate(start);
    this.loadReport();
  }

  loadReport(): void {
    if (this.fromDate && this.toDate && this.fromDate > this.toDate) {
      this.error = 'From date cannot be after To date.';
      return;
    }
    this.loading = true;
    this.error = '';
    this.notesError = '';
    this.service.getReport(this.fromDate, this.toDate, this.displayUnit).subscribe({
      next: (report) => {
        this.report = report;
        this.editableNotes = this.cloneNotes(report.notes.editable);
        this.editingNotes = false;
        this.loading = false;
        this.cdr.markForCheck();
      },
      error: (error) => {
        this.loading = false;
        this.error = error?.status === 403
          ? 'You do not have access to an active family portfolio.'
          : 'Unable to load the MIS Report. Please try again.';
        this.cdr.markForCheck();
      },
    });
  }

  selectSheet(sheet: MISSheet): void {
    this.activeSheet = sheet;
    if (sheet !== 'notes') this.editingNotes = false;
  }

  startNotesEditing(): void {
    if (!this.report) return;
    this.editableNotes = this.cloneNotes(this.report.notes.editable);
    this.notesError = '';
    this.editingNotes = true;
  }

  cancelNotesEditing(): void {
    if (!this.report) return;
    this.editableNotes = this.cloneNotes(this.report.notes.editable);
    this.notesError = '';
    this.editingNotes = false;
  }

  saveNotes(): void {
    if (!this.editableNotes || this.savingNotes) return;
    this.savingNotes = true;
    this.notesError = '';

    this.service.saveNotes(
      this.editableNotes,
      this.fromDate,
      this.toDate,
      this.autoFillNewRows,
    ).subscribe({
      next: (response) => {
        if (this.report) this.report.notes = response.notes;
        this.editableNotes = this.cloneNotes(response.notes.editable);
        this.editingNotes = false;
        this.savingNotes = false;
        this.cdr.markForCheck();
      },
      error: (error) => {
        this.savingNotes = false;
        this.notesError = error?.error?.detail || 'Unable to save MIS Notes changes.';
        this.cdr.markForCheck();
      },
    });
  }

  addSection(): void {
    if (!this.editableNotes) return;
    const number = this.editableNotes.sections.length + 1;
    const section: MISEditableSection = {
      id: this.newId('section'),
      section_number: number,
      title: 'New Section',
      note: null,
      columns: [
        { id: this.newId('column'), label: 'Particulars', type: 'text' },
        { id: this.newId('column'), label: 'Value', type: 'number' },
      ],
      rows: [],
    };
    this.editableNotes.sections.push(section);
  }

  removeSection(index: number): void {
    if (!this.editableNotes) return;
    this.editableNotes.sections.splice(index, 1);
    this.editableNotes.sections.forEach((section, i) => section.section_number = i + 1);
  }

  addColumn(section: MISEditableSection): void {
    const label = window.prompt('Column name:', 'New Column');
    if (!label?.trim()) return;
    const type = window.prompt('Column type (text or number):', 'text')?.trim().toLowerCase() === 'number'
      ? 'number'
      : 'text';
    const column: MISEditableColumn = {
      id: this.newId('column'),
      label: label.trim(),
      type,
    };
    section.columns.push(column);
    section.rows.forEach((row) => row.cells[column.id] = type === 'number' ? null : '');
  }

  removeColumn(section: MISEditableSection, index: number): void {
    if (section.columns.length <= 1) {
      this.notesError = 'A Notes section must keep at least one column.';
      return;
    }
    const [column] = section.columns.splice(index, 1);
    section.rows.forEach((row) => delete row.cells[column.id]);
  }

  addRow(section: MISEditableSection): void {
    const cells: Record<string, string | number | null> = {};
    section.columns.forEach((column) => {
      cells[column.id] = column.type === 'number' ? null : '';
    });
    section.rows.push({
      id: this.newId('row'),
      cells,
    });
  }

  coerceCellValue(column: MISEditableColumn, value: string): string | number | null {
    if (column.type !== 'number') return value;
    if (value === '') return null;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  }

  removeRow(section: MISEditableSection, index: number): void {
    section.rows.splice(index, 1);
  }

  downloadExcel(): void {
    if (this.downloading || !this.report) return;
    if (!this.fromDate || !this.toDate || this.fromDate > this.toDate) {
      this.error = 'Please select a valid date range.';
      return;
    }
    this.downloading = true;
    this.service.downloadReport(this.fromDate, this.toDate, this.displayUnit).subscribe({
      next: (blob) => {
        const filename = 'MIS_Report_' + this.safeFilename(this.report?.family_name ?? 'Family') + '_' + (this.report?.reporting_date ?? '') + '.xlsx';
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename;
        anchor.click();
        URL.revokeObjectURL(url);
        this.downloading = false;
        this.cdr.markForCheck();
      },
      error: () => {
        this.downloading = false;
        this.error = 'Unable to download the MIS Report Excel file. Please try again.';
        this.cdr.markForCheck();
      },
    });
  }

  get dataRows(): MISDataSheetRow[] { return this.report?.data_sheet ?? []; }
  get taxRows(): MISTaxReportRow[] { return this.report?.tax_report ?? []; }

  get fundSummaryGrandTotal(): number {
    return (this.report?.fund_type_summary ?? []).reduce((total, group) => total + Number(group.subtotal || 0), 0);
  }

  ipsGrandTotal(family?: string): number {
    const rows = this.report?.ips ?? [];
    if (family) return rows.reduce((total, row) => total + Number(row.family_values?.[family] || 0), 0);
    return rows.reduce((total, row) => total + Number(row.grand_total || 0), 0);
  }

  formatNumber(value: number | null | undefined, digits = 2): string {
    if (value === null || value === undefined) return '—';
    return new Intl.NumberFormat('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
  }

  setDisplayUnit(unit: 'amount' | 'lakhs' | 'crores'): void {
    this.displayUnit = unit;
  }

  get displayUnitLabel(): string {
    return this.displayUnit === 'amount' ? '₹ Amount' : this.displayUnit === 'lakhs' ? '₹ Lakhs' : '₹ Crores';
  }

  formatDisplayAmount(value: number | null | undefined, digits = 2): string {
    if (value === null || value === undefined) return '—';
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) return '—';
    const divisor = this.displayUnit === 'amount' ? 1 : this.displayUnit === 'lakhs' ? 100000 : 10000000;
    return new Intl.NumberFormat('en-IN', {
      minimumFractionDigits: this.displayUnit === 'amount' ? 0 : digits,
      maximumFractionDigits: this.displayUnit === 'amount' ? 0 : digits,
    }).format(numericValue / divisor);
  }

  formatDate(value: string | null | undefined): string {
    if (!value) return '—';
    const parsed = new Date(value + 'T00:00:00');
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
  }

  trackByDataRow(index: number, row: MISDataSheetRow): string {
    return row.family_name + '-' + row.asset_class + '-' + row.asset_name + '-' + index;
  }

  trackByEditableRow(index: number, row: MISEditableRow): string {
    return row.id + '-' + index;
  }

  trackByEditableColumn(index: number, column: MISEditableColumn): string {
    return column.id + '-' + index;
  }

  private cloneNotes(notes: MISEditableNotes): MISEditableNotes {
    return JSON.parse(JSON.stringify(notes)) as MISEditableNotes;
  }

  private newId(prefix: string): string {
    return prefix + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
  }

  private toInputDate(value: Date): string {
    return value.toISOString().slice(0, 10);
  }

  private safeFilename(value: string): string {
    return value.replace(/[^a-zA-Z0-9._-]+/g, '_').replace(/^[_\\.]+|[_\\.]+$/g, '') || 'Family';
  }
}
