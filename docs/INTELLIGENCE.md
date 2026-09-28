# Revenue evidence imports

Public store metadata does not expose reliable app-level revenue totals. Connect a licensed intelligence feed or supply an attributable publisher disclosure using this neutral JSON schema. The bot does not automatically purchase market-data access.

Set `APP_INTELLIGENCE_URL` as a GitHub secret for a public HTTPS or signed HTTPS endpoint you control. Alternatively, write `data/imports/app-intelligence.json` locally. URLs with embedded username/password are rejected; use a signed URL if your provider supports it. Do not expose secret feed URLs in report fields.

```json
{
  "apps": [
    {
      "store": "apple",
      "app_id": "123456789",
      "title": "Example only — replace with real evidence",
      "url": "https://apps.apple.com/app/id123456789",
      "country": "us",
      "category": "Utilities",
      "amount": 12500,
      "currency": "USD",
      "period_start": "2026-08-01",
      "period_end": "2026-08-31",
      "evidence_type": "third_party_estimate",
      "source_url": "https://example.com/replace-with-the-actual-source",
      "published_at": "2026-09-01",
      "methodology": "Explain territory, gross/net basis, stores included and provider estimation method."
    }
  ]
}
```

`store`: `apple` or `google_play`. `app_id`: numeric Apple ID or Android package name. `evidence_type`: `publisher_reported` or `third_party_estimate`; neither means audited profit. Do not use a purchase price, download count or anonymous search snippet as a revenue amount. All fields shown except `published_at` and `methodology` are required, and source URLs must be valid HTTP(S) URLs. The report preserves the revenue period even when the observation was collected later.

To connect Sensor Tower, Appfigures, AppMagic or another provider, transform the data permitted by your account into this schema. Their products and entitlements differ; this repository does not pretend that an undocumented universal revenue API exists.
