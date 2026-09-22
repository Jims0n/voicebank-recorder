# Voice banking speech collector

Collects consented recordings of Nigerian banking commands (Pidgin and Nigerian English)
for fine-tuning Whisper.

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then export the vars, or use direnv
python manage.py makemigrations collector
python manage.py migrate
python manage.py generate_prompts
python manage.py createsuperuser
python manage.py runserver
```
Open http://localhost:8000. The mic works on localhost; on any other host it needs HTTPS.

## Deploy (Render)
1. Push to GitHub. New Web Service on Render, plus a Render Postgres instance.
2. Build: `pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate`
3. Start: `gunicorn config.wsgi --timeout 60`
4. Env vars: everything in `.env.example`, with `DJANGO_DEBUG=0`, `DATABASE_URL` from Render,
   `DJANGO_ALLOWED_HOSTS=<your-app>.onrender.com`, and `USE_S3=1` with Cloudflare R2 credentials.
   Render's disk is wiped on redeploy, so audio MUST go to R2/S3 in production.
5. One-off shell: `python manage.py generate_prompts && python manage.py createsuperuser`

## Daily
- Review clips at /admin/collector/recording/ (approve, reject, transcribe elicited).
  The page title shows usable hours so far.
- Export: `python manage.py export_dataset ./dataset` (needs ffmpeg). Splits are by speaker.
