# Plan: Dashboard Watch Lists

## Goal

Add personal watch lists to the Omniscrobble dashboard. An admin can create and manage multiple lists of movies, TV shows, and anime they intend to watch. Lists are private to this Omniscrobble installation and remain available after restarts.

## Product decisions

- Lists are local to the installation; no household sharing is required.
- A user can create, rename, and delete lists, and add, remove, and reorder items.
- Items support movies, TV shows, and anime. Anime is an optional type/category, not a required anime-specific workflow.
- Anime items can be searched/resolved through the existing AniList resolver when possible. If resolution fails, the user can still save the title and optionally its year.
- A list item can be sent to the configured acquisition service with one click to start the existing add flow: movies to Radarr and TV/anime series to Sonarr. The user reviews the matched item and service options and confirms before it is added.
- TV/anime series can be enrolled in or removed from the existing Co-Watch shared-show list from the item actions. The UI reports current enrollment state.
- Acquisition and Co-Watch actions require admin access and the corresponding service configuration. They do not remove an item from its watch list.
- Persist lists in a small JSON file under `data/`, using the existing atomic JSON writer pattern. Do not download poster images; retain poster URLs only if already available from metadata.
- Import and export support JSON, readable TXT, and clipboard text. JSON is the lossless backup/restore format. TXT and clipboard use the same documented, readable representation and preserve list names, item titles, and any media types/years encoded in that representation. Clipboard import uses a paste field; the browser should not read clipboard contents automatically.
- Imports should be previewed and validated before applying. Define duplicate behavior explicitly (default: skip duplicate items within a list) and preserve existing lists unless the user selects a replace/merge action.

## Existing foundations

- `app/main.py` has admin-protected state-changing API routes and an existing `/api/watchlist` endpoint that sends one item to Trakt. That endpoint is not local list storage, so the new feature should use its own API and persistence service.
- `app/services/atomic_writer.py` provides crash-safe JSON writes.
- `app/services/anime_resolver.py` and the AniList, MAL, and Kitsu clients provide anime ID and metadata resolution.
- The dashboard is composed from workspace templates and JavaScript modules. Integrate watch lists as a first-class dashboard workspace or coherent section, with mobile-friendly list and item controls.

## Proposed data model

Store a versioned document in `data/watch_lists.json`:

```json
{
  "version": 1,
  "lists": [
    {
      "id": "stable-unique-id",
      "name": "Weekend picks",
      "created_at": "ISO-8601 timestamp",
      "updated_at": "ISO-8601 timestamp",
      "items": [
        {
          "id": "stable-unique-id",
          "title": "Example title",
          "media_type": "movie",
          "year": 2025,
          "ids": {"tmdb": 123, "anilist": null, "mal": null},
          "added_at": "ISO-8601 timestamp",
          "position": 0
        }
      ]
    }
  ]
}
```

Fields other than `title` and `media_type` are optional where appropriate. Validate imported IDs and types, impose reasonable limits on file size, list count, item count, and text lengths, and write updates atomically. Use stable IDs rather than list names as API identifiers.

## User experience

1. Add a Watch Lists destination to dashboard navigation and command palette.
2. Show the selected list, list selector, item count, and actions to create, rename, and delete lists.
3. Provide an add-item flow with search and a media-type selector. Search movies and TV through existing catalog sources where available; search anime through AniList metadata resolution. Keep manual title entry available if a lookup is unavailable or inconclusive.
4. Render items with title, optional year/type, and available metadata. Support removal and drag/reorder controls with keyboard-accessible alternatives.
5. Add an acquisition action for each item. Resolve movie items through Radarr and TV/anime series through Sonarr, then reuse the existing acquisition confirmation flow for root folder, quality profile, monitoring, and search options. Keep the watch-list item after acquisition.
6. Add Co-Watch enrollment controls for TV/anime series, reusing the existing shared-show management behavior. Show enrolled state and allow removal; explain when Co-Watch is unavailable because configuration or admin access is missing.
7. Provide export actions for JSON download, TXT download, and copy text. Provide imports for JSON file, TXT file, and pasted text, all through a preview/confirm step.
8. Display clear validation, duplicate, and partial-import results. Never silently overwrite saved lists.

## API and service changes

Implement a focused `WatchListManager` service and authenticated endpoints, for example:

