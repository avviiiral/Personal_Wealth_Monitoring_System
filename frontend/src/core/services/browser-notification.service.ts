import { Injectable } from '@angular/core';

const HAS_PROMPTED_STORAGE_KEY = 'pwms_notification_permission_prompted';

export interface BrowserNotificationOptions {
  title: string;
  body?: string;
  tag?: string;
  onClick?: () => void;
}

@Injectable({
  providedIn: 'root',
})
export class BrowserNotificationService {
  isSupported(): boolean {
    return typeof window !== 'undefined' && 'Notification' in window;
  }

  getPermission(): NotificationPermission | 'unsupported' {
    if (!this.isSupported()) {
      return 'unsupported';
    }

    return Notification.permission;
  }

  private hasPromptedBefore(): boolean {
    try {
      return localStorage.getItem(HAS_PROMPTED_STORAGE_KEY) === 'true';
    } catch {
      // localStorage can throw in some privacy modes - treat as "already prompted"
      // so we never repeatedly nag the user.
      return true;
    }
  }

  private markPrompted(): void {
    try {
      localStorage.setItem(HAS_PROMPTED_STORAGE_KEY, 'true');
    } catch {
      // Ignore - non-critical.
    }
  }

  /**
   * Requests permission at most once per browser, ever. Safe to call
   * repeatedly - it's a no-op after the first real prompt, and a no-op
   * entirely if the browser doesn't support notifications. Never throws.
   */
  async requestPermissionIfNeeded(): Promise<void> {
    if (!this.isSupported()) {
      return;
    }

    if (Notification.permission !== 'default') {
      // Already granted, denied, or otherwise decided - never re-prompt.
      return;
    }

    if (this.hasPromptedBefore()) {
      return;
    }

    this.markPrompted();

    try {
      await Notification.requestPermission();
    } catch (error) {
      console.error('Notification permission request failed:', error);
    }
  }

  /**
   * Requests browser notification permission when needed and returns the
   * resulting permission state. Unlike requestPermissionIfNeeded(), this
   * method intentionally does not persist a separate "prompted" flag because
   * callers may explicitly request permission from a user action.
   */
  async requestPermission(): Promise<NotificationPermission | 'unsupported'> {
    if (!this.isSupported()) {
      return 'unsupported';
    }

    if (Notification.permission === 'default') {
      try {
        return await Notification.requestPermission();
      } catch (error) {
        console.error('Notification permission request failed:', error);
      }
    }

    return Notification.permission;
  }

  showNotification(options: BrowserNotificationOptions): void {
    if (!this.isSupported() || Notification.permission !== 'granted') {
      return;
    }

    try {
      const notification = new Notification(options.title, {
        body: options.body,
        tag: options.tag,
      });

      if (options.onClick) {
        notification.onclick = () => {
          window.focus();
          options.onClick?.();
          notification.close();
        };
      }
    } catch (error) {
      console.error('Failed to show browser notification:', error);
    }
  }
}
