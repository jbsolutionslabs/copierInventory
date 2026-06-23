# mailer.py — Resend email for watchlist matches

import os

EMAIL_FROM = os.environ.get("EMAIL_FROM", "inventory@yourdomain.com")


def _build_email_html(customer_name: str, matches: list[dict], criteria: dict) -> str:
    crit_parts = []
    if criteria.get("brand"):
        crit_parts.append(f"Brand: <strong>{criteria['brand']}</strong>")
    if criteria.get("model"):
        crit_parts.append(f"Model: <strong>{criteria['model']}</strong>")
    if criteria.get("maxMeter"):
        crit_parts.append(f"Max meter: <strong>{int(criteria['maxMeter']):,}</strong>")
    if criteria.get("maxPrice"):
        crit_parts.append(f"Max price: <strong>${int(criteria['maxPrice']):,}</strong>")
    if criteria.get("state"):
        crit_parts.append(f"State: <strong>{criteria['state']}</strong>")
    crit_html = " &nbsp;·&nbsp; ".join(crit_parts) if crit_parts else "Any"

    rows = ""
    for m in matches:
        price_str = f"${int(m['price']):,}" if m.get("price") else "—"
        meter_str = f"{int(m['total']):,}" if m.get("total") else "—"
        rows += f"""
        <tr>
          <td style="padding:6px 10px;border-bottom:1px solid #eee">{m.get('vendor','')}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #eee"><strong>{m.get('brand','')} {m.get('model','')}</strong></td>
          <td style="padding:6px 10px;border-bottom:1px solid #eee">{m.get('state','')}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #eee;text-align:right">{meter_str}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #eee;text-align:right;color:#1a6b2a;font-weight:700">{price_str}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #eee;font-size:11px;color:#666">{m.get('config','')}</td>
        </tr>"""

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,Arial,sans-serif;color:#1a2540;background:#f2f4f8;margin:0;padding:0">
  <div style="max-width:700px;margin:30px auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.1)">
    <div style="background:linear-gradient(135deg,#1F3864,#2E5090);padding:20px 24px">
      <h1 style="color:#C9A84C;margin:0;font-size:18px">Imaging Connection — Inventory Alert</h1>
      <p style="color:rgba(255,255,255,.7);margin:4px 0 0;font-size:13px">{len(matches)} machine(s) matched your watchlist</p>
    </div>
    <div style="padding:20px 24px">
      <p>Hi {customer_name},</p>
      <p>New inventory matching your request has arrived:</p>
      <p style="font-size:12px;color:#6b7a99;margin:8px 0 16px">{crit_html}</p>
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <thead>
          <tr style="background:#1F3864;color:#fff">
            <th style="padding:7px 10px;text-align:left">Source</th>
            <th style="padding:7px 10px;text-align:left">Machine</th>
            <th style="padding:7px 10px;text-align:left">State</th>
            <th style="padding:7px 10px;text-align:right">Meter</th>
            <th style="padding:7px 10px;text-align:right">Price</th>
            <th style="padding:7px 10px;text-align:left">Config</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      <p style="margin-top:20px;font-size:12px;color:#6b7a99">
        Reply to this email or call us to arrange purchase. This alert was generated automatically.
      </p>
    </div>
  </div>
</body>
</html>"""


def send_watchlist_match(
    customer_name: str,
    email: str,
    matches: list[dict],
    criteria: dict,
) -> None:
    """Send a watchlist match email via Resend. Raises on failure."""
    import resend  # type: ignore

    resend.api_key = os.environ["RESEND_API_KEY"]
    resend.Emails.send({
        "from":    EMAIL_FROM,
        "to":      email,
        "subject": f"Inventory Match: {len(matches)} machine(s) found",
        "html":    _build_email_html(customer_name, matches, criteria),
    })
