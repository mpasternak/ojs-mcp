#!/usr/bin/env bash
#
# Bring up a throwaway OJS 3.5 instance and fill it with fictional content,
# so ojs-mcp has something real to talk to.
#
#   ./setup.sh                    # start containers, install, seed, print token
#   docker compose down -v        # destroy everything, including the database
#
# Re-running is safe: each step checks whether it already happened.
#
# Nothing here is a secret worth protecting -- the passwords are in the file
# on purpose and the whole instance is meant to be thrown away.

set -euo pipefail
cd "$(dirname "$0")"

BASE_URL="http://localhost:8081"
JOURNAL="demojournal"
ADMIN_USER="admin"
ADMIN_PASS="ojsdemo1234"
ADMIN_EMAIL="admin@example.org"
CONFIG="config/ojs.config.inc.php"
APP="ojs-mcp-demo-app"
DB="ojs-mcp-demo-db"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# --------------------------------------------------------------------------
# OJS rewrites config.inc.php during installation, so it has to be writable by
# the container. `sed -i` inside the container cannot touch it (a bind-mounted
# file cannot be renamed), which is why base_url, the API secret and
# restful_urls are set here rather than through the image's own entrypoint.
#
# Rewrites in place rather than deleting first: the file is a bind mount
# target, and replacing it with a new inode would leave the container looking
# at the old, deleted one.
prepare_config() {
    curl -sSL "https://raw.githubusercontent.com/pkp/ojs/3_5_0-5/config.TEMPLATE.inc.php" -o "$CONFIG"
    python3 - "$CONFIG" <<'PY'
import secrets, sys
p = sys.argv[1]
s = open(p).read()
s = s.replace('base_url = "https://pkp.sfu.ca/ojs"', 'base_url = "http://localhost:8081"')
s = s.replace('salt = "YouMustSetASecretKeyHere!!"', f'salt = "{secrets.token_hex(16)}"')
s = s.replace('api_key_secret = ""', f'api_key_secret = "{secrets.token_hex(32)}"')
s = s.replace('files_dir = files', 'files_dir = /var/www/files')
s = s.replace('restful_urls = Off', 'restful_urls = On')
open(p, 'w').write(s)
PY
    chmod 666 "$CONFIG"
}

say "Preparing config.inc.php"
if [ ! -f "$CONFIG" ]; then
    prepare_config
else
    chmod 666 "$CONFIG"
    echo "already present"
fi

if [ ! -f config/pkp.conf ]; then
    say "config/pkp.conf missing -- see README (Apache must forward the Authorization header)"
    exit 1
fi

# --------------------------------------------------------------------------
say "Starting containers"
docker compose up -d

printf 'waiting for OJS to answer'
for _ in $(seq 1 120); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -L "$BASE_URL/" || true)
    # 500 counts: before installation OJS answers with an error, but it is
    # answering, which is all this loop is waiting for.
    if [ "$code" = "200" ] || [ "$code" = "500" ]; then break; fi
    printf '.'; sleep 2
done
echo

# `docker compose down -v` destroys the database but not this file, which
# lives on the host. Left alone, the next run would read `installed = On`,
# skip the installation, and hand you an OJS pointing at an empty schema --
# a 500 on every page with nothing saying why.
tables=$(docker exec "$DB" mariadb -uojs -pojspass ojs -N -B \
    -e "select count(*) from information_schema.tables where table_schema='ojs'")
if [ "$tables" = "0" ] && grep -q '^installed = On' "$CONFIG"; then
    say "Database is empty but config.inc.php says installed - regenerating it"
    prepare_config
fi

# --------------------------------------------------------------------------
say "Installing OJS"
if grep -q '^installed = On' "$CONFIG"; then
    echo "already installed"
else
    # OJS has no real CLI installer: PKP's own automation posts the web
    # installer's form to itself, and so do we. This takes several minutes on
    # Apple Silicon, because the OJS image is amd64 and runs emulated.
    echo "posting the installer form (this takes a few minutes)..."
    curl -sL -X POST "$BASE_URL/index/en/install/install" \
        --data-urlencode "installing=0" \
        --data-urlencode "installLanguage=en" \
        --data-urlencode "locale=en" \
        --data-urlencode "additionalLocales[]=en" \
        --data-urlencode "timeZone=UTC" \
        --data-urlencode "filesDir=/var/www/files" \
        --data-urlencode "databaseDriver=mysqli" \
        --data-urlencode "databaseHost=db" \
        --data-urlencode "databaseUsername=ojs" \
        --data-urlencode "databasePassword=ojspass" \
        --data-urlencode "databaseName=ojs" \
        --data-urlencode "adminUsername=$ADMIN_USER" \
        --data-urlencode "adminPassword=$ADMIN_PASS" \
        --data-urlencode "adminPassword2=$ADMIN_PASS" \
        --data-urlencode "adminEmail=$ADMIN_EMAIL" \
        --data-urlencode "oaiRepositoryId=ojs.localhost" \
        -o /dev/null
    grep -q '^installed = On' "$CONFIG" || { echo "installation did not complete"; exit 1; }
    echo "installed"
