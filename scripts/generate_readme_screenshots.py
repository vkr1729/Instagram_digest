#!/usr/bin/env python3
"""
generate_readme_screenshots.py — Generates clean, production-grade mobile screenshots
for README.md using Playwright (iPhone 15 Pro viewport).
Features real-world educational creators: @hubermanlab, @raydalio, @markmanson.
"""

from pathlib import Path
import json
import jinja2
from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT_DIR / "templates"
SCREENS_DIR = ROOT_DIR / "assets" / "screens"
SCREENS_DIR.mkdir(parents=True, exist_ok=True)

IPHONE_15_PRO = {
    "viewport": {"width": 393, "height": 852},
    "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15",
    "has_touch": True,
    "is_mobile": True,
    "device_scale_factor": 2,
}

# Sample educational items featuring @hubermanlab, @raydalio, and @markmanson
DEMO_ITEMS = [
    {
        "id": "huberman_sleep_protocol",
        "creator_handle": "hubermanlab",
        "category": "health",
        "rank": 1,
        "rank_display": "#1",
        "views": 2450000,
        "caption": "Mastering Sleep: Morning sunlight within 30 min of waking sets your circadian clock, triggers cortisol peak, and optimizes melatonin release 14h later.",
        "video_url": "",
        "thumbnail": "https://images.unsplash.com/photo-1544717305-2782549b5136?w=600&q=80",
        "poster": "https://images.unsplash.com/photo-1544717305-2782549b5136?w=600&q=80",
        "is_external": False,
    },
    {
        "id": "raydalio_economic_principles",
        "creator_handle": "raydalio",
        "category": "finance",
        "rank": 2,
        "rank_display": "#2",
        "views": 1890000,
        "caption": "How the Economic Machine Works: Short-term debt cycles last 5-8 years; long-term debt cycles last 75-100 years. Don't let debt rise faster than income.",
        "video_url": "",
        "thumbnail": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=600&q=80",
        "poster": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=600&q=80",
        "is_external": False,
    },
    {
        "id": "markmanson_subtle_art",
        "creator_handle": "markmanson",
        "category": "niche",
        "rank": 3,
        "rank_display": "#3",
        "views": 3100000,
        "caption": "The Subtle Art of Choosing Your Struggles: True confidence isn't about avoiding discomfort; it is becoming comfortable with failure and rejection.",
        "video_url": "",
        "thumbnail": "https://images.unsplash.com/photo-1499209974431-9dddcece7f88?w=600&q=80",
        "poster": "https://images.unsplash.com/photo-1499209974431-9dddcece7f88?w=600&q=80",
        "is_external": False,
    },
    {
        "id": "lexfridman_ai_future",
        "creator_handle": "lexfridman",
        "category": "ai_tech",
        "rank": 4,
        "rank_display": "#4",
        "views": 1420000,
        "caption": "The future of intelligence: What happens when autonomous reasoning agents write 90% of production software?",
        "video_url": "",
        "thumbnail": "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=600&q=80",
        "poster": "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=600&q=80",
        "is_external": False,
    },
    {
        "id": "veritasium_science_laws",
        "creator_handle": "veritasium",
        "category": "entertainment",
        "rank": 5,
        "rank_display": "#5",
        "views": 4200000,
        "caption": "Why the speed of light might not be what you think: the one-way speed of light paradox explained.",
        "video_url": "",
        "thumbnail": "https://images.unsplash.com/photo-1507668077129-56e32842fceb?w=600&q=80",
        "poster": "https://images.unsplash.com/photo-1507668077129-56e32842fceb?w=600&q=80",
        "is_external": False,
    },
    {
        "id": "jamieoliver_clean_cooking",
        "creator_handle": "jamieoliver",
        "category": "food",
        "rank": 6,
        "rank_display": "#6",
        "views": 980000,
        "caption": "10-Minute Mediterranean Skillet: Fresh olive oil, blistered tomatoes, basil, and wild herbs.",
        "video_url": "",
        "thumbnail": "https://images.unsplash.com/photo-1540420773420-3366772f4999?w=600&q=80",
        "poster": "https://images.unsplash.com/photo-1540420773420-3366772f4999?w=600&q=80",
        "is_external": False,
    },
]

# Generate additional mock cards up to 50 for realistic grid density
for i in range(7, 51):
    creators = [("hubermanlab", "health"), ("raydalio", "finance"), ("markmanson", "niche"), ("lexfridman", "ai_tech"), ("veritasium", "entertainment"), ("jamieoliver", "food")]
    handle, cat = creators[(i - 1) % len(creators)]
    DEMO_ITEMS.append({
        "id": f"demo_item_{i}",
        "creator_handle": handle,
        "category": cat,
        "rank": i,
        "rank_display": f"#{i}",
        "views": 500000 + i * 15000,
        "caption": f"Deep insights and practical actionable principles on {cat.replace('_', ' ')} from @{handle}.",
        "video_url": "",
        "thumbnail": DEMO_ITEMS[(i - 1) % 6]["thumbnail"],
        "poster": DEMO_ITEMS[(i - 1) % 6]["poster"],
        "is_external": False,
    })


