"""TLS-only SMTP delivery with explicit failure and a persisted delivery ledger."""
import hashlib
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate

from .core import read_json, utcnow, write_json


def send_digest(config, period, directory, ledger_path, force=False):
    apps = read_json(directory / "apps.json")
    freelance = read_json(directory / "freelance.json")
    recipient = os.getenv("EMAIL_TO") or config["recipient"]
    key = f"{recipient}|{period}|{apps['start']}|{apps['end']}"
    ledger = read_json(ledger_path) if ledger_path.exists() else {}
    if key in ledger and not force:
        print(f"Email already delivered for {period} {apps['start']}–{apps['end']}; skipping", flush=True)
        return False
    defaults = config.get("smtp", {})
    host = os.getenv("SMTP_HOST") or defaults.get("host")
    username = os.getenv("SMTP_USERNAME") or defaults.get("username")
    password = os.getenv("SMTP_PASSWORD")
    missing = [name for name, value in (("SMTP_HOST", host), ("SMTP_USERNAME", username), ("SMTP_PASSWORD", password)) if not value]
    if missing:
        raise RuntimeError("Email is not configured. Add GitHub Actions secrets: " + ", ".join(missing))
    attachments = [directory / "apps.pdf", directory / "freelance.pdf"]
    if not all(path.exists() for path in attachments):
        raise RuntimeError("Both PDF reports must exist before delivery")
    message = EmailMessage()
    message["From"] = os.getenv("SMTP_FROM") or username
    message["To"] = recipient
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = f"<trendscan-{hashlib.sha256(key.encode()).hexdigest()[:28]}@trendscan.local>"
    message["Subject"] = f"TrendScan {period}: apps + freelance | {apps['start']} to {apps['end']}"
    lines = [f"Your {period} research reports are attached: 1. B2C apps; 2. Freelance demand.", "",
             f"Coverage: {apps['scan_days']}/{apps['expected_days']} daily snapshots.",
             f"Apps: {apps['app_count']} distinct products. Freelance: {freelance['project_count']} projects and {freelance['contract_count']} contract listings.", ""]
    for label, report in (("App niches to validate", apps), ("Freelance niches to validate", freelance)):
        lines.append(label + ":")
        lines.extend(f"- {r['name']}: {r['proposal']} (confidence: {r['confidence']})" for r in report["opportunities"][:3])
    lines += ["", "Revenue signals are labeled; no earnings are guaranteed. Missing sources and incomplete history are listed in each PDF."]
    message.set_content("\n".join(lines))
    for path in attachments:
        message.add_attachment(path.read_bytes(), maintype="application", subtype="pdf", filename=f"{path.stem}-{period}-{apps['end']}.pdf")
    port = int(os.getenv("SMTP_PORT") or defaults.get("port", 587))
    context = ssl.create_default_context()
    if port == 465:
        connection = smtplib.SMTP_SSL(host, port, timeout=45, context=context)
    else:
        connection = smtplib.SMTP(host, port, timeout=45)
    with connection as smtp:
        if port != 465:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(username, password)
        rejected = smtp.send_message(message)
        if rejected:
            raise RuntimeError("SMTP server rejected a recipient")
    ledger[key] = {"sent_at": utcnow(), "message_id": message["Message-ID"]}
    write_json(ledger_path, ledger)
    print(f"Delivered {period} email with 2 PDFs", flush=True)
    return True