fi

# --------------------------------------------------------------------------
say "Generating an API token for $ADMIN_USER"
# The token is a JWT carrying a one-element array with the user's apiKey,
# signed with api_key_secret -- see PKP's APIProfileForm. Generating it here
# is the same thing the "User Profile -> API Key" tab does in the browser.
if ! docker exec "$DB" mariadb -uojs -pojspass ojs -N -B \
        -e "select 1 from user_settings where user_id=1 and setting_name='apiKey'" | grep -q 1; then
    KEY=$(python3 -c "import secrets;print(secrets.token_hex(32))")
    docker exec "$DB" mariadb -uojs -pojspass ojs -e "
        INSERT INTO user_settings (user_id, locale, setting_name, setting_value)
        VALUES (1,'','apiKey','$KEY'),(1,'','apiKeyEnabled','1');"
fi
KEY=$(docker exec "$DB" mariadb -uojs -pojspass ojs -N -B \
        -e "select setting_value from user_settings where user_id=1 and setting_name='apiKey'")
TOKEN=$(docker exec "$APP" php -r '
    require "/var/www/html/lib/pkp/lib/vendor/autoload.php";
    $cfg = parse_ini_file("/var/www/html/config.inc.php", true);
    echo \Firebase\JWT\JWT::encode([trim($argv[1])], $cfg["security"]["api_key_secret"], "HS256");
' "$KEY")

api() {  # api METHOD PATH [curl args...]
    local method=$1 path=$2; shift 2
    curl -s -X "$method" -H "Authorization: Bearer $TOKEN" \
         -H "Content-Type: application/json" "$BASE_URL/index.php/$path" "$@"
}

# --------------------------------------------------------------------------
say "Creating the journal"
if api GET "index/api/v1/contexts" | grep -q "\"urlPath\":\"$JOURNAL\""; then
    echo "journal $JOURNAL already exists"
else
    api POST "index/api/v1/contexts" -d '{
        "urlPath": "demojournal",
        "name": {"en": "Journal of Demonstrative Studies"},
        "acronym": {"en": "JDS"},
        "abbreviation": {"en": "J. Demonstr. Stud."},
        "description": {"en": "<p>A fictional open-access journal used only to exercise the OJS REST API. Nothing published here is real.</p>"},
        "primaryLocale": "en",
        "supportedLocales": ["en"],
        "contactName": "Dr Ada Testowa",
        "contactEmail": "editor@example.org",
        "enabled": true
    }' > /dev/null
    echo "created $JOURNAL"
fi

# --------------------------------------------------------------------------
say "Importing demo content"
# /var/www/files, not /tmp: the user importer insists the file be writable,
# and files copied into the container's /tmp are not.
for f in demo-content.xml demo-submissions.xml demo-users.xml; do
    docker cp "seed/$f" "$APP:/var/www/files/$f"
done
docker cp seed/assign-reviewers.php "$APP:/var/www/files/assign-reviewers.php"

if [ "$(docker exec "$DB" mariadb -uojs -pojspass ojs -N -B -e 'select count(*) from submissions')" = "0" ]; then
    docker exec "$APP" php /var/www/html/tools/importExport.php NativeImportExportPlugin \
        import /var/www/files/demo-content.xml "$JOURNAL" "$ADMIN_USER" 2>&1 | grep -v Deprecated || true
    docker exec "$APP" php /var/www/html/tools/importExport.php NativeImportExportPlugin \
        import /var/www/files/demo-submissions.xml "$JOURNAL" "$ADMIN_USER" 2>&1 | grep -v Deprecated || true
    docker exec "$APP" php /var/www/html/tools/importExport.php UserImportExportPlugin \
        import /var/www/files/demo-users.xml "$JOURNAL" 2>&1 | grep -v Deprecated || true
    docker exec "$APP" php /var/www/files/assign-reviewers.php 2>&1 | grep -v Deprecated || true
else
    echo "submissions already present, skipping import"
fi

# The native importer does not carry these over: the current issue is a
# journal setting rather than a column, and the show_* flags decide whether
# the volume/number/year appear in the issue's title.
docker exec "$DB" mariadb -uojs -pojspass ojs -e "
    INSERT INTO journal_settings (journal_id, locale, setting_name, setting_value)
    VALUES (1,'','currentIssueId','2')
    ON DUPLICATE KEY UPDATE setting_value='2';
    UPDATE issues SET show_volume=1, show_number=1, show_year=1, show_title=1 WHERE journal_id=1;"

# --------------------------------------------------------------------------
say "Ready"
cat <<EOF
  Site           $BASE_URL
  Journal        $BASE_URL/$JOURNAL
  Admin          $ADMIN_USER / $ADMIN_PASS
  Editor         editor / $ADMIN_PASS
  Reviewers      reviewer1, reviewer2 / $ADMIN_PASS

  OJS_BASE_URL=$BASE_URL
  OJS_JOURNAL=$JOURNAL
  OJS_API_TOKEN=$TOKEN

Exercise the MCP server against it:

  OJS_API_TOKEN=$TOKEN \\
    .venv/bin/python demo/check_api.py
EOF
