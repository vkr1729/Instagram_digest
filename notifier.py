"""
notifier.py — Sends email notifications confirming weekly Instagram Digest refreshes.
"""

from __future__ import annotations

import argparse
import html
import logging
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import config

logger = logging.getLogger("InstagramDigest.Notifier")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def is_email_configured() -> bool:
    """Check if SMTP credentials and recipient email are configured."""
    return bool(config.SMTP_USER and config.NOTIFICATION_EMAIL and config.SMTP_PASS)


def build_email_message(
    week_id: str,
    count: int = 200,
    top_reels: list[dict[str, Any]] | None = None,
    site_url: str | None = None,
) -> MIMEMultipart:
    """Build a rich, responsive multipart HTML and plain-text email message."""
    url = site_url or config.PAGES_BASE_URL
    subject = f"✨ Instagram Digest Ready — Week of {week_id} ({count} Reels)"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Instagram Digest <{config.SMTP_USER}>"
    msg["To"] = config.NOTIFICATION_EMAIL

    # Plain-text version
    text_lines = [
        f"Instagram Digest — Weekly Refresh Complete ({week_id})",
        "=" * 55,
        "",
        f"Your weekly feed has been refreshed with {count} high-signal reels.",
        "",
        f"View Digest: {url}",
        "",
    ]
    if top_reels:
        text_lines.append("Top Featured Reels:")
        for r in top_reels[:5]:
            rank = r.get("rank_display") or f"#{r.get('rank', 1):02d}"
            handle = r.get("creator_handle", "creator")
            cat = r.get("category", "").replace("_", " ").title()
            caption = (r.get("caption") or "").replace("\n", " ")[:90]
            text_lines.append(f"  • {rank} @{handle} [{cat}]: {caption}...")
        text_lines.append("")

    text_lines.append(f"Open the PWA on mobile or desktop: {url}")
    text_content = "\n".join(text_lines)

    # Rich HTML version
    reels_html = ""
    if top_reels:
        reels_rows = []
        for r in top_reels[:5]:
            rank = html.escape(str(r.get("rank_display") or f"#{r.get('rank', 1):02d}"))
            handle = html.escape(str(r.get("creator_handle", "creator")))
            cat = html.escape(str(r.get("category", "")).replace("_", " ").title())
            raw_caption = str(r.get("caption") or "").strip().replace("\n", " ")
            caption = html.escape(raw_caption[:100] + ("..." if len(raw_caption) > 100 else ""))
            reels_rows.append(f"""
                <tr style="border-bottom: 1px solid #2d3748;">
                    <td style="padding: 10px 12px; font-weight: bold; color: #e1306c; font-size: 13px;">{rank}</td>
                    <td style="padding: 10px 12px;">
                        <div style="font-weight: 600; color: #f7fafc; font-size: 14px;">@{handle}</div>
                        <div style="color: #a0aec0; font-size: 12px; margin-top: 2px;">{caption}</div>
                    </td>
                    <td style="padding: 10px 12px; text-align: right;">
                        <span style="display: inline-block; padding: 2px 8px; border-radius: 12px; background: #2d3748; color: #cbd5e0; font-size: 11px; text-transform: uppercase;">{cat}</span>
                    </td>
                </tr>
            """)
        reels_html = f"""
            <div style="margin-top: 24px;">
                <h3 style="color: #edf2f7; font-size: 15px; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px;">🔥 Top Featured Highlights</h3>
                <table style="width: 100%; border-collapse: collapse; background: #1a202c; border-radius: 8px; overflow: hidden;">
                    <tbody>
                        {"".join(reels_rows)}
                    </tbody>
                </table>
            </div>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin: 0; padding: 0; background-color: #0f141c; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #e2e8f0;">
        <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #0f141c; padding: 24px 12px;">
            <tr>
                <td align="center">
                    <table width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width: 580px; background-color: #161e2e; border-radius: 12px; border: 1px solid #2d3748; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.5);">
                        <!-- Header Banner -->
                        <tr>
                            <td style="background: linear-gradient(135deg, #833ab4, #fd1d1d, #fcb045); padding: 28px 24px; text-align: center;">
                                <div style="font-size: 32px; line-height: 1;">✨</div>
                                <h1 style="color: #ffffff; font-size: 22px; font-weight: 800; margin: 8px 0 0 0; letter-spacing: -0.5px;">Instagram Digest</h1>
                                <p style="color: rgba(255, 255, 255, 0.9); font-size: 13px; margin: 4px 0 0 0; font-weight: 500;">Weekly High-Signal Reel Curation</p>
                            </td>
                        </tr>
                        
                        <!-- Content Body -->
                        <tr>
                            <td style="padding: 28px 24px;">
                                <h2 style="color: #f7fafc; font-size: 18px; margin: 0 0 12px 0;">This Week's Feed Is Ready!</h2>
                                <p style="color: #cbd5e0; font-size: 14px; line-height: 1.6; margin: 0 0 20px 0;">
                                    Your weekly digest for <strong>{week_id}</strong> has been refreshed with <strong>{count} curated reels</strong> across Entertainment, Finance, AI & Tech, Health, Food, and Niche topics.
                                </p>
                                
                                <!-- CTA Button -->
                                <div style="text-align: center; margin: 24px 0;">
                                    <a href="{url}" style="display: inline-block; background: linear-gradient(135deg, #e1306c, #c13584); color: #ffffff; text-decoration: none; font-size: 15px; font-weight: 700; padding: 12px 28px; border-radius: 24px; box-shadow: 0 4px 12px rgba(225, 48, 108, 0.35);">
                                        Open Instagram Digest &rarr;
                                    </a>
                                </div>
                                
                                {reels_html}
                                
                                <div style="margin-top: 24px; padding: 14px 16px; background-color: #1a202c; border-radius: 8px; border-left: 4px solid #e1306c; font-size: 12px; color: #a0aec0;">
                                    💡 <strong>PWA Tip:</strong> Add to your phone's Home Screen for the edge-to-edge immersive experience with offline caching.
                                </div>
                            </td>
                        </tr>
                        
                        <!-- Footer -->
                        <tr>
                            <td style="padding: 18px 24px; background-color: #111827; border-top: 1px solid #1f2937; text-align: center; color: #718096; font-size: 12px;">
                                Sent automatically by Instagram Digest Bot &bull; Week {week_id}<br>
                                <a href="{url}" style="color: #a0aec0; text-decoration: underline;">{url}</a>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    msg.attach(MIMEText(text_content, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))
    return msg


def send_digest_email(
    week_id: str,
    count: int = 200,
    top_reels: list[dict[str, Any]] | None = None,
    site_url: str | None = None,
) -> bool:
    """
    Send an email confirmation that the weekly feed refresh is complete.
    Returns True on successful transmission, False otherwise.
    """
    if not is_email_configured():
        logger.info("SMTP email notifications are not configured (SMTP_USER/NOTIFICATION_EMAIL). Skipping email.")
        return False

    logger.info("Sending weekly refresh confirmation email to %s...", config.NOTIFICATION_EMAIL)
    try:
        msg = build_email_message(
            week_id=week_id,
            count=count,
            top_reels=top_reels,
            site_url=site_url,
        )

        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=25) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config.SMTP_USER, config.SMTP_PASS)
            server.send_message(msg)

        logger.info("Successfully sent weekly digest confirmation email to %s!", config.NOTIFICATION_EMAIL)
        return True
    except Exception as exc:
        logger.error("Failed to deliver weekly digest email: %s", exc)
        return False


def main() -> int:
    """CLI runner for testing email delivery."""
    parser = argparse.ArgumentParser(description="Instagram Digest Email Notifier")
    parser.add_argument("--test", action="store_true", help="Send a test notification email")
    parser.add_argument("--week-id", type=str, default="2026-09-11", help="Week ID for test")
    parser.add_argument("--count", type=int, default=200, help="Reel count for test")
    args = parser.parse_args()

    sample_reels = [
        {"rank": 1, "rank_display": "#01", "creator_handle": "veritasium", "category": "niche", "caption": "The incredible physics behind why things spin."},
        {"rank": 2, "rank_display": "#02", "creator_handle": "hubermanlab", "category": "health", "caption": "Optimal protocols for focus, energy, and sleep."},
        {"rank": 3, "rank_display": "#03", "creator_handle": "mrwhosetheboss", "category": "ai_tech", "caption": "The future of smartphones and AI hardware."},
    ]

    success = send_digest_email(
        week_id=args.week_id,
        count=args.count,
        top_reels=sample_reels,
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
