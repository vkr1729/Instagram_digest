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
    const todayStr = new Date().toISOString().slice(0, 10);
    const DAILY_SET_KEY = 'ig_digest_daily_viewed_' + todayStr;
    const DAILY_SNOOZE_KEY = 'ig_digest_daily_snooze_' + todayStr;
    let dailyViewedSet = new Set();
    try {
      dailyViewedSet = new Set(JSON.parse(localStorage.getItem(DAILY_SET_KEY) || '[]'));
    } catch (e) {}

    function updateDailyCounterBadge() {
      const badge = document.getElementById('dailyCounterBadge');
      if (!badge) return;
      const cnt = dailyViewedSet.size;
      badge.textContent = `🎯 ${cnt}/50`;
      if (cnt >= 50) {
        badge.style.color = '#f09433';
        badge.style.borderColor = 'rgba(240, 148, 51, 0.4)';
      } else {
        badge.style.color = '#a1a1aa';
        badge.style.borderColor = 'rgba(255, 255, 255, 0.15)';
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
      localStorage.setItem(STORAGE_KEY, jsonStr);
      localStorage.setItem(LAST_WATCHED_KEY, reelId);

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
              localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(set)));
              filterCategory(currentCategory);
            }
          }
        })
        .catch(() => {});
    }

    function switchWeek(targetUrl) {
      if (!targetUrl) return;
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

    function armHoldTimer(card) {
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
        if (!video || video.paused) return;
        engageBoost(card, video);
        // Swallow the finger-up click so release never pauses or double-taps.
        suppressNextClick = true;
      }, HOLD_MS);
    }

    // P3 & P4: Bounded LRU in-memory prefetch cache for physical thumbnail file sharing
    const THUMB_CACHE_MAX = 6;
    const cachedThumbnailFiles = new Map();

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

      window.__dispatchedShareUrl = `whatsapp://send?text=${encodeURIComponent(shareText)}`;

      // 1. Try native Web Share synchronously to retain transient activation
      if (navigator.share) {
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

      if (timeDiff < 380 && Math.abs(clickX - lastTapX) < 100) {
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
      // Arm press-and-hold 2x for the rightmost 35% — never on controls.
      if (e.clientX > window.innerWidth * HOLD_ZONE && currentActiveCard &&
          !e.target.closest('button') && !e.target.closest('.creator-meta') &&
          !e.target.closest('.caption-snippet') && !e.target.closest('.mute-pill')) {
        armHoldTimer(currentActiveCard);
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
      if (e.target.closest('.reel-video')) e.preventDefault();
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
        if (video && video.duration && video.readyState > 0) {
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
    function _paintCategoryProgressRings() {
      const watched = getWatchedIds();
      const allCards = Array.from(document.querySelectorAll('.reel-card'));
      const bubbles = document.querySelectorAll('.story-bubble');

      bubbles.forEach(bubble => {
        const cat = bubble.dataset.category;
        const matchingCards = allCards.filter(card => {
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
        });

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
        localStorage.setItem(LAST_ACTIVE_KEY, card.dataset.id);
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

    // P2: Stall/Error Recovery: skip unplayable cards permanently (ignoring file:// test fixtures)
    function skipDeadCard(card, why) {
      if (!card || card.dataset.dead) return;
      if (window.location.protocol === 'file:') return;
      const v = card.querySelector('.reel-video');
      if (v && v.dataset.src && v.dataset.src.startsWith('data:')) return;
      // Snapshot position BEFORE marking dead (visibleCards excludes dead
      // cards, so indexing after would miss and jump to the first reel).
      const cards = visibleCards();
      const i = cards.indexOf(card);
      const wasCurrent = (card === currentActiveCard);
      card.dataset.dead = '1';
      console.warn('Skipping dead reel', card.dataset.id, why);
      showToast('Reel unavailable, skipping');
      card.style.display = 'none';
      if (v) {
        v.pause();
        v.removeAttribute('src');
        v.load();
      }
      // Only navigate when the dead card is the current one; offscreen cards
      // are just marked so they never stall a later scroll into view.
      if (!wasCurrent) return;
      const next = cards[i + 1] || cards[i - 1];
      if (next) {
        goToCard(next);
      } else {
        filterCategory(currentCategory);
      }
    }

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
        const cardCat = card.dataset.category;
        const matchesCat = (
          cat === 'all' ||
          cardCat === cat ||
          (cat === 'ai_tech' && cardCat === 'tech') ||
          (cat === 'tech' && (cardCat === 'ai_tech' || cardCat === 'tech')) ||
          (cat === 'niche' && cardCat === 'explainer') ||
          (cat === 'explainer' && (cardCat === 'niche' || cardCat === 'explainer')) ||
          (cat === 'entertainment' && cardCat === 'culture') ||
          (cat === 'culture' && (cardCat === 'entertainment' || cardCat === 'culture'))
        );

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
        if (isInitialLaunch) {
          // WebKit snap-scroll lock fix on initial load: unlock snap, set scrollTop, prepare paused, restore snap
          feed.style.setProperty('scroll-snap-type', 'none');
          feed.scrollTop = targetCard.offsetTop;
          prepareCardVideoPaused(targetCard);
          setTimeout(() => {
            feed.style.setProperty('scroll-snap-type', 'y mandatory');
          }, 120);
        } else {
          goToCard(targetCard, { play: allWatched ? false : shouldPlay });
        }
        if (isAllCaughtUp) {
          showToast("You're all caught up! Scroll up to rewatch.");
        }
      }
      isInitialLaunch = false;
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
        localStorage.setItem(LAST_ACTIVE_KEY, card.dataset.id);
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
              const icon = card.querySelector('.play-indicator');
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
      }, 350);
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
        skipDeadCard(card, 'media error');
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
            return;
          }
          if (video.paused) {
            manualPause = { card: null, at: 0 };
            playCardVideo(card, true);
            playIcon.textContent = '▶';
            playIcon.classList.add('visible');
            setTimeout(() => playIcon.classList.remove('visible'), 400);
          } else {
            video.pause();
            manualPause = { card, at: performance.now() };
            syncImmersive();
            playIcon.textContent = '❚❚';
            playIcon.classList.add('visible');
            setTimeout(() => playIcon.classList.remove('visible'), 400);
          }
        }, 280);
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

      const targetCard = allCards[targetIdx];
      if (targetCard && targetCard.dataset.id) {
        watched.delete(targetCard.dataset.id);
        delete targetCard.dataset.markedWatched;
      }

      localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(watched)));
      if (targetCard && targetCard.dataset.id) {
        localStorage.setItem(LAST_WATCHED_KEY, targetCard.dataset.id);
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
        return;
      }

      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;

      if (e.key === 'b' || e.key === 'B') {
        e.preventDefault();
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

    // Progressive Service Worker Caching (Requirements 5 & 8: Prev 5 + Next 20)
    function syncCacheWindow(activeCard) {
      if (!navigator.serviceWorker || !navigator.serviceWorker.controller || !activeCard) return;
      const vCards = visibleCards();
      const activeIdx = vCards.indexOf(activeCard);
      if (activeIdx === -1) return;

      const start = Math.max(0, activeIdx - 5);
      const end = Math.min(vCards.length - 1, activeIdx + 20);

      const keepUrls = [];
      const prefetchUrls = [];

      // Immediate high-priority: next 1, next 2, next 3
      for (let i = 1; i <= 3; i++) {
        if (activeIdx + i <= end) {
          const v = vCards[activeIdx + i].querySelector('.reel-video');
          if (v && v.dataset.src) prefetchUrls.push(v.dataset.src);
        }
      }

      // Rest of the 20-ahead window
      for (let i = activeIdx + 4; i <= end; i++) {
        const v = vCards[i].querySelector('.reel-video');
        if (v && v.dataset.src) prefetchUrls.push(v.dataset.src);
      }

      // Prev 5 window
      for (let i = activeIdx - 1; i >= start; i--) {
        const v = vCards[i].querySelector('.reel-video');
        if (v && v.dataset.src) prefetchUrls.push(v.dataset.src);
      }

      // Keep set for cache pruning
      for (let i = start; i <= end; i++) {
        const v = vCards[i].querySelector('.reel-video');
        if (v && v.dataset.src) keepUrls.push(v.dataset.src);
      }

      navigator.serviceWorker.controller.postMessage({
        action: 'PRECACHE_VIDEOS',
        urls: prefetchUrls
      });

      const isDownloaded = localStorage.getItem('ig_digest_download_completed_' + currentWeekId) === 'true';
      navigator.serviceWorker.controller.postMessage({
        action: 'PRUNE_CACHE',
        keepUrls: keepUrls,
        preventPrune: isDownloaded
      });
    }

    // Full Batch Offline Download (Travel / Airplane Mode)
    function startFullDownload() {
      if (!navigator.serviceWorker || !navigator.serviceWorker.controller) {
        showToast('Service worker active on reload');
        return;
      }
      const allUrls = visibleCards()
        .map(c => c.querySelector('.reel-video'))
        .filter(v => v && v.dataset.src)
        .map(v => v.dataset.src);

      const btn = document.getElementById('startOfflineDownloadBtn');
      if (btn) {
        btn.disabled = true;
        btn.textContent = 'Downloading...';
      }

      const pCont = document.getElementById('offlineProgressContainer');
      if (pCont) pCont.style.display = 'block';

      requestWakeLock(); // Keep screen awake during download

      navigator.serviceWorker.controller.postMessage({
        action: 'DOWNLOAD_ALL',
        urls: allUrls
      });
    }

    if (navigator.serviceWorker) {
      navigator.serviceWorker.ready.then(() => {
        queryServiceWorkerCache();
      });
      navigator.serviceWorker.addEventListener('controllerchange', () => {
        queryServiceWorkerCache();
      });

      navigator.serviceWorker.addEventListener('message', (event) => {
        const data = event.data;
        if (!data) return;
        if (data.action === 'CACHED_URLS_LIST' && Array.isArray(data.urls)) {
          cachedVideoUrlsSet = new Set(data.urls);
          syncDownloadButtonState();
        } else if (data.action === 'DOWNLOAD_PROGRESS') {
          const bar = document.getElementById('offlineProgressBar');
          const txt = document.getElementById('offlineProgressText');
          if (bar && txt) {
            const pct = data.total > 0 ? Math.round((data.completed / data.total) * 100) : 0;
            bar.style.width = pct + '%';
            txt.textContent = `Downloading ${data.completed} / ${data.total} (${pct}%)`;
          }
          if (data.url) {
            cachedVideoUrlsSet.add(data.url);
          }
        } else if (data.action === 'DOWNLOAD_COMPLETE') {
          const txt = document.getElementById('offlineProgressText');
          if (txt) txt.textContent = `All ${data.total} reels stored offline! 🎉`;
          const btn = document.getElementById('startOfflineDownloadBtn');
          if (btn) {
            btn.disabled = true;
            btn.textContent = 'All Downloaded ✓';
          }
          try {
            localStorage.setItem('ig_digest_download_completed_' + currentWeekId, 'true');
            localStorage.setItem('ig_digest_downloaded_count_' + currentWeekId, String(data.total));
          } catch (e) {}
          syncDownloadButtonState();
          setTimeout(() => closeModal('offlineModal'), 1200);
          showToast('Offline download complete');
        }
      });
    }

    // Ops controls (+100 expand, cookie refresh, ad-hoc sync) live on the
    // desktop Ops Dashboard (/dashboard) now; the viewer keeps pure viewing
    // plus offline download. The /api/* endpoints they used are unchanged.

    // Initialize & Resume from Last Active Reel / First Unwatched Reel (or Deep Link ?reel=ID)
    const urlParams = new URLSearchParams(window.location.search);
    const targetReelId = urlParams.get('reel');
    const lastActiveId = localStorage.getItem(LAST_ACTIVE_KEY);
    const initialTargetId = targetReelId || lastActiveId;

    filterCategory('all', { play: false, initialTargetId: initialTargetId });
