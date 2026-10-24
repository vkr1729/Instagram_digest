# Frontier Requirements Review — Zero-Cloud / Direct-Meta Stream Architecture

**Date:** 2026-10-24  
**Scope:** `.workflow/REQUIREMENTS.md` §§1–4  
**Scale anchor:** Single-person personal use: one user, one primary iPhone, one workstation, and a weekly batch of 250 reels. Optimize for a functional, recoverable personal app—not multi-tenant infrastructure, provider abstraction layers, or high-availability services.

Single-user scale removes operational complexity, but it does not remove iOS background execution limits, a 2.5–3.5 GB device-storage commitment, short-lived signed URLs, or third-party resolver failures. The requirements currently describe the happy path but leave several recovery transitions undefined.

**Terminology to clarify:** “Zero-cloud” can only mean **no user-managed cloud media storage and no Telegram forwarding** under this design. GitHub Pages, Meta CDNs, and a public resolver remain cloud/third-party dependencies. Long-term bookmark playback is therefore best-effort and online-only unless the media is still cached locally.

## Q1. When a 250-reel bulk download is interrupted, what resumes, and what counts as “cached”?

### Ambiguity / failure modes

§2 requires background bulk caching and progress reporting, but does not define per-item completion, retry, or recovery semantics. A batch of this size can be interrupted by iOS suspending or terminating the app, a reboot, MRT connectivity loss, low storage, an HTTP 403/429, or a CDN disconnect. Signed URLs may expire while the queue is paused.

Without an explicit contract:

- A zero-byte or truncated file may be treated as cached, producing a black or stalled player offline.
- Relaunching may restart all 250 downloads, wasting bandwidth and consuming the remaining fresh-URL window.
- One persistent failure may block the other 249 items indefinitely.
- Deleting temporary files on every launch may unnecessarily destroy resumable progress.
- “250/250” may be displayed even though some files cannot be decoded.

### Recommended approach

Define bulk caching as a **resumable, per-item operation with partial success**, not an all-or-nothing transaction:

1. Key cache entries by `(week_id, reel_id)`, not by the expiring CDN URL. Persist each item as `pending`, `downloading`, `ready`, or `failed`, along with its local filename and expected byte count when known.
2. Use an iOS background `URLSession` download queue with bounded concurrency. Reconcile persisted state and files after relaunch; items already marked `ready` must not be downloaded again.
3. Download to a temporary file. Mark an item `ready` only after a successful response, non-zero/expected-size validation where possible, and an atomic move to its final cache location. Never expose temporary files to `AVPlayerPool`.
4. Preflight available storage before starting. Because the local pipeline already downloads and validates the media, include `expected_bytes` in the manifest when available so the app can make a useful estimate and reject an impossible batch before filling the disk.
5. Retry transient network and 5xx failures with bounded exponential backoff. Treat 403/410 as URL expiry and follow Q2 rather than repeatedly retrying the same URL. Preserve failed items for an explicit “Retry failed” action.
6. Make completed items playable immediately, show an honest `ready / 250` count plus failure count, and never let one bad item invalidate the rest. If a supposedly ready file later fails to decode, quarantine/delete only that file and return it to `failed` for redownload.

A useful acceptance case is: terminate the app halfway through the batch, relaunch, and verify that completed items remain playable, incomplete items resume or restart safely, and no partial file enters the player.

### Alternatives

- **Foreground-only download with a keep-screen-awake prompt:** Simpler and easier to validate, but unreliable for 2.5–3.5 GB and conflicts with the requested background experience. Reasonable only as an explicit v1 limitation.
- **All-or-nothing week commit:** Gives a clean week state, but one failed reel can discard or block an otherwise useful cache. If chosen, retain completed files for retry rather than deleting them.
- **Small sequential batches:** Reduces memory, storage churn, and CDN pressure, but may take longer than the signed-URL lifetime. Bounded parallel downloads are preferable if measured on the target iPhone.

## Q2. Who refreshes media URLs when the user misses the 48-hour window or it expires mid-download?

### Ambiguity / failure modes

The manifest contains URLs resolved at workstation sync time, while the user chooses when to start the phone download. The 48-hour requirement does not say when that window begins, whether 48 hours is guaranteed, or what happens if the app first opens after it ends. GitHub Pages cannot independently refresh those URLs.

A 403 may affect one URL or the whole batch. Refetching `data.json` may simply return the same expired URLs. Using the bookmark resolver to repair an entire week would turn an unspecified public API into a 250-call bulk dependency and could trigger rate limits.

### Recommended approach

Make URL freshness and refresh ownership explicit:

1. Add `week_id`, `generated_at`, and per-item `resolved_at` to the manifest. Include `expires_at` only when the source actually supplies a trustworthy expiry; do not invent a guaranteed 48-hour lifetime.
2. Display link freshness before starting a large batch. On 403/410, pause new requests and refetch the manifest once in case the workstation has published a newer version. Do not issue the remaining 249 requests against URLs already known to be stale.
3. For the single-user v1, make the **weekly workstation pipeline the authoritative bulk URL refresher**. If no fresher manifest exists, stop with a recoverable message such as “Download links expired; refresh this week’s sync, then retry.” Preserve all completed items and failed queue state.
4. Keep the public resolver scoped to individual, on-demand playback as currently specified. Do not silently launch a bulk resolver storm as a hidden recovery path.

