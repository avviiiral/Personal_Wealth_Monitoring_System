# Portfolio News Live Frontend Updates

## Purpose

The Portfolio News frontend now behaves as a live monitoring page.

When the backend news-monitoring process finishes fetching, matching, analyzing, or updating recent portfolio news, the Portfolio News page checks for changes automatically. The user does not need to refresh the browser.

## Architecture

```text
Background news monitor
        |
        v
Fetch / analyze / score news
        |
        v
Save or update NewsArticle / PortfolioNewsAlert
        |
        v
Angular Portfolio News page
        |
        | every 10 seconds while the page is open
        v
Fetch recent portfolio news
        |
        v
Merge by article/alert ID
        |
        v
Angular updates the visible list
```

## Polling interval

The frontend checks every:

```text
10 seconds
```

This interval is defined in:

```text
frontend/src/features/portfolio-news/portfolio-news-list.component.ts
```

as:

```text
livePollIntervalMs = 10_000
```

The interval is intentionally independent of the backend monitoring interval.

For example:

- Backend news monitoring can run every 30 minutes.
- Frontend can check every 10 seconds.

The frontend therefore detects a newly completed backend analysis shortly after the backend saves it, without forcing the backend news agent to run more frequently.

## What is requested

The frontend does **not** download the entire historical news database on every poll.

The live poll requests only a recent window:

- News view: up to 25 recent matched articles.
- Alerts view: up to 25 recent analyzed alerts.

The normal initial page loads remain unchanged.

## Why the recent window is used

The backend can update an existing alert when:

- another source is attached to an event,
- a corporate filing upgrades an existing event,
- source confidence changes,
- alert scoring changes,
- filing information enriches an existing clustered event.

A pure `id > last_id` cursor would detect newly-created rows but could miss an existing row that was updated.

The rolling recent-window approach therefore deliberately re-reads recent records and merges them by ID.

This keeps the request small while also allowing updated alerts to appear without a page refresh.

## Merge behavior

The frontend uses the database/API ID as the stable identity.

When a polled item already exists in the current Angular list:

```text
existing item + same ID
        |
        v
replace with newest API representation
```

When a new item appears:

```text
new ID
   |
   v
insert into current list
```

Duplicates are therefore not appended.

## Ordering

### News view

Recent raw portfolio news remains ordered by the API's publication/creation ordering.

### Alerts view

Alerts are ordered by:

1. Higher alert score first.
2. Higher ID as the tie-breaker.

This means a newly analyzed high-impact alert can move into the appropriate position automatically.

## Page lifecycle

Polling starts when:

```text
PortfolioNewsListComponent.ngOnInit()
```

Polling stops when:

```text
PortfolioNewsListComponent.ngOnDestroy()
```

Therefore navigating away from Portfolio News clears the timer.

The application does not continue polling this page in the background after the component has been destroyed.

## View modes

Live polling is active for:

- News
- Alerts

The Digest view is not polled every 10 seconds because it is a daily summary rather than a live event stream.

When the user switches between News and Alerts, the same live polling mechanism automatically follows the active view.

## Filters

The polling request respects the currently selected filters.

For Alerts this includes:

- Notification tier
- Sentiment
- Source type
- Date range

For News it includes:

- Date range

If the user changes a filter while a polling request is in flight, the response is applied only when the returned request still matches the active view/filter state. This prevents a stale response from one filter selection from appearing under another selection.

## Error handling

A failed live poll does not replace the existing visible news.

The existing page remains usable and the next polling cycle tries again.

Live polling errors are logged as warnings rather than shown as a full-page error because the initial page data may still be valid.

## UI indication

The Portfolio News header displays:

```text
● LIVE
```

The News view also displays:

```text
Auto-updating every 10 seconds
```

This makes it clear that the list is being refreshed automatically.

## Backend API support

The Portfolio News API also supports an optional:

```text
after_id
```

query parameter for incremental retrieval.

Examples:

```text
GET /api/ai/news/?after_id=2431
GET /api/ai/news/raw/?after_id=2431
```

When supplied, the backend restricts results to records with an ID greater than the supplied cursor.

This API capability is useful for future push/SSE or more aggressive incremental polling implementations.

The current frontend intentionally uses the recent-window approach so that updates to existing alerts are also detected.

## Performance characteristics

The live mechanism does not:

- reload the browser page,
- restart the news agent,
- fetch the complete historical news feed every 10 seconds,
- open a WebSocket connection,
- require Redis,
- require Django Channels,
- require an AI API,
- require a paid service.

It makes small authenticated HTTP requests while the Portfolio News component is visible.

## Why polling was selected

For PWMS, 10-second polling is a good first implementation because it provides near-real-time behavior with substantially less infrastructure than WebSockets.

### Current approach

```text
Angular
  |
  | HTTP every 10 seconds
  v
Django REST API
```

### Future alternative

If true server-push behavior becomes necessary, the same API/data model can later be exposed through:

- Server-Sent Events (SSE), or
- WebSockets/Django Channels.

No such infrastructure is required for the current implementation.

## Result

The Portfolio News page now behaves like a live monitoring dashboard:

```text
News analysis completes
        |
        v
Database is updated
        |
        | <= 10 seconds
        v
Frontend detects the change
        |
        v
New/updated news appears automatically
```

The user no longer needs to manually refresh Portfolio News to see recently analyzed items.
