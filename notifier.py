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
    recommended: list[dict[str, Any]] | None = None,
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
    if recommended:
        text_lines.append("New Creators To Try (dashboard → Recommended):")
        for r in recommended[:6]:
            handle = str(r.get("handle", "creator")).lstrip("@")
            name = str(r.get("name") or handle)
            reason = str(r.get("reason") or "").replace("\n", " ")[:90]
            text_lines.append(f"  • @{handle} ({name}): {reason}...")
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

    # Recommended-creators section (AI scout picks not yet followed)
    recs_html = ""
    if recommended:
        rec_rows = []
        for r in recommended[:6]:
            handle = html.escape(str(r.get("handle", "creator")).lstrip("@"))
            name = html.escape(str(r.get("name") or handle))
            reason_raw = str(r.get("reason") or "").strip().replace("\n", " ")
            reason = html.escape(reason_raw[:110] + ("..." if len(reason_raw) > 110 else ""))
            rec_rows.append(
                '<tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.06);">'
                '<td style="padding: 10px 14px; vertical-align: top;">'
                f'<div style="font-weight: 700; color: #f4f4f5; font-size: 14px;">@{handle}</div>'
                f'<div style="color: #a1a1aa; font-size: 12px; margin-top: 2px;">{name}</div>'
                f'<div style="color: #71717a; font-size: 12px; margin-top: 4px; line-height: 1.45;">{reason}</div>'
                "</td></tr>"
            )
        recs_html = (
            '<div style="margin-top: 28px;">'
            '<h3 style="color: #f4f4f5; font-size: 13px; margin: 0 0 12px 0; '
            'text-transform: uppercase; letter-spacing: 0.8px; font-weight: 700;">'
            "&#10024; New Creators To Try</h3>"
            '<table style="width: 100%; border-collapse: collapse; background: #141419; '
            "border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 14px; overflow: hidden;\">"
            "<tbody>" + "".join(rec_rows) + "</tbody></table>"
            '<p style="color: #71717a; font-size: 12px; margin: 8px 0 0 0;">'
            "Add them permanently from the dashboard &#8594; Recommended.</p></div>"
        )

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

                            {recs_html}
                            
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


def _safe_outbox_pending(report: dict[str, Any]) -> int | None:
    """Coerce outbox_pending ("?" on collection failure) without raising."""
    try:
        return int(report.get("outbox_pending") or 0)
    except (TypeError, ValueError):
        return None


def build_health_report_message(report: dict[str, Any]) -> MIMEMultipart:
    """Weekly self-audit email: digest count vs target, R2 vs quota, Pages,
    session health, pending outbox/resume state. One summary so drift gets
    noticed without opening dashboards."""
    week = str(report.get("week_id") or "unknown").replace("\r", " ").replace("\n", " ").strip()
    ok = bool(report.get("healthy", False))
    subject = f"{'✅' if ok else '⚠️'} Instagram Digest Health — {week} " \
              f"({report.get('digest_count', '?')}/{report.get('digest_target', '?')} reels)"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Instagram Digest <{config.SMTP_USER}>"
    msg["To"] = config.NOTIFICATION_EMAIL

    def _row(label: str, value: str, good: bool) -> str:
        dot = "#30d158" if good else "#ff9f0a"
        return (f'<tr><td style="padding:8px 12px;color:#a1a1aa;font-size:13px;">{html.escape(label)}</td>'
                f'<td style="padding:8px 12px;color:#f4f4f5;font-size:13px;font-weight:700;text-align:right;">'
                f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{dot};'
                f'margin-right:8px;"></span>{html.escape(value)}</td></tr>')

    rows = "".join([
        _row("Digest reels", f"{report.get('digest_count', '?')} / {report.get('digest_target', '?')}",
             bool(report.get("digest_ok", False))),
        _row("R2 usage", f"{report.get('r2_gb', '?')} / {report.get('quota_gb', '?')} GB",
             bool(report.get("r2_ok", False))),
        _row("Pages", str(report.get("pages_status", "?")), bool(report.get("pages_ok", False))),
        _row("Session", str(report.get("session_status", "?")), bool(report.get("session_ok", False))),
        _row("Outbox pending", str(report.get("outbox_pending", "?")),
             _safe_outbox_pending(report) == 0),
        _row("Resume pending", str(report.get("resume_pending", "none")),
             str(report.get("resume_pending", "none")) == "none"),
        _row("Recommendations", str(report.get("recs_status", "?")),
             bool(report.get("recs_ok", False))),
    ])
    notes = "<br>".join(html.escape(n) for n in report.get("notes", [])) or "All checks green."
    html_content = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:24px;background:#09090b;font-family:sans-serif;color:#f4f4f5;">