- `GET /api/watch-lists` — list all lists and their items.
- `POST /api/watch-lists` — create a list.
- `PATCH /api/watch-lists/{list_id}` — rename a list.
- `DELETE /api/watch-lists/{list_id}` — delete a list.
- `POST /api/watch-lists/{list_id}/items` — add an item.
- `PATCH /api/watch-lists/{list_id}/items/order` — reorder items.
- `DELETE /api/watch-lists/{list_id}/items/{item_id}` — remove an item.
- `POST /api/watch-lists/import/preview` and `POST /api/watch-lists/import` — validate/preview and apply JSON or text imports.
- `GET /api/watch-lists/export?format=json|txt` — download the selected scope or all lists.
- Acquisition should reuse or extend `/api/arr/lookup`, `/api/arr/config`, and `/api/arr/add` so list items use the same matching and confirmation options as the existing dashboard flow.
- Co-Watch actions should reuse or extend `/api/cowatch/shows` and its status response so enrollment state is consistent with the existing Co-Watch UI.

All mutations must use `is_admin_request(request)`. Reuse the app's CSRF-protected request conventions. Keep parsing/serialization and list operations in the service layer rather than putting persistence logic in route handlers. Export only the watch-list data; do not include tokens, settings, or unrelated backup contents.

## Implementation phases

### Phase 1: Persistence and API

- Define the versioned schema, validation, and atomic persistence service.
- Add list/item CRUD and ordering endpoints, admin checks, and API documentation.
- Add unit tests for persistence, migration/version handling, validation, duplicate handling, authorization, and API responses.

### Phase 2: Dashboard management

- Add a Watch Lists workspace/section and responsive list/item views.
- Add create, rename, delete, search/add, remove, and reorder interactions.
- Add loading, empty, error, and success states, with accessible controls.

### Phase 3: Anime resolution

- Add optional anime search using the existing AniList resolver/client.
- Preserve AniList/MAL/Kitsu IDs when resolved, while allowing unresolved manual entries.
- Verify non-anime additions do not depend on anime tracker authentication.

### Phase 4: Import and export

- Implement versioned JSON round-trip import/export.
- Implement readable TXT serialization and parser for exported text; accept pasted clipboard text through the UI.
- Add preview, validation, merge/replace choice, duplicate reporting, and safe limits.
- Document the text format so users can prepare compatible lists manually.

### Phase 5: Documentation and release readiness

- Update `docs/API.md`, `docs/FEATURES.md`, and `docs/ARCHITECTURE.md`; update endpoint-count metrics in `README.md` and architecture docs if counts change.
- Update backup/restore behavior only if watch lists should be included in the app's full backup. Preferred behavior: include the watch-list file in `/api/backup` and restore it through the existing restore flow, while retaining standalone JSON/TXT exports.
- Review privacy, file permissions, mobile layout, error states, and import size limits.
- Run repository-required checks and keep generated static dashboard assets synchronized if dashboard templates or demo data are changed.

## Acceptance criteria

- Lists and items remain intact after process restart.
- Admins can create, rename, delete, populate, and reorder multiple lists from the dashboard; non-admin mutations are rejected.
- Movie, TV, and anime entries can be added, including an unresolved anime/manual title.
- JSON export/import round-trips names, ordering, media types, years, and IDs without loss.
- TXT and clipboard exports are readable, and their imports recover list names, titles, and represented media metadata.
- Imports validate before modifying saved data, report skipped/invalid entries, and do not overwrite existing lists without an explicit user choice.
- A movie can be sent through the existing Radarr lookup/add flow, and a TV/anime series through Sonarr, with confirmation of the match and service options; the list item remains saved afterward.
- TV/anime series can be enrolled in and removed from Co-Watch, with current state shown and unauthorized mutations rejected.
- UI works on mobile and supports keyboard navigation for essential actions.
- Watch-list data contains no credentials and uses atomic writes.

## Effort estimate

Estimated **4–6 engineering days** for a polished first release, assuming existing dashboard conventions and acquisition/Co-Watch flows are reused and no tracker synchronization is added. A reduced CRUD-only version is approximately **1–2 days**. More advanced anime discovery, tracker synchronization, or collaborative lists should be scoped separately.

## Out of scope for the first release

- Shared or collaborative lists across household users.
- Automatic synchronization to Trakt, TMDb, AniList, MAL, or other services.
- Downloading and storing poster art.
- Watched-state synchronization and playback-driven list completion.
