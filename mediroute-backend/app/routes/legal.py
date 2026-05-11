"""
legal.py — Public compliance pages for MediRoute.

Provides:
  GET /privacy         → Privacy Policy (HTML) — required by Google Play Store
  GET /delete-account  → Account Deletion instructions (HTML) — required by Google Play Store

No auth required — these are intentionally public pages.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["Legal"])

_BASE_STYLE = """
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f8fafc;
    color: #1e293b;
    line-height: 1.7;
    padding: 24px 16px 48px;
  }
  .card {
    max-width: 720px;
    margin: 0 auto;
    background: #fff;
    border-radius: 16px;
    padding: 32px 28px;
    box-shadow: 0 1px 3px rgba(0,0,0,.08);
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 28px;
  }
  .brand-logo {
    width: 36px; height: 36px;
    background: #4f46e5;
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    color: #fff; font-weight: 700; font-size: 18px;
  }
  .brand-name { font-size: 20px; font-weight: 700; color: #4f46e5; }
  h1 { font-size: 24px; font-weight: 700; color: #0f172a; margin-bottom: 6px; }
  .updated { font-size: 13px; color: #64748b; margin-bottom: 28px; }
  h2 {
    font-size: 15px; font-weight: 600; color: #0f172a;
    margin: 28px 0 10px;
    padding-bottom: 6px;
    border-bottom: 1px solid #e2e8f0;
  }
  p { font-size: 14px; color: #334155; margin-bottom: 10px; }
  ul { font-size: 14px; color: #334155; padding-left: 20px; margin-bottom: 10px; }
  li { margin-bottom: 4px; }
  a { color: #4f46e5; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .highlight {
    background: #f1f5f9;
    border-left: 3px solid #4f46e5;
    padding: 12px 16px;
    border-radius: 0 8px 8px 0;
    font-size: 14px;
    margin: 16px 0;
  }
  .back { display: inline-block; margin-top: 28px; font-size: 13px; color: #64748b; }
</style>
"""


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
def privacy_policy():
    """
    Privacy Policy — required by Google Play Store for apps with user accounts.
    """
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
{_BASE_STYLE}
<title>Privacy Policy — MediRoute</title>
</head>
<body>
<div class="card">
  <div class="brand">
    <div class="brand-logo">M</div>
    <span class="brand-name">MediRoute</span>
  </div>

  <h1>Privacy Policy</h1>
  <p class="updated">Last updated: May 2026</p>

  <p>
    MediRoute (&ldquo;we&rdquo;, &ldquo;our&rdquo;, &ldquo;us&rdquo;) operates the MediRoute mobile application
    and website (the &ldquo;Service&rdquo;). This Privacy Policy explains how we collect, use,
    and protect your personal information when you use MediRoute.
  </p>

  <h2>1. Information We Collect</h2>
  <p>We collect the following categories of personal information:</p>
  <ul>
    <li><strong>Identity:</strong> Name, profile photo (optional)</li>
    <li><strong>Contact:</strong> Mobile phone number, email address (from Google login)</li>
    <li><strong>Professional:</strong> Years of experience, skills, education, current location,
        job preferences, uploaded resume (PDF)</li>
    <li><strong>Authentication:</strong> Google account ID (for Google Sign-In)</li>
    <li><strong>Usage:</strong> Job views, applications submitted, session tokens</li>
  </ul>

  <h2>2. How We Collect Your Information</h2>
  <ul>
    <li><strong>Google Sign-In:</strong> When you sign in with Google, we receive your
        name, email address, and Google account ID from Google&rsquo;s OAuth2 service.</li>
    <li><strong>OTP Verification:</strong> We use MSG91 (a third-party SMS gateway) to send
        a one-time password to your mobile number to verify your identity. Your phone number
        is transmitted to MSG91 for this purpose.</li>
    <li><strong>Profile Setup:</strong> Information you enter directly during onboarding
        and profile editing.</li>
    <li><strong>File Uploads:</strong> Resumes (PDF) you choose to upload.</li>
  </ul>

  <h2>3. How We Use Your Information</h2>
  <ul>
    <li>To create and manage your account</li>
    <li>To verify your mobile number via OTP</li>
    <li>To match you with relevant healthcare job opportunities</li>
    <li>To allow recruiters to view candidate profiles (name, skills, experience — no phone/email)</li>
    <li>To send job-related notifications (in-app only)</li>
    <li>To maintain platform security and prevent abuse</li>
  </ul>

  <h2>4. Data Storage and Security</h2>
  <p>
    Your data is stored securely on <strong>Supabase</strong> (PostgreSQL database hosted on
    AWS ap-south-1, India region) and <strong>Supabase Storage</strong> (for resume/photo files).
    All data is encrypted in transit using TLS/HTTPS. Access tokens (JWTs) are stored locally
    on your device and are not accessible to other apps.
  </p>
  <div class="highlight">
    We do <strong>not</strong> sell, rent, or share your personal information with advertisers
    or unrelated third parties.
  </div>

  <h2>5. Third-Party Services</h2>
  <ul>
    <li><strong>Google OAuth</strong> — sign-in authentication (<a href="https://policies.google.com/privacy" target="_blank">Google Privacy Policy</a>)</li>
    <li><strong>MSG91</strong> — OTP SMS delivery (<a href="https://msg91.com/privacy" target="_blank">MSG91 Privacy Policy</a>)</li>
    <li><strong>Supabase</strong> — database and file storage (<a href="https://supabase.com/privacy" target="_blank">Supabase Privacy Policy</a>)</li>
    <li><strong>Render</strong> — backend hosting (<a href="https://render.com/privacy" target="_blank">Render Privacy Policy</a>)</li>
  </ul>

  <h2>6. Recruiter Access</h2>
  <p>
    Recruiters on MediRoute can view candidate profiles including name, skills, experience,
    education, and location. <strong>Phone numbers and email addresses are never shown to
    recruiters.</strong> Recruiters must be verified by MediRoute administrators before
    accessing candidate information.
  </p>

  <h2>7. Data Retention</h2>
  <p>
    We retain your personal data for as long as your account is active. If you delete your
    account, all your personal data (profile, preferences, applications, resume, and session
    tokens) is permanently deleted within <strong>30 days</strong>. Job listings you posted
    as a recruiter are anonymised (your name is removed) rather than deleted, to preserve
    ongoing job search results for candidates.
  </p>

  <h2>8. Your Rights</h2>
  <ul>
    <li><strong>Access:</strong> View all your data in the Profile section of the app</li>
    <li><strong>Edit:</strong> Update your profile, preferences, and resume at any time</li>
    <li><strong>Delete:</strong> Permanently delete your account and all data from
        <em>Profile → Delete Account</em> in the app, or visit
        <a href="/delete-account">/delete-account</a></li>
    <li><strong>Portability:</strong> Contact us to request a copy of your data</li>
  </ul>

  <h2>9. Children&rsquo;s Privacy</h2>
  <p>
    MediRoute is a professional healthcare hiring platform intended for users aged 18 and above.
    We do not knowingly collect data from anyone under 18.
  </p>

  <h2>10. Changes to This Policy</h2>
  <p>
    We may update this Privacy Policy from time to time. We will notify you of significant
    changes by updating the date at the top of this page. Continued use of the app after
    changes constitutes acceptance.
  </p>

  <h2>11. Contact Us</h2>
  <p>
    For privacy questions, data requests, or account deletion assistance, contact us at:<br>
    <a href="mailto:support@mediroute.in">support@mediroute.in</a>
  </p>

  <a class="back" href="/">&larr; Back to MediRoute</a>
</div>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.get("/delete-account", response_class=HTMLResponse, include_in_schema=False)
def delete_account_page():
    """
    Account deletion instructions — required by Google Play Store (Data deletion policy).
    Explains how users can delete their account and what data is removed.
    """
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
{_BASE_STYLE}
<title>Delete Your Account — MediRoute</title>
</head>
<body>
<div class="card">
  <div class="brand">
    <div class="brand-logo">M</div>
    <span class="brand-name">MediRoute</span>
  </div>

  <h1>Delete Your Account</h1>
  <p class="updated">How to permanently delete your MediRoute account and personal data</p>

  <div class="highlight">
    Deleting your account is <strong>permanent and cannot be undone.</strong>
    All your personal data will be removed within 30 days.
  </div>

  <h2>Option 1 — Delete from Inside the App (Recommended)</h2>
  <ol style="font-size:14px;color:#334155;padding-left:20px;margin-bottom:12px;">
    <li style="margin-bottom:8px;">Open the <strong>MediRoute</strong> app</li>
    <li style="margin-bottom:8px;">Tap <strong>Profile</strong> in the bottom navigation</li>
    <li style="margin-bottom:8px;">Scroll to the bottom of the Profile page</li>
    <li style="margin-bottom:8px;">Tap <strong>&ldquo;Delete My Account&rdquo;</strong> (red button)</li>
    <li style="margin-bottom:8px;">Read the confirmation message and tap <strong>&ldquo;Yes, Delete My Account&rdquo;</strong></li>
    <li style="margin-bottom:8px;">Your account is immediately deleted and you are logged out</li>
  </ol>

  <h2>Option 2 — Contact Support</h2>
  <p>
    If you cannot access the app, email us at
    <a href="mailto:support@mediroute.in">support@mediroute.in</a> from the email address
    associated with your account. Include &ldquo;Account Deletion Request&rdquo; in the subject line.
    We will process your request within <strong>7 business days</strong>.
  </p>

  <h2>What Gets Deleted</h2>
  <ul>
    <li>Your name, phone number, and email address</li>
    <li>Your professional profile (experience, skills, education, location)</li>
    <li>Your job preferences</li>
    <li>Your uploaded resume (PDF file)</li>
    <li>Your job applications and application history</li>
    <li>Your login sessions and authentication tokens</li>
    <li>Your resume builder data and generated resumes</li>
    <li>Any OTP records associated with your phone number</li>
  </ul>

  <h2>What Is Retained</h2>
  <ul>
    <li>
      <strong>Job listings you posted</strong> (if you are a recruiter) — job posts are
      anonymised (your name is removed) and kept so candidates who applied are not
      disrupted. Listings are closed within 30 days.
    </li>
  </ul>

  <h2>Deletion Timeline</h2>
  <p>
    In-app deletion is <strong>immediate</strong> — your account is deactivated and tokens
    are invalidated the moment you confirm. Full data removal from our database and
    storage backups is completed within <strong>30 days</strong>.
  </p>

  <h2>Questions?</h2>
  <p>
    Contact us at <a href="mailto:support@mediroute.in">support@mediroute.in</a> or
    read our <a href="/privacy">Privacy Policy</a>.
  </p>

  <a class="back" href="/">&larr; Back to MediRoute</a>
</div>
</body>
</html>"""
    return HTMLResponse(content=html)
