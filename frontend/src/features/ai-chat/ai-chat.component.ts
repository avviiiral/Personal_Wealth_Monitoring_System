import {
  ChangeDetectorRef,
  Component,
  ElementRef,
  HostListener,
  ViewChild,
  inject,
} from '@angular/core';

import { CommonModule } from '@angular/common';

import { FormsModule } from '@angular/forms';

import { AiChatApiService } from '../../core/services/ai-chat-api.service';

interface ChatMessage {
  role: 'user' | 'assistant';

  text: string;
}

@Component({
  selector: 'app-ai-chat',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './ai-chat.component.html',
  styleUrl: './ai-chat.component.scss',
})
export class AiChatComponent {
  private readonly aiChatApi = inject(AiChatApiService);

  private readonly cdr = inject(ChangeDetectorRef);

  @ViewChild('messageList') private messageList?: ElementRef<HTMLDivElement>;

  messages: ChatMessage[] = [];

  draft = '';

  sending = false;

  error = '';

  expanded = false;

  // ======================================================
  // EXPAND / COLLAPSE
  // ======================================================

  toggleExpand(): void {
    this.expanded = !this.expanded;

    this.scrollToBottom();
  }

  @HostListener('document:keydown.escape')
  onEscapeKey(): void {
    if (this.expanded) {
      this.expanded = false;
    }
  }

  // ======================================================
  // SEND
  // ======================================================

  sendMessage(): void {
    const message = this.draft.trim();

    if (!message || this.sending) {
      return;
    }

    this.messages.push({
      role: 'user',
      text: message,
    });

    this.draft = '';

    this.sending = true;

    this.error = '';

    this.scrollToBottom();

    this.aiChatApi.sendMessage(message).subscribe({
      next: (response) => {
        this.sending = false;

        if (response.answer) {
          this.messages.push({
            role: 'assistant',
            text: response.answer,
          });
        } else {
          this.error = response.error || 'The AI returned an empty response.';
        }

        this.cdr.detectChanges();

        this.scrollToBottom();
      },

      error: (error) => {
        console.error('AI chat error:', error);

        this.sending = false;

        this.error = error?.error?.error || 'Unable to reach the AI assistant.';

        this.cdr.detectChanges();

        this.scrollToBottom();
      },
    });
  }

  onInputKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();

      this.sendMessage();
    }
  }

  clearChat(): void {
    if (this.sending) {
      return;
    }

    this.messages = [];

    this.error = '';
  }

  // ======================================================
  // SCROLL
  // ======================================================

  private scrollToBottom(): void {
    setTimeout(() => {
      const element = this.messageList?.nativeElement;

      if (element) {
        element.scrollTop = element.scrollHeight;
      }
    });
  }
}
