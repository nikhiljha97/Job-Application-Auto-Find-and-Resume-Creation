# Job Application Auto-Find and Resume Creation

Local LinkedIn job scanner for Nikhil Jha's job search workflow. It scans LinkedIn jobs, dedupes old postings, scores each role, generates ATS-aligned DOCX resumes from real resume evidence, uploads resumes and Excel output to OneDrive, and learns from resumes that are later marked `Applied`.

The scanner is designed to run locally without an AI agent after setup. It does not fake experience, dates, employers, or metrics. Missing JD keywords are treated as gaps unless they can be truthfully mapped to existing resume evidence.

## Primary Workflow

1. The scanner opens LinkedIn and scans the configured search.
2. Jobs are ranked by fit and saved to `outputs/linkedin_job_results.xlsx`.
3. Tailored DOCX resumes, cover letters, and cold outreach notes are created in `outputs/resumes/`, `outputs/cover_letters/`, and `outputs/cold_outreach/`.
4. Resume DOCX files and the Excel workbook are uploaded to OneDrive.
5. In the Excel workbook, set the `Applied` dropdown to `Applied` after you apply.
6. On the next run, the scanner downloads the current OneDrive Excel first, preserves your dropdown choices, and downloads any `Applied` OneDrive resumes into `onedrive_source_resumes/`.
7. Those applied resumes become trusted source resumes for future tailoring.

Google Sheets and Google Drive support still exists in the code, but the default config now keeps Google disabled. Turn it back on later by setting `google_sheets.enabled` and `google_drive.enabled` to `true`.

## Current ATS Score

The generated resume ATS score uses this 100-point weighting:

```text
Keywords   max 50
Experience max 20
Education  max 20
Formatting max 10
```

Important: real ATS systems usually do not assign a universal 80% or 90% score. Treat this score as a local checklist for keyword coverage, experience alignment, education alignment, and parseable formatting.

## Resume Tailoring Rules

- Resumes now use the Claude/Cowork-style DOCX layout: centered name/headline/contact, Calibri text, single-column ATS-readable structure, section rules, right-aligned dates, justified bullets, and newest-to-oldest experience.
- Match the exact job title in the resume headline when possible.
- Extract the 5-15 most important JD phrases, especially title words, named tools, hard skills, and industry terms.
- Put keywords in three places: headline/summary, skills/core competencies, and relevant experience bullets.
- Prefer exact JD language over synonyms when truthful. If the JD says `stakeholder communication`, use that exact phrase instead of only `executive reporting`.
- Keep the format ATS-readable: single column, selectable text, no text boxes, no image-only resumes.
- Use only real impact numbers already supported by source resumes.
- Never invent metrics, employers, tools, dates, or responsibilities.
- Work-experience bullets are generated from a controlled evidence bank for Loblaw Advance, Exera, McMaster, and Verizon. JD language is blended in only when it truthfully maps to that evidence.
- Cover letters and cold outreach use the same blended evidence rules: company/role-specific language from the JD, real experience from the resume bank, and only approved impact metrics.
- When OneDrive is available, the tracker also stores OneDrive links for generated cover letters and cold outreach DOCX files.

## Nikhil Resume Cheat Sheet

Core positioning used by the resume generator:

```text
Category & Shopper Strategy / Business Analysis & Insights professional with an MBA in Finance from McMaster University and 4+ years of analytics experience across FMCG/CPG, retail media, financial services, telecom, and technology.
At Loblaw Advance, supported Confectionery and Beauty category insights for Tier 1 CPG clients including Hershey, Lindt, Mondelez, L'Oreal, Nestle, and Ferrero on Canada's 41M+ member PC Optimum loyalty platform.
NielsenIQ/Nielsen RMC, POS, panel, campaign, loyalty, and behavioral data. SQL, Python, Power BI, Tableau, Looker Studio, category growth strategy, shopper insights, pricing and promotions, assortment, KPI dashboards, and executive storytelling.
```

Reusable impact metrics:

| Metric | Context |
| --- | --- |
| `$24M+` | Total revenue opportunities identified at Loblaw Advance |
| `$15M` | Revenue retention uncovered through shopper insights and pricing gap analysis |
| `$9M` | Organic growth potential from market share and competitive analysis |
| `40%+` | Faster decision-making through Power BI dashboards and KPI frameworks |
| `$38M` | Revenue protected at Verizon through ML fraud detection models |
| `95%` | Manual processing time reduction at Verizon |
| `26%` | Analytics platform utilization uplift at Verizon |
| `60%` | Reporting time reduction at McMaster |
| `22%` | Campaign engagement uplift at Shelby's Food Chain project |
| `20%` | Holding cost reduction in ADONIS METRO pricing project |

Keyword tiers:

```text
Tier 1: exact role title words, named tools, core function, must-have JD skills.
Tier 2: industry terms, methods, stakeholder/cross-functional phrases.
Tier 3: culture words, generic soft skills, broad business language.
```

Bullet style:

```text
**[Keyword Title]:** [Action + context + method + tools/data/client].
[Real quantified outcome or business impact.]
```

Avoid weak starts such as `Utilized`, `Responsible for`, `Helped`, `Assisted`, and `Worked on`. Prefer `Led`, `Built`, `Delivered`, `Uncovered`, `Drove`, `Developed`, `Automated`, and `Translated`.

## Setup

Because the project path contains a colon, do not create a virtual environment inside this folder. Use a home-directory venv:

