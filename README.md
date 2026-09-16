# Kirby Cove overnight campsite monitor

This small Python monitor checks Recreation.gov for newly available **overnight**
campsites at Kirby Cove (sites 001–005). It deliberately ignores the Day Use
picnic site. GitHub Actions runs it every 5 minutes and sends an email through
a Gmail account and app password.

The monitor checks from today through Kirby Cove's six-month reservation window.
It alerts only for site/date combinations that were not available on the prior
successful run. The first successful run will alert if any openings already exist.

## 1. Create the notification accounts

### Email: Gmail

Use a Gmail account with two-step verification enabled, then create a Google app
password. The app password—not your normal Gmail password—will be stored in
GitHub. The sending Gmail address and receiving address can be the same.

## 2. Put this project in GitHub

1. Create a new **public** GitHub repository. Public repositories receive free
   standard GitHub-hosted Actions usage, and this project contains no credentials.
   The credentials added later remain encrypted GitHub Actions secrets.
2. Upload all files and folders from this project, including `.github/workflows`.
3. Do **not** put credentials directly in any file.

From a terminal, the equivalent commands are:

```bash
git init
git add .
git commit -m "Add Kirby Cove campsite monitor"
git branch -M main
git remote add origin https://github.com/YOUR-USER/YOUR-REPO.git
git push -u origin main
```

## 3. Add GitHub Actions secrets

In the repository, open **Settings → Secrets and variables → Actions → New
repository secret**. Add these exact names:

| Secret | Value |
| --- | --- |
| `GMAIL_USERNAME` | Gmail address used to send alerts |
| `GMAIL_APP_PASSWORD` | 16-character Google app password |
| `ALERT_EMAIL_TO` | destination email address |

## 4. Send a test

1. Open the repository's **Actions** tab.
2. Select **Monitor Kirby Cove**.
3. Choose **Run workflow**.
4. Check **Send a test email instead of checking availability**.
5. Run it and confirm the email arrives.

Then run it once more with the box unchecked. The workflow will check live
availability and update `state.json`. Scheduled runs continue automatically.

## Frequency and cost

The default schedule is every five minutes, beginning at 2 minutes past each
hour. Five minutes is GitHub's shortest supported scheduled-workflow interval.
Runs can still be delayed during busy periods, and an opening may disappear
before an alert arrives.

This frequency produces about 8,640 runs in a 30-day month. Use a public
repository if you want the GitHub-hosted runner usage to remain free. A private
repository's included Actions minutes will generally not cover this frequency.
To reduce the cadence later, edit the `cron` expression in
`.github/workflows/monitor.yml`.

## Local checks

No third-party Python packages are required.

```bash
python -m unittest discover -s tests -v
python monitor.py --dry-run
```

`--dry-run` prints current overnight openings without sending notifications or
changing `state.json`. To test notification credentials locally, export the three
environment variables listed above and run:

```bash
python monitor.py --test-notifications
```

## Important limitations

- This monitor reports availability; it never reserves or holds a campsite.
- It uses the same Recreation.gov availability endpoint used by the booking site.
  If Recreation.gov changes that endpoint or response format, the workflow will
  fail visibly in the Actions tab instead of silently overwriting state.
- Recreation.gov currently says Kirby Cove reservations open six months ahead and
  may extend two days past that window. The monitor follows that rule.
- Keep `state.json` tracked in Git. The workflow uses it to avoid duplicate alerts.
