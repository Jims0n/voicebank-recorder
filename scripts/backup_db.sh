#!/usr/bin/env bash
# Dump the Render Postgres database.
#
# Render's free Postgres is DELETED 30 days after creation. The audio lives in R2
# and survives, but without this database those files are unlabelled noise: the
# transcripts, prompt mapping, speaker demographics and consent records are all here.
#
# Credentials are read from ~/.voicebank-backup.env so they are never committed.
set -o errexit
set -o pipefail
set -o nounset

# cron runs with a minimal PATH and will not find pg_dump otherwise. pg_dump must be
# at least the server's major version, so prefer the newest client installed.
for v in 19 18 17 16; do
  if [[ -d "/opt/homebrew/opt/postgresql@$v/bin" ]]; then
    export PATH="/opt/homebrew/opt/postgresql@$v/bin:$PATH"
    break
  fi
done
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin"

CONF="$HOME/.voicebank-backup.env"
# shellcheck source=/dev/null
[[ -f "$CONF" ]] && source "$CONF"
: "${RENDER_DATABASE_URL:?Set RENDER_DATABASE_URL in $CONF}"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/backups"
mkdir -p "$DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DIR/voicebank-$STAMP.dump"

pg_dump "$RENDER_DATABASE_URL" --format=custom --no-owner --no-privileges --file "$OUT"

# A tiny dump means auth failed or the database is empty. Fail loudly rather than
# leaving a useless file that looks like a backup.
SIZE=$(wc -c < "$OUT" | tr -d ' ')
if (( SIZE < 10000 )); then
  echo "FAILED: dump is only ${SIZE} bytes" >&2
  rm -f "$OUT"
  exit 1
fi

CLIPS=$(psql "$RENDER_DATABASE_URL" -tAc "select count(*) from collector_recording")
SPEAKERS=$(psql "$RENDER_DATABASE_URL" -tAc "select count(*) from collector_speaker where not withdrawn")
echo "$STAMP  $(basename "$OUT")  ${SIZE}b  clips=${CLIPS}  speakers=${SPEAKERS}" >> "$DIR/history.log"

# Keep the 14 most recent dumps.
ls -1t "$DIR"/voicebank-*.dump | tail -n +15 | while read -r old; do rm -- "$old"; done

echo "OK  $OUT  (${SIZE} bytes, ${CLIPS} clips, ${SPEAKERS} speakers)"
