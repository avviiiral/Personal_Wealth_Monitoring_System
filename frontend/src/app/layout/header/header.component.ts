import { Component, HostListener, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { Subscription, interval } from 'rxjs';

import { AuthService } from '../../../core/services/auth.service';
import { RbacService, ROLE_LABELS, FamilySummary } from '../../../core/services/rbac.service';
import { AiChatApiService } from '../../../core/services/ai-chat-api.service';
import {
  NewsApiService,
  PortfolioNewsAlertListItem,
} from '../../../core/services/news-api.service';
import { BrowserNotificationService } from '../../../core/services/browser-notification.service';

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
}

@Component({
  selector: 'app-header',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './header.component.html',
  styleUrl: './header.component.scss',
})
export class HeaderComponent implements OnInit, OnDestroy {
  private readonly authService = inject(AuthService);
  readonly rbac = inject(RbacService);
  private readonly aiChatApi = inject(AiChatApiService);
  private readonly newsApi = inject(NewsApiService);
  private readonly browserNotifications = inject(BrowserNotificationService);
  private readonly router = inject(Router);

  readonly roleLabels = ROLE_LABELS;

  private pollSubscription?: Subscription;

  private static readonly POLL_INTERVAL_MS = 60000;

  private knownAlertIds = new Set<number>();
  private notificationsBaselineEstablished = false;

  profileMenuOpen = false;
  loggingOut = false;

  /*
   * SEARCH
   */
  searchOpen = false;
  searchText = '';

  /*
   * AI CHAT
   */
  chatOpen = false;
  chatExpanded = false;
  chatMessage = '';
  chatLoading = false;

  chatMessages: ChatMessage[] = [];

  /*
   * NOTIFICATIONS
   */
  notificationsOpen = false;
  notificationsLoading = false;
  notifications: PortfolioNewsAlertListItem[] = [];
  unreadCount = 0;

  get currentUser() {
    return this.authService.currentUser;
  }

  /*
   * --------------------------------------------------
   * FAMILY SWITCHER (only shown when the user belongs to more
   * than one family - see rbac.hasMultipleFamilies()). Switching
   * changes which family's Dashboard/Portfolio/Analytics/Mutual
   * Fund data the user sees; it is never a membership change,
   * and every role may do it for their own families.
   * --------------------------------------------------
   */

  switchingFamily = false;

  switchFamily(family: FamilySummary): void {
    if (this.switchingFamily || family.id === this.rbac.activeFamily()?.id) {
      this.profileMenuOpen = false;
      return;
    }

    this.switchingFamily = true;

    this.rbac.setActiveFamily(family.id).subscribe({
      next: () => {
        // Every data screen (Dashboard/Portfolio/Analytics/Mutual
        // Funds) fetches its own data on init from the backend,
        // which now scopes to the newly-selected family - a full
        // reload is the simplest way to guarantee every already-
        // loaded screen reflects the new scope immediately.
        window.location.reload();
      },

      error: () => {
        this.switchingFamily = false;
        this.profileMenuOpen = false;
      },
    });
  }

  /*
   * --------------------------------------------------
   * LIFECYCLE
   * --------------------------------------------------
   */

  ngOnInit(): void {
    this.refreshNotifications();

    this.pollSubscription = interval(HeaderComponent.POLL_INTERVAL_MS).subscribe(() => {
      this.refreshNotifications();
    });
  }

  ngOnDestroy(): void {
    this.pollSubscription?.unsubscribe();
  }

  /*
   * --------------------------------------------------
   * PROFILE MENU
   * --------------------------------------------------
   */

  toggleProfileMenu(event: MouseEvent): void {
    event.stopPropagation();

    this.profileMenuOpen = !this.profileMenuOpen;

    if (this.profileMenuOpen) {
      this.searchOpen = false;
      this.chatOpen = false;
    }
  }

  openSettings(): void {
    this.profileMenuOpen = false;
    this.router.navigate(['/settings']);
  }

  logout(): void {
    if (this.loggingOut) {
      return;
    }

    this.loggingOut = true;
    this.profileMenuOpen = false;

    this.authService.logout().subscribe({
      next: () => {
        this.loggingOut = false;
        this.router.navigate(['/login']);
      },

      error: (error) => {
        console.error('Logout failed:', error);

        this.loggingOut = false;
        this.router.navigate(['/login']);
      },
    });
  }

  /*
   * --------------------------------------------------
   * DASHBOARD SEARCH
   * --------------------------------------------------
   */

  toggleSearch(event: MouseEvent): void {
    event.stopPropagation();

    this.searchOpen = !this.searchOpen;

    if (this.searchOpen) {
      this.chatOpen = false;
      this.profileMenuOpen = false;
    }
  }

  closeSearch(): void {
    this.searchOpen = false;
    this.searchText = '';
  }

  /*
   * --------------------------------------------------
   * AI CHAT WINDOW
   * --------------------------------------------------
   */

  toggleChat(event: MouseEvent): void {
    event.stopPropagation();

    this.chatOpen = !this.chatOpen;

    if (this.chatOpen) {
      this.searchOpen = false;
      this.profileMenuOpen = false;
    }
  }

