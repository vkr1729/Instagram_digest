"""
notifier.py — Sends email notifications confirming weekly Instagram Digest refreshes
and alerting on session cookie expiration.
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
    count: int = 250,
    followed_count: int | None = None,
    external_count: int | None = None,
    top_reels: list[dict[str, Any]] | None = None,
    site_url: str | None = None,
) -> MIMEMultipart:
    """Build a rich, responsive multipart HTML and plain-text email message using UI Pro Max OLED Dark theme."""
    url = site_url or config.PAGES_BASE_URL
    subject = f"✨ Instagram Digest Ready — Week of {week_id} ({count} Reels)"

    fol_cnt = count if followed_count is None else followed_count
    ext_cnt = 0 if external_count is None else external_count

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
        f"  • Followed Channels: {fol_cnt}",
        f"  • External Discovery: {ext_cnt}",
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
    text_lines.append(f"Local Desktop Dashboard: http://localhost:8080")
    text_content = "\n".join(text_lines)

    # Category colors mapping for pills
    cat_colors = {
        "ai_tech": ("#38bdf8", "rgba(56, 189, 248, 0.15)"),
        "finance": ("#4ade80", "rgba(74, 222, 128, 0.15)"),
        "health": ("#f43f5e", "rgba(244, 63, 94, 0.15)"),
        "entertainment": ("#f59e0b", "rgba(245, 158, 11, 0.15)"),
        "niche": ("#a855f7", "rgba(168, 85, 247, 0.15)"),
        "food": ("#ec4899", "rgba(236, 72, 153, 0.15)"),
    }

    # Rich HTML version
    reels_html = ""
    if top_reels:
        reels_rows = []
        for r in top_reels[:5]:
            rank = html.escape(str(r.get("rank_display") or f"#{r.get('rank', 1):02d}"))
            handle = html.escape(str(r.get("creator_handle", "creator")))
            cat_raw = str(r.get("category", "")).lower()
            cat = html.escape(cat_raw.replace("_", " ").title())
            is_ext = bool(r.get("is_external"))
            raw_caption = str(r.get("caption") or "").strip().replace("\n", " ")
            caption = html.escape(raw_caption[:110] + ("..." if len(raw_caption) > 110 else ""))

            chip_color, chip_bg = cat_colors.get(cat_raw, ("#cbd5e0", "#27272a"))
            disc_badge = '<span style="display:inline-block;margin-left:6px;padding:1px 6px;border-radius:10px;background:rgba(236,72,153,0.2);color:#f472b6;font-size:10px;font-weight:600;">🌐 Discovery</span>' if is_ext else ""

            reels_rows.append(f"""
                <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.06);">
                    <td style="padding: 12px 14px; font-weight: 800; color: #f43f5e; font-size: 13px; vertical-align: top; width: 36px;">{rank}</td>
                    <td style="padding: 12px 14px; vertical-align: top;">
                        <div style="font-weight: 700; color: #f4f4f5; font-size: 14px; display: flex; align-items: center;">
                            @{handle} {disc_badge}
                        </div>
                        <div style="color: #a1a1aa; font-size: 12px; margin-top: 4px; line-height: 1.45;">{caption}</div>
                    </td>
                    <td style="padding: 12px 14px; text-align: right; vertical-align: top; white-space: nowrap;">
                        <span style="display: inline-block; padding: 3px 9px; border-radius: 12px; background: {chip_bg}; color: {chip_color}; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.3px;">{cat}</span>
                    </td>
                </tr>
            """)
        reels_html = f"""
            <div style="margin-top: 28px;">
                <h3 style="color: #f4f4f5; font-size: 13px; margin: 0 0 12px 0; text-transform: uppercase; letter-spacing: 0.8px; font-weight: 700;">🔥 Top Featured Highlights</h3>
                <table style="width: 100%; border-collapse: collapse; background: #141419; border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 14px; overflow: hidden;">
                    <tbody>
                        {"".join(reels_rows)}
                    </tbody>
                </table>
            </div>
        """

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; background-color: #09090b; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #f4f4f5;">
    <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #09090b; padding: 32px 14px;">
        <tr>
            <td align="center">
                <table width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width: 580px; background-color: #121217; border-radius: 20px; border: 1px solid rgba(255, 255, 255, 0.08); overflow: hidden; box-shadow: 0 20px 50px rgba(0,0,0,0.8);">
                    <!-- Header Banner -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #833ab4, #fd1d1d, #fcb045); padding: 32px 24px; text-align: center;">
                            <div style="font-size: 34px; line-height: 1;">✨</div>
                            <h1 style="color: #ffffff; font-size: 24px; font-weight: 800; margin: 8px 0 0 0; letter-spacing: -0.5px;">Instagram Digest</h1>
                            <p style="color: rgba(255, 255, 255, 0.92); font-size: 13px; margin: 6px 0 0 0; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Weekly High-Signal Curation &bull; {week_id}</p>
                        </td>
                    </tr>
                    
                    <!-- Content Body -->
                    <tr>
                        <td style="padding: 28px 24px;">
                            <h2 style="color: #ffffff; font-size: 18px; font-weight: 700; margin: 0 0 10px 0;">This Week's Feed Is Ready!</h2>
                            <p style="color: #a1a1aa; font-size: 14px; line-height: 1.6; margin: 0 0 24px 0;">
                                Your weekly digest has been curated with <strong>{count} high-signal reels</strong> across AI &amp; Tech, Finance, Health, Entertainment, Food, and Niche topics.
                            </p>
                            
                            <!-- Metrics Cards Grid -->
                            <table width="100%" border="0" cellspacing="0" cellpadding="0" style="margin-bottom: 24px;">
                                <tr>
                                    <td width="32%" style="background: #18181f; border: 1px solid rgba(255,255,255,0.06); border-radius: 12px; padding: 14px 10px; text-align: center;">
                                        <div style="font-size: 20px; font-weight: 800; color: #f4f4f5;">{count}</div>
                                        <div style="font-size: 11px; font-weight: 600; color: #71717a; text-transform: uppercase; margin-top: 4px;">🎯 Total Reels</div>
                                    </td>
                                    <td width="2%"></td>
                                    <td width="32%" style="background: #18181f; border: 1px solid rgba(255,255,255,0.06); border-radius: 12px; padding: 14px 10px; text-align: center;">
                                        <div style="font-size: 20px; font-weight: 800; color: #38bdf8;">{fol_cnt}</div>
                                        <div style="font-size: 11px; font-weight: 600; color: #71717a; text-transform: uppercase; margin-top: 4px;">👥 Followed</div>
                                    </td>
                                    <td width="2%"></td>
                                    <td width="32%" style="background: #18181f; border: 1px solid rgba(255,255,255,0.06); border-radius: 12px; padding: 14px 10px; text-align: center;">
                                        <div style="font-size: 20px; font-weight: 800; color: #f43f5e;">{ext_cnt}</div>
                                        <div style="font-size: 11px; font-weight: 600; color: #71717a; text-transform: uppercase; margin-top: 4px;">🌐 External</div>
                                    </td>
                                </tr>
                            </table>

                            <!-- Primary CTA Button -->
                            <div style="text-align: center; margin: 28px 0 20px 0;">
                                <a href="{url}" style="display: inline-block; background: linear-gradient(135deg, #e1306c, #c13584); color: #ffffff; text-decoration: none; font-size: 15px; font-weight: 700; padding: 14px 36px; border-radius: 28px; box-shadow: 0 4px 20px rgba(225, 48, 108, 0.4);">
                                    Open Instagram Digest &rarr;
                                </a>
                            </div>

                            {reels_html}
                            
                            <!-- PWA & Desktop Info Box -->
                            <div style="margin-top: 28px; padding: 16px 18px; background-color: #141419; border-radius: 12px; border: 1px solid rgba(255,255,255,0.06); border-left: 4px solid #fd1d1d; font-size: 12px; color: #a1a1aa; line-height: 1.5;">
                                💡 <strong>Tip:</strong> Open the link on your mobile phone and tap <em>Share &rarr; Add to Home Screen</em> for full-screen immersive view with progressive offline caching.<br><br>
                                💻 <strong>Desktop Dashboard:</strong> <a href="http://localhost:8080" style="color: #38bdf8; text-decoration: underline;">http://localhost:8080</a>
                            </div>
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="padding: 20px 24px; background-color: #0c0c0f; border-top: 1px solid rgba(255,255,255,0.06); text-align: center; color: #52525b; font-size: 12px;">
                            Sent automatically by Instagram Digest Bot &bull; Week {week_id}<br>
                            <a href="{url}" style="color: #71717a; text-decoration: underline;">{url}</a>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

    msg.attach(MIMEText(text_content, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))
    return msg


def build_cookie_alert_message(retrigger_url: str = "http://localhost:8080/retrigger") -> MIMEMultipart:
    """Build high-priority alert email when Instagram session cookies are expired."""
    subject = "⚠️ Action Required: Instagram Session Expired"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Instagram Digest <{config.SMTP_USER}>"
    msg["To"] = config.NOTIFICATION_EMAIL

    text_content = f"""ACTION REQUIRED: Instagram Session Cookies Expired
