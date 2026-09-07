import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { RbacService, ROLE_LABELS } from '../../../core/services/rbac.service';
import { ToastService } from '../../../core/services/toast.service';

import {
  CreateUserPayload,
  FamilyGroup,
  ManagedUser,
  UpdateUserPayload,
  UserManagementApiService,
} from '../../../core/services/user-management-api.service';

import { PwmsRole } from '../../../core/services/rbac.service';

type ModalMode = 'add' | 'edit' | null;

@Component({
  selector: 'app-user-management',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './user-management.component.html',
  styleUrl: './user-management.component.scss',
})
export class UserManagementComponent implements OnInit {
  private readonly api = inject(UserManagementApiService);

  readonly rbac = inject(RbacService);

  private readonly toast = inject(ToastService);

  private readonly cdr = inject(ChangeDetectorRef);

  readonly roleLabels = ROLE_LABELS;

  users: ManagedUser[] = [];

  loading = true;

  error = '';

  // Only populated (and only rendered) for a System Owner, who is
  // the only role allowed to see/assign family membership from
  // this screen - see users.permissions matrix.
  families: FamilyGroup[] = [];

  // --------------------------------------------------------
  // MODAL STATE
  // --------------------------------------------------------

  modalMode: ModalMode = null;

  saving = false;

  formErrors: Record<string, string> = {};

  editingUserId: number | null = null;

  form: {
    first_name: string;
    last_name: string;
    username: string;
    email: string;
    role: PwmsRole;
    is_active: boolean;
    password: string;
    confirm_password: string;
    family_ids: number[];
  } = this.emptyForm();

  // --------------------------------------------------------
  // DELETE STATE
  // --------------------------------------------------------

  deletingUser: ManagedUser | null = null;

  deleteConfirmText = '';

  deleting = false;

  // --------------------------------------------------------
  // RESET PASSWORD STATE
  // --------------------------------------------------------

  resetPasswordUserId: number | null = null;

  resetPasswordValue = '';

  resetPasswordConfirm = '';

  resettingPassword = false;

  ngOnInit(): void {
    this.loadUsers();

    if (this.rbac.canManageFamilies()) {
      this.loadFamilies();
    }
  }

  // ==========================================================
  // PERMISSIONS (display only - backend is authoritative)
  // ==========================================================

  canManageFamilies(): boolean {
    return this.rbac.canManageFamilies();
  }

  currentUserId(): number | null {
    return this.rbac.currentUser()?.id ?? null;
  }

  /** Roles the modal's Role dropdown should offer. */
  assignableRoles(): PwmsRole[] {
    return this.rbac.assignableRoles();
  }

  /** Whether the Role field should appear at all in the Edit modal. */
  canShowRoleField(): boolean {
    if (this.modalMode === 'add') {
      return true;
    }

    // Editing: only a Super User/System Owner can actually change
    // a role, and never their own (self-role-change is always
    // rejected server-side) - hide the control entirely rather
    // than show a disabled one.
    return this.rbac.canChangeRoles() && this.editingUserId !== this.currentUserId();
  }

  // ==========================================================
  // LOAD
  // ==========================================================

  loadUsers(): void {
    this.loading = true;

    this.error = '';

    this.api.listUsers().subscribe({
      next: (users) => {
        this.users = users;

        this.loading = false;

        this.cdr.detectChanges();
      },

      error: (err) => {
        this.loading = false;

        this.error = this.extractError(err) || 'Unable to load users.';

        this.cdr.detectChanges();
      },
    });
  }

  loadFamilies(): void {
    this.api.listGroups().subscribe({
      next: (families) => {
        this.families = families;

        this.cdr.detectChanges();
      },

      error: () => {
        // Non-fatal: the Add/Edit User family checklist simply
        // shows no options if this fails; the user list itself
        // still works.
      },
    });
  }

  familyNames(user: ManagedUser): string {
    if (!user.families || user.families.length === 0) {
      return '-';
    }

    return user.families.map((f) => f.name).join(', ');
  }

  // ==========================================================
  // ADD USER
  // ==========================================================

