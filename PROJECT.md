# Newsletter Digest - Project Links

## Production App
- **Live URL:** https://newsletter-digest-production-44a2.up.railway.app
- **Dashboard:** https://newsletter-digest-production-44a2.up.railway.app/dashboard

## Services

### Railway (Hosting)
- **Dashboard:** https://railway.app/dashboard
- **Project:** Newsletter-Digest
- **Variables:** Environment secrets stored here

### GitHub (Code)
- **Repo:** https://github.com/ChrisMalmborg/Newsletter-Digest
- **Issues:** https://github.com/ChrisMalmborg/Newsletter-Digest/issues

### Google Cloud Console (OAuth)
- **Console:** https://console.cloud.google.com
- **Project:** newsletter-digest
- **OAuth credentials:** APIs & Services → Credentials
- **Test users:** APIs & Services → OAuth consent screen → Test users

### Cron-job.org (Scheduling)
- **Dashboard:** https://cron-job.org
- **Schedule:** Daily at 11:00 AM PST

### Anthropic (AI API)
- **Console:** https://console.anthropic.com
- **Usage:** Check API usage and billing here

## Local Development
```bash
cd ~/coding-projects/newsletter-digest
python3 -m uvicorn src.web.app:app --port 8000
# Then visit http://localhost:8000
```

## Key Files
- `.env` — Local environment variables (never commit)
- `src/processing/prompts.py` — AI prompts to tune
- `config/interests.yaml` — User interests for relevance scoring

## Useful Commands
```bash
# Push changes to production
git add . && git commit -m "message" && git push

# Test digest manually (replace secret)
curl -X POST "https://newsletter-digest-production-44a2.up.railway.app/api/run-digest?hours=72" -H "X-Cron-Secret: YOUR_SECRET"
```