<div style="max-width:560px;margin:0 auto;background:#121217;border-radius:16px;padding:24px;">
<h2 style="margin:0 0 4px 0;">Weekly Health Report — {html.escape(week)}</h2>
<p style="color:#a1a1aa;font-size:13px;">Exit code {html.escape(str(report.get('exit_code', '?')))} · {html.escape(str(report.get('run_summary', '')))}</p>
<table style="width:100%;border-collapse:collapse;">{rows}</table>
<p style="color:#a1a1aa;font-size:13px;margin-top:16px;">{notes}</p>
</div></body></html>"""
    text_lines = [f"Weekly Health Report — {week} (exit {report.get('exit_code', '?')})", ""]
    for key in ("digest", "r2", "pages", "session", "outbox", "resume", "recs"):
        text_lines.append(f"- {key}: {report.get(key + '_status', report.get(key, '?'))}")
    text_lines += [""] + [str(n) for n in report.get("notes", [])]
    msg.attach(MIMEText("\n".join(text_lines), "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))
    return msg


def collect_health_report(week_id: str = "", exit_code: int = 0,
                          run_summary: str = "") -> dict[str, Any]:
    """Gather read-only health signals. Never raises; unknown reads as '?'/False."""
    import glob as _glob
    import json as _json
    import urllib.request as _url
    from pathlib import Path as _Path

    report: dict[str, Any] = {"exit_code": exit_code, "run_summary": run_summary,
                              "notes": []}
    try:
        target = int(os.getenv("TOP_DIGEST_COUNT", "250") or 250)
    except ValueError:
        target = 250
    try:
        import config as _cfg
        target = int(getattr(_cfg, "TOP_DIGEST_COUNT", target))
    except Exception:
        pass
    report["digest_target"] = target
    try:
        import config as _cfg2
        digest = _json.loads(_cfg2.DIGEST_BATCH_FILE.read_text(encoding="utf-8"))
        items = digest.get("items", [])
        report["week_id"] = week_id or digest.get("run_date", "")
        report["digest_count"] = len(items)
        report["digest_ok"] = len(items) >= int(target * 0.6)
        report["digest_status"] = f"{len(items)}/{target}"
        if len(items) < target:
            report["notes"].append(f"Digest holds {len(items)} reels vs {target} target.")
    except Exception as exc:
        report["week_id"] = week_id
        report["digest_count"] = "?"
        report["digest_ok"] = False
        report["digest_status"] = "unreadable"
        report["notes"].append(f"Digest unreadable: {exc}")
    try:
        import config as _cfg3
        import storage_r2 as _r2
        cur, _n = _r2.get_bucket_storage_usage()
        quota = int(getattr(_cfg3, "R2_STORAGE_QUOTA_BYTES", 8 * 1024**3))
        report["r2_gb"] = f"{cur / 1024**3:.2f}" if cur >= 0 else "?"
        report["quota_gb"] = f"{quota / 1024**3:.0f}"
        report["r2_ok"] = 0 <= cur < quota
        report["r2_status"] = f"{report['r2_gb']}/{report['quota_gb']} GB"
    except Exception as exc:
        report["r2_ok"] = False
        report["r2_status"] = "unreachable"
        report["notes"].append(f"R2 unreachable: {exc}")
    try:
        import config as _cfg4
        base = str(getattr(_cfg4, "PAGES_BASE_URL", "")).rstrip("/")
        with _url.urlopen(base + "/", timeout=20) as r:
            code = r.getcode()
        report["pages_ok"] = code == 200
        report["pages_status"] = f"HTTP {code}"
    except Exception as exc:
        report["pages_ok"] = False
        report["pages_status"] = "unreachable"
        report["notes"].append(f"Pages unreachable: {exc}")
    try:
        import config as _cfg5
        cdata = _json.loads((_cfg5.DATA_DIR / "cookies.json").read_text(encoding="utf-8"))
        has_session = bool((cdata.get("cookies_dict") or {}).get("sessionid"))
        report["session_ok"] = has_session
        report["session_status"] = "sessionid present" if has_session else "no sessionid"
        if not has_session:
            report["notes"].append("cookies.json has no sessionid; refresh login in Chrome.")
    except Exception as exc:
        report["session_ok"] = False
        report["session_status"] = "unreadable"
        report["notes"].append(f"cookies.json unreadable: {exc}")
    try:
        import config as _cfg6
        boxes = sorted(_Path(_cfg6.DATA_DIR).glob("upload_outbox_*.json"))
        pending = 0
        for b in boxes:
            try:
                pending += len((_json.loads(b.read_text(encoding="utf-8")) or {}).get("reels", []))
            except Exception:
                pass
        report["outbox_pending"] = pending
        if pending:
            report["notes"].append(f"{pending} reels parked in upload outbox; run --reconcile.")
    except Exception:
        report["outbox_pending"] = "?"
    try:
        import config as _cfg7
        syncs = sorted(_Path(_cfg7.DATA_DIR).glob("sync_progress_*.json"))
        exps = sorted(_Path(_cfg7.DATA_DIR).glob("expand_checkpoint_*.json"))
        leftovers = [p.name for p in syncs + exps]
        report["resume_pending"] = ", ".join(leftovers) if leftovers else "none"
        if leftovers:
            report["notes"].append(f"Resume state left behind: {report['resume_pending']}.")
    except Exception:
        report["resume_pending"] = "?"
    try:
        import recommendations as _recs
        fb = _recs.load_feedback()
        dnr = fb.get("do_not_recommend") or []
        exp = fb.get("exposures") or {}
        retired = sum(1 for _h, _c in exp.items()
                      if isinstance(_c, int) and _c >= _recs.MAX_EXPOSURES)
        served, stale = 0, False
        try:
            raw = _json.loads((_recs.get_recommended_file()).read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                served = len(raw.get("creators", []))
                stale = bool(raw.get("recommendations_stale", False))
        except Exception:
            pass
        report["recs_status"] = (f"{served} served · {len(dnr)} muted · "
                                 f"{retired} retired" + (" · STALE" if stale else ""))
        report["recs_ok"] = not stale
        if stale:
            report["notes"].append("Recommendations cache is stale; refresh from the dashboard.")
    except Exception as exc:
        report["recs_ok"] = False
        report["recs_status"] = "unreadable"
        report["notes"].append(f"Recommendations feedback unreadable: {exc}")
    report["healthy"] = bool(report.get("digest_ok") and report.get("r2_ok")
                              and report.get("pages_ok") and report.get("session_ok")
                              and report.get("outbox_pending") == 0)
    return report


def send_health_report_email(report: dict[str, Any]) -> bool:
    """Send the weekly self-audit health email. Returns False when unconfigured."""
    if not is_email_configured():
        logger.info("SMTP email notifications are not configured. Skipping health report.")
        return False
    logger.info("Sending weekly health report email to %s...", config.NOTIFICATION_EMAIL)
    try:
        msg = build_health_report_message(report)
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=25) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config.SMTP_USER, config.SMTP_PASS)
            server.send_message(msg)
        logger.info("Successfully sent health report email!")
        return True
    except Exception as exc:
        logger.error("Failed to deliver health report email: %s", exc)
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

Context: {context}
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
    parser.add_argument("--health-report", action="store_true", help="Collect and send the weekly self-audit health email")
    parser.add_argument("--context", type=str, default="Manual test", help="Failure context for --failure-alert")
    parser.add_argument("--exit-code", type=int, default=1, help="Exit code for --failure-alert")
    parser.add_argument("--week-id", type=str, default="2026-09-11", help="Week ID for test")
    parser.add_argument("--count", type=int, default=250, help="Reel count for test")
    args = parser.parse_args()

    if args.cookie_alert:
        success = send_cookie_alert_email()
        return 0 if success else 1

    if args.health_report:
        report = collect_health_report(week_id=args.week_id, exit_code=args.exit_code)
        success = send_health_report_email(report)
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