This favors a small amount of explicit manual recovery over an unreliable claim that the phone can always repair an expired weekly batch by itself.

### Alternatives

- **User-initiated resolver backfill for missing current-week items:** More autonomous and useful when only a few items failed, but requires the rate-limit policy in Q4 and a defined provider. It is a reasonable later enhancement, not a safe default for all 250 items.
- **Always resolve the full week on the phone:** Removes the workstation refresh dependency, but makes resolver availability and provider limits critical to the core weekly workflow. Not recommended without measured provider behavior.
- **Require immediate download after every publish:** Simple, but not enforceable without a reliable notification/background-fetch mechanism and still does not handle interrupted downloads.

## Q3. Which event authorizes manifest replacement, old-cache deletion, and bookmark fallback?

### Ambiguity / failure modes

§2 says the previous cache is wiped “when the subsequent week’s manifest arrives.” That timing can destroy the only offline copy before the replacement is usable—for example, if the app fetches a new manifest just before entering an MRT tunnel and the new download then fails.

There are also unspecified cache-invalidation cases:

- `URLSession`, GitHub Pages/CDN caching, or an app-saved manifest may return different weeks.
- A malformed or older response could roll the app back or trigger deletion.
- Re-resolving the same reel changes its URL but not its identity; URL-keyed caching could duplicate or lose files.
- Two 2.5–3.5 GB weeks may need 5–7 GB during overlap, which may not fit on the phone.
- A crash during deletion or activation can leave the UI pointing at a partially removed cache.

### Recommended approach

Separate **manifest acceptance** from **media-cache activation**:

1. Give the manifest a `schema_version`, `week_id`, and `generated_at`. Retain the last known-good manifest on fetch/parse failure, ignore unsupported or older versions, and never delete media because a fetch failed.
2. Use HTTP revalidation (`ETag`/`Last-Modified` where available) and an explicit refresh path that bypasses local URL caching. Validate the returned week/version before accepting it so a stale cached response cannot roll back state.
3. A valid new manifest may update metadata and show the download prompt, but **must not by itself authorize old-cache deletion**.
4. Keep old and new media in week-specific cache namespaces. Atomically mark the new cache active only when all items are ready or the user explicitly accepts a partial week. Only then delete the old week. A startup reconciliation step should recover from an interrupted commit using the active-week marker and on-disk week directories.
5. Resolve bookmarks by stable reel ID: use a `ready` item in the active local cache first; otherwise require online resolution. A changed signed URL must not create a second copy of an already-ready `(week_id, reel_id)` item.
6. Preflight the overlap. If there is insufficient space, retain the old cache and ask the user to free space or explicitly choose an early replacement with an offline gap; never silently wipe it.

### Alternatives

- **Delete the old week as soon as the new manifest validates:** Minimizes peak storage and is simple, but creates a predictable offline outage. Acceptable only if the user explicitly prefers storage over continuity.
- **Replace items one by one:** Bounds overlap and avoids two full weeks, but creates mixed-week state and more complicated crash recovery. It needs a clear rule for reels absent from the new manifest.
- **Retain the previous week after activation:** Provides a fallback during failed downloads and expired links, but doubles normal storage use and contradicts the current reclamation requirement.

## Q4. What is the resolver API contract and the required behavior on 429, 5xx, downtime, or “no media found”?

### Ambiguity / failure modes

“Cobalt / SnapInsta backend” identifies examples, not an implementable dependency. Public instances can differ in endpoint, authentication, request/response schema, media variants, URL lifetime, rate limits, and availability. A resolver may return an HTML error, no compatible MP4, or a fresh URL that expires before playback finishes.

The requirements do not define timeout/retry limits, `Retry-After` handling, concurrent taps on the same bookmark, resolver caching, provider selection, or what the user sees underground. They also do not state that the resolver receives the canonical Instagram reel URL and can observe that request.

### Recommended approach

Specify one concrete provider and failure contract for v1:

1. Name the provider/instance and document the HTTPS endpoint, request and response schema, authentication if any, media-selection rule, and expiry metadata if supplied. Treat provider changes as configuration/code changes, not as an implicit guarantee.
2. Send only the canonical reel URL; never send workstation Chrome cookies or other credentials to a public resolver. Accept only HTTPS media URLs and reject HTML/error responses rather than passing arbitrary content to the player.
3. Resolve on demand and deduplicate concurrent requests for the same reel ID. Cache a successful resolution only for its known safe lifetime; reel references and bookmarks remain durable even when resolution fails.
4. Use short network timeouts and bounded retries. Honor `Retry-After` for 429, use exponential backoff for transient network/5xx failures, and persist a cooldown/circuit-breaker deadline so app relaunches cannot reset it and hammer the provider.
5. Distinguish “offline,” “resolver busy/rate-limited,” and “media unavailable.” Preserve the bookmark and watched state in every case. If a freshly resolved CDN URL returns 403/410, permit one re-resolution, then stop rather than loop.
6. With no connectivity, fail immediately with an “online required for this older bookmark” state. Do not imply that resolution can work offline.

