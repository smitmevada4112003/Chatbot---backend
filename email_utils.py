import os
import smtplib
import logging
import threading
from typing import Optional, Any
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("stock_alerts")

# Thread-safe registry to ensure background email alerts are sent at most once per order per product
_order_alerts_lock = threading.Lock()
_order_alerts_sent = set()  # stores (order_id, product_key)

def safe_console_print(msg: str):
    """Safely prints messages to console regardless of terminal encoding (Windows cp1252 / utf-8)."""
    try:
        print(msg)
    except (UnicodeEncodeError, UnicodeError):
        try:
            print(msg.encode("ascii", errors="backslashreplace").decode("ascii"))
        except Exception:
            pass

# ==============================================================================
# SMTP CONFIGURATION FROM ENVIRONMENT (.env)
# ==============================================================================
SMTP_SERVER = os.getenv("SMTP_SERVER") or os.getenv("SMTP_HOST") or "smtp.gmail.com"
SMTP_PORT = int(os.getenv("SMTP_PORT") or "587")
SMTP_EMAIL = (os.getenv("SMTP_EMAIL") or os.getenv("SMTP_USER") or "").strip()
SMTP_PASSWORD = (os.getenv("SMTP_APP_PASSWORD") or os.getenv("SMTP_PASSWORD") or "").strip()
ADMIN_EMAIL = (os.getenv("ADMIN_ALERT_EMAIL") or os.getenv("ADMIN_EMAIL") or SMTP_EMAIL or "").strip()

LOW_STOCK_THRESHOLD = int(os.getenv("LOW_STOCK_THRESHOLD") or "5")