===================================================

Instagram rejected your session cookies during this week's digest refresh.
External Reels discovery was skipped to protect your account.

HOW TO REFRESH COOKIES (2 Easy Steps):
1. Open Google Chrome on your computer and log in to Instagram (https://www.instagram.com).
2. Click the link below to automatically decrypt cookies and retrigger the refresh:

Retrigger Link: {retrigger_url}

Terminal Shortcut:
  cd ~/Instagram_digest && bash run_weekly.sh
"""

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; background-color: #09090b; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #f4f4f5;">
    <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #09090b; padding: 32px 14px;">
        <tr>
            <td align="center">
                <table width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width: 540px; background-color: #121217; border-radius: 20px; border: 1px solid rgba(245, 158, 11, 0.3); overflow: hidden; box-shadow: 0 20px 50px rgba(0,0,0,0.8);">
                    <!-- Header -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #b45309, #d97706); padding: 28px 24px; text-align: center;">
                            <div style="font-size: 36px; line-height: 1;">⚠️</div>
                            <h1 style="color: #ffffff; font-size: 22px; font-weight: 800; margin: 8px 0 0 0;">Session Expired</h1>
                            <p style="color: rgba(255, 255, 255, 0.95); font-size: 13px; margin: 4px 0 0 0;">Instagram Cookies Need Refresh</p>
                        </td>
                    </tr>
                    
                    <!-- Content -->
                    <tr>
                        <td style="padding: 28px 24px;">
                            <p style="color: #e4e4e7; font-size: 14px; line-height: 1.6; margin: 0 0 20px 0;">
                                Instagram rejected your current session cookies during the automated feed refresh. To prevent account restrictions, external feed discovery was skipped.
                            </p>
                            
                            <!-- Steps Card -->
                            <div style="background: #18181f; border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 14px; padding: 20px; margin-bottom: 24px;">
                                <h3 style="color: #f59e0b; font-size: 13px; margin: 0 0 12px 0; text-transform: uppercase; letter-spacing: 0.5px;">2-Step Quick Fix</h3>
                                <div style="display: flex; align-items: flex-start; margin-bottom: 12px;">
                                    <span style="background: #27272a; color: #f4f4f5; font-size: 12px; font-weight: 700; width: 22px; height: 22px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; margin-right: 12px; flex-shrink: 0;">1</span>
                                    <span style="font-size: 13px; color: #d4d4d8; line-height: 1.5;">Open <strong>Google Chrome</strong> on your desktop and log in to <a href="https://www.instagram.com" style="color: #38bdf8;">Instagram</a>.</span>
                                </div>
                                <div style="display: flex; align-items: flex-start;">
                                    <span style="background: #27272a; color: #f4f4f5; font-size: 12px; font-weight: 700; width: 22px; height: 22px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; margin-right: 12px; flex-shrink: 0;">2</span>
                                    <span style="font-size: 13px; color: #d4d4d8; line-height: 1.5;">Click the button below to decrypt fresh cookies and retrigger the run:</span>
                                </div>
                            </div>

                            <!-- Retrigger CTA Button -->
                            <div style="text-align: center; margin: 24px 0;">
                                <a href="{retrigger_url}" style="display: inline-block; background: linear-gradient(135deg, #f59e0b, #d97706); color: #000000; text-decoration: none; font-size: 15px; font-weight: 800; padding: 14px 32px; border-radius: 28px; box-shadow: 0 4px 20px rgba(245, 158, 11, 0.4);">
                                    🔄 Refresh Cookies &amp; Retrigger Sync &rarr;
                                </a>
                            </div>

                            <p style="font-size: 12px; color: #71717a; text-align: center; margin-top: 18px;">
                                Or run via shell: <code style="background: #000; padding: 3px 8px; border-radius: 6px; color: #a1a1aa;">cd ~/Instagram_digest &amp;&amp; bash run_weekly.sh</code>
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

    msg.attach(MIMEText(text_content, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))
    return msg


def send_digest_email(
    week_id: str,
    count: int = 250,
    followed_count: int | None = None,
    external_count: int | None = None,
    top_reels: list[dict[str, Any]] | None = None,
    site_url: str | None = None,
) -> bool:
    """Send an email confirmation that the weekly feed refresh is complete."""
    if not is_email_configured():
        logger.info("SMTP email notifications are not configured. Skipping email.")
        return False

    logger.info("Sending weekly refresh confirmation email to %s...", config.NOTIFICATION_EMAIL)
    try:
        msg = build_email_message(
            week_id=week_id,
            count=count,
            followed_count=followed_count,
            external_count=external_count,
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


def send_cookie_alert_email(retrigger_url: str = "http://localhost:8080/retrigger") -> bool:
    """Send high-priority alert email when Instagram session cookies are expired."""
    if not is_email_configured():
        logger.info("SMTP email notifications are not configured. Skipping cookie alert email.")
        return False

    logger.warning("Sending Instagram cookie expiration alert email to %s...", config.NOTIFICATION_EMAIL)
    try:
        msg = build_cookie_alert_message(retrigger_url=retrigger_url)

        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=25) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config.SMTP_USER, config.SMTP_PASS)
            server.send_message(msg)

        logger.info("Successfully sent cookie alert email to %s!", config.NOTIFICATION_EMAIL)
        return True
    except Exception as exc:
        logger.error("Failed to deliver cookie alert email: %s", exc)
        return False


def build_failure_alert_message(context: str, exit_code: int = 1) -> MIMEMultipart:
    """Build a pipeline-failure alert email (sync aborts, non-zero exits)."""
    subject = f"❌ Instagram Digest Failed — {context} (exit {exit_code})"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Instagram Digest <{config.SMTP_USER}>"
    msg["To"] = config.NOTIFICATION_EMAIL

    text_content = f"""INSTAGRAM DIGEST PIPELINE FAILURE
==================================

Context: {html.escape(context)}
Exit code: {exit_code}

The run produced nothing new; the previous working digest was preserved.
Inspect the run log for details:

  ~/Instagram_digest/logs/weekly_sync.log

Terminal Shortcut:
  cd ~/Instagram_digest && bash run_weekly.sh
"""

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; background-color: #09090b; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #f4f4f5;">
    <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #09090b; padding: 32px 14px;">
        <tr>
            <td align="center">
                <table width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width: 540px; background-color: #121217; border-radius: 20px; border: 1px solid rgba(255, 69, 58, 0.3); overflow: hidden;">
                    <tr>
                        <td style="background: linear-gradient(135deg, #b91c1c, #dc2626); padding: 28px 24px; text-align: center;">
                            <div style="font-size: 36px; line-height: 1;">❌</div>
                            <h1 style="color: #ffffff; font-size: 22px; font-weight: 800; margin: 8px 0 0 0;">Pipeline Failure</h1>
                            <p style="color: rgba(255, 255, 255, 0.95); font-size: 13px; margin: 4px 0 0 0;">{html.escape(context)} (exit {exit_code})</p>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 28px 24px;">
                            <p style="color: #e4e4e7; font-size: 14px; line-height: 1.6; margin: 0;">
                                The run produced nothing new; the previous working digest was preserved.
                                Inspect <code style="background: #000; padding: 3px 8px; border-radius: 6px; color: #a1a1aa;">~/Instagram_digest/logs/weekly_sync.log</code> for details.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

    msg.attach(MIMEText(text_content, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))
    return msg


def send_failure_alert_email(context: str, exit_code: int = 1) -> bool:
    """Send an alert email when the digest pipeline fails or aborts a run."""
    if not is_email_configured():
        logger.info("SMTP email notifications are not configured. Skipping failure alert email.")
        return False

    logger.warning("Sending pipeline failure alert email to %s...", config.NOTIFICATION_EMAIL)
    try:
        msg = build_failure_alert_message(context=context, exit_code=exit_code)

        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=25) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config.SMTP_USER, config.SMTP_PASS)
            server.send_message(msg)

        logger.info("Successfully sent failure alert email to %s!", config.NOTIFICATION_EMAIL)
        return True
    except Exception as exc:
        logger.error("Failed to deliver failure alert email: %s", exc)
        return False


def main() -> int:
    """CLI runner for testing email delivery."""
    parser = argparse.ArgumentParser(description="Instagram Digest Email Notifier")
    parser.add_argument("--test", action="store_true", help="Send a test notification email")
    parser.add_argument("--cookie-alert", action="store_true", help="Send a test cookie alert email")
    parser.add_argument("--failure-alert", action="store_true", help="Send a pipeline failure alert email")
    parser.add_argument("--context", type=str, default="Manual test", help="Failure context for --failure-alert")
    parser.add_argument("--exit-code", type=int, default=1, help="Exit code for --failure-alert")
    parser.add_argument("--week-id", type=str, default="2026-09-11", help="Week ID for test")
    parser.add_argument("--count", type=int, default=250, help="Reel count for test")
    args = parser.parse_args()

    if args.cookie_alert:
        success = send_cookie_alert_email()
        return 0 if success else 1

    if args.failure_alert:
        success = send_failure_alert_email(context=args.context, exit_code=args.exit_code)
        return 0 if success else 1

    sample_reels = [
        {"rank": 1, "rank_display": "#01", "creator_handle": "veritasium", "category": "niche", "caption": "The incredible physics behind why things spin."},
        {"rank": 2, "rank_display": "#02", "creator_handle": "hubermanlab", "category": "health", "caption": "Optimal protocols for focus, energy, and sleep."},
        {"rank": 3, "rank_display": "#03", "creator_handle": "mrwhosetheboss", "category": "ai_tech", "caption": "The future of smartphones and AI hardware."},
        {"rank": 4, "rank_display": "#04", "creator_handle": "techlead", "category": "ai_tech", "caption": "Coding with AI models in 2026.", "is_external": True},
    ]

    success = send_digest_email(
        week_id=args.week_id,
        count=args.count,
        followed_count=args.count - 42,
        external_count=42,
        top_reels=sample_reels,
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
