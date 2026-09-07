import { ChangeDetectionStrategy, Component, inject } from '@angular/core';

import { ToastService, ToastType } from '../../../core/services/toast.service';

@Component({
  selector: 'app-toast',
  standalone: true,
  template: `
    <div class="toast-container">
      @for (toast of toastService.toasts(); track toast.id) {
        <div
          class="toast"
          [class.toast-success]="toast.type === 'success'"
          [class.toast-error]="toast.type === 'error'"
          [class.toast-info]="toast.type === 'info'"
          [class.toast-warning]="toast.type === 'warning'"
        >
          <div class="toast-icon">
            @switch (toast.type) {
              @case ('success') {
                ✓
              }

              @case ('error') {
                !
              }

              @case ('warning') {
                !
              }

              @case ('info') {
                i
              }
            }
          </div>

          <div class="toast-message">
            {{ toast.message }}
          </div>

          <button
            type="button"
            class="toast-close"
            (click)="toastService.remove(toast.id)"
            aria-label="Close notification"
          >
            ×
          </button>
        </div>
      }
    </div>
  `,
  styles: [
    `
      .toast-container {
        position: fixed;
        top: 24px;
        right: 24px;
        z-index: 10000;

        display: flex;
        flex-direction: column;
        gap: 10px;

        width: min(380px, calc(100vw - 32px));

        pointer-events: none;
      }

      .toast {
        display: flex;
        align-items: center;
        gap: 12px;

        min-height: 52px;
        padding: 12px 14px;

        border: 1px solid #e5e7eb;
        border-radius: 10px;

        background: #ffffff;

        box-shadow:
          0 10px 30px rgba(15, 23, 42, 0.12),
          0 2px 8px rgba(15, 23, 42, 0.06);

        animation: toast-in 0.2s ease-out;

        pointer-events: auto;
      }

      .toast-icon {
        display: flex;
        align-items: center;
        justify-content: center;

        flex: 0 0 28px;

        width: 28px;
        height: 28px;

        border-radius: 50%;

        font-size: 14px;
        font-weight: 700;
      }

      .toast-message {
        flex: 1;

        color: #1f2937;

        font-size: 13px;
        font-weight: 600;
        line-height: 1.4;
      }

      .toast-close {
        flex: 0 0 auto;

        width: 26px;
        height: 26px;

        border: 0;
        border-radius: 6px;

        background: transparent;

        color: #6b7280;

        font-size: 20px;
        line-height: 1;

        cursor: pointer;
      }

      .toast-close:hover {
        background: #f3f4f6;
      }

      .toast-success {
        border-color: #bbf7d0;
      }

      .toast-success .toast-icon {
        background: #dcfce7;
        color: #15803d;
      }

      .toast-error {
        border-color: #fecaca;
      }

      .toast-error .toast-icon {
        background: #fee2e2;
        color: #b91c1c;
      }

      .toast-warning {
        border-color: #fde68a;
      }

      .toast-warning .toast-icon {
        background: #fef3c7;
        color: #b45309;
      }

      .toast-info {
        border-color: #bfdbfe;
      }

      .toast-info .toast-icon {
        background: #dbeafe;
        color: #1d4ed8;
      }

      @keyframes toast-in {
        from {
          opacity: 0;
          transform: translateY(-8px);
        }

        to {
          opacity: 1;
          transform: translateY(0);
        }
      }

      @media (max-width: 600px) {
        .toast-container {
          top: 12px;
          right: 16px;
          left: 16px;

          width: auto;
        }
      }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ToastComponent {
  readonly toastService = inject(ToastService);
}
