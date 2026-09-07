import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ToastService } from '../../../core/services/toast.service';

import {
  FamilyGroup,
  ManagedUser,
  UserManagementApiService,
} from '../../../core/services/user-management-api.service';

import { ROLE_LABELS } from '../../../core/services/rbac.service';

/**
 * System Owner-only screen: create/rename families, and add or
 * remove members - the ONLY place in the app that can change
 * family membership. A user may be added to any number of
 * families at once; adding to one never removes them from
 * another (see users.api_views.group_add_member on the backend).
 */
@Component({
  selector: 'app-family-management',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './family-management.component.html',
  styleUrl: './family-management.component.scss',
})
export class FamilyManagementComponent implements OnInit {
  private readonly api = inject(UserManagementApiService);

  private readonly toast = inject(ToastService);

  private readonly cdr = inject(ChangeDetectorRef);

  readonly roleLabels = ROLE_LABELS;

  families: FamilyGroup[] = [];

  allUsers: ManagedUser[] = [];

  loading = true;

  error = '';

  newFamilyName = '';

  creatingFamily = false;

  renamingFamilyId: number | null = null;

  renameFamilyText = '';

  addMemberSelection: Record<number, number | null> = {};

  ngOnInit(): void {
    this.loadFamilies();
    this.loadUsers();
  }

  loadFamilies(): void {
    this.loading = true;
    this.error = '';

    this.api.listGroups().subscribe({
      next: (families) => {
        this.families = families;
        this.loading = false;
        this.cdr.detectChanges();
      },

      error: (err) => {
        this.loading = false;
        this.error = this.extractError(err) || 'Unable to load families.';
        this.cdr.detectChanges();
      },
    });
  }

  loadUsers(): void {
    this.api.listUsers().subscribe({
      next: (users) => {
        this.allUsers = users;
        this.cdr.detectChanges();
      },
      error: () => {
        // Non-fatal: the "add member" dropdown simply stays empty.
      },
    });
  }

  usersNotInFamily(family: FamilyGroup): ManagedUser[] {
    const memberIds = new Set(family.members.map((m) => m.id));

    return this.allUsers.filter((u) => !memberIds.has(u.id));
  }

  createFamily(): void {
    const name = this.newFamilyName.trim();

    if (this.creatingFamily) {
      return;
    }

    if (!name) {
      this.toast.error('Please enter a family name.');
      return;
    }

    this.creatingFamily = true;

    this.api.createGroup(name).subscribe({
      next: () => {
        this.creatingFamily = false;
        this.newFamilyName = '';

        this.toast.success(`Family "${name}" created.`);

        this.loadFamilies();
      },

      error: (err) => {
        this.creatingFamily = false;

        this.toast.error(this.extractError(err) || 'Unable to create family.');

        this.cdr.detectChanges();
      },
    });
  }

  startRename(family: FamilyGroup): void {
    this.renamingFamilyId = family.id;
    this.renameFamilyText = family.name;
  }

  cancelRename(): void {
    this.renamingFamilyId = null;
    this.renameFamilyText = '';
  }

  saveRename(family: FamilyGroup): void {
    const name = this.renameFamilyText.trim();

    if (!name) {
      return;
    }

    this.api.renameGroup(family.id, name).subscribe({
      next: () => {
        this.cancelRename();
        this.loadFamilies();
      },

      error: (err) => {
        this.toast.error(this.extractError(err) || 'Unable to rename family.');
      },
    });
  }

  deleteFamily(family: FamilyGroup): void {
    const confirmed = window.confirm(
      `Delete family "${family.name}"? Members will no longer share this family's data - their own accounts and other family memberships are untouched.`,
    );

    if (!confirmed) {
      return;
    }

    this.api.deleteGroup(family.id).subscribe({
      next: () => {
        this.toast.success(`Family "${family.name}" deleted.`);

        this.loadFamilies();
        this.loadUsers();
      },

      error: (err) => {
        this.toast.error(this.extractError(err) || 'Unable to delete family.');
      },
    });
  }

  addMember(family: FamilyGroup): void {
    const userId = this.addMemberSelection[family.id];

    if (!userId) {
      return;
    }

    this.api.addGroupMember(family.id, userId).subscribe({
      next: () => {
        this.addMemberSelection[family.id] = null;

        this.loadFamilies();
        this.loadUsers();
      },

      error: (err) => {
        this.toast.error(this.extractError(err) || 'Unable to add member.');
      },
    });
  }

  removeMember(family: FamilyGroup, userId: number): void {
    this.api.removeGroupMember(family.id, userId).subscribe({
      next: () => {
        this.loadFamilies();
        this.loadUsers();
      },

      error: (err) => {
        this.toast.error(this.extractError(err) || 'Unable to remove member.');
      },
    });
  }

  /** How many OTHER families this user also belongs to, for the "also in ..." hint. */
  otherFamiliesFor(userId: number, currentFamilyId: number): string[] {
    return this.families
      .filter((f) => f.id !== currentFamilyId && f.members.some((m) => m.id === userId))
      .map((f) => f.name);
  }

  private extractError(err: any): string {
    const detail = err?.error?.detail;

    if (!detail) {
      return '';
    }

    if (typeof detail === 'string') {
      return detail;
    }

    if (Array.isArray(detail)) {
      return detail.join(' ');
    }

    if (typeof detail === 'object') {
      return Object.values(detail)
        .map((value) => (Array.isArray(value) ? value.join(' ') : String(value)))
        .join(' ');
    }

    return '';
  }
}