def send_low_stock_alert(
    product_name: str,
    product_id: Any,
    current_stock: int,
    status: Optional[str] = None,
    order_id: Optional[int] = None
) -> dict:
    """
    Sends an email alert to the admin when product stock reaches 0 or falls below threshold.
    
    Parameters:
      - product_name (str): Name of the product
      - product_id (int/str): ID of the product
      - current_stock (int): New stock level after deduction
      - status (str, optional): "Out of Stock" or "Low Stock". If None, calculated automatically.
      - order_id (int, optional): Order ID to enforce deduplication (at most once per order).
      
    Error Handling:
      Wrapped in try/except so that if sending fails (network error, bad password, etc.),
      it does NOT break the order-placing flow. Errors are logged to console.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if not status:
        status = "Out of Stock" if current_stock <= 0 else "Low Stock"

    # Deduplication check per order & product
    if order_id is not None:
        prod_key = str(product_id) if product_id is not None else product_name.strip().lower()
        alert_key = (order_id, prod_key)
        with _order_alerts_lock:
            if alert_key in _order_alerts_sent:
                skip_msg = f"[Duplicate Alert Suppressed] Low stock alert for '{product_name}' already triggered for order #{order_id}."
                logger.info(skip_msg)
                safe_console_print(skip_msg)
                return {
                    "status": "already_sent",
                    "message": skip_msg,
                    "product_name": product_name,
                    "product_id": product_id,
                    "current_stock": current_stock,
                    "condition": status,
                    "timestamp": timestamp
                }
            _order_alerts_sent.add(alert_key)
            if len(_order_alerts_sent) > 5000:
                _order_alerts_sent.clear()

    subject = f"🚨 Stock Alert: {product_name} (ID: #{product_id}) is {status}"

    plain_content = (
        f"STOCK ALERT NOTIFICATION\n"
        f"========================\n"
        f"Product ID:    #{product_id}\n"
        f"Product Name:  {product_name}\n"
        f"Current Stock: {current_stock} unit(s)\n"
        f"Status:        {status}\n"
        f"Detected At:   {timestamp}\n\n"
        f"{'CRITICAL: Product is completely out of stock!' if current_stock <= 0 else 'Notice: Inventory has fallen to or below the safety threshold. Please restock soon.'}"
    )

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #111827; background-color: #f9fafb; padding: 20px;">
        <div style="max-width: 520px; margin: auto; background: #ffffff; border-radius: 8px; border: 1px solid #e5e7eb; padding: 24px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);">
          <div style="margin-bottom: 16px;">
            <h2 style="color: {'#dc2626' if current_stock <= 0 else '#d97706'}; margin: 0;">
              ⚠️ {status.upper()} ALERT
            </h2>
          </div>
          <p style="font-size: 15px; margin: 0 0 16px 0;">
            Product <strong>{product_name}</strong> has reached an alert condition.
          </p>
          <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
            <tr>
              <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">Product ID:</td>
              <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">#{product_id}</td>
            </tr>
            <tr>
              <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">Product Name:</td>
              <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">{product_name}</td>
            </tr>
            <tr>
              <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">Current Stock:</td>
              <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; font-weight: 600; color: {'#dc2626' if current_stock <= 0 else '#d97706'};">
                {current_stock} unit(s)
              </td>
            </tr>
            <tr>
              <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">Condition:</td>
              <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">
                <span style="background: {'#fee2e2' if current_stock <= 0 else '#fef3c7'}; color: {'#991b1b' if current_stock <= 0 else '#92400e'}; padding: 2px 8px; border-radius: 4px;">
                  {status}
                </span>
              </td>
            </tr>
            <tr>
              <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">Detected At:</td>
              <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">{timestamp}</td>
            </tr>
          </table>
          <p style="font-size: 13px; color: #6b7280; margin: 0;">Automated email generated by Product & Order System.</p>
        </div>
      </body>
    </html>
    """

    # If SMTP is not yet configured, log safe fallback to console
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        msg = (
            f"[MOCK EMAIL ALERT] Product: '{product_name}' (ID: #{product_id}) | "
            f"Stock: {current_stock} | Status: {status} | Time: {timestamp} | "
            f"Fill in SMTP credentials in .env to send real emails."
        )
        print(f"\n{msg}\n")
        logger.warning(msg)
        return {
            "status": "logged_to_console",
            "message": msg,
            "product_name": product_name,
            "product_id": product_id,
            "current_stock": current_stock,
            "condition": status,
            "timestamp": timestamp
        }

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_EMAIL
        msg["To"] = ADMIN_EMAIL

        msg.attach(MIMEText(plain_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, ADMIN_EMAIL, msg.as_string())

        success_msg = f"Stock alert email sent to {ADMIN_EMAIL} for '{product_name}' ({status})."
        print(f"\n[EMAIL SENT] {success_msg}\n")
        logger.info(success_msg)
        return {
            "status": "sent",
            "message": success_msg,
            "product_name": product_name,
            "product_id": product_id,
            "current_stock": current_stock,
            "condition": status,
            "timestamp": timestamp
        }

    except Exception as e:
        error_msg = f"Failed to send stock alert email for '{product_name}': {e}"
        print(f"\n[EMAIL ERROR LOGGED] {error_msg}\n")
        logger.error(error_msg)
        return {
            "status": "error_logged",
            "message": error_msg,
            "product_name": product_name,
            "product_id": product_id,
            "current_stock": current_stock,
            "condition": status,
            "timestamp": timestamp
        }


def send_password_reset_email(
    to_email: str,
    reset_link: str,
    user_name: str = None,
    expires_minutes: int = 30
) -> dict:
    """
    Sends a password reset email with a secure reset link.
    
    Parameters:
      - to_email (str): Recipient email address
      - reset_link (str): Fully qualified URL with reset token (e.g. http://localhost:5173/reset-password?token=...)
      - user_name (str, optional): User's display name
      - expires_minutes (int): Minutes until the reset token expires (default: 30)
      
    Error Handling:
      Wrapped in try/except so email delivery errors (e.g. SMTP unavailable, bad credentials)
      NEVER crash the forgot-password flow. The reset link is prominently logged to the console
      so development and testing work immediately even without a working SMTP server.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    display_name = (user_name or to_email.split("@")[0]).strip()
    subject = "🔑 Reset Your Password - Product & Order Assistant"

    plain_content = (
        f"PASSWORD RESET REQUEST\n"
        f"======================\n\n"
        f"Hello {display_name},\n\n"
        f"We received a request to reset the password for your account ({to_email}).\n\n"
        f"To reset your password, please visit the following link:\n"
        f"{reset_link}\n\n"
        f"IMPORTANT: This link will expire in {expires_minutes} minutes.\n\n"
        f"If you did not request this password reset, please ignore this email. Your password will remain unchanged.\n\n"
        f"---\n"
        f"Sent at: {timestamp}\n"
        f"Product & Order Management System"
    )

    html_content = f"""
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>
          body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            color: #1e293b;
            background-color: #f1f5f9;
            margin: 0;
            padding: 24px;
          }}
          .card {{
            max-width: 520px;
            margin: 0 auto;
            background: #ffffff;
            border-radius: 12px;
            border: 1px solid #e2e8f0;
            padding: 32px;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.05);
          }}
          .header {{
            text-align: center;
            margin-bottom: 24px;
          }}
          .icon-badge {{
            display: inline-block;
            background: #eff6ff;
            color: #2563eb;
            font-size: 32px;
            padding: 12px 18px;
            border-radius: 50%;
            margin-bottom: 12px;
          }}
          h2 {{
            color: #0f172a;
            margin: 0 0 8px 0;
            font-size: 22px;
          }}
          p {{
            font-size: 15px;
            line-height: 1.6;
            color: #475569;
            margin: 0 0 16px 0;
          }}
          .btn-container {{
            text-align: center;
            margin: 28px 0;
          }}
          .reset-btn {{
            display: inline-block;
            background: linear-gradient(135deg, #2563eb, #1d4ed8);
            color: #ffffff !important;
            text-decoration: none;
            font-weight: 600;
            font-size: 15px;
            padding: 14px 28px;
            border-radius: 8px;
            box-shadow: 0 4px 6px -1px rgba(37, 99, 235, 0.3);
          }}
          .notice-box {{
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-left: 4px solid #3b82f6;
            padding: 12px 16px;
            border-radius: 6px;
            font-size: 13px;
            color: #64748b;
            margin-bottom: 20px;
          }}
          .url-fallback {{
            word-break: break-all;
            font-size: 12px;
            color: #2563eb;
            background: #f8fafc;
            padding: 8px;
            border-radius: 4px;
          }}
          .footer {{
            border-top: 1px solid #e2e8f0;
            padding-top: 16px;
            margin-top: 24px;
            font-size: 12px;
            color: #94a3b8;
            text-align: center;
          }}
        </style>
      </head>
      <body>
        <div class="card">
          <div class="header">
            <div class="icon-badge">🔐</div>
            <h2>Password Reset Request</h2>
          </div>
          <p>Hello <strong>{display_name}</strong>,</p>
          <p>
            We received a request to reset the password for your account linked to <strong>{to_email}</strong>.
            Click the button below to choose a new password:
          </p>
          <div class="btn-container">
            <a href="{reset_link}" target="_blank" class="reset-btn">Reset Password</a>
          </div>
          <div class="notice-box">
            ⏰ <strong>Note:</strong> This link is valid for <strong>{expires_minutes} minutes</strong>. If you did not make this request, you can safely ignore this email; your account remains secure.
          </div>
          <p style="font-size: 13px; color: #64748b; margin-bottom: 6px;">
            If the button doesn't work, copy and paste this link into your browser:
          </p>
          <div class="url-fallback">{reset_link}</div>
          <div class="footer">
            Product &amp; Order Assistant &bull; Generated at {timestamp}
          </div>
        </div>
      </body>
    </html>
    """

    # Always log reset link to console for immediate developer access & testing
    console_notice = (
        f"\n==================== [PASSWORD RESET EMAIL] ====================\n"
        f"To:          {to_email}\n"
        f"Subject:     Password Reset Request\n"
        f"Reset Link:  {reset_link}\n"
        f"Expires In:  {expires_minutes} minutes\n"
        f"=================================================================\n"
    )
    safe_console_print(console_notice)
    logger.info(f"Password reset link generated for {to_email}: {reset_link}")

    # If SMTP is not configured, return immediately with logged status
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        return {
            "status": "logged_to_console",
            "message": "SMTP credentials not configured. Reset link printed to console.",
            "to_email": to_email,
            "reset_link": reset_link,
            "timestamp": timestamp
        }

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_EMAIL
        msg["To"] = to_email

        msg.attach(MIMEText(plain_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, to_email, msg.as_string())

        success_msg = f"Password reset email sent to {to_email}."
        safe_console_print(f"[EMAIL SENT] {success_msg}")
        logger.info(success_msg)
        return {
            "status": "sent",
            "message": success_msg,
            "to_email": to_email,
            "reset_link": reset_link,
            "timestamp": timestamp
        }

    except Exception as e:
        error_msg = f"Failed to send password reset email to {to_email}: {e}"
        safe_console_print(f"[EMAIL ERROR LOGGED] {error_msg}")
        logger.error(error_msg)
        return {
            "status": "error_logged",
            "message": error_msg,
            "to_email": to_email,
            "reset_link": reset_link,
            "timestamp": timestamp
        }


def send_verification_email(
    to_email: str,
    verification_link: str,
    user_name: str = None
) -> dict:
    """
    Sends an account verification email containing an activation link.

    Parameters:
      - to_email (str): Recipient email address
      - verification_link (str): Activation / verification link (e.g. http://localhost:5173/verify-email?token=...)
      - user_name (str, optional): User's display name

    Error Handling:
      Wrapped in try/except so email delivery errors (e.g. SMTP unavailable, bad credentials)
      NEVER crash the user registration flow. The verification link is prominently logged to the console
      so local testing works immediately even without a working SMTP server.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    display_name = (user_name or to_email.split("@")[0]).strip()
    subject = "✉️ Verify Your Email Address - Product & Order Assistant"

    plain_content = (
        f"ACCOUNT EMAIL VERIFICATION\n"
        f"==========================\n\n"
        f"Hello {display_name},\n\n"
        f"Thank you for registering with Product & Order Assistant!\n\n"
        f"To complete your registration and verify your email address ({to_email}),\n"
        f"please visit the following link:\n"
        f"{verification_link}\n\n"
        f"If you did not register for an account, please disregard this email.\n\n"
        f"---\n"
        f"Sent at: {timestamp}\n"
        f"Product & Order Management System"
    )

    html_content = f"""
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="utf-8">
        <style>
          body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            color: #1e293b;
            background-color: #f1f5f9;
            margin: 0;
            padding: 24px;
          }}
          .card {{
            max-width: 520px;
            margin: 0 auto;
            background: #ffffff;
            border-radius: 12px;
            border: 1px solid #e2e8f0;
            padding: 32px;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.05);
          }}
          .header {{
            text-align: center;
            margin-bottom: 24px;
          }}
          .icon-badge {{
            display: inline-block;
            background: #ecfdf5;
            color: #059669;
            font-size: 32px;
            padding: 12px 18px;
            border-radius: 50%;
            margin-bottom: 12px;
          }}
          h2 {{
            color: #0f172a;
            margin: 0 0 8px 0;
            font-size: 22px;
          }}
          p {{
            font-size: 15px;
            line-height: 1.6;
            color: #475569;
            margin: 0 0 16px 0;
          }}
          .btn-container {{
            text-align: center;
            margin: 28px 0;
          }}
          .verify-btn {{
            display: inline-block;
            background: linear-gradient(135deg, #10b981, #059669);
            color: #ffffff !important;
            text-decoration: none;
            font-weight: 600;
            font-size: 15px;
            padding: 14px 28px;
            border-radius: 8px;
            box-shadow: 0 4px 6px -1px rgba(16, 185, 129, 0.3);
          }}
          .url-fallback {{
            word-break: break-all;
            font-size: 12px;
            color: #059669;
            background: #f8fafc;
            padding: 8px;
            border-radius: 4px;
            border: 1px solid #e2e8f0;
          }}
          .footer {{
            border-top: 1px solid #e2e8f0;
            padding-top: 16px;
            margin-top: 24px;
            font-size: 12px;
            color: #94a3b8;
            text-align: center;
          }}
        </style>
      </head>
      <body>
        <div class="card">
          <div class="header">
            <div class="icon-badge">✉️</div>
            <h2>Verify Your Email Address</h2>
          </div>
          <p>Hello <strong>{display_name}</strong>,</p>
          <p>
            Welcome to <strong>Product &amp; Order Assistant</strong>! To complete your registration and confirm your account ({to_email}), please click the button below:
          </p>
          <div class="btn-container">
            <a href="{verification_link}" target="_blank" class="verify-btn">Verify Email Address</a>
          </div>
          <p style="font-size: 13px; color: #64748b; margin-bottom: 6px;">
            If the button doesn't work, copy and paste this link into your browser:
          </p>
          <div class="url-fallback">{verification_link}</div>
          <div class="footer">
            Product &amp; Order Assistant &bull; Generated at {timestamp}
          </div>
        </div>
      </body>
    </html>
    """

    # Always log verification link to console for immediate developer access & testing
    console_notice = (
        f"\n==================== [EMAIL VERIFICATION] ====================\n"
        f"To:                 {to_email}\n"
        f"Subject:            Verify Your Email Address\n"
        f"Verification Link:  {verification_link}\n"
        f"===============================================================\n"
    )
    safe_console_print(console_notice)
    logger.info(f"Verification link generated for {to_email}: {verification_link}")

    # If SMTP is not configured, return immediately with logged status
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        return {
            "status": "logged_to_console",
            "message": "SMTP credentials not configured. Verification link printed to console.",
            "to_email": to_email,
            "verification_link": verification_link,
            "timestamp": timestamp
        }

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_EMAIL
        msg["To"] = to_email

        msg.attach(MIMEText(plain_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, to_email, msg.as_string())

        success_msg = f"Verification email sent to {to_email}."
        safe_console_print(f"[EMAIL SENT] {success_msg}")
        logger.info(success_msg)
        return {
            "status": "sent",
            "message": success_msg,
            "to_email": to_email,
            "verification_link": verification_link,
            "timestamp": timestamp
        }

    except Exception as e:
        error_msg = f"Failed to send verification email to {to_email}: {e}"
        safe_console_print(f"[EMAIL ERROR LOGGED] {error_msg}")
        logger.error(error_msg)
        return {
            "status": "error_logged",
            "message": error_msg,
            "to_email": to_email,
            "verification_link": verification_link,
            "timestamp": timestamp
        }


__all__ = [
    "send_low_stock_alert",
    "send_password_reset_email",
    "send_verification_email",
    "SMTP_SERVER",
    "SMTP_PORT",
    "SMTP_EMAIL",
]