The value proposition should describe long-term bookmark playback as **best-effort, dependent on a third-party resolver and Meta**, not guaranteed retrieval.

### Alternatives

- **Ordered fallback across multiple providers:** May improve availability, but multiplies schemas, failure behavior, privacy exposure, and testing. This is disproportionate until one-provider failures are observed in real use.
- **Self-hosted or workstation-hosted resolver:** Offers more control, but a cloud instance violates the zero-user-managed-cloud goal and a home workstation may be unreachable during the commute. It also adds credential and exposure concerns.
- **Permanently cache bookmarked media:** Removes resolver dependency for those items and works offline, but makes storage unbounded unless a pin limit/eviction policy is added. This is a product tradeoff, not a transparent implementation detail.
- **In-app webview extraction:** Avoids a separate resolver API but introduces brittle page automation and changes the confirmed backend decision.

## Q5. Exactly which playback paths are guaranteed offline, and how should connectivity loss behave during streaming?

### Ambiguity / failure modes

“Seamless offline playback” and “zero buffering” currently read more broadly than the design supports. Only validated local files can be network-independent. Older bookmarked reels require both the resolver and Meta CDN, while uncached current-week reels also require connectivity. A cached thumbnail/manifest does not make its video available offline.

Online playback can also fail differently during an MRT ride: a timeout means connectivity loss, a 403 often means URL expiry, and a resolver 5xx is a provider failure. Retrying all of them through the same resolver loop wastes battery and may worsen rate limiting.

### Recommended approach

Add an explicit playback matrix to the requirements:

| Item state | No network | With network |
|---|---|---|
| Current-week item marked `ready` locally, bookmarked or not | Must play from the local file without manifest, resolver, or CDN access | Prefer the same local file |
| Current-week item pending/failed/not downloaded | Show unavailable; retain metadata and retry state | Stream if its URL is fresh, or offer download/retry |
| Older bookmark whose local week was purged | Show “online required”; never claim offline playback | Resolve on demand, then stream |
| Cached metadata without cached media | Show cached caption/thumbnail when available and an offline media state | Normal online behavior |

For local files, a decode failure should mark only that item failed and stop repeated playback attempts. For remote streams, preserve buffered playback where possible, show a reconnect/resume state after transport loss, and do not mark the reel watched merely because playback failed. Re-resolve on an expiry response, not on every tunnel-related timeout.

Reword the core promise to **“network-independent playback for validated, locally cached items; best-effort online playback otherwise.”** “60fps” and “zero buffering” should not be universal guarantees because source frame rate, decode performance, and remote networking vary.

Validate the matrix in airplane mode and with connectivity removed during both a bulk download and an online playback session on the target iPhone.

### Alternatives

- **Guarantee offline playback for every bookmark:** Requires retaining or prefetching all bookmarked media, with a storage budget and eviction policy; it is incompatible with unconditional week wipes.
- **Offer a bounded “keep offline” bookmark pin:** Improves the most valuable offline cases without retaining everything, but adds a user setting and cache-lifecycle rules. Consider only after the base week cache is reliable.
- **Label all non-cached content online-only without automatic streaming recovery:** Simple and honest, but less seamless during intermittent above-ground connectivity.

## Additional scope inconsistency to resolve

§1 requires side-by-side comparison with `main`, while §2 has this branch publish to the same GitHub Pages `data.json` used by production. A code-level R2 bypass alone does not provide side-by-side operation: deploying this branch can overwrite the legacy manifest with short-lived Meta URLs that older clients may not understand.

For benchmarking, prefer a separate static channel/path and manifest `schema_version`, plus a separate app cache namespace. Alternatively, state explicitly that this branch replaces the production manifest and that side-by-side comparison is local-only. Do not leave both interpretations implicit.

## Decisions to add to `REQUIREMENTS.md` before implementation

1. Define per-item download state, atomic file completion, validation, retry limits, relaunch reconciliation, and partial-batch UI semantics.
2. Define manifest freshness fields and name the workstation pipeline as the v1 bulk URL refresher; explicitly prohibit unbounded bulk resolver retries.
3. Replace “wipe when the manifest arrives” with a cache-activation/deletion contract, including low-storage overlap and crash recovery.
4. Select and document the v1 resolver provider, privacy boundary, rate-limit handling, cooldowns, and user-visible failure states.
5. Add the offline/online playback matrix and narrow the “zero buffering / 60fps / seamless” claims to what local caching can actually guarantee.
6. Clarify that “zero-cloud” still uses GitHub Pages, Meta, and a resolver, and decide whether side-by-side benchmarking uses a separate manifest channel or is local-only.
