    // iOS Viewport Height Synchronization
    function syncAppHeight() {
      const h = window.innerHeight;
      document.documentElement.style.setProperty('--app-height', `${h}px`);
    }
    window.addEventListener('resize', syncAppHeight);
    window.addEventListener('orientationchange', syncAppHeight);
    syncAppHeight();

    // State Variables
    const defaultSpeed = {{ default_speed|default(1.25) }};
    let currentSpeed = defaultSpeed;
    const availableSpeeds = [1.0, 1.25, 1.5, 1.75, 2.0];
    document.querySelectorAll('.reel-video').forEach(v => {
      v.defaultPlaybackRate = currentSpeed;
      v.playbackRate = currentSpeed;
      v.addEventListener('loadstart', () => {
        if (v.playbackRate !== 2.0) {
          v.defaultPlaybackRate = currentSpeed;
          v.playbackRate = currentSpeed;
        }
      });
      v.addEventListener('loadedmetadata', () => {
        if (v.playbackRate !== 2.0) {
          v.defaultPlaybackRate = currentSpeed;
          v.playbackRate = currentSpeed;
        }
      });
    });
    let currentCategory = 'all';
    let isAudioMuted = false;
    // Tap-to-unmute guard: stamped when a gesture handler flips a muted video
    // back to sound. The card's tap timer then swallows the same gesture's
    // pause-toggle (the tap meant "sound", not "pause"). Consumed synchronously
    // by the card click handler; timestamped so a stale stamp can never eat a
    // later, unrelated pause tap.
    let gestureUnmuteAt = 0;
    // First-tap play guard: the bootstrap below runs on touchstart, before the
    // click handler captures tap intent. Without this stamp, a first tap on a
    // paused video would capture "was playing" and the timer would pause the
    // video the same gesture just started.
    let gesturePlayAt = 0;
    let autoAdvanceTimeout = null;
    let isScrollingTransition = false;
    let currentActiveCard = null;
    let navGen = 0;
    // Media-debug overlay (?mediadebug=1): ring buffer of playback events for
    // field diagnosis, rendered on-screen so a screenshot captures it. Zero
    // impact when the flag is absent (a single boolean per call site).
    const MEDIA_DEBUG = new URLSearchParams(location.search).has('mediadebug');
    const mediaTrace = [];
    let traceEl = null;
    function mtrace(evt) {
      if (!MEDIA_DEBUG) return;
      const t = new Date();
      const stamp = `${String(t.getMinutes()).padStart(2, '0')}:${String(t.getSeconds()).padStart(2, '0')}.${String(t.getMilliseconds()).padStart(3, '0')}`;
      mediaTrace.push(`${stamp} ${evt}`);
      if (mediaTrace.length > 80) mediaTrace.shift();
      if (!traceEl) {
        traceEl = document.createElement('div');
        traceEl.id = 'mediaDebugOverlay';
        traceEl.style.cssText = 'position:fixed;left:0;right:0;bottom:0;max-height:38vh;overflow:hidden;z-index:9999;pointer-events:none;background:rgba(0,0,0,.82);color:#7CFC98;font:10px/1.5 monospace;white-space:pre-wrap;padding:6px 8px;';
        document.body.appendChild(traceEl);
      }
      traceEl.textContent = mediaTrace.slice(-25).join('\n');
    }
    let lastProgressAt = performance.now();
    let lastProgressTime = -1;
    let pendingSingleTapTimer = null;
    let isInitialLaunch = true;
    // Manual-pause cooldown: a tap-paused card stays paused until tap-resumed.
    // (iOS Safari scrolls tapped content into view; without this, the
    // resulting observer/settle drive re-runs playCardVideo and undoes the
    // pause ~80-300ms later.)
    let manualPause = { card: null, at: 0 };
    const MANUAL_PAUSE_COOLDOWN_MS = 1500;

    // Current Week ID & Week-Scoped LocalStorage Keys
    const currentWeekId = {{ week_id|default('current')|tojson }};
    const STORAGE_KEY = 'ig_digest_watched_ids_' + currentWeekId;
    const LAST_WATCHED_KEY = 'ig_digest_last_watched_id_' + currentWeekId;
    const LAST_ACTIVE_KEY = 'ig_digest_last_active_id_' + currentWeekId;

    // Daily Mindful Limit Tracking (50 reels/day goal)
    function localDateStr(d = new Date()) {
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }
    const todayStr = localDateStr();
    const DAILY_SET_KEY = 'ig_digest_daily_viewed_' + todayStr;
    const DAILY_SNOOZE_KEY = 'ig_digest_daily_snooze_' + todayStr;
    let dailyViewedSet = new Set();
    try {
      dailyViewedSet = new Set(JSON.parse(localStorage.getItem(DAILY_SET_KEY) || '[]'));
    } catch (e) {}

    function updateDailyCounterBadge() {
      const badge = document.getElementById('dailyCounterBadge');
      const modalCount = document.getElementById('modalDailyCount');
      const cnt = dailyViewedSet.size;
      if (modalCount) modalCount.textContent = cnt;
      if (badge) {
        badge.textContent = `🎯 ${cnt}/50`;
        if (cnt >= 50) {
          badge.style.color = '#f09433';
          badge.style.borderColor = 'rgba(240, 148, 51, 0.4)';
        } else {
          badge.style.color = '#a1a1aa';
          badge.style.borderColor = 'rgba(255, 255, 255, 0.15)';
        }
      }
    }

    function recordDailyView(reelId) {
      if (!reelId) return;
      if (!dailyViewedSet.has(reelId)) {
        dailyViewedSet.add(reelId);
        try {
          localStorage.setItem(DAILY_SET_KEY, JSON.stringify(Array.from(dailyViewedSet)));
        } catch (e) {}
        updateDailyCounterBadge();
        checkDailyLimitTrigger();
      }
    }

    function checkDailyLimitTrigger() {
      if (dailyViewedSet.size >= 50) {
        const snoozed = localStorage.getItem(DAILY_SNOOZE_KEY) === 'true';
        if (!snoozed) {
          openModal('dailyLimitModal');
        }
      }
    }

    function pauseAndTakeBreak() {
      closeModal('dailyLimitModal');
      if (currentActiveCard) {
        const v = currentActiveCard.querySelector('.reel-video');
        if (v) v.pause();
      }
      syncImmersive();
      showToast('Taking a break 🌿 Continue whenever you like.');
    }

    function continueMindfulWatching() {
      try {
        localStorage.setItem(DAILY_SNOOZE_KEY, 'true');
      } catch (e) {}
      closeModal('dailyLimitModal');
      if (currentActiveCard) {
        playCardVideo(currentActiveCard);
      }
    }
    updateDailyCounterBadge();

    // P6: Storage hygiene: prune stale week keys outside the retention window + legacy key
    (function pruneOldWeeks() {
      try {
        const keep = new Set({{ available_weeks | map(attribute='week_id') | list | tojson }});
        keep.add(currentWeekId);
        Object.keys(localStorage).forEach(k => {
          const m = k.match(/^ig_digest_(?:watched_ids|last_watched_id|last_active_id|download_completed|downloaded_count)_(\d{4}-\d{2}-\d{2})$/);
          if ((m && !keep.has(m[1])) || k === 'ig_digest_watched_ids') localStorage.removeItem(k);
          const d = k.match(/^ig_digest_daily_(?:viewed|snooze)_(\d{4}-\d{2}-\d{2})$/);
          if (d && d[1] !== todayStr) localStorage.removeItem(k);
        });
      } catch (e) {}
    })();

    // Differential Download State: queries SW cache to know exact cached status
    let cachedVideoUrlsSet = new Set();

    function queryServiceWorkerCache() {
      if (navigator.serviceWorker && navigator.serviceWorker.controller) {
        navigator.serviceWorker.controller.postMessage({ action: 'GET_CACHED_URLS' });
      }
    }

    function syncDownloadButtonState() {
      const headerDlBtn = document.getElementById('offlineDownloadBtn');
      if (!headerDlBtn) return;
      const allUrls = visibleCards()
        .map(c => c.querySelector('.reel-video'))
        .filter(v => v && v.dataset.src)
        .map(v => v.dataset.src.split('?')[0]);
      const totalCount = allUrls.length;

      let cachedCount = 0;
      for (const u of allUrls) {
        if (cachedVideoUrlsSet.has(u)) cachedCount++;
      }

      const storedCompleted = localStorage.getItem('ig_digest_download_completed_' + currentWeekId) === 'true';
      const lastDownloadedCount = parseInt(localStorage.getItem('ig_digest_downloaded_count_' + currentWeekId) || '0', 10);

      let isFullyDownloaded = false;
      if (storedCompleted) {
        if (lastDownloadedCount > 0 && totalCount > lastDownloadedCount) {
          localStorage.removeItem('ig_digest_download_completed_' + currentWeekId);
          isFullyDownloaded = false;
        } else {
          isFullyDownloaded = true;
        }
      } else if (totalCount > 0 && cachedCount >= totalCount) {
        isFullyDownloaded = true;
      }

      const effectiveCached = isFullyDownloaded ? totalCount : cachedCount;
      const diffCount = Math.max(0, totalCount - effectiveCached);
      const dlBtn = document.getElementById('startOfflineDownloadBtn');
      const descEl = document.getElementById('offlineDesc');

      if (descEl) {
        descEl.innerHTML = `You have <strong>${effectiveCached} of ${totalCount}</strong> reels stored locally for offline & airplane mode.<br>${diffCount > 0 ? `<strong>${diffCount} new reels</strong> available to download.` : 'All reels are stored offline! 🎉'}`;
      }

      if (dlBtn) {
        if (diffCount === 0 && totalCount > 0) {
          dlBtn.textContent = 'All Downloaded ✓';
          dlBtn.disabled = true;
        } else if (effectiveCached > 0) {
          dlBtn.textContent = `📥 Download ${diffCount} New`;
          dlBtn.disabled = false;
        } else {
          dlBtn.textContent = `📥 Download All (${totalCount})`;
          dlBtn.disabled = false;
        }
      }

      headerDlBtn.style.display = 'inline-flex';
      if (isFullyDownloaded) {
        headerDlBtn.title = 'Offline Cache (All reels downloaded)';
      } else {
        headerDlBtn.title = diffCount > 0 ? `Offline Download (${diffCount} new)` : 'Offline Cache & Download All';
      }
    }
    syncDownloadButtonState();

    // Helper to get active, visible, non-dead cards (P1, P2)
    function visibleCards() {
      return Array.from(document.querySelectorAll('.reel-card'))
        .filter(c => c.style.display !== 'none' && !c.dataset.dead);
    }

    // Session-based watched tracking (P6: scoped strictly to STORAGE_KEY)
    function getWatchedIds() {
      try {
        const stored = localStorage.getItem(STORAGE_KEY);
        return new Set(JSON.parse(stored || '[]'));
      } catch (e) {
        return new Set();
      }
    }

    const sessionWatchedIds = new Set();

    function markAsWatched(reelId) {
      if (!reelId) return;
      sessionWatchedIds.add(reelId);
      const set = getWatchedIds();
      set.add(reelId);
      const jsonStr = JSON.stringify(Array.from(set));
      try {
        localStorage.setItem(STORAGE_KEY, jsonStr);
        localStorage.setItem(LAST_WATCHED_KEY, reelId);
      } catch (e) { /* Safari private mode / quota: session set keeps the session coherent */ }

      if (typeof updateCategoryProgressRings === 'function') {
        updateCategoryProgressRings();
      }

      // Dual-layer persistence: sync to local server backend
      if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
        fetch('/api/watched', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ week_id: currentWeekId, reel_id: reelId })
        }).catch(() => {});
      }
    }

    function resetWatchedHistory() {
      sessionWatchedIds.clear();
      localStorage.removeItem(STORAGE_KEY);
      localStorage.removeItem(LAST_WATCHED_KEY);
      if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
        fetch('/api/watched', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'reset', week_id: currentWeekId })
        }).catch(() => {});
      }
      filterCategory(currentCategory);
      if (typeof updateCategoryProgressRings === 'function') {
        updateCategoryProgressRings();
      }
    }

    // Sync from local server backend if available on startup
    if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
      fetch('/api/watched?week_id=' + encodeURIComponent(currentWeekId))
        .then(r => r.json())
        .then(data => {
          if (data && Array.isArray(data.watched) && data.watched.length > 0) {
            const set = getWatchedIds();
            let changed = false;
            data.watched.forEach(id => {
              if (!set.has(id)) {
                set.add(id);
                changed = true;
              }
            });
            if (changed) {
              try { localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(set))); } catch (e) {}
              filterCategory(currentCategory);
            }
          }
        })
        .catch(() => {});
    }

    function switchWeek(targetUrl) {
      if (!targetUrl) return;
      if (/^\s*javascript:/i.test(targetUrl)) return;
      if (/^https?:\/\//i.test(targetUrl)) {
        let u; try { u = new URL(targetUrl); } catch (e) { return; }
        if (u.origin !== location.origin) return;
      }
      const inArchive = window.location.pathname.includes('/archive/');
      if (inArchive) {
        if (!targetUrl.startsWith('http') && !targetUrl.startsWith('/')) {
          if (targetUrl.startsWith('archive/')) {
            targetUrl = targetUrl.replace('archive/', '');
          } else {
            targetUrl = '../' + targetUrl;
          }
        }
      }
      window.location.href = targetUrl;
    }

    // Playback Speed Cycling (Header button cycles through available speeds)
    function cycleSpeed() {
      const nextIdx = (availableSpeeds.indexOf(currentSpeed) + 1) % availableSpeeds.length;
      currentSpeed = availableSpeeds[nextIdx];
      const spdEl = document.getElementById('speedDisplay');
      if (spdEl) spdEl.textContent = currentSpeed + 'x';

      // Cycling the default clears any latched hold-2x first
      latchedBoostCard = null;

      // Apply to all currently loaded videos
      document.querySelectorAll('.reel-video').forEach(v => {
        v.playbackRate = currentSpeed;
        v.preservesPitch = true;
        v.webkitPreservesPitch = true;
        v.mozPreservesPitch = true;
      });
    }

    // Press-and-hold 2x (latched per reel): finger down in the rightmost 35%
    // starts a 500ms timer; moving >10px (scrub/swipe) or lifting early cancels
    // it; firing latches 2x for that reel until a right-zone tap exits or the
    // reel changes. No button; the gesture owns the boost.
    const HOLD_ZONE = 0.65;
    const HOLD_MS = 500;
    let holdTimer = null;
    let latchedBoostCard = null;
    let suppressNextClick = false;

    function cancelHold() {
      if (holdTimer) {
        clearTimeout(holdTimer);
        holdTimer = null;
      }
    }

    function engageBoost(card, video) {
      if (pendingSingleTapTimer) {
        clearTimeout(pendingSingleTapTimer);
        pendingSingleTapTimer = null;
      }
      latchedBoostCard = card;
      video.defaultPlaybackRate = 2.0;
      video.playbackRate = 2.0;
      video.preservesPitch = true;
      video.webkitPreservesPitch = true;
      showToast('Boosted to 2x for this reel');
    }

    function exitBoost(video) {
      latchedBoostCard = null;
      video.defaultPlaybackRate = currentSpeed;
      video.playbackRate = currentSpeed;
      showToast(`Speed reset to ${currentSpeed}x`);
    }

    function triggerHapticFeedback() {
      if ('vibrate' in navigator && typeof navigator.vibrate === 'function') {
        try { navigator.vibrate(50); } catch (e) {}
      }
    }

    function showGestureIconPop(icon, x, y) {
      const pop = document.createElement('div');
      pop.className = 'gesture-pop-icon';
      pop.textContent = icon;
      const safeX = Math.max(30, Math.min(window.innerWidth - 30, (typeof x === 'number') ? x : (window.innerWidth / 2)));
      const safeY = Math.max(30, Math.min(window.innerHeight - 30, (typeof y === 'number') ? y : (window.innerHeight * 0.8)));
      pop.style.left = `${safeX}px`;
      pop.style.top = `${safeY}px`;
      document.body.appendChild(pop);
      pop.addEventListener('animationend', () => {
        if (pop.parentNode) pop.remove();
      });
      setTimeout(() => {
        if (pop.parentNode) pop.remove();
      }, 800);
    }

    async function triggerGestureBookmark(card, x, y) {
      const reelId = card ? card.dataset.id : '';
      if (!reelId) return;
      triggerHapticFeedback();
      showGestureIconPop('🔖', x, y);
      await toggleBookmark(reelId, card);
    }

    function triggerGestureShare(card, x, y) {
      const reelId = card ? card.dataset.id : '';
      if (!reelId) return;
      const shareBtn = card.querySelector('.whatsapp-share-btn');
      const badge = card.querySelector('.creator-badge');
      const handle = shareBtn ? (shareBtn.dataset.handle || '') : (badge ? badge.textContent.replace(/^@/, '').trim() : '');

      triggerHapticFeedback();
      showGestureIconPop('↗️', x, y);
      showToast('Opening share options...');
      shareReelWhatsApp(reelId, handle);
    }

    function armHoldTimer(card, startX, startY) {
      cancelHold();
      if (pendingSingleTapTimer) {
        clearTimeout(pendingSingleTapTimer);
        pendingSingleTapTimer = null;
      }
      lastTapTime = 0;
      holdTimer = setTimeout(() => {
        holdTimer = null;
        if (currentActiveCard !== card) return;
        const video = card.querySelector('.reel-video');
        if (!video) return;

        const w = window.innerWidth;
        const h = window.innerHeight;
        const x = (typeof startX === 'number') ? startX : (w * 0.8);
        const y = (typeof startY === 'number') ? startY : (h * 0.5);

        const isLower = y > h * 0.65;
        const isRight = x > w * HOLD_ZONE;
        const isCenter = x >= w * 0.35 && x <= w * HOLD_ZONE;

        if (isRight && !isLower) {
          // Upper/Middle Right: 2x speed boost (only when video is playing)
          if (video.paused) return;
          engageBoost(card, video);
          suppressNextClick = true;
        } else if (isRight && isLower) {
          // Lower Right: Automatic share
          triggerGestureShare(card, x, y);
          suppressNextClick = true;
        } else if (isCenter && isLower) {
          // Center Lower: Automatic bookmark
          triggerGestureBookmark(card, x, y);
          suppressNextClick = true;
        }
      }, HOLD_MS);
    }

    // P3 & P4: Bounded LRU in-memory prefetch cache for physical thumbnail file sharing
    const THUMB_CACHE_MAX = 6;
    const cachedThumbnailFiles = new Map();

    // Feature 1: 1-slot bounded mp4 File for gesture-safe Web Share Level 2.
    // Resolved at card activation (never inside the tap handler) so that
    // navigator.share() fires synchronously within transient activation.
    let activeShareFile = null;
    let activeShareReelId = null;

    async function resolveShareFile(card) {
      const reelId = card && card.dataset.id;
      activeShareFile = null;
      activeShareReelId = null;
      if (!reelId) return;
      try {
        const video = card.querySelector('.reel-video');
        const src = video && (video.dataset.src || video.currentSrc || video.src);
        if (!src || !('caches' in window)) return;
        const cache = await caches.open('ig-digest-media-v1');
        const res = await cache.match(src.split('?')[0]);
        if (!res) return;
        const blob = await res.blob();
        if (currentActiveCard !== card) return; // swiped away mid-resolve
        if (blob && blob.size) {
          activeShareFile = new File([blob], `${reelId}.mp4`, { type: 'video/mp4' });
          activeShareReelId = reelId;
        }
      } catch (err) {}
    }

    async function preloadReelThumbnail(reelId) {
      if (!reelId || cachedThumbnailFiles.has(reelId)) return;
      try {
        const origin = window.location.origin;
        const inArchive = window.location.pathname.includes('/archive/');
        const basePath = inArchive
          ? origin + window.location.pathname.replace(/\/archive\/.*$/, '')
          : origin + window.location.pathname.replace(/\/[^\/]*$/, '');
        const thumbUrl = `${basePath}/thumbnails/${reelId}_portrait.jpg`;
        const res = await fetch(thumbUrl);
        if (res.ok) {
          const blob = await res.blob();
          const file = new File([blob], `${reelId}.jpg`, { type: 'image/jpeg' });
          if (cachedThumbnailFiles.size >= THUMB_CACHE_MAX) {
            const oldestKey = cachedThumbnailFiles.keys().next().value;
            cachedThumbnailFiles.delete(oldestKey);
          }
          cachedThumbnailFiles.set(reelId, file);
        }
      } catch (err) {}
    }

    // P4: Share reel via WhatsApp with physical thumbnail attached and deep link fallback (no await before share)
    function shareReelWhatsApp(reelId, creatorHandle, e) {
      if (e) {
        e.stopPropagation();
        e.preventDefault();
      }
      const origin = window.location.origin;
      const inArchive = window.location.pathname.includes('/archive/');
      const basePath = inArchive
        ? origin + window.location.pathname.replace(/\/archive\/.*$/, '')
        : origin + window.location.pathname.replace(/\/[^\/]*$/, '');
      const shareUrl = `${basePath}/share/${reelId}.html?v=3`;
      const shareText = `Watch @${creatorHandle || 'reel'} on Instagram Digest: ${shareUrl}`;

      // Extract caption from active card snippet (capped at 1,000 chars matching Telegram bookmarking)
      const escId = (window.CSS && CSS.escape) ? CSS.escape(reelId) : String(reelId).replace(/["\\]/g, '\\$&');
      const card = document.querySelector(`.reel-card[data-id="${escId}"]`);
      const captionEl = card ? card.querySelector('.caption-snippet') : null;
      const rawCaption = captionEl ? (captionEl.innerText || captionEl.textContent || '').trim() : '';
      const shareCaption = rawCaption ? rawCaption.slice(0, 1000) : `Reel by @${creatorHandle || 'creator'}`;

      window.__dispatchedShareUrl = `whatsapp://send?text=${encodeURIComponent(shareText)}`;

      // 1. Try native Web Share synchronously to retain transient activation.
      // Hot path: mp4 File pre-resolved at card activation (gesture-safe).
      if (navigator.share) {
        if (reelId && reelId === activeShareReelId && activeShareFile &&
            navigator.canShare && navigator.canShare({ files: [activeShareFile] })) {
          navigator.share({
            files: [activeShareFile],
            title: `Reel by @${creatorHandle || 'creator'}`,
            text: shareCaption
          }).catch(err => {
            if (err && err.name === 'AbortError') return;
            fallbackShare(shareText);
          });
          return;
        }
        const fileToShare = cachedThumbnailFiles.get(reelId);
        if (fileToShare && navigator.canShare && navigator.canShare({ files: [fileToShare] })) {
          navigator.share({
            files: [fileToShare],
            title: `Reel by @${creatorHandle || 'creator'}`,
            text: shareText
          }).catch(err => {
            if (err && err.name === 'AbortError') return;
            fallbackShare(shareText);
          });
          return;
        }

        // Fallback to URL/text share without await
        navigator.share({
          title: `Reel by @${creatorHandle || 'creator'}`,
          text: shareText,
          url: shareUrl
        }).catch(err => {
          if (err && err.name === 'AbortError') return;
          fallbackShare(shareText);
        });
        return;
      }

      fallbackShare(shareText);
    }

    function fallbackShare(shareText) {
      const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
      if (isMobile) {
        window.location.href = window.__dispatchedShareUrl || `whatsapp://send?text=${encodeURIComponent(shareText)}`;
      } else {
        window.open(`https://api.whatsapp.com/send?text=${encodeURIComponent(shareText)}`, '_blank');
      }
    }

    // Screen Wake Lock API (Keeps screen awake without touch input)
    let wakeLock = null;
    async function requestWakeLock() {
      if ('wakeLock' in navigator) {
        try {
          wakeLock = await navigator.wakeLock.request('screen');
        } catch (err) {}
      }
    }
    document.addEventListener('visibilitychange', () => {
      if (wakeLock !== null && document.visibilityState === 'visible') {
        requestWakeLock();
      }
    });

    // Single auto-immersive state: immersive ⇔ (native fullscreen OR current
    // video playing). Play adds it, pause drops it (unless fullscreen holds
    // it) — the old twin state class is retired.
    function isNativeFullscreen() {
      return !!(document.fullscreenElement || document.webkitFullscreenElement);
    }

    function syncImmersive() {
      const shell = document.getElementById('appShell');
      if (!shell) return;
      let playing = false;
      if (currentActiveCard) {
        const v = currentActiveCard.querySelector('.reel-video');
        playing = !!(v && !v.paused && !v.ended);
      }
      shell.classList.toggle('immersive-mode', isNativeFullscreen() || playing);
    }

    // Center double-tap toggles the native Fullscreen API only; the immersive
    // look follows on its own (auto on play, fullscreenchange keeps it).
    function toggleUnifiedFullscreen() {
      if (!isNativeFullscreen()) {
        const docEl = document.documentElement;
        if (docEl.requestFullscreen) {
          docEl.requestFullscreen().catch(() => {});
        } else if (docEl.webkitRequestFullscreen) {
          try { docEl.webkitRequestFullscreen(); } catch (e) {}
        }
        showToast('Fullscreen Active');
      } else {
        if (document.exitFullscreen) {
          document.exitFullscreen().catch(() => {});
        } else if (document.webkitExitFullscreen) {
          try { document.webkitExitFullscreen(); } catch (e) {}
        }
        showToast('Fullscreen Exited');
      }
    }

    // Sync UI with native fullscreen state changes
    function onFullscreenChange() {
      syncImmersive();
    }
    document.addEventListener('fullscreenchange', onFullscreenChange);
    document.addEventListener('webkitfullscreenchange', onFullscreenChange);

    // P5: Mute Pill controls
    function showMutePill(card) {
      const pill = card ? card.querySelector('.mute-pill') : null;
      if (pill) pill.classList.add('visible');
    }

    // Scoped: only the passed card's pill is cleared, so acting on a departing
    // card can never wipe the newly active card's just-shown pill mid-scroll.
    function hideMutePill(card) {
      if (!card) return;
      const pill = card.querySelector('.mute-pill');
      if (pill) pill.classList.remove('visible');
    }

    function unmuteFromPill(pill, e) {
      if (e) {
        e.stopPropagation();
        e.preventDefault();
      }
      isAudioMuted = false;
      // Explicit pill tap owns the unmute: no pause-toggle is pending, so any
      // stamp from this gesture's touchstart must not swallow a later tap.
      gestureUnmuteAt = 0;
      if (currentActiveCard) {
        const card = currentActiveCard;
        const v = card.querySelector('.reel-video');
        if (v) {
          v.muted = false;
          const myGen = navGen;
          v.play().then(() => {
            // Retargeted mid-play (fling): stop the stale card instead of
            // leaving it playing offscreen.
            if (myGen !== navGen || currentActiveCard !== card) v.pause();
          }).catch(() => {});
        }
        hideMutePill(card);
      }
    }

    // Un-mute active video on touch/click if browser temporarily muted it
    function restoreAudioOnInteraction() {
      if (!currentActiveCard) return;
      const activeVideo = currentActiveCard.querySelector('.reel-video');
      if (activeVideo && !isAudioMuted && activeVideo.muted) {
        activeVideo.muted = false;
        gestureUnmuteAt = performance.now();
      }
      hideMutePill(currentActiveCard);
    }
    ['touchstart', 'touchend', 'click'].forEach(evt => {
      window.addEventListener(evt, restoreAudioOnInteraction, { passive: true });
    });

    // First Interaction Bootstrap (Unmutes audio inside the gesture & acquires wake lock)
    // Self-disarming: `once` applies per event type, so without this the tap,
    // touch, key and scroll registrations would each replay the bootstrap.
    const initController = new AbortController();
    function handleInitialInteraction() {
      initController.abort();
      isAudioMuted = false;
      if (currentActiveCard) {
        const v = currentActiveCard.querySelector('.reel-video');
        if (v) {
          if (v.muted) gestureUnmuteAt = performance.now();
          if (v.paused) gesturePlayAt = performance.now();
          v.muted = false;
          v.play().catch(() => {});
        }
        hideMutePill(currentActiveCard);
      } else {
        restoreAudioOnInteraction();
      }
      requestWakeLock();
    }
    ['click', 'touchstart', 'keydown', 'scroll'].forEach(evt => {
      window.addEventListener(evt, handleInitialInteraction, { once: true, passive: true, signal: initController.signal });
    });
    requestWakeLock();

    // Double-tap zones: center toggles native fullscreen; the side zones belong
    // to press-and-hold 2x (hold) and tap-to-exit-2x (single tap), so
    // double-taps there intentionally do nothing. P7 Single-Tap Timer
    // Cancellation retained.
    let lastTapTime = 0;
    let lastTapX = 0;
    let isTouchSwiping = false;
    // Single gesture tap window: double-tap (above) and single-tap debounce
    // (below) must agree, or taps in the gap both pause AND toggle fullscreen.
    const TAP_WINDOW_MS = 320;
    const feed = document.getElementById('feedContainer');

    let lastDoubleActionTime = 0;
    function handleDoubleAction(clickX, width) {
      const now = Date.now();
      if (now - lastDoubleActionTime < 100) return;
      lastDoubleActionTime = now;
      if (pendingSingleTapTimer) {
        clearTimeout(pendingSingleTapTimer);
        pendingSingleTapTimer = null;
      }
      // A rapid right-zone double while latched is still an exit intent —
      // otherwise two quick exit taps would cancel each other out.
      if (latchedBoostCard && currentActiveCard === latchedBoostCard &&
          clickX > window.innerWidth * HOLD_ZONE) {
        const booted = currentActiveCard.querySelector('.reel-video');
        if (booted) exitBoost(booted);
        return;
      }
      if (clickX >= width * 0.35 && clickX <= width * 0.65) {
        toggleUnifiedFullscreen();
      }
    }

    feed.addEventListener('click', (e) => {
      // Hold-2x release clicks never pause and never seed a double-tap pair.
      if (suppressNextClick) {
        suppressNextClick = false;
        lastTapTime = 0;
        return;
      }
      if (isTouchSwiping) return;
      if (e.target.closest('.creator-meta') || e.target.closest('.caption-snippet') || e.target.closest('button') || e.target.closest('.mute-pill')) return;
      const now = Date.now();
      const clickX = e.clientX;
      const width = window.innerWidth;
      const timeDiff = now - lastTapTime;

      if (timeDiff < TAP_WINDOW_MS && Math.abs(clickX - lastTapX) < 100) {
        lastTapTime = 0;
        handleDoubleAction(clickX, width);
      } else {
        lastTapTime = now;
        lastTapX = clickX;
      }
    });

    feed.addEventListener('dblclick', (e) => {
      if (suppressNextClick) { suppressNextClick = false; return; }
      if (isTouchSwiping || (performance.now() - lastScrollTime < 500)) return;
      if (e.target.closest('.creator-meta') || e.target.closest('.caption-snippet') || e.target.closest('button') || e.target.closest('.mute-pill')) return;
      handleDoubleAction(e.clientX, window.innerWidth);
    });

    // Unified pointer tracking: only flag drag if pointer is actually down and moves > 10px
    let isPointerDown = false;
    let pointerStartX = 0;
    let pointerStartY = 0;
    window.addEventListener('pointerdown', (e) => {
      isPointerDown = true;
      pointerStartX = e.clientX;
      pointerStartY = e.clientY;
      // Arm press-and-hold: right zone (2x boost or share) or center-lower (bookmark) — never on controls.
      if (currentActiveCard &&
          !e.target.closest('button') && !e.target.closest('.creator-meta') &&
          !e.target.closest('.caption-snippet') && !e.target.closest('.mute-pill')) {
        const w = window.innerWidth;
        const h = window.innerHeight;
        const isRight = e.clientX > w * HOLD_ZONE;
        const isCenter = e.clientX >= w * 0.35 && e.clientX <= w * HOLD_ZONE;
        const isLower = e.clientY > h * 0.65;
        if (isRight || (isCenter && isLower)) {
          armHoldTimer(currentActiveCard, e.clientX, e.clientY);
        }
      }
    }, { passive: true });
    window.addEventListener('pointermove', (e) => {
      if (!isPointerDown) return;
      if (Math.hypot(e.clientX - pointerStartX, e.clientY - pointerStartY) > 10) {
        isTouchSwiping = true;
        cancelHold();
        if (pendingSingleTapTimer) {
          clearTimeout(pendingSingleTapTimer);
          pendingSingleTapTimer = null;
        }
      }
    }, { passive: true });
    window.addEventListener('pointerup', () => {
      isPointerDown = false;
      cancelHold();
      if (isTouchSwiping) {
        setTimeout(() => { isTouchSwiping = false; }, 350);
      }
    }, { passive: true });
    window.addEventListener('pointercancel', () => {
      isPointerDown = false;
      cancelHold();
      isTouchSwiping = false;
    }, { passive: true });

    // Long-press reliability: never let the OS callout steal the hold gesture.
    feed.addEventListener('contextmenu', (e) => {
      if (e.target.closest('.reel-video') || e.target.closest('.reel-card')) e.preventDefault();
    });

    // Horizontal Touch Gesture Video Seeking & Vertical Swipe Isolation
    let touchStartX = 0;
    let touchStartY = 0;
    let isHorizontalScrubbing = false;
    let scrubInitialTime = 0;
    let scrubTargetTime = 0;

    feed.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1) return;
      touchStartX = e.touches[0].clientX;
      touchStartY = e.touches[0].clientY;
      isTouchSwiping = false;
      isHorizontalScrubbing = false;
      if (currentActiveCard) {
        const video = currentActiveCard.querySelector('.reel-video');
        if (video) scrubInitialTime = video.currentTime;
      }
    }, { passive: true });

    feed.addEventListener('touchmove', (e) => {
      if (e.touches.length !== 1 || !currentActiveCard) return;
      const currentX = e.touches[0].clientX;
      const currentY = e.touches[0].clientY;
      const deltaX = currentX - touchStartX;
      const deltaY = currentY - touchStartY;
      const dist = Math.hypot(deltaX, deltaY);

      if (dist > 10) {
        isTouchSwiping = true;
        cancelHold();
        if (pendingSingleTapTimer) {
          clearTimeout(pendingSingleTapTimer);
          pendingSingleTapTimer = null;
        }
      }

      if (!isHorizontalScrubbing) {
        if (Math.abs(deltaX) > 18 && Math.abs(deltaX) > Math.abs(deltaY) * 1.4) {
          isHorizontalScrubbing = true;
        }
      }

      if (isHorizontalScrubbing) {
        const video = currentActiveCard.querySelector('.reel-video');
        if (!video || !video.duration) return;

        const seekOffset = (deltaX / 220) * 20;
        scrubTargetTime = Math.max(0, Math.min(video.duration, scrubInitialTime + seekOffset));

        const hud = document.getElementById('seekHud');
        const hudTime = document.getElementById('seekHudTime');
        const hudDelta = document.getElementById('seekHudDelta');
        if (hud && hudTime && hudDelta) {
          hudTime.textContent = `${formatTime(scrubTargetTime)} / ${formatTime(video.duration)}`;
          const diff = Math.round(scrubTargetTime - scrubInitialTime);
          hudDelta.textContent = `(${diff >= 0 ? '+' : ''}${diff}s)`;
          hud.classList.add('visible');
        }

        const fill = currentActiveCard.querySelector('.reel-progress-fill');
        if (fill) {
          fill.style.width = `${(scrubTargetTime / video.duration) * 100}%`;
        }
      }
    }, { passive: true });

    feed.addEventListener('touchend', () => {
      if (isHorizontalScrubbing && currentActiveCard) {
        const video = currentActiveCard.querySelector('.reel-video');
        if (video && (video.duration || video.readyState > 0)) {
          video.currentTime = scrubTargetTime;
          if (scrubTargetTime / video.duration >= 0.35 && !currentActiveCard.dataset.markedWatched) {
            currentActiveCard.dataset.markedWatched = 'true';
            markAsWatched(currentActiveCard.dataset.id);
          }
        }
      }
      isHorizontalScrubbing = false;
      const hud = document.getElementById('seekHud');
      if (hud) hud.classList.remove('visible');

      if (isTouchSwiping) {
        setTimeout(() => { isTouchSwiping = false; }, 350);
      }
    }, { passive: true });

    function formatTime(secs) {
      if (isNaN(secs) || secs < 0) return '0:00';
      const m = Math.floor(secs / 60);
      const s = Math.floor(secs % 60);
      return `${m}:${s < 10 ? '0' : ''}${s}`;
    }

    // Overlapping toasts must not kill each other: only the latest timer hides.
    let toastTimer = null;
    function showToast(msg) {
      const t = document.getElementById('globalToast');
      if (!t) return;
      t.textContent = msg;
      t.classList.add('visible');
      if (toastTimer) clearTimeout(toastTimer);
      toastTimer = setTimeout(() => {
        toastTimer = null;
        t.classList.remove('visible');
      }, 1600);
    }

    // Update Story Rings: cross-browser dynamic unwinding progress ring
    // B5: coalesce hot-path calls (every watched mark) into one rAF per frame
    let ringsQueued = false;
    function updateCategoryProgressRings() {
      if (ringsQueued) return;
      ringsQueued = true;
      requestAnimationFrame(() => {
        ringsQueued = false;
        _paintCategoryProgressRings();
      });
    }
    function cardMatchesCategory(card, cat) {
      const cardCat = card.dataset.category;
      return (
        cat === 'all' ||
        cardCat === cat ||
        (cat === 'ai_tech' && cardCat === 'tech') ||
        (cat === 'tech' && (cardCat === 'ai_tech' || cardCat === 'tech')) ||
        (cat === 'niche' && cardCat === 'explainer') ||
        (cat === 'explainer' && (cardCat === 'niche' || cardCat === 'explainer')) ||
        (cat === 'entertainment' && cardCat === 'culture') ||
        (cat === 'culture' && (cardCat === 'entertainment' || cardCat === 'culture'))
      );
    }
    function _paintCategoryProgressRings() {
      const watched = getWatchedIds();
      const allCards = Array.from(document.querySelectorAll('.reel-card'));
      const bubbles = document.querySelectorAll('.story-bubble');

      bubbles.forEach(bubble => {
        const cat = bubble.dataset.category;
        const matchingCards = allCards.filter(card => cardMatchesCategory(card, cat));

        const total = matchingCards.length;
        const watchedCount = matchingCards.filter(c => watched.has(c.dataset.id)).length;
        const leftCount = Math.max(0, total - watchedCount);
        const pctLeft = total > 0 ? (leftCount / total) : 0;
        const deg = Math.round(pctLeft * 360);

        const ring = bubble.querySelector('.story-ring');
        if (ring) {
          ring.style.background = `conic-gradient(from -90deg, #f09433 0deg, #dc2743 ${deg * 0.5}deg, #bc1888 ${deg}deg, rgba(255, 255, 255, 0.16) ${deg}deg 360deg)`;
        }
        bubble.setAttribute('title', `${leftCount} of ${total} left (${Math.round(pctLeft * 100)}%)`);
      });
    }

    // P1: Discrete Navigation with Generation Counter + Instant Scroll & Forward Watch Tracking
    function goToCard(card, options = {}) {
      if (!card) return;
      mtrace(`goto idx=${card.dataset.index}`);
      const gen = ++navGen;
      const shouldPlay = (options.play !== false);
      const vCards = visibleCards();
      const prevCard = currentActiveCard;

      if (prevCard && prevCard !== card) {
        const prevIdx = vCards.indexOf(prevCard);
        const targetIdx = vCards.indexOf(card);
        // Requirement 6: Swipe-up / forward advance marks departed card as watched immediately
        if (targetIdx > prevIdx && prevCard.dataset.id) {
          markAsWatched(prevCard.dataset.id);
          prevCard.dataset.markedWatched = 'true';
        }
      }

      currentActiveCard = card;
      isScrollingTransition = true;
      requestAnimationFrame(() => {
        if (gen !== navGen) { isScrollingTransition = false; return; }
        card.scrollIntoView({ behavior: 'instant', block: 'start' });
        requestAnimationFrame(() => {
          if (gen !== navGen) { isScrollingTransition = false; return; }
          if (shouldPlay) {
            playCardVideo(card);
          } else {
            prepareCardVideoPaused(card);
          }
          isScrollingTransition = false;
        });
      });
    }

    // Launch & Paused State Preparation (Requirement 4: Top choices visible until play)
    function prepareCardVideoPaused(card) {
      if (!card || card.dataset.dead) return;
      // Invalidate any pending goToCard rAF chain so it cannot hijack this
      // paused card after its generation check.
      navGen++;
      currentActiveCard = card;
      document.querySelectorAll('.reel-card.is-active').forEach(c => {
        if (c !== card) c.classList.remove('is-active');
      });
      card.classList.add('is-active');
      syncImmersive();

      if (card.dataset.index !== undefined) {
        const rankNum = parseInt(card.dataset.index, 10) + 1;
        const rankEl = document.getElementById('currentRankDisplay');
        if (rankEl) rankEl.textContent = rankNum;
      }

      if (card.dataset.id) {
        try { localStorage.setItem(LAST_ACTIVE_KEY, card.dataset.id); } catch (e) {}
        recordDailyView(card.dataset.id);
      }

      updateSlidingWindow(card);
      syncCacheWindow(card);

      const video = card.querySelector('.reel-video');
      if (!video) return;

      if (!video.getAttribute('src') && video.dataset.src) {
        video.src = video.dataset.src;
      }
      video.preload = 'auto';
      video.playbackRate = currentSpeed;
      video.pause();

      const playIcon = card.querySelector('.play-pause-indicator');
      if (playIcon) {
        playIcon.textContent = '▶';
        playIcon.classList.add('visible');
      }
    }

    // P2: Stall/Error Recovery. A stall or an error while offline is transient:
    // skip past it now, keep the card, retry on a later visit; only a second
    // strike (or a decode/unsupported-source error while online) hides it.
    const DEAD_STRIKES = 2;
    function strikesOf(card) { return parseInt(card.dataset.strikes || '0', 10); }
    function skipDeadCard(card, why, permanent = false) {
      if (!card || card.dataset.dead) return;
      if (window.location.protocol === 'file:') return;
      const v = card.querySelector('.reel-video');
      if (v && v.dataset.src && v.dataset.src.startsWith('data:')) return;
      const strikes = strikesOf(card) + 1;
      card.dataset.strikes = String(strikes);
      const transient = !permanent && (navigator.onLine === false || strikes < DEAD_STRIKES);
      // Snapshot position BEFORE hiding (visibleCards excludes hidden cards).
      const cards = visibleCards();
      const i = cards.indexOf(card);
      const wasCurrent = (card === currentActiveCard);
      console.warn(transient ? 'Skipping stalled reel' : 'Skipping dead reel', card.dataset.id, why, 'strike', strikes);
      showToast(transient ? 'Reel is slow to load, skipping for now' : 'Reel unavailable, skipping');
      if (v) {
        v.pause();
        v.removeAttribute('src');
        delete v.dataset.warmed;
        delete v.dataset.readyWaiter;
        v.load();
      }
      if (!transient) {
        card.dataset.dead = '1';
        card.style.display = 'none';
      }
      if (!wasCurrent) return;
      const next = cards[i + 1] || cards[i - 1];
      if (next && next !== card) {
        goToCard(next);
      } else {
        filterCategory(currentCategory);
      }
    }

    // Back online: every card hidden by a strike gets another chance.
    window.addEventListener('online', () => {
      document.querySelectorAll('.reel-card[data-dead]').forEach(c => {
        delete c.dataset.dead;
        delete c.dataset.strikes;
        c.style.display = cardMatchesCategory(c, currentCategory) ? 'flex' : 'none';
      });
      updateCategoryProgressRings();
    });

    // Story Category Filtering & Resume from First Unwatched Reel (Requirement 7)
    function filterCategory(cat, options = {}) {
      currentCategory = cat;
      const watched = getWatchedIds();
      const bubbles = document.querySelectorAll('.story-bubble');
      bubbles.forEach(b => b.classList.toggle('active', b.dataset.category === cat));
      updateCategoryProgressRings();

      const cards = Array.from(document.querySelectorAll('.reel-card'));
      let matchingCards = [];

      cards.forEach(card => {
        const matchesCat = cardMatchesCategory(card, cat);

        // Requirement 7: Keep watched videos visible and accessible in feed!
        if (matchesCat && !card.dataset.dead) {
          card.style.display = 'flex';
          matchingCards.push(card);
        } else {
          card.style.display = 'none';
          card.classList.remove('is-active');
          const video = card.querySelector('video');
          if (video) {
            video.pause();
            // F1-class guard: currentTime at HAVE_NOTHING throws and would
            // abort the filter loop mid-iteration on category switches.
            if (video.readyState > 0) video.currentTime = 0;
            // Release the decoder for hidden cards (was: pause only, src kept).
            if (video.getAttribute('src')) {
              video.removeAttribute('src');
              delete video.dataset.warmed;
              delete video.dataset.readyWaiter;
              video.load();
            }
          }
        }
      });

      const celebration = document.getElementById('celebrationScreen');
      const allWatched = matchingCards.length > 0 && matchingCards.every(c => watched.has(c.dataset.id));

      if (matchingCards.length === 0 || allWatched) {
        celebration.classList.add('active');
      } else {
        celebration.classList.remove('active');
      }

      if (matchingCards.length === 0) {
        currentActiveCard = null;
        return;
      }

      // Locate target card: initialTargetId -> first unwatched -> final reel if all watched
      let targetCard = null;
      if (options.initialTargetId) {
        targetCard = matchingCards.find(c => c.dataset.id === options.initialTargetId);
      }
      if (!targetCard) {
        targetCard = matchingCards.find(c => !watched.has(c.dataset.id));
      }
      let isAllCaughtUp = allWatched;

      if (!targetCard) {
        isAllCaughtUp = true;
        targetCard = matchingCards[matchingCards.length - 1]; // Open at final reel
      }

      const shouldPlay = (options.play !== undefined) ? options.play : (!isInitialLaunch);
      if (targetCard) {
        if (isInitialLaunch && feed.style.display !== 'none') {
          // WebKit snap-scroll lock fix on initial load: unlock snap, set scrollTop, prepare paused, restore snap
          feed.style.setProperty('scroll-snap-type', 'none');
          feed.scrollTop = targetCard.offsetTop;
          prepareCardVideoPaused(targetCard);
          setTimeout(() => {
            feed.style.setProperty('scroll-snap-type', 'y mandatory');
          }, 120);
          isInitialLaunch = false;
        } else if (!isInitialLaunch) {
          goToCard(targetCard, { play: allWatched ? false : shouldPlay });
        } else {
          // Feed is hidden behind lock screen; prepare card state, defer scroll to resumeInitialPosition
          prepareCardVideoPaused(targetCard);
        }
        if (isAllCaughtUp) {
          showToast("You're all caught up! Scroll up to rewatch.");
        }
      }
    }

    // P3 & P8: High-Performance Sliding Window Loader (Max 5 posters, prefetch & warmup)
    function updateSlidingWindow(activeCard) {
      if (!activeCard) return;
      const vCards = visibleCards();
      const activeIdx = vCards.indexOf(activeCard);
      if (activeIdx === -1) return;

      const lookAhead = 3;
      const lookBehind = 2;

      vCards.forEach((card, idx) => {
        const video = card.querySelector('.reel-video');
        if (!video) return;
        const targetSrc = video.dataset.src;
        const targetPoster = video.dataset.poster;
        const distance = idx - activeIdx;
        const isWithinWindow = (distance >= -lookBehind && distance <= lookAhead);

        // Dynamic poster management
        if (distance >= -1 && distance <= 4 && targetPoster) {
          if (video.getAttribute('poster') !== targetPoster) {
            video.setAttribute('poster', targetPoster);
          }
        }

        if (isWithinWindow) {
          if (card.dataset.id) {
            preloadReelThumbnail(card.dataset.id);
          }
          if (!video.getAttribute('src') && targetSrc) {
            video.src = targetSrc;
          }

          if (distance === 0) {
            video.preload = 'auto';
            if ('fetchPriority' in video) video.fetchPriority = 'high';
          } else if (distance >= 1 && distance <= 2) {
            if (!video.dataset.warmed) {
              video.preload = 'metadata';
              video.load();
              video.dataset.warmed = '1';
            }
            if ('fetchPriority' in video) video.fetchPriority = 'low';
          } else {
            video.preload = 'metadata';
          }
        } else {
          if (video.getAttribute('src')) {
            video.pause();
            video.removeAttribute('src');
            delete video.dataset.warmed;
            delete video.dataset.readyWaiter;
            video.load();
          }
        }
      });
    }

    // Video Playback, Resilient iOS Fast-Scrolling & Fullscreen State (Requirements 3 & 4)
    function playCardVideo(card, isManual = false) {
      if (!card || card.dataset.dead) return;
      const feedEl = document.getElementById('feedContainer');
      if (feedEl && feedEl.style.display === 'none') return; // bookmarks view owns playback
      // Manual-pause cooldown blocks auto paths (observer, settle, advance);
      // explicit tap resume passes isManual and always goes through.
      if (!isManual && manualPause.card === card &&
          performance.now() - manualPause.at < MANUAL_PAUSE_COOLDOWN_MS) return;
      // Redundant same-card drive (observer + scroll settle): already playing
      // means setup is complete — return WITHOUT bumping navGen so the
      // in-flight call's generation (and any pending auto-advance) survives.
      if (currentActiveCard === card) {
        const activeVideo = card.querySelector('.reel-video');
        if (activeVideo && !activeVideo.paused && activeVideo.readyState >= 2) return;
      }
      const myGen = ++navGen;
      // Hold-2x is scoped to one reel: changing cards clears the latch.
      if (currentActiveCard !== card) latchedBoostCard = null;
      currentActiveCard = card;
      if (manualPause.card !== card) manualPause = { card: null, at: 0 };
      mtrace(`playcard idx=${card.dataset.index} gen=${myGen}`);
      document.querySelectorAll('.reel-card.is-active').forEach(c => {
        if (c !== card) c.classList.remove('is-active');
      });
      card.classList.add('is-active');
      lastProgressAt = performance.now();
      lastProgressTime = -1;

      if (card.dataset.id) {
        preloadReelThumbnail(card.dataset.id);
        resolveShareFile(card);
        try { localStorage.setItem(LAST_ACTIVE_KEY, card.dataset.id); } catch (e) {}
        recordDailyView(card.dataset.id);
      }

      if (card.dataset.index !== undefined) {
        const rankNum = parseInt(card.dataset.index, 10) + 1;
        const rankEl = document.getElementById('currentRankDisplay');
        if (rankEl) rankEl.textContent = rankNum;
      }

      const video = card.querySelector('.reel-video');
      if (!video) return;

      const playIcon = card.querySelector('.play-pause-indicator');
      if (playIcon) playIcon.classList.remove('visible');

      updateSlidingWindow(card);
      syncCacheWindow(card);

      if (!video.getAttribute('src') && video.dataset.src) {
        video.src = video.dataset.src;
      }

      // Pause only nearby videos within sliding window instead of scanning all DOM cards
      const vCards = visibleCards();
      const activeIdx = vCards.indexOf(card);
      vCards.forEach((c, idx) => {
        if (c !== card && Math.abs(idx - activeIdx) <= 4) {
          const v = c.querySelector('.reel-video');
          if (v && !v.paused) {
            v.pause();
            mtrace(`pausing-neighbor idx=${c.dataset.index}`);
            // Fast-scroll race guard: setting currentTime while readyState is
            // HAVE_NOTHING throws InvalidStateError, which used to abort this
            // function before the new card's triggerPlay ran — the settled
            // reel sat paused until manual tap. Metadata-less videos are
            // already at 0, so skip the reset.
            if (v.readyState > 0) v.currentTime = 0;
          }
        }
      });

      // Replay-from-end: a fully-watched reel sits at ended/currentTime ==
      // duration. Swiping back must restart it from 0 (bookmark/share
      // return path), never strand it on the last frame. Some WebKit builds
      // do not auto-rewind play() after ended, so reset explicitly.
      if (video.ended) {
        delete card.dataset.endedHandled;
        try { if (video.readyState > 0) video.currentTime = 0; } catch (e) {}
      }

      // Redundant same-card calls (observer + scroll settle) must not drop a latch.
      video.playbackRate = (latchedBoostCard === card) ? 2.0 : currentSpeed;
      video.preservesPitch = true;
      video.webkitPreservesPitch = true;
      video.mozPreservesPitch = true;
      video.muted = isAudioMuted;
      video.loop = false;

      const triggerPlay = () => {
        if (myGen !== navGen || currentActiveCard !== card) return;
        mtrace(`doplay idx=${card.dataset.index} rs=${video.readyState}`);
        const playPromise = video.play();
        if (playPromise !== undefined) {
          playPromise.then(() => {
            if (myGen !== navGen || currentActiveCard !== card) return;
            mtrace(`play-ok idx=${card.dataset.index}`);
            syncImmersive();
          }).catch((err) => {
            // Superseded by a newer card (fast scroll): the newer
            // playCardVideo owns playback — never mute or replay this stale
            // card in the background.
            if (myGen !== navGen || currentActiveCard !== card) return;
            mtrace(`play-reject idx=${card.dataset.index} err=${err && err.name}`);
            // Interrupted play request, not a policy block: nothing to do.
            if (err && err.name === 'AbortError') return;
            // P5: If browser restricts unmuted autoplay, mute and show mute pill
            video.muted = true;
            video.play().then(() => {
              if (myGen !== navGen || currentActiveCard !== card) return;
              syncImmersive();
              showMutePill(card);
            }).catch((err2) => {
              if (myGen !== navGen || currentActiveCard !== card) return;
              mtrace(`play-reject2 idx=${card.dataset.index} err=${err2 && err2.name}`);
              // Total playback failure (e.g. Low Power Mode rejects even muted
              // programmatic play): leave a visible tap affordance. The next
              // tap resumes through the normal manual path.
              const icon = card.querySelector('.play-pause-indicator');
              if (icon) {
                icon.textContent = '▶';
                icon.classList.add('visible');
              }
              syncImmersive();
            });
          });
        }
      };

      // Resilient playback for momentum scrolling: if metadata isn't buffered yet, attach listeners
      if (video.readyState >= 2) {
        triggerPlay();
      } else {
        // Single-flight ready wait: an older waiter only drops itself via the
        // token, and load() is skipped while a fetch is already in flight so
        // redundant calls stop aborting each other's streams.
        const onReady = () => {
          video.removeEventListener('canplay', onReady);
          video.removeEventListener('loadeddata', onReady);
          if (video.dataset.readyWaiter !== String(myGen)) return;
          delete video.dataset.readyWaiter;
          if (myGen !== navGen || currentActiveCard !== card) return;
          mtrace(`ready idx=${card.dataset.index}`);
          triggerPlay();
        };
        const alreadyLoading = !!video.dataset.readyWaiter && video.networkState === 2;
        video.dataset.readyWaiter = String(myGen);
        video.addEventListener('canplay', onReady, { once: true });
        video.addEventListener('loadeddata', onReady, { once: true });
        if (!alreadyLoading) video.load();
      }
    }

    // Video End & Auto-Advance Handler with P1 navGen race guard
    function handleVideoEnd(card) {
      const gen = ++navGen;
      markAsWatched(card.dataset.id);
      const toast = card.querySelector('.countdown-toast');
      if (toast) toast.classList.add('visible');

      clearTimeout(autoAdvanceTimeout);
      autoAdvanceTimeout = setTimeout(() => {
        if (toast) toast.classList.remove('visible');
        card.dataset.endedHandled = '';
        if (gen === navGen && currentActiveCard === card) {
          advanceToNextReel(card);
        }
      }, 500);
    }

    // Wire Up Video Events
    document.querySelectorAll('.reel-card').forEach(card => {
      const video = card.querySelector('.reel-video');
      const fill = card.querySelector('.reel-progress-fill');
      const playIcon = card.querySelector('.play-pause-indicator');

      // P2: media error listener marks the card dead immediately, active or not
    // (skipDeadCard only navigates away when the dead card is current).
      video.addEventListener('error', () => {
        mtrace(`ev-error idx=${card.dataset.index}`);
        delete video.dataset.readyWaiter;
        const code = video.error && video.error.code;
        const permanent = navigator.onLine !== false && (code === 3 || code === 4) && strikesOf(card) >= 1;
        skipDeadCard(card, 'media error', permanent);
      });

      // P5: clear the tap-to-unmute pill as soon as audio is back
      video.addEventListener('volumechange', () => {
        if (!video.muted) hideMutePill(card);
      });

      // Auto-immersive: play hides chrome + scrim, pause restores them.
      video.addEventListener('play', () => {
        mtrace(`ev-play idx=${card.dataset.index} cur=${card === currentActiveCard}`);
        if (card === currentActiveCard) {
          syncImmersive();
          if (playIcon) playIcon.classList.remove('visible');
        }
      });

      video.addEventListener('pause', () => {
        mtrace(`ev-pause idx=${card.dataset.index} cur=${card === currentActiveCard} t=${video.currentTime.toFixed(1)}`);
        if (card === currentActiveCard) {
          syncImmersive();
        }
      });

      // Update hairline progress (Requirement 6: 30% rule removed; watched recorded on swipe-up)
      video.addEventListener('timeupdate', () => {
        if (video.currentTime !== lastProgressTime) {
          lastProgressTime = video.currentTime;
          lastProgressAt = performance.now();
        }
        if (video.duration) {
          const pct = (video.currentTime / video.duration) * 100;
          fill.style.width = pct + '%';

          // 80% duration milestone watched recording
          if (video.currentTime / video.duration >= 0.80 && !card.dataset.markedWatched && card.dataset.id) {
            card.dataset.markedWatched = 'true';
            markAsWatched(card.dataset.id);
          }

          if (video.currentTime >= video.duration - 0.25 && !video.paused && !card.dataset.endedHandled) {
            card.dataset.endedHandled = 'true';
            handleVideoEnd(card);
          }
        }
      });

      // P7: Tap card to toggle play/pause with 280ms debounce so double-tap cancels single-tap
      card.addEventListener('click', (e) => {
        // Same-gesture unmute capture: if this tap's touchstart already
        // restored sound, the pending single-tap must not ALSO pause. Consumed
        // here (not in the timer) so only this gesture's toggle is suppressed.
        const tapUnmutedAt = gestureUnmuteAt;
        gestureUnmuteAt = 0;
        const tapPlayedAt = gesturePlayAt;
        gesturePlayAt = 0;
        // Hold-2x release clicks are swallowed here, but the flag is LEFT SET:
        // the card handler fires before the bubbled feed handler, which must
        // still see it to consume the tap — clearing here seeded phantom
        // double-taps from the release + next tap pairing up.
        if (suppressNextClick) {
          lastTapTime = 0;
          return;
        }
        if (isTouchSwiping || (performance.now() - lastScrollTime < 500)) return;
        if (e.target.closest('.creator-meta') || e.target.closest('.caption-snippet') || e.target.closest('button') || e.target.closest('.mute-pill')) return;
        if (pendingSingleTapTimer) {
          clearTimeout(pendingSingleTapTimer);
          pendingSingleTapTimer = null;
        }
        const clickX = e.clientX;
        // Tap-intent capture: the debounce races autoplay — a tap committed
        // while paused must not pause a video that started during the wait
        // (field traces: ev-play -> ev-pause ~320ms at t~0, then the
        // manual-pause cooldown wedges it until the next tap). A fresh
        // bootstrap stamp means this same gesture already started it.
        const pausedAtTap = Boolean(video.paused ||
          (tapPlayedAt && performance.now() - tapPlayedAt < 500));
        pendingSingleTapTimer = setTimeout(() => {
          pendingSingleTapTimer = null;
          if (card !== currentActiveCard) return;
          if (isTouchSwiping || (performance.now() - lastScrollTime < 500)) return;
          // Latched 2x exit: a right-zone tap only exits boost, never pauses.
          if (latchedBoostCard === card && clickX > window.innerWidth * HOLD_ZONE) {
            exitBoost(video);
            return;
          }
          // Tap-to-unmute: this gesture already restored sound on a playing
          // video, so the tap meant "sound", not "pause" — keep playing. A
          // paused video still resumes (never a dead tap). The 1500ms bound
          // keeps a stale capture from eating an unrelated later pause.
          if (tapUnmutedAt && performance.now() - tapUnmutedAt < 1500 && !video.paused) {
            mtrace(`tap-unmute-keep idx=${card.dataset.index}`);
            return;
          }
          // Intent already satisfied: play state flipped during the debounce
          // (autoplay won the race, or the video ended/was driven elsewhere).
          if (video.paused !== pausedAtTap) {
            mtrace(`tap-noop idx=${card.dataset.index} wasPaused=${pausedAtTap}`);
            return;
          }
          if (video.paused) {
            mtrace(`tap-play idx=${card.dataset.index}`);
            manualPause = { card: null, at: 0 };
            playCardVideo(card, true);
            playIcon.textContent = '▶';
            playIcon.classList.add('visible');
            setTimeout(() => playIcon.classList.remove('visible'), 400);
          } else {
            mtrace(`tap-pause idx=${card.dataset.index}`);
            video.pause();
            manualPause = { card, at: performance.now() };
            syncImmersive();
            playIcon.textContent = '❚❚';
            playIcon.classList.add('visible');
            setTimeout(() => playIcon.classList.remove('visible'), 400);
          }
        }, TAP_WINDOW_MS);
      });

      video.addEventListener('ended', () => {
        if (!card.dataset.endedHandled) {
          card.dataset.endedHandled = 'true';
          handleVideoEnd(card);
        }
      });
    });

    // P1: Discrete Advance to Next Card
    function advanceToNextReel(currentCard) {
      mtrace(`adv from idx=${currentCard && currentCard.dataset.index}`);
      const vCards = visibleCards();
      const currentIndex = vCards.indexOf(currentCard);

      if (currentIndex !== -1 && currentIndex + 1 < vCards.length) {
        goToCard(vCards[currentIndex + 1]);
      } else {
        filterCategory(currentCategory);
      }
    }

    // P1: Discrete Advance to Previous Card
    function advanceToPreviousReel(currentCard) {
      const vCards = visibleCards();
      const currentIndex = vCards.indexOf(currentCard);

      if (currentIndex > 0) {
        goToCard(vCards[currentIndex - 1]);
      }
    }

    // Modal Management Helpers
    function openModal(modalId) {
      const m = document.getElementById(modalId);
      if (!m) return;
      m.classList.add('active');
      if (modalId === 'jumpModal') {
        const input = document.getElementById('jumpInput');
        if (input) {
          input.value = '';
          setTimeout(() => input.focus(), 100);
        }
      }
    }

    function closeModal(modalId) {
      const m = document.getElementById(modalId);
      if (m) m.classList.remove('active');
    }

    function handleModalBackdrop(e, modalId) {
      if (e.target && e.target.id === modalId) {
        closeModal(modalId);
      }
    }

    // Jump Directly to Video #X
    function openJumpModal() {
      openModal('jumpModal');
    }

    function handleJumpSubmit() {
      const input = document.getElementById('jumpInput');
      if (!input) return;
      const targetRank = parseInt(input.value.trim(), 10);
      const allCards = Array.from(document.querySelectorAll('.reel-card'));
      const totalCount = allCards.length;

      if (isNaN(targetRank) || targetRank < 1 || targetRank > totalCount) {
        showToast('Enter a number between 1 and ' + totalCount);
        return;
      }

      closeModal('jumpModal');

      const targetIdx = targetRank - 1;
      const watched = getWatchedIds();
      const newlyWatched = [];

      for (let i = 0; i < targetIdx; i++) {
        const c = allCards[i];
        if (c && c.dataset.id) {
          watched.add(c.dataset.id);
          c.dataset.markedWatched = 'true';
          newlyWatched.push(c.dataset.id);
        }
      }

      let targetCard = allCards[targetIdx];
      if (targetCard && targetCard.dataset.dead) {
        targetCard = allCards.slice(targetIdx).find(c => !c.dataset.dead) || null;
      }
      if (!targetCard) { showToast('That reel is unavailable'); return; }
      if (targetCard && targetCard.dataset.id) {
        watched.delete(targetCard.dataset.id);
        delete targetCard.dataset.markedWatched;
      }

      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(watched))); } catch (e) {}
      if (targetCard && targetCard.dataset.id) {
        try { localStorage.setItem(LAST_WATCHED_KEY, targetCard.dataset.id); } catch (e) {}
      }

      if (newlyWatched.length > 0 && (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')) {
        fetch('/api/watched/bulk', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ week_id: currentWeekId, watched_ids: newlyWatched })
        }).catch(() => {});
      }

      currentCategory = 'all';
      document.querySelectorAll('.story-bubble').forEach(b => {
        b.classList.toggle('active', b.dataset.category === 'all');
      });

      filterCategory('all');

      if (targetCard) {
        const rankEl = document.getElementById('currentRankDisplay');
        if (rankEl) rankEl.textContent = targetRank;
        goToCard(targetCard);
      }

      showToast('Jumped to Reel #' + targetRank);
    }

    // Instant Unselect Creator Channel
    function unselectCreator(handle, e) {
      if (e) {
        e.stopPropagation();
        e.preventDefault();
      }
      if (!handle) return;
      const cleanHandle = handle.replace('@', '').trim().toLowerCase();

      const cardsToRemove = Array.from(document.querySelectorAll('.reel-card')).filter(card => {
        const badge = card.querySelector('.creator-badge');
        return badge && badge.textContent.replace('@', '').trim().toLowerCase() === cleanHandle;
      });

      if (cardsToRemove.length === 0) return;

      const allVisible = visibleCards();
      const isActiveRemoved = currentActiveCard && cardsToRemove.includes(currentActiveCard);

      let targetCard = null;
      if (isActiveRemoved) {
        const currentIdx = allVisible.indexOf(currentActiveCard);
        for (let i = currentIdx + 1; i < allVisible.length; i++) {
          if (!cardsToRemove.includes(allVisible[i])) {
            targetCard = allVisible[i];
            break;
          }
        }
        if (!targetCard) {
          for (let i = currentIdx - 1; i >= 0; i--) {
            if (!cardsToRemove.includes(allVisible[i])) {
              targetCard = allVisible[i];
              break;
            }
          }
        }
      }

      cardsToRemove.forEach(c => {
        const v = c.querySelector('video');
        if (v) v.pause();
        try { observer.unobserve(c); } catch (e) {}
        c.remove();
      });
      if (typeof updateCategoryProgressRings === 'function') {
        updateCategoryProgressRings();
      }

      showToast('@' + cleanHandle + ' unselected & muted');

      if (isActiveRemoved) {
        if (targetCard) {
          goToCard(targetCard);
        } else {
          const celebration = document.getElementById('celebrationScreen');
          if (celebration) celebration.classList.add('active');
          currentActiveCard = null;
        }
      } else if (currentActiveCard) {
        playCardVideo(currentActiveCard);
      }

      fetch('/api/blacklist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ creator_handle: cleanHandle, action: 'add' })
      }).catch(() => {});
    }

    // Desktop Keyboard Controls: F (fullscreen), Space (play/pause), ArrowDown/Up (next/prev), G (jump), B (unselect)
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeModal('jumpModal');
        const grid = document.getElementById('gridContainer');
        if (grid && grid.style.display !== 'none') toggleGridView(false);
        return;
      }

      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;

      if (e.key === 'v' || e.key === 'V') {
        e.preventDefault();
        const grid = document.getElementById('gridContainer');
        toggleGridView(grid && grid.style.display !== 'none' ? false : true);
        return;
      }

      if (e.key === 'b' || e.key === 'B') {
        e.preventDefault();
        if (!window.__IS_LOCAL) return; // unselect is a local-dashboard-only control
        if (currentActiveCard) {
          const badge = currentActiveCard.querySelector('.creator-badge');
          if (badge) {
            unselectCreator(badge.textContent);
          }
        }
        return;
      }

      if (e.key === 'g' || e.key === 'G') {
        e.preventDefault();
        openJumpModal();
        return;
      }

      if (e.key === 'f' || e.key === 'F') {
        e.preventDefault();
        toggleUnifiedFullscreen();
      }

      if (e.code === 'Space' || e.key === ' ') {
        e.preventDefault();
        if (currentActiveCard) {
          const video = currentActiveCard.querySelector('.reel-video');
          const playIcon = currentActiveCard.querySelector('.play-pause-indicator');
          if (video) {
            if (video.paused) {
              video.play().catch(() => {});
              if (playIcon) {
                playIcon.textContent = '▶';
                playIcon.classList.add('visible');
                setTimeout(() => playIcon.classList.remove('visible'), 400);
              }
            } else {
              video.pause();
              if (playIcon) {
                playIcon.textContent = '❚❚';
                playIcon.classList.add('visible');
                setTimeout(() => playIcon.classList.remove('visible'), 400);
              }
            }
          }
        }
      }

      if (e.key === 'ArrowDown' || e.key === 'j') {
        e.preventDefault();
        if (currentActiveCard) advanceToNextReel(currentActiveCard);
      }

      if (e.key === 'ArrowUp' || e.key === 'k') {
        e.preventDefault();
        if (currentActiveCard) advanceToPreviousReel(currentActiveCard);
      }
    });

    // IntersectionObserver to play video when snapped into view (user scroll)
    const observer = new IntersectionObserver((entries) => {
      if (isScrollingTransition) return;
      entries.forEach(entry => {
        if (entry.isIntersecting && entry.intersectionRatio >= 0.5) {
          mtrace(`io idx=${entry.target.dataset.index} ratio=${entry.intersectionRatio.toFixed(2)}`);
          playCardVideo(entry.target);
        }
      });
    }, { threshold: [0.5] });

    document.querySelectorAll('.reel-card').forEach(c => observer.observe(c));

    // Scroll settlement listener to guarantee playback when scrolling stops
    let scrollEndTimer = null;

    function checkCenteredCard() {
      if (isScrollingTransition) return;
      const feedRect = feed.getBoundingClientRect();
      const feedCenter = feedRect.top + feedRect.height / 2;
      const vCards = visibleCards();

      let closestCard = null;
      let closestDist = Infinity;

      vCards.forEach(card => {
        const r = card.getBoundingClientRect();
        const cardCenter = r.top + r.height / 2;
        const dist = Math.abs(feedCenter - cardCenter);
        if (dist < closestDist) {
          closestDist = dist;
          closestCard = card;
        }
      });

      if (closestCard && closestCard !== currentActiveCard && closestDist < feedRect.height * 0.45) {
        const prevIdx = vCards.indexOf(currentActiveCard);
        const newIdx = vCards.indexOf(closestCard);
        if (currentActiveCard && newIdx > prevIdx && currentActiveCard.dataset.id) {
          markAsWatched(currentActiveCard.dataset.id);
          currentActiveCard.dataset.markedWatched = 'true';
        }
        mtrace(`settle idx=${closestCard.dataset.index}`);
        playCardVideo(closestCard);
      }
    }

    let lastScrollTime = 0;
    feed.addEventListener('scroll', () => {
      lastScrollTime = performance.now();
      if (pendingSingleTapTimer) {
        clearTimeout(pendingSingleTapTimer);
        pendingSingleTapTimer = null;
      }
      clearTimeout(scrollEndTimer);
      scrollEndTimer = setTimeout(checkCenteredCard, 80);
    }, { passive: true });

    if ('onscrollend' in window) {
      feed.addEventListener('scrollend', checkCenteredCard, { passive: true });
    }

    // P2: Global watchdog: if playing card stalls > 8s without progress, skip it
    setInterval(() => {
      const card = currentActiveCard;
      if (!card || document.visibilityState !== 'visible') return;
      const v = card.querySelector('.reel-video');
      if (!v || v.paused || v.ended) return;
      if (v.dataset.src && v.dataset.src.startsWith('data:')) return;
      if (v.error) return skipDeadCard(card, 'media error');
      if (performance.now() - lastProgressAt > 8000 && v.readyState < 3) {
        skipDeadCard(card, 'stall > 8s');
      }
    }, 2000);

    // Progressive Service Worker Caching (Requirements 5 & 8: Prev 5 + Next 20).
    // Coalesced: a fling across ten reels posts one window, not ten.
    let cacheWindowTimer = null;
    function syncCacheWindow(activeCard) {
      if (!navigator.serviceWorker || !navigator.serviceWorker.controller || !activeCard) return;
      clearTimeout(cacheWindowTimer);
      cacheWindowTimer = setTimeout(() => _postCacheWindow(activeCard), 250);
    }

    function _postCacheWindow(activeCard) {
      if (activeCard !== currentActiveCard) return;
      if (!navigator.serviceWorker || !navigator.serviceWorker.controller) return;
      const vCards = visibleCards();
      const activeIdx = vCards.indexOf(activeCard);
      if (activeIdx === -1) return;

      const start = Math.max(0, activeIdx - 5);
      const end = Math.min(vCards.length - 1, activeIdx + 20);
      const keepUrls = [];
      const prefetchUrls = [];
      const srcAt = (i) => { const v = vCards[i].querySelector('.reel-video'); return v && v.dataset.src ? v.dataset.src : null; };

      for (let i = 1; i <= 3; i++) { if (activeIdx + i <= end) { const s = srcAt(activeIdx + i); if (s) prefetchUrls.push(s); } }
      for (let i = activeIdx + 4; i <= end; i++) { const s = srcAt(i); if (s) prefetchUrls.push(s); }
      for (let i = activeIdx - 1; i >= start; i--) { const s = srcAt(i); if (s) prefetchUrls.push(s); }
      for (let i = start; i <= end; i++) { const s = srcAt(i); if (s) keepUrls.push(s); }

      navigator.serviceWorker.controller.postMessage({ action: 'PRECACHE_VIDEOS', urls: prefetchUrls });

      const isDownloaded = localStorage.getItem('ig_digest_download_completed_' + currentWeekId) === 'true';
      navigator.serviceWorker.controller.postMessage({
        action: 'PRUNE_CACHE',
        keepUrls: keepUrls,
        // Never prune while a bulk download is filling the cache.
        preventPrune: isDownloaded || fullDownload.running
      });
    }

    // Full Batch Offline Download (Travel / Airplane Mode). Runs on the page:
    // the Cache API is available here, and unlike a service-worker message
    // handler the page is not killed at the extendable-event lifetime cap.
    // Failures are counted and surfaced; "completed" is written only when
    // every reel is actually in the cache.
    const fullDownload = { running: false, cancel: false };
    const DOWNLOAD_CONCURRENCY = 3;

    function paintDownloadProgress(done, total, failed) {
      const bar = document.getElementById('offlineProgressBar');
      const txt = document.getElementById('offlineProgressText');
      const pct = total > 0 ? Math.round((done / total) * 100) : 0;
      if (bar) bar.style.width = pct + '%';
      if (txt) txt.textContent = `Downloading ${done} / ${total} (${pct}%)` + (failed ? ` · ${failed} failed` : '');
    }

    async function startFullDownload() {
      if (fullDownload.running) return;
      if (!('caches' in window)) { showToast('Offline cache is not available in this browser'); return; }
      const urls = visibleCards()
        .map(c => c.querySelector('.reel-video'))
        .filter(v => v && v.dataset.src)
        .map(v => v.dataset.src);
      const total = urls.length;
      const btn = document.getElementById('startOfflineDownloadBtn');
      if (btn) { btn.disabled = true; btn.textContent = 'Downloading...'; }
      const pCont = document.getElementById('offlineProgressContainer');
      if (pCont) pCont.style.display = 'block';
      requestWakeLock();
      fullDownload.running = true;
      fullDownload.cancel = false;
      let done = 0, failed = 0;
      try {
        const cache = await caches.open('ig-digest-media-v1');
        const existing = new Set((await cache.keys()).map(r => r.url.split('?')[0]));
        const missing = [];
        for (const raw of urls) {
          const clean = raw.split('?')[0];
          if (existing.has(clean)) { done++; cachedVideoUrlsSet.add(clean); } else missing.push(raw);
        }
        paintDownloadProgress(done, total, failed);
        let idx = 0;
        const worker = async () => {
          while (idx < missing.length && !fullDownload.cancel) {
            const raw = missing[idx++];
            const clean = raw.split('?')[0];
            let ok = false;
            for (let attempt = 0; attempt < 2 && !ok && !fullDownload.cancel; attempt++) {
              try {
                const res = await fetch(raw, { mode: 'cors' });
                if (res.ok) {
                  const headers = { 'Content-Type': res.headers.get('Content-Type') || 'video/mp4', 'Accept-Ranges': 'bytes' };
                  const len = res.headers.get('Content-Length');
                  if (len) headers['Content-Length'] = len;
                  await cache.put(clean, new Response(res.body, { status: 200, headers }));
                  ok = true;
                }
              } catch (err) {
                if (err && err.name === 'QuotaExceededError') {
                  fullDownload.cancel = true;
                  showToast('Device storage is full');
                  break;
                }
                if (attempt === 0) await new Promise(r => setTimeout(r, 600));
              }
            }
            if (ok) { done++; cachedVideoUrlsSet.add(clean); } else failed++;
            paintDownloadProgress(done, total, failed);
          }
        };
        await Promise.all(Array.from({ length: DOWNLOAD_CONCURRENCY }, worker));
      } finally {
        fullDownload.running = false;
      }
      const txt = document.getElementById('offlineProgressText');
      const complete = total > 0 && done === total;
      if (complete) {
        if (txt) txt.textContent = `All ${total} reels stored offline! 🎉`;
        try {
          localStorage.setItem('ig_digest_download_completed_' + currentWeekId, 'true');
          localStorage.setItem('ig_digest_downloaded_count_' + currentWeekId, String(total));
        } catch (e) {}
        showToast('Offline download complete');
        setTimeout(() => closeModal('offlineModal'), 1200);
      } else {
        if (txt) txt.textContent = fullDownload.cancel
          ? `Stopped at ${done} / ${total}`
          : `${done} / ${total} stored · ${failed} could not be downloaded`;
        try { localStorage.removeItem('ig_digest_download_completed_' + currentWeekId); } catch (e) {}
        showToast(fullDownload.cancel ? 'Download stopped' : `${failed} reels failed — tap Download to retry`);
      }
      syncDownloadButtonState();
    }

    function cancelFullDownload() { fullDownload.cancel = true; }

    if (navigator.serviceWorker) {
      navigator.serviceWorker.ready.then(() => { queryServiceWorkerCache(); });
      navigator.serviceWorker.addEventListener('controllerchange', () => { queryServiceWorkerCache(); });
      navigator.serviceWorker.addEventListener('message', (event) => {
        const data = event.data;
        if (!data) return;
        if (data.action === 'CACHED_URLS_LIST' && Array.isArray(data.urls)) {
          cachedVideoUrlsSet = new Set(data.urls);
          syncDownloadButtonState();
        }
      });
    }

    // Ops controls (+100 expand, cookie refresh, ad-hoc sync) live on the
    // desktop Ops Dashboard (/dashboard) now; the viewer keeps pure viewing
    // plus offline download. The /api/* endpoints they used are unchanged.

    // Initialize & Resume from Last Active Reel / First Unwatched Reel (or Deep Link ?reel=ID)
    function resumeInitialPosition(forceTargetId) {
      const feed = document.getElementById('feedContainer');
      if (!feed || feed.style.display === 'none') return;
      const urlParams = new URLSearchParams(window.location.search);
      const targetReelId = forceTargetId || urlParams.get('reel') || localStorage.getItem(LAST_ACTIVE_KEY);
      const watched = getWatchedIds();

      const vCards = visibleCards();
      if (!vCards.length) return;

      let targetCard = null;
      if (targetReelId) {
        targetCard = vCards.find(c => c.dataset.id === targetReelId);
      }
      if (!targetCard) {
        targetCard = vCards.find(c => !watched.has(c.dataset.id));
      }
      if (!targetCard) {
        targetCard = vCards[vCards.length - 1]; // All watched -> last card
      }

      if (targetCard) {
        feed.style.setProperty('scroll-snap-type', 'none');
        feed.scrollTop = targetCard.offsetTop;
        prepareCardVideoPaused(targetCard);
        requestAnimationFrame(() => {
          if (targetCard.offsetTop > 0) feed.scrollTop = targetCard.offsetTop;
          setTimeout(() => {
            if (targetCard.offsetTop > 0) feed.scrollTop = targetCard.offsetTop;
            feed.style.setProperty('scroll-snap-type', 'y mandatory');
            updateSlidingWindow(targetCard);
          }, 80);
        });
      }
      isInitialLaunch = false;
    }
    window.resumeInitialPosition = resumeInitialPosition;

    const urlParams = new URLSearchParams(window.location.search);
    const targetReelId = urlParams.get('reel');
    const lastActiveId = localStorage.getItem(LAST_ACTIVE_KEY);
    const initialTargetId = targetReelId || lastActiveId;

    filterCategory('all', { play: false, initialTargetId: initialTargetId });
    resumeInitialPosition();

    // ---- Hybrid Bookmarks: API, snapshot, IndexedDB outbox, sync ----
    function bookmarkApiBase() {
      // Dev-only override: honoured solely on the local dashboard origin, so a
      // poisoned localStorage entry can never redirect the public PWA's
      // Bearer-authenticated calls (see auth.html authBoot).
      const h = location.hostname;
      if (h === 'localhost' || h === '127.0.0.1') {
        try {
          const override = localStorage.getItem('digest_api_base');
          if (override && /^https?:\/\/[a-z0-9.-]+(:\d+)?$/i.test(override)) return override.replace(/\/$/, '');
        } catch (e) {}
      } else {
        try { localStorage.removeItem('digest_api_base'); } catch (e) {} // purge anything a link left behind
      }
      return (window.__BOOKMARK_API_BASE || '').replace(/\/$/, '');
    }

    function bookmarkAuthHeaders() {
      const h = { 'Content-Type': 'application/json' };
      try {
        const k = localStorage.getItem('digest_owner_key');
        if (k) h['Authorization'] = 'Bearer ' + k;
      } catch (e) {}
      return h;
    }

    const SNAPSHOT_KEY = 'ig_digest_bookmarks_snapshot';
    function getBookmarkSnapshot() {
      try {
        const raw = localStorage.getItem(SNAPSHOT_KEY);
        const arr = JSON.parse(raw || '[]');
        return Array.isArray(arr) ? arr : [];
      } catch (e) { return []; }
    }
    function setBookmarkSnapshot(rows) {
      try { localStorage.setItem(SNAPSHOT_KEY, JSON.stringify(rows || [])); } catch (e) {}
      paintBookmarkButtons();
      updateBookmarkBadge(0);
    }
    function snapshotIds() {
      return new Set(getBookmarkSnapshot().map(r => r.id));
    }

    function openIdb() {
      return new Promise((resolve, reject) => {
        if (!('indexedDB' in window)) { reject(new Error('no-indexeddb')); return; }
        const req = indexedDB.open('ig-digest-store', 1);
        req.onupgradeneeded = () => {
          req.result.createObjectStore('pending-bookmark-ops', { keyPath: 'opId', autoIncrement: true });
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      });
    }
    function idbAll(db) {
      return new Promise((resolve, reject) => {
        const tx = db.transaction('pending-bookmark-ops', 'readonly');
        const rq = tx.objectStore('pending-bookmark-ops').getAll();
        rq.onsuccess = () => resolve(rq.result || []);
        rq.onerror = () => reject(rq.error);
      });
    }
    function idbAdd(db, op) {
      return new Promise((resolve, reject) => {
        const tx = db.transaction('pending-bookmark-ops', 'readwrite');
        tx.objectStore('pending-bookmark-ops').add(op);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    }
    function idbDelete(db, opId) {
      return new Promise((resolve, reject) => {
        const tx = db.transaction('pending-bookmark-ops', 'readwrite');
        tx.objectStore('pending-bookmark-ops').delete(opId);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    }
    function idbPut(db, op) {
      return new Promise((resolve, reject) => {
        const tx = db.transaction('pending-bookmark-ops', 'readwrite');
        tx.objectStore('pending-bookmark-ops').put(op);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    }
    function pendingOpCount() {
      return openIdb().then(db => idbAll(db).then(ops => ops.length)).catch(() => 0);
    }

    function reelMetaFromCard(card) {
      const v = card ? card.querySelector('.reel-video') : null;
      const badge = card ? card.querySelector('.creator-badge') : null;
      const cap = card ? card.querySelector('.caption-snippet') : null;
      return {
        id: card ? card.dataset.id : '',
        creator_handle: badge ? badge.textContent.replace(/^@/, '').trim() : '',
        caption: cap ? cap.textContent.trim().slice(0, 500) : '',
        category: card ? (card.dataset.category || '') : '',
        video_url: v ? (v.dataset.r2Src || v.dataset.src || v.currentSrc || v.src || '') : '',
        thumbnail_url: v ? (v.dataset.poster || '') : '',
      };
    }

    async function toggleBookmark(reelId, e) {
      if (e && typeof e.stopPropagation === 'function') { e.stopPropagation(); }
      if (e && typeof e.preventDefault === 'function') { e.preventDefault(); }
      if (!reelId) return;
      if (typeof isOwnerDevice === 'function' && !isOwnerDevice()) {
        const entered = window.prompt('Enter your Owner Key to enable bookmarking on this device:');
        if (entered && entered.trim()) {
          localStorage.setItem('digest_owner_key', entered.trim());
          document.body.classList.remove('readonly');
          updateOwnerKeyStatus();
          showToast('Owner key linked! Saving bookmark...');
        } else {
          return;
        }
      }
      const card = (e && e.target && e.target.closest) ? e.target.closest('.reel-card')
        : (e && e.closest ? e.closest('.reel-card')
        : (currentActiveCard && currentActiveCard.dataset.id === reelId ? currentActiveCard : document.querySelector(`.reel-card[data-id="${(window.CSS && CSS.escape) ? CSS.escape(reelId) : reelId}"]`)));
      const meta = card ? reelMetaFromCard(card) : { id: reelId };
      const ids = snapshotIds();
      const adding = !ids.has(reelId);
      // Optimistic toggle.
      const snap = getBookmarkSnapshot().filter(r => r.id !== reelId);
      if (adding) snap.push({ ...meta, bookmarked_at: new Date().toISOString() });
      setBookmarkSnapshot(snap);
      showToast(adding ? '🔖 Saved to Bookmarks' : '🔖 Removed from Bookmarks');
      // Queue op; private-mode (no IDB) falls back to direct fetch.
      try {
        const db = await openIdb();
        await idbAdd(db, { op: adding ? 'POST' : 'DELETE', id: reelId, payload: meta, ts: Date.now(), attempts: 0 });
        flushBookmarkOutbox();
      } catch (err) {
        sendBookmarkOp(adding ? 'POST' : 'DELETE', reelId, meta)
          .then(() => syncBookmarksFromServer()).catch(() => {});
      }
    }

    async function sendBookmarkOp(op, id, payload) {
      const base = bookmarkApiBase();
      if (!base) throw new Error('no-api-base');
      const init = { method: op === 'POST' ? 'POST' : 'DELETE', headers: bookmarkAuthHeaders() };
      if (op === 'POST') init.body = JSON.stringify(payload);
      const url = op === 'POST' ? `${base}/api/bookmark` : `${base}/api/bookmark/${encodeURIComponent(id)}`;
      return fetch(url, init);
    }

    const OUTBOX_BACKOFF = [1000, 5000, 30000, 300000];
    let outboxFlushing = false;
    let outboxDirty = false;      // an op was queued while a flush was running -> flush again
    let outboxRetryTimer = null;

    function revertOptimisticAdd(id) {
      setBookmarkSnapshot(getBookmarkSnapshot().filter(r => r.id !== id));
    }

    function scheduleOutboxRetry(attempts) {
      if (attempts >= 5) return; // parked: badge shows ⚠; the next online/launch/tab trigger retries
      clearTimeout(outboxRetryTimer);
      outboxRetryTimer = setTimeout(flushBookmarkOutbox, OUTBOX_BACKOFF[Math.min(Math.max(attempts - 1, 0), 3)]);
    }

    async function flushBookmarkOutbox() {
      if (outboxFlushing) { outboxDirty = true; return; }
      const base = bookmarkApiBase();
      if (!base) return;
      let db;
      try { db = await openIdb(); } catch (e) { return; }
      outboxFlushing = true;
      try {
        do {
          outboxDirty = false;
          // opId is monotonic per device; ts is wall-clock and can move backwards.
          const ops = (await idbAll(db)).sort((a, b) => a.opId - b.opId);
          // Coalesce: POST then DELETE for the same id is a no-op — drop both
          // so a toggle-on-then-off while offline performs zero server work.
          // (DELETE then POST replays in order, which is already correct.)
          {
            const firstById = new Map();
            for (const op of ops) if (!firstById.has(op.id)) firstById.set(op.id, op);
            const lastById = new Map();
            for (const op of ops) lastById.set(op.id, op);
            for (const [id, last] of lastById) {
              const first = firstById.get(id);
              if (first !== last && first.op === 'POST' && last.op === 'DELETE') {
                for (const op of ops.filter(o => o.id === id)) await idbDelete(db, op.opId);
              }
            }
          }
          const effective = (await idbAll(db)).sort((a, b) => a.opId - b.opId);
          for (const op of effective) {
            let res;
            try {
              res = await sendBookmarkOp(op.op, op.id, op.payload);
            } catch (err) {
              // Network down: persist the attempt so backoff actually backs
              // off, and do not arm a timer while offline at all — the
              // 'online' event listener re-flushes. (Previously attempts
              // stayed 0 and this re-armed every 1000ms forever.)
              op.attempts = (op.attempts || 0) + 1;
              await idbPut(db, op);
              if (navigator.onLine === false) return;
              scheduleOutboxRetry(op.attempts);
              return;
            }
            if (res.ok) {
              await idbDelete(db, op.opId);
              continue;
            }
            if (res.status === 403) {
              await idbDelete(db, op.opId);
              if (op.op === 'POST') revertOptimisticAdd(op.id);
              document.body.classList.add('readonly');
              updateOwnerKeyStatus();
              showToast('Owner key invalid — re-link this device.');
              continue;
            }
            if (res.status === 400 || res.status === 404 || res.status === 409 || res.status === 413) {
              await idbDelete(db, op.opId); // permanent: poison-pill drop, and undo the optimistic paint
              if (op.op === 'POST') revertOptimisticAdd(op.id);
              showToast(res.status === 404 ? 'That reel expired from the weekly digest.'
                      : res.status === 413 ? 'That reel is too large to bookmark (50 MB limit).'
                      : `Bookmark rejected (${res.status}).`);
              continue;
            }
            // 5xx / other: persist the attempt count and retry later WITHOUT sleeping here.
            op.attempts = (op.attempts || 0) + 1;
            await idbPut(db, op);
            scheduleOutboxRetry(op.attempts);
            return;
          }
        } while (outboxDirty);
        await syncBookmarksFromServer();
      } finally {
        outboxFlushing = false;
        updateBookmarkBadge(await pendingOpCount());
      }
    }

    async function syncBookmarksFromServer() {
      const base = bookmarkApiBase();
      if (!base) return;
      if (typeof isOwnerDevice === 'function' && !isOwnerDevice()) return;
      try {
        const res = await fetch(`${base}/api/bookmarks`, { headers: bookmarkAuthHeaders() });
        if (!res.ok) return;
        let rows = await res.json();
        if (!Array.isArray(rows)) return;
        // Re-apply ops still in the outbox so a fetch that raced a fresh tap
        // cannot wipe its optimistic state (the op lands on the next flush).
        let pending = [];
        try { pending = (await idbAll(await openIdb())).sort((a, b) => a.opId - b.opId); } catch (e) {}
        for (const op of pending) {
          rows = rows.filter(r => r.id !== op.id);
          if (op.op === 'POST') rows.push({ ...(op.payload || { id: op.id }), bookmarked_at: new Date(op.ts || Date.now()).toISOString() });
        }
        setBookmarkSnapshot(rows);
      } catch (e) {}
    }

    function paintBookmarkButtons() {
      const rows = new Map(getBookmarkSnapshot().map(r => [r.id, r]));
      document.querySelectorAll('.bookmark-btn[data-id]').forEach(b => {
        const row = rows.get(b.dataset.id);
        const isBm = !!row;
        b.classList.toggle('bookmarked', isBm);
        // telegram_message_id null (after flush+sync) = cold backup pending:
        // say so instead of showing a confident "Saved".
        b.textContent = !isBm ? '🔖 Save'
          : (row.telegram_message_id == null ? '🔖 Saving…' : '🔖 Saved');
      });
    }

    async function updateBookmarkBadge(pending) {
      const badge = document.getElementById('bookmarkBadge');
      if (!badge) return;
      const n = getBookmarkSnapshot().length;
      let p = pending;
      if (p === undefined) p = await pendingOpCount();
      badge.textContent = p > 0 ? `${n} (⚠${p})` : String(n);
    }

    window.addEventListener('online', () => { flushBookmarkOutbox(); });

    function updateOwnerKeyStatus() {
      const btn = document.getElementById('ownerKeyStatusBtn');
      if (!btn) return;
      const isOwner = typeof isOwnerDevice === 'function' ? isOwnerDevice() : Boolean(localStorage.getItem('digest_owner_key'));
      if (isOwner) {
        btn.textContent = '🔑 Linked';
        btn.classList.add('linked');
        btn.title = 'Owner key is active on this device (tap to manage)';
      } else {
        btn.textContent = '🔑 Link Key';
        btn.classList.remove('linked');
        btn.title = 'Link Owner Key to save and sync bookmarks';
      }
    }

    async function promptOwnerKey() {
      const existing = localStorage.getItem('digest_owner_key') || '';
      const promptText = existing
        ? 'Owner key is active on this device.\nEnter a new Owner Key to update, or leave blank to keep:'
        : 'Enter your 32-character Owner Key to enable saving and syncing bookmarks:';
      const entered = window.prompt(promptText, existing);
      if (entered === null) return;
      const trimmed = entered.trim();
      if (trimmed) {
        localStorage.setItem('digest_owner_key', trimmed);
        document.body.classList.remove('readonly');
        updateOwnerKeyStatus();
        showToast('Owner key linked! Syncing bookmarks...');
        paintBookmarkButtons();
        await syncBookmarksFromServer();
        renderBookmarksGrid(document.getElementById('bookmarkSearchInput')?.value || '');
        flushBookmarkOutbox();
      } else if (existing && entered === '') {
        if (window.confirm('Do you want to unlink the Owner Key from this device?')) {
          localStorage.removeItem('digest_owner_key');
          document.body.classList.add('readonly');
          updateOwnerKeyStatus();
          paintBookmarkButtons();
          showToast('Owner key unlinked.');
        }
      }
    }

    // ---- Bookmarks grid, overlay, view-switch ----
    let savedFeedScroll = 0, savedGridScroll = 0, overlayList = [], overlayIdx = 0;
    let searchDebounce = null;

    function toggleBookmarksView(open) {
      const feed = document.getElementById('feedContainer');
      const bm = document.getElementById('bookmarksContainer');
      const chrome = document.getElementById('topChrome');
      if (!feed || !bm) return;
      if (open) {
        const grid = document.getElementById('gridContainer');
        if (grid && grid.style.display !== 'none') toggleGridView(false);
        savedFeedScroll = feed.scrollTop;
        if (currentActiveCard) {
          const v = currentActiveCard.querySelector('.reel-video');
          if (v) v.pause();
        }
        feed.style.display = 'none';
        if (chrome) chrome.style.display = 'none';
        document.body.style.overflow = 'hidden';
        updateOwnerKeyStatus();
        renderBookmarksGrid(document.getElementById('bookmarkSearchInput')?.value || '');
        bm.style.display = 'flex';
        flushBookmarkOutbox();
      } else {
        closeBookmarkOverlay();
        bm.style.display = 'none';
        if (chrome) chrome.style.display = '';
        document.body.style.overflow = '';
        feed.style.display = '';
        feed.scrollTop = savedFeedScroll;
        // Never autoplay on return (respects manual-pause contract).
      }
    }

    function renderBookmarksGrid(filter) {
      const grid = document.getElementById('bookmarksGrid');
      if (!grid) return;
      const q = (filter || '').toLowerCase();
      overlayList = getBookmarkSnapshot().slice().reverse().filter(r => {
        if (!q) return true;
        return ((r.creator_handle || '') + ' ' + (r.caption || '')).toLowerCase().includes(q);
      });
      grid.textContent = '';
      if (!overlayList.length) {
        const empty = document.createElement('div');
        empty.style.cssText = 'color:#a1a1aa;text-align:center;padding:48px 16px;grid-column:1/-1;display:flex;flex-direction:column;align-items:center;';
        if (q) {
          empty.innerHTML = `
            <div style="font-size:32px;margin-bottom:8px;">🔍</div>
            <div style="font-size:15px;font-weight:600;color:#fff;margin-bottom:4px;">No matching bookmarks</div>
            <div style="color:#8e8e93;font-size:13px;">Try searching for a different handle or keyword.</div>
          `;
        } else {
          empty.innerHTML = `
            <div style="font-size:40px;margin-bottom:12px;">🔖</div>
            <div style="font-size:17px;font-weight:700;color:#fff;margin-bottom:6px;">No Bookmarks Yet</div>
            <div style="color:#8e8e93;font-size:13px;max-width:280px;line-height:1.4;margin-bottom:20px;">Tap the <strong>🔖 Save</strong> button on any reel to save it to your permanent library.</div>
            <button type="button" class="back-to-feed-btn" onclick="toggleBookmarksView(false)">Browse Reels</button>
          `;
        }
        grid.appendChild(empty);
        return;
      }
      // Repo-root-aware base (Pages serves under /Instagram_digest/; the
      // archive pages live one level deeper). Same pattern as share/preload.
      const _origin = window.location.origin;
      const _inArchive = window.location.pathname.includes('/archive/');
      const _basePath = _inArchive
        ? _origin + window.location.pathname.replace(/\/archive\/.*$/, '')
        : _origin + window.location.pathname.replace(/\/[^\/]*$/, '');
      overlayList.forEach((r, i) => {
        const btn = document.createElement('button');
        btn.className = 'bookmark-card';
        btn.setAttribute('aria-label', `Open bookmark ${r.creator_handle || r.id}`);
        const img = document.createElement('img');
        img.loading = 'lazy';
        // R2 permanent portrait first (survives weekly pruning); Pages
        // portrait covers current-week saves whose R2 copy failed to fetch.
        img.src = r.thumbnail_url || `${_basePath}/thumbnails/${r.id}_portrait.jpg`;
        img.alt = '';
        img.onerror = () => { img.style.visibility = 'hidden'; };
        const handle = document.createElement('div');
        handle.className = 'bm-handle';
        handle.textContent = '@' + (r.creator_handle || 'reel');
        btn.appendChild(img);
        btn.appendChild(handle);
        btn.addEventListener('click', () => openBookmarkOverlay(i));
        grid.appendChild(btn);
      });
    }

    function filterBookmarksGrid(value) {
      if (searchDebounce) clearTimeout(searchDebounce);
      searchDebounce = setTimeout(() => renderBookmarksGrid(value || ''), 120);
    }

    function openBookmarkOverlay(i) {
      const ov = document.getElementById('bookmarkOverlay');
      const video = document.getElementById('bookmarkOverlayVideo');
      if (!ov || !video || !overlayList.length) return;
      savedGridScroll = document.getElementById('bookmarksContainer')?.scrollTop || 0;
      overlayIdx = Math.max(0, Math.min(i, overlayList.length - 1));
      paintOverlay();
      ov.style.display = '';
      document.body.style.overflow = 'hidden';
    }

    function paintOverlay() {
      const r = overlayList[overlayIdx];
      const video = document.getElementById('bookmarkOverlayVideo');
      const meta = document.getElementById('bookmarkOverlayMeta');
      const unbm = document.getElementById('bookmarkOverlayUnbookmark');
      if (!r || !video) return;
      video.pause();
      video.src = r.video_url || '';
      video.poster = r.thumbnail_url || '';
      const pp = video.play();
      if (pp && pp.catch) pp.catch(() => {});
      if (meta) {
        // Server rows are data, never markup: build nodes via the DOM API.
        meta.textContent = '';
        const h = document.createElement('span');
        h.className = 'meta-handle';
        h.textContent = '@' + (r.creator_handle || 'reel');
        const dot = document.createElement('span');
        dot.className = 'meta-dot';
        dot.textContent = '•';
        const cnt = document.createElement('span');
        cnt.className = 'meta-count';
        cnt.textContent = `${overlayIdx + 1}/${overlayList.length}`;
        meta.append(h, dot, cnt);
      }
      if (unbm) unbm.textContent = '🔖 Saved';
    }

    function stepOverlay(d) {
      if (!overlayList.length) return;
      overlayIdx = (overlayIdx + d + overlayList.length) % overlayList.length;
      paintOverlay();
    }

    function closeBookmarkOverlay() {
      const ov = document.getElementById('bookmarkOverlay');
      const video = document.getElementById('bookmarkOverlayVideo');
      if (video) { video.pause(); video.removeAttribute('src'); video.load(); }
      if (ov) ov.style.display = 'none';
      const grid = document.getElementById('bookmarksContainer');
      if (grid) grid.scrollTop = savedGridScroll;
    }

    function overlayUnbookmark() {
      const r = overlayList[overlayIdx];
      if (!r) return;
      toggleBookmark(r.id, null).then(() => {
        overlayList = overlayList.filter(x => x.id !== r.id);
        if (!overlayList.length) { closeBookmarkOverlay(); renderBookmarksGrid(''); return; }
        overlayIdx = Math.min(overlayIdx, overlayList.length - 1);
        paintOverlay();
        renderBookmarksGrid(document.getElementById('bookmarkSearchInput')?.value || '');
      });
    }

    // Overlay swipe (vertical) + Esc wiring, attached once.
    (function initOverlayGestures() {
      const ov = document.getElementById('bookmarkOverlay');
      if (!ov || ov.dataset.wired) return;
      ov.dataset.wired = '1';
      let y0 = null;
      ov.addEventListener('touchstart', (t) => { y0 = t.changedTouches[0].clientY; }, { passive: true });
      ov.addEventListener('touchend', (t) => {
        if (y0 === null) return;
        const dy = t.changedTouches[0].clientY - y0;
        y0 = null;
        if (Math.abs(dy) > 48) stepOverlay(dy < 0 ? 1 : -1);
      }, { passive: true });
      document.addEventListener('keydown', (k) => {
        if (k.key === 'Escape' && ov.style.display !== 'none') closeBookmarkOverlay();
      });
    })();

    // ==========================================================================
    // Digest Grid View: 3-Column Visual Grid for 300 Reels (ui-ux-pro-max)
    // ==========================================================================
    let gridCategory = 'all';
    let gridSearchDebounce = null;
    let savedFeedScrollBeforeGrid = 0;

    function toggleGridView(open) {
      const feed = document.getElementById('feedContainer');
      const grid = document.getElementById('gridContainer');
      const chrome = document.getElementById('topChrome');
      if (!feed || !grid) return;
      if (open) {
        if (typeof toggleBookmarksView === 'function') {
          const bm = document.getElementById('bookmarksContainer');
          if (bm && bm.style.display !== 'none') toggleBookmarksView(false);
        }
        savedFeedScrollBeforeGrid = feed.scrollTop;
        if (currentActiveCard) {
          const v = currentActiveCard.querySelector('.reel-video');
          if (v) v.pause();
        }
        feed.style.display = 'none';
        if (chrome) chrome.style.display = 'none';
        document.body.style.overflow = 'hidden';
        renderDigestGrid(document.getElementById('digestGridSearchInput')?.value || '', gridCategory);
        grid.style.display = 'flex';
        // Auto-scroll to current active reel in grid
        requestAnimationFrame(() => {
          const activeEl = grid.querySelector('.digest-grid-card.active-reel');
          if (activeEl) {
            activeEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
        });
      } else {
        grid.style.display = 'none';
        if (chrome) chrome.style.display = '';
        document.body.style.overflow = '';
        feed.style.display = '';
        feed.scrollTop = savedFeedScrollBeforeGrid;
      }
    }

    function selectGridCategory(cat, btn) {
      gridCategory = cat || 'all';
      const chips = document.querySelectorAll('#gridCategoryChips .grid-cat-chip');
      chips.forEach(c => c.classList.remove('active'));
      if (btn) {
        btn.classList.add('active');
      } else {
        const target = document.querySelector(`#gridCategoryChips .grid-cat-chip[data-cat="${gridCategory}"]`);
        if (target) target.classList.add('active');
      }
      renderDigestGrid(document.getElementById('digestGridSearchInput')?.value || '', gridCategory);
    }

    function filterDigestGrid(val) {
      if (gridSearchDebounce) clearTimeout(gridSearchDebounce);
      gridSearchDebounce = setTimeout(() => {
        renderDigestGrid(val || '', gridCategory);
      }, 120);
    }

    function renderDigestGrid(filter, cat) {
      const container = document.getElementById('digestGrid');
      if (!container) return;
      const q = (filter || '').trim().toLowerCase();
      const selectedCat = cat || 'all';
      const allCards = Array.from(document.querySelectorAll('#feedContainer .reel-card')).filter(c => !c.dataset.dead);
      const watchedSet = (typeof getWatchedIds === 'function') ? getWatchedIds() : new Set();

      let watchedCount = 0;
      allCards.forEach(c => {
        if (watchedSet.has(c.dataset.id)) watchedCount++;
      });
      const counter = document.getElementById('gridWatchedCounter');
      if (counter) {
        counter.textContent = `${watchedCount}/${allCards.length} watched`;
      }

      const filteredCards = allCards.filter(c => {
        if (selectedCat !== 'all' && c.dataset.category !== selectedCat) return false;
        if (!q) return true;
        const handle = (c.querySelector('.creator-badge')?.textContent || '').toLowerCase();
        const caption = (c.querySelector('.caption-snippet')?.textContent || '').toLowerCase();
        const rank = (c.querySelector('.rank-pill')?.textContent || '').toLowerCase();
        return handle.includes(q) || caption.includes(q) || rank.includes(q);
      });

      container.textContent = '';
      if (!filteredCards.length) {
        const empty = document.createElement('div');
        empty.style.cssText = 'color:#a1a1aa;text-align:center;padding:48px 16px;grid-column:1/-1;display:flex;flex-direction:column;align-items:center;';
        empty.innerHTML = `
          <div style="font-size:32px;margin-bottom:8px;">🔍</div>
          <div style="font-size:15px;font-weight:600;color:#fff;margin-bottom:4px;">No matching reels</div>
          <div style="color:#8e8e93;font-size:13px;">Try adjusting your search or category filter.</div>
        `;
        container.appendChild(empty);
        return;
      }

      filteredCards.forEach(card => {
        const reelId = card.dataset.id;
        const isActive = (currentActiveCard === card);
        const isWatched = watchedSet.has(reelId);
        const rankText = card.querySelector('.rank-pill')?.textContent || `#${parseInt(card.dataset.index || 0, 10) + 1}`;
        const handleText = card.querySelector('.creator-badge')?.textContent || '@reel';
        const videoEl = card.querySelector('.reel-video');
        const posterSrc = videoEl?.dataset?.poster || videoEl?.getAttribute('poster') || '';

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'digest-grid-card' + (isActive ? ' active-reel' : '') + (isWatched ? ' is-watched' : '');
        btn.setAttribute('aria-label', `Play ${rankText} by ${handleText}`);
        btn.dataset.id = reelId;

        const img = document.createElement('img');
        img.loading = 'lazy';
        img.src = posterSrc;
        img.alt = '';
        img.onerror = () => { img.style.opacity = '0.2'; };

        const rankBadge = document.createElement('div');
        rankBadge.className = 'grid-card-rank';
        rankBadge.textContent = rankText;

        const watchedBadge = document.createElement('div');
        watchedBadge.className = 'grid-card-watched-badge';
        watchedBadge.textContent = '✓';
        watchedBadge.title = 'Watched';

        if (isActive) {
          const activeTag = document.createElement('div');
          activeTag.className = 'grid-card-active-tag';
          activeTag.textContent = 'Active';
          btn.appendChild(activeTag);
        }

        const handleDiv = document.createElement('div');
        handleDiv.className = 'grid-card-handle';
        handleDiv.textContent = handleText;

        btn.appendChild(img);
        btn.appendChild(rankBadge);
        btn.appendChild(watchedBadge);
        btn.appendChild(handleDiv);

        btn.addEventListener('click', () => selectReelFromGrid(reelId));
        container.appendChild(btn);
      });
    }

    function selectReelFromGrid(reelId) {
      if (!reelId) return;
      const esc = (window.CSS && CSS.escape) ? CSS.escape(reelId) : reelId.replace(/["\\]/g, '\\$&');
      const targetCard = document.querySelector(`#feedContainer .reel-card[data-id="${esc}"]`);
      if (!targetCard || targetCard.dataset.dead) return;

      if (typeof currentCategory !== 'undefined' && currentCategory !== 'all' && targetCard.dataset.category !== currentCategory) {
        if (typeof filterCategory === 'function') {
          filterCategory('all');
        }
      }

      toggleGridView(false);

      if (typeof goToCard === 'function') {
        goToCard(targetCard, { play: true });
      }
    }

    // Init: paint snapshot state, owner-gate, opportunistic sync.
    document.addEventListener('DOMContentLoaded', () => {
      try {
        document.body.classList.toggle('readonly', typeof isOwnerDevice === 'function' && !isOwnerDevice());
        updateOwnerKeyStatus();
        paintBookmarkButtons();
        updateBookmarkBadge();
        flushBookmarkOutbox();
      } catch (e) {}
    });