  closeChat(): void {
    this.chatOpen = false;
    this.chatExpanded = false;
  }

  toggleChatExpand(event: MouseEvent): void {
    event.stopPropagation();

    this.chatExpanded = !this.chatExpanded;
  }

  /*
   * --------------------------------------------------
   * CHAT MESSAGE
   * --------------------------------------------------
   */

  handleChatKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();

      if (!this.chatLoading && this.chatMessage.trim()) {
        this.sendChatMessage();
      }
    }
  }

  sendChatMessage(): void {
    const message = this.chatMessage.trim();

    if (!message || this.chatLoading) {
      return;
    }

    this.chatMessages.push({
      role: 'user',
      text: message,
    });

    this.chatMessage = '';
    this.chatLoading = true;

    this.aiChatApi.sendMessage(message).subscribe({
      next: (response) => {
        this.chatLoading = false;

        this.chatMessages.push({
          role: 'assistant',
          text: response.answer || response.error || 'The AI returned an empty response.',
        });
      },

      error: (error) => {
        console.error('AI chat error:', error);

        this.chatLoading = false;

        this.chatMessages.push({
          role: 'assistant',
          text: error?.error?.error || 'Unable to reach the AI assistant.',
        });
      },
    });
  }

  /*
   * --------------------------------------------------
   * NOTIFICATIONS
   * --------------------------------------------------
   */

  refreshNotifications(): void {
    this.newsApi.getNotifications(10).subscribe({
      next: (response) => {
        const newItems = response.results.filter((item) => !this.knownAlertIds.has(item.id));

        if (this.notificationsBaselineEstablished) {
          for (const item of newItems) {
            this.fireBrowserNotification(item);
          }
        }

        this.knownAlertIds = new Set(response.results.map((item) => item.id));
        this.notificationsBaselineEstablished = true;

        this.notifications = response.results;
        this.unreadCount = response.unread_count;
      },

      error: (error) => {
        console.error('Failed to load notifications:', error);
      },
    });
  }

  private fireBrowserNotification(item: PortfolioNewsAlertListItem): void {
    const tierLabel = item.notification_tier === 'critical' ? 'Critical Impact' : 'High Impact';

    this.browserNotifications.showNotification({
      title: `${tierLabel} - ${item.holding_display_name}`,
      body: item.article_title,
      tag: `pwms-alert-${item.id}`,
      onClick: () => {
        this.router.navigate(['/portfolio-news', item.id]);
      },
    });
  }

  toggleNotifications(event: MouseEvent): void {
    event.stopPropagation();

    this.notificationsOpen = !this.notificationsOpen;

    if (this.notificationsOpen) {
      this.searchOpen = false;
      this.chatOpen = false;
      this.profileMenuOpen = false;
      this.refreshNotifications();
      void this.browserNotifications.requestPermissionIfNeeded();
    }
  }

  closeNotifications(): void {
    this.notificationsOpen = false;
  }

  openNotification(item: PortfolioNewsAlertListItem): void {
    if (!item.is_read) {
      this.newsApi.markNotificationRead(item.id).subscribe({
        next: () => {
          item.is_read = true;
          this.unreadCount = Math.max(0, this.unreadCount - 1);
        },

        error: (error) => {
          console.error('Failed to mark notification read:', error);
        },
      });
    }

    this.notificationsOpen = false;
    this.router.navigate(['/portfolio-news', item.id]);
  }

  markAllNotificationsRead(event: MouseEvent): void {
    event.stopPropagation();

    this.newsApi.markAllNotificationsRead().subscribe({
      next: () => {
        this.notifications = this.notifications.map((item) => ({ ...item, is_read: true }));
        this.unreadCount = 0;
      },

      error: (error) => {
        console.error('Failed to mark all notifications read:', error);
      },
    });
  }

  impactDotClass(item: PortfolioNewsAlertListItem): string {
    return `impact-dot impact-dot--${item.notification_tier}`;
  }

  timeAgo(isoDate: string | null): string {
    if (!isoDate) {
      return '';
    }

    const then = new Date(isoDate).getTime();
    const diffMs = Date.now() - then;
    const diffMinutes = Math.floor(diffMs / 60000);

    if (diffMinutes < 1) {
      return 'just now';
    }

    if (diffMinutes < 60) {
      return `${diffMinutes} minute${diffMinutes === 1 ? '' : 's'} ago`;
    }

    const diffHours = Math.floor(diffMinutes / 60);

    if (diffHours < 24) {
      return `${diffHours} hour${diffHours === 1 ? '' : 's'} ago`;
    }

    const diffDays = Math.floor(diffHours / 24);

    return `${diffDays} day${diffDays === 1 ? '' : 's'} ago`;
  }

  /*
   * --------------------------------------------------
   * CLOSE MENUS WHEN CLICKING OUTSIDE
   * --------------------------------------------------
   */

  @HostListener('document:click')
  closeMenus(): void {
    this.profileMenuOpen = false;
    this.searchOpen = false;
    this.notificationsOpen = false;
  }
}
