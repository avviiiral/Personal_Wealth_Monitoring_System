import { CommonModule } from '@angular/common';
import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import {
  LiquidPositionDocument,
  LiquidPositionLine,
  LiquidPositionReport,
  LiquidPositionService,
} from '../../core/services/liquid-position.service';

@Component({
  selector: 'app-liquid-position',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './liquid-position.component.html',
  styleUrl: './liquid-position.component.scss',
})
export class LiquidPositionComponent implements OnInit {
  private readonly service = inject(LiquidPositionService);
  private readonly cdr = inject(ChangeDetectorRef);
  private lineSequence = 0;

  report: LiquidPositionReport | null = null;
  asOfDate = '';
  loading = true;
  saving = false;
  isDirty = false;
  isEditing = false;
  private editBackup: LiquidPositionReport | null = null;
  error = '';
  success = '';

  ngOnInit(): void {
    this.asOfDate = this.localDateStamp(new Date());
    this.loadPosition();
  }

  loadPosition(): void {
    if (!this.asOfDate) {
      this.error = 'Select a reporting date.';
      return;
    }

    this.loading = true;
    this.isDirty = false;
    this.isEditing = false;
    this.editBackup = null;
    this.error = '';
    this.success = '';

    this.service.getPosition(this.asOfDate).subscribe({
      next: (report) => {
        this.report = report;
        this.loading = false;
        this.cdr.markForCheck();
      },
      error: (error) => {
        this.report = null;
        this.loading = false;
        this.error = error?.error?.detail
          || (error?.status === 403
            ? 'You do not have access to the selected family.'
            : 'Unable to load the Liquid Position. Please try again.');
        this.cdr.markForCheck();
      },
    });
  }

  beginEdit(): void {
    if (!this.report?.can_edit || this.saving || this.isEditing) return;
    this.editBackup = JSON.parse(JSON.stringify(this.report)) as LiquidPositionReport;
    this.isEditing = true;
    this.success = '';
  }

  cancelEdit(): void {
    if (!this.isEditing || this.saving) return;
    if (this.editBackup) this.report = this.editBackup;
    this.editBackup = null;
    this.isEditing = false;
    this.isDirty = false;
    this.error = '';
    this.success = '';
    this.cdr.markForCheck();
  }

  markDirty(): void {
    if (!this.isEditing) return;
    this.isDirty = true;
    this.success = '';
  }

  addCommitment(): void {
    if (!this.report?.can_edit || !this.isEditing) return;
    this.report.commitments.push(this.newLine('New commitment'));
    this.markDirty();
  }

  addDeployment(): void {
    if (!this.report?.can_edit || !this.isEditing) return;
    this.report.deployment.push(this.newLine('New deployment'));
    this.markDirty();
  }

  removeLine(section: 'commitments' | 'deployment', index: number): void {
    if (!this.report?.can_edit || !this.isEditing) return;
    this.report[section].splice(index, 1);
    this.markDirty();
  }

  trackByLine(_index: number, line: LiquidPositionLine): string {
    return line.id;
  }

  save(): void {
    if (!this.report?.can_edit || !this.isEditing || !this.isDirty || this.saving) return;

    const document: LiquidPositionDocument = {
      bank: {
        daj: this.report.bank.daj,
        djt: this.report.bank.djt,
      },
      commitments: this.report.commitments.map(({ id, label, daj, djt }) => ({
        id, label, daj, djt,
      })),
      deployment: this.report.deployment.map(({ id, label, daj, djt }) => ({
        id, label, daj, djt,
      })),
    };

    this.saving = true;
    this.error = '';
    this.success = '';

    this.service.savePosition(this.asOfDate, document).subscribe({
      next: (report) => {
        this.report = report;
        this.isDirty = false;
        this.isEditing = false;
        this.editBackup = null;
        this.saving = false;
        this.success = 'Liquid Position saved. Backend totals have been refreshed.';
        this.cdr.markForCheck();
      },
      error: (error) => {
        this.saving = false;
        this.error = error?.error?.detail
          || error?.error?.bank?.daj?.[0]
          || error?.error?.commitments?.[0]
          || 'Unable to save Liquid Position changes.';
        this.cdr.markForCheck();
      },
    });
  }

  formatAmount(value: number | null | undefined): string {
    return new Intl.NumberFormat('en-IN', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(Number(value ?? 0));
  }

  formatCalculated(value: number): string {
    return this.isDirty ? 'Save to recalculate' : this.formatAmount(value);
  }

  formatSavedAt(value: string | null): string {
    if (!value) return 'Not saved yet';
    return new Intl.DateTimeFormat('en-IN', {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(value));
  }

  private newLine(label: string): LiquidPositionLine {
    this.lineSequence += 1;
    return {
      id: 'line-' + Date.now() + '-' + this.lineSequence,
      label,
      daj: 0,
      djt: 0,
      total: 0,
    };
  }

  private localDateStamp(value: Date): string {
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, '0');
    const day = String(value.getDate()).padStart(2, '0');
    return year + '-' + month + '-' + day;
  }
}
