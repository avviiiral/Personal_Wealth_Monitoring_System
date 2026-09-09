import { Injectable, signal } from '@angular/core';

const STORAGE_KEY = 'pwms-theme';
const DARK_CLASS = 'dark-theme';

export type ThemeMode = 'light' | 'dark';

/*
 * Tracks the active light/dark theme, persists the user's explicit
 * choice to localStorage, and toggles the `dark-theme` class on
 * <html> (see index.html for the pre-bootstrap inline script that
 * applies this same class before Angular loads, to avoid a flash
 * of the wrong theme).
 */
@Injectable({ providedIn: 'root' })
export class ThemeService {
  readonly mode = signal<ThemeMode>(this.readInitialMode());

  constructor() {
    this.applyMode(this.mode());
  }

  isDark(): boolean {
    return this.mode() === 'dark';
  }

  toggle(): void {
    this.setMode(this.isDark() ? 'light' : 'dark');
  }

  setMode(mode: ThemeMode): void {
    this.mode.set(mode);
    this.applyMode(mode);

    try {
      localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      // localStorage may be unavailable (e.g. private browsing) -
      // the in-memory signal still keeps the theme correct for
      // this session.
    }
  }

  private applyMode(mode: ThemeMode): void {
    document.documentElement.classList.toggle(DARK_CLASS, mode === 'dark');
  }

  private readInitialMode(): ThemeMode {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);

      if (stored === 'dark' || stored === 'light') {
        return stored;
      }
    } catch {
      // Fall through to system preference.
    }

    const prefersDark =
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-color-scheme: dark)').matches;

    return prefersDark ? 'dark' : 'light';
  }
}