def render_demo_html() -> Path:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES_DIR),
        autoescape=jinja2.select_autoescape(["html", "xml"]),
    )
    template = env.get_template("viewer.html")
    html_content = template.render(
        items=DEMO_ITEMS,
        week_id="2026-W37",
        available_weeks=[{"week_id": "2026-W37", "label": "This Week (Sep 14)", "url": "./"}],
        is_local=False,
        default_speed=1.0,
        pages_base_url="./",
        pin_sha256="",
        bookmark_api_base="https://api.example.com",
        r2_public_domain="",
    )
    demo_file = ROOT_DIR / "site" / "readme_demo.html"
    demo_file.parent.mkdir(parents=True, exist_ok=True)
    demo_file.write_text(html_content, encoding="utf-8")
    return demo_file


def capture_screenshots():
    demo_path = render_demo_html()
    print(f"Rendered demo site to {demo_path}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(**IPHONE_15_PRO)
        page = context.new_page()

        page.goto(f"file://{demo_path.resolve()}")
        page.wait_for_selector(".reel-card", state="visible")
        page.wait_for_timeout(500)

        # Style simulated video posters with elegant gradients in case unsplash is offline
        page.evaluate("""() => {
            const colors = [
                'linear-gradient(135deg, #0f2027 0%, #203a43 50%, #2c5364 100%)',
                'linear-gradient(135deg, #1f1c2c 0%, #928dab 100%)',
                'linear-gradient(135deg, #141e30 0%, #243b55 100%)',
                'linear-gradient(135deg, #16222f 0%, #36485e 100%)',
                'linear-gradient(135deg, #0d1117 0%, #161b22 100%)',
                'linear-gradient(135deg, #1a1a24 0%, #2a2b3d 100%)'
            ];
            document.querySelectorAll('.reel-card').forEach((card, i) => {
                card.style.background = colors[i % colors.length];
                const video = card.querySelector('.reel-video');
                if (video) video.style.display = 'none';
            });
            // Mark items 1, 2, 4 as watched for badges demo
            if (typeof markAsWatched === 'function') {
                markAsWatched('raydalio_economic_principles');
                markAsWatched('lexfridman_ai_future');
            }
        }""")
        page.wait_for_timeout(300)

        # 1. Screen: Full Vertical Feed View
        feed_screen = SCREENS_DIR / "feed_view.png"
        page.screenshot(path=str(feed_screen))
        print(f"Captured: {feed_screen}")

        # 2. Screen: 300-Reel Visual Grid View
        page.evaluate("() => toggleGridView(true)")
        page.wait_for_timeout(400)
        # Apply visual card gradients to grid cards
        page.evaluate("""() => {
            const colors = [
                'linear-gradient(135deg, #1e3c72 0%, #2a5298 100%)',
                'linear-gradient(135deg, #2c3e50 0%, #3498db 100%)',
                'linear-gradient(135deg, #232526 0%, #414345 100%)',
                'linear-gradient(135deg, #114357 0%, #f29492 100%)',
                'linear-gradient(135deg, #134e5e 0%, #71b280 100%)',
                'linear-gradient(135deg, #283048 0%, #859398 100%)'
            ];
            document.querySelectorAll('#digestGrid .digest-grid-card').forEach((btn, i) => {
                btn.style.background = colors[i % colors.length];
            });
        }""")
        page.wait_for_timeout(200)
        grid_screen = SCREENS_DIR / "grid_view.png"
        page.screenshot(path=str(grid_screen))
        print(f"Captured: {grid_screen}")

        # 3. Screen: Bookmarks View
        page.evaluate("""() => {
            toggleGridView(false);
            const demoBookmarks = [
                {
                    id: 'huberman_sleep_protocol',
                    creator_handle: 'hubermanlab',
                    caption: 'Mastering Sleep Protocol',
                    thumbnail_url: '',
                    video_url: ''
                },
                {
                    id: 'raydalio_economic_principles',
                    creator_handle: 'raydalio',
                    caption: 'How The Economic Machine Works',
                    thumbnail_url: '',
                    video_url: ''
                },
                {
                    id: 'markmanson_subtle_art',
                    creator_handle: 'markmanson',
                    caption: 'The Subtle Art of Choosing Struggles',
                    thumbnail_url: '',
                    video_url: ''
                }
            ];
            localStorage.setItem('ig_digest_bookmarks_snapshot', JSON.stringify(demoBookmarks));
            toggleBookmarksView(true);
            if (typeof renderBookmarksGrid === 'function') renderBookmarksGrid('');
        }""")
        page.wait_for_timeout(400)
        page.evaluate("""() => {
            const colors = [
                'linear-gradient(135deg, #1e3c72 0%, #2a5298 100%)',
                'linear-gradient(135deg, #2c3e50 0%, #3498db 100%)',
                'linear-gradient(135deg, #232526 0%, #414345 100%)'
            ];
            document.querySelectorAll('#bookmarksGrid .bookmark-card').forEach((btn, i) => {
                btn.style.background = colors[i % colors.length];
            });
        }""")
        page.wait_for_timeout(200)
        bm_screen = SCREENS_DIR / "bookmarks_view.png"
        page.screenshot(path=str(bm_screen))
        print(f"Captured: {bm_screen}")

        # 4. Screen: Mindful Jump to Reel Modal
        page.evaluate("""() => {
            toggleBookmarksView(false);
            openJumpModal();
            const input = document.getElementById('jumpInput');
            if (input) input.value = '42';
        }""")
        page.wait_for_timeout(300)
        jump_screen = SCREENS_DIR / "jump_modal.png"
        page.screenshot(path=str(jump_screen))
        print(f"Captured: {jump_screen}")

        browser.close()

    if demo_path.exists():
        demo_path.unlink()
    print("Screenshot generation completed successfully!")


if __name__ == "__main__":
    capture_screenshots()
