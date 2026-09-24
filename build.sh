#!/usr/bin/env bash
# Render build step. Every command here must be safe to re-run on each deploy,
# because Render's free instances have no shell for one-off jobs.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
python manage.py generate_prompts

# Optional first-deploy bootstrap. Set DJANGO_SUPERUSER_USERNAME / _EMAIL / _PASSWORD
# in Render, deploy once, then delete them. Fails harmlessly if the user exists.
if [[ -n "${DJANGO_SUPERUSER_USERNAME:-}" ]]; then
  python manage.py createsuperuser --noinput || echo "superuser already exists, skipping"
fi