```bash
cd "/Users/nikhiljha/Desktop/Resume:CoverLetters/FULL TIME ROLES /job_scanner"
python3 -m venv ~/.venvs/job_scanner
source ~/.venvs/job_scanner/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Create local config:

```bash
cp config.example.json config.json
```

Then edit `config.json` with your local resume paths and OneDrive app details.

## First LinkedIn Run

```bash
source ~/.venvs/job_scanner/bin/activate
python run_job_scanner.py --once
```

The first run opens Chromium. Sign in to LinkedIn manually if asked, complete any security checks, return to the terminal, and press Enter. The session is stored locally in `.linkedin_profile/`.

## Scheduled Runs

The current schedule is:

```text
08:00
10:00
14:00
18:00
22:00
```

Install or refresh the macOS LaunchAgent:

```bash
source ~/.venvs/job_scanner/bin/activate
python run_job_scanner.py --install-launch-agent
launchctl unload ~/Library/LaunchAgents/com.nikhil.linkedin-job-scanner.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.nikhil.linkedin-job-scanner.plist
```

Stop scheduled runs:

```bash
launchctl unload ~/Library/LaunchAgents/com.nikhil.linkedin-job-scanner.plist
```

Logs:

```text
outputs/logs/launchd.out.log
outputs/logs/launchd.err.log
```

## OneDrive Setup

Create a Microsoft Entra app registration:

1. Open Azure Portal or Microsoft Entra ID.
2. Go to `App registrations`.
3. Create a new app.
4. For a personal Outlook account, use supported account type `Personal Microsoft accounts` and set `tenant_id` to `consumers`.
5. In `Authentication`, enable public client flows.
6. In API permissions, add delegated Microsoft Graph permissions:
   - `Files.ReadWrite`
   - `User.Read`
   - `offline_access`
7. Put the Application/client ID in `config.json`.

Authenticate once:

```bash
source ~/.venvs/job_scanner/bin/activate
python - <<'PY'
from linkedin_job_scanner.config import load_config
from linkedin_job_scanner.onedrive import upload_excel_to_onedrive

config = load_config("config.json")
upload_excel_to_onedrive(config, "outputs/linkedin_job_results.xlsx")
print("OneDrive connected.")
PY
```

After the first approval, `onedrive_token.json` is reused locally. Do not commit token files.

## Applied Dropdown Learning Loop

The Excel `Ranked Jobs` tab includes:

- `Applied`: dropdown with `Not Applied Yet` and `Applied`
- `Use As Source`: optional dropdown with `No` and `Yes`
- `Application Date`
- `Application Notes`

When you mark a row as `Applied`, the next run:

1. Downloads the latest OneDrive Excel file.
2. Reads the `Applied` column.
3. Downloads that job's OneDrive resume DOCX.
4. Stores it in `onedrive_source_resumes/`.
5. Adds it to the future source-resume bank.

This gives the scanner a conservative feedback loop: only resumes you actually used become future source material.

## If The OneDrive Excel Is Open

If `linkedin_job_results.xlsx` is open in Excel or OneDrive when a scheduled run finishes, Microsoft Graph may lock the workbook. The scanner still completes the scan, writes the updated local Excel, and uploads a timestamped backup copy such as:

```text
linkedin_job_results_backup_20260430_101530.xlsx
```

Close the open workbook before the next run if you want the main `linkedin_job_results.xlsx` file to be replaced directly.

## Google Integrations

Google support is kept in the repo but disabled by default:

```json
"google_sheets": {"enabled": false},
"google_drive": {"enabled": false}
```

To re-enable later, add the Sheet ID, Drive folder ID, service-account JSON, and OAuth client JSON back into `config.json`, then set both `enabled` flags to `true`.

## Notifications

The scanner supports Telegram, SMTP email, and Twilio WhatsApp summaries. Configure `.env` and set the desired channel to enabled in `config.json`.

Telegram:

```bash
JOB_SCANNER_TELEGRAM_BOT_TOKEN=123456:abc...
JOB_SCANNER_TELEGRAM_CHAT_ID=123456789
```

Email:

```bash
JOB_SCANNER_SMTP_HOST=smtp.gmail.com
JOB_SCANNER_SMTP_PORT=587
JOB_SCANNER_SMTP_USERNAME=you@gmail.com
JOB_SCANNER_SMTP_PASSWORD=app_password
JOB_SCANNER_EMAIL_FROM=you@gmail.com
JOB_SCANNER_EMAIL_TO=destination@email.com
```

Twilio WhatsApp:

```bash
JOB_SCANNER_TWILIO_ACCOUNT_SID=AC...
JOB_SCANNER_TWILIO_AUTH_TOKEN=...
JOB_SCANNER_TWILIO_FROM=whatsapp:+14155238886
JOB_SCANNER_TWILIO_TO=whatsapp:+1YOURNUMBER
```

## Test Without LinkedIn

```bash
source ~/.venvs/job_scanner/bin/activate
python run_job_scanner.py --sample
```

## Outputs

- `outputs/linkedin_job_results.xlsx`: ranked jobs and raw scraped jobs.
- `outputs/resumes/`: generated DOCX resumes.
- `outputs/cover_letters/`: generated DOCX cover letters.
- `outputs/cold_outreach/`: generated LinkedIn/email outreach text.
- `outputs/data/jobs.json`: historical job data.
- `outputs/data/scores.json`: historical score data.
- `onedrive_source_resumes/`: applied/approved OneDrive resumes downloaded for future learning.

## Safety Notes

- Do not commit `.env`, `config.json`, token files, `.linkedin_profile/`, or `outputs/`.
- LinkedIn may change markup, request login, or throttle automated browsing. The scanner does not bypass authentication, CAPTCHAs, or access controls.
- Review every generated resume before applying. The scanner is optimized for speed and keyword alignment, but you stay the final reviewer.