  openAddUser(): void {
    this.modalMode = 'add';
    this.editingUserId = null;
    this.formErrors = {};
    this.form = this.emptyForm();

    if (this.assignableRoles().length > 0) {
      this.form.role = this.assignableRoles()[0];
    }
  }

  openEditUser(user: ManagedUser): void {
    this.modalMode = 'edit';
    this.editingUserId = user.id;
    this.formErrors = {};
    this.form = {
      first_name: user.first_name || '',
      last_name: user.last_name || '',
      username: user.username,
      email: user.email,
      role: user.role,
      is_active: user.is_active,
      password: '',
      confirm_password: '',
      family_ids: user.families.map((f) => f.id),
    };
  }

  closeModal(): void {
    this.modalMode = null;
    this.editingUserId = null;
    this.formErrors = {};
    this.saving = false;
  }

  toggleFamilySelection(familyId: number): void {
    const index = this.form.family_ids.indexOf(familyId);

    if (index === -1) {
      this.form.family_ids = [...this.form.family_ids, familyId];
    } else {
      this.form.family_ids = this.form.family_ids.filter((id) => id !== familyId);
    }
  }

  isFamilySelected(familyId: number): boolean {
    return this.form.family_ids.includes(familyId);
  }

  submitForm(): void {
    if (this.saving) {
      return;
    }

    this.formErrors = {};

    if (this.modalMode === 'add') {
      this.submitAddUser();
    } else if (this.modalMode === 'edit' && this.editingUserId !== null) {
      this.submitEditUser(this.editingUserId);
    }
  }

  private submitAddUser(): void {
    if (!this.form.username.trim()) {
      this.formErrors['username'] = 'Username is required.';
      return;
    }

    if (!this.form.email.trim()) {
      this.formErrors['email'] = 'Email is required.';
      return;
    }

    if (!this.form.password) {
      this.formErrors['password'] = 'Password is required.';
      return;
    }

    if (this.form.password !== this.form.confirm_password) {
      this.formErrors['confirm_password'] = 'Passwords do not match.';
      return;
    }

    const payload: CreateUserPayload = {
      first_name: this.form.first_name,
      last_name: this.form.last_name,
      username: this.form.username.trim(),
      email: this.form.email.trim(),
      password: this.form.password,
      confirm_password: this.form.confirm_password,
      role: this.form.role,
      is_active: this.form.is_active,
    };

    // Family assignment is a System Owner-only action - every
    // other role must omit the field entirely rather than send an
    // empty array (the backend rejects the field outright if a
    // non-System-Owner sends it, even empty).
    if (this.rbac.canManageFamilies()) {
      payload.family_ids = this.form.family_ids;
    }

    this.saving = true;

    this.api.createUser(payload).subscribe({
      next: (user) => {
        this.saving = false;

        this.toast.success(`User "${user.username}" created successfully.`);

        this.closeModal();
        this.loadUsers();
      },

      error: (err) => {
        this.saving = false;

        this.applyFieldErrors(err);

        this.cdr.detectChanges();
      },
    });
  }

  private submitEditUser(userId: number): void {
    if (!this.form.username.trim()) {
      this.formErrors['username'] = 'Username is required.';
      return;
    }

    if (!this.form.email.trim()) {
      this.formErrors['email'] = 'Email is required.';
      return;
    }

    const payload: UpdateUserPayload = {
      first_name: this.form.first_name,
      last_name: this.form.last_name,
      username: this.form.username.trim(),
      email: this.form.email.trim(),
    };

    const isSelfEdit = userId === this.currentUserId();

    // Only send role/is_active if this user is allowed to manage
    // them, and never for a self-edit - avoids the backend
    // rejecting the request for attempting to touch fields nobody
    // may change on their own account.
    if (this.rbac.canManageUsers() && !isSelfEdit) {
      payload.is_active = this.form.is_active;

      if (this.canShowRoleField()) {
        payload.role = this.form.role;
      }
    }

    if (this.rbac.canManageFamilies()) {
      payload.family_ids = this.form.family_ids;
    }

    this.saving = true;

    this.api.updateUser(userId, payload).subscribe({
      next: (user) => {
        this.saving = false;

        this.toast.success(`User "${user.username}" updated successfully.`);

        this.closeModal();
        this.loadUsers();
      },

      error: (err) => {
        this.saving = false;

        this.applyFieldErrors(err);

        this.cdr.detectChanges();
      },
    });
  }

  // ==========================================================
  // ACTIVATE / DEACTIVATE
  // ==========================================================

  toggleActive(user: ManagedUser): void {
    const action = user.is_active
      ? this.api.deactivateUser(user.id)
      : this.api.activateUser(user.id);

    action.subscribe({
      next: (updated) => {
        this.toast.success(
          `User "${updated.username}" ${updated.is_active ? 'activated' : 'deactivated'}.`,
        );

        this.loadUsers();
      },

      error: (err) => {
        this.toast.error(this.extractError(err) || 'Unable to update user status.');
      },
    });
  }

  // ==========================================================
  // RESET PASSWORD
  // ==========================================================

  openResetPassword(user: ManagedUser): void {
    this.resetPasswordUserId = user.id;
    this.resetPasswordValue = '';
    this.resetPasswordConfirm = '';
  }

  closeResetPassword(): void {
    this.resetPasswordUserId = null;
    this.resetPasswordValue = '';
    this.resetPasswordConfirm = '';
    this.resettingPassword = false;
  }

  submitResetPassword(): void {
    if (this.resetPasswordUserId === null || this.resettingPassword) {
      return;
    }

    if (!this.resetPasswordValue) {
      this.toast.error('Enter a new password.');
      return;
    }

    if (this.resetPasswordValue !== this.resetPasswordConfirm) {
      this.toast.error('New passwords do not match.');
      return;
    }

    this.resettingPassword = true;

    this.api
      .resetPassword(this.resetPasswordUserId, this.resetPasswordValue, this.resetPasswordConfirm)
      .subscribe({
        next: () => {
          this.resettingPassword = false;

          this.toast.success('Password reset successfully.');

          this.closeResetPassword();
        },

        error: (err) => {
          this.resettingPassword = false;

          this.toast.error(this.extractError(err) || 'Unable to reset password.');

          this.cdr.detectChanges();
        },
      });
  }

  // ==========================================================
  // DELETE
  // ==========================================================

  openDeleteUser(user: ManagedUser): void {
    this.deletingUser = user;
    this.deleteConfirmText = '';
  }

  closeDeleteUser(): void {
    this.deletingUser = null;
    this.deleteConfirmText = '';
    this.deleting = false;
  }

  canConfirmDelete(): boolean {
    return !!this.deletingUser && this.deleteConfirmText === this.deletingUser.username;
  }

  confirmDeleteUser(): void {
    if (!this.deletingUser || this.deleting || !this.canConfirmDelete()) {
      return;
    }

    const user = this.deletingUser;

    this.deleting = true;

    this.api.deleteUser(user.id).subscribe({
      next: () => {
        this.deleting = false;

        this.toast.success(`User "${user.username}" deleted.`);

        this.closeDeleteUser();
        this.loadUsers();
      },

      error: (err) => {
        this.deleting = false;

        this.toast.error(this.extractError(err) || 'Unable to delete user.');

        this.cdr.detectChanges();
      },
    });
  }

  // ==========================================================
  // HELPERS
  // ==========================================================

  private emptyForm() {
    return {
      first_name: '',
      last_name: '',
      username: '',
      email: '',
      role: 'VIEWER' as PwmsRole,
      is_active: true,
      password: '',
      confirm_password: '',
      family_ids: [] as number[],
    };
  }

  private applyFieldErrors(err: any): void {
    const detail = err?.error?.detail;

    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      const flattened: Record<string, string> = {};

      for (const key of Object.keys(detail)) {
        const value = detail[key];

        flattened[key] = Array.isArray(value) ? value.join(' ') : String(value);
      }

      this.formErrors = flattened;

      return;
    }

    this.toast.error(this.extractError(err) || 'Unable to save user.');
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

  formatDate(value: string | null): string {
    if (!value) {
      return 'Never';
    }

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
      return value;
    }

    return date.toLocaleString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }
}
