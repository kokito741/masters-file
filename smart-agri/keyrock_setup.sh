#!/bin/bash
# =====================================================================
#  Keyrock configuration for the SmartAgri dashboard
#  Creates: application -> roles (Admin, Viewer) -> users -> assignments
#  Safe to read before running. Prints the CLIENT_ID / CLIENT_SECRET
#  you need for the Flask dashboard at the end.
# =====================================================================
set -e

KEYROCK=http://localhost:3005
ADMIN_EMAIL="admin@test.com"
ADMIN_PASS="1234"
CALLBACK="http://localhost:5000/callback"

# Passwords for the two demo accounts. CHANGE THESE.
VIEWER_EMAIL="viewer@smartagri.local"
VIEWER_PASS="viewer1234"
OPERATOR_EMAIL="operator@smartagri.local"
OPERATOR_PASS="operator1234"

need() { command -v "$1" >/dev/null || { echo "missing: $1"; exit 1; }; }
need curl
need jq

echo "== 1. authenticating as Keyrock admin =="
TOKEN=$(curl -s -i -X POST "$KEYROCK/v1/auth/tokens" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\"}" \
  | grep -i '^X-Subject-Token:' | tr -d '\r' | awk '{print $2}')

if [ -z "$TOKEN" ]; then
  echo "FAILED: no token. Is Keyrock up on $KEYROCK, and are the admin"
  echo "credentials correct? Check: docker logs keyrock --tail 30"
  exit 1
fi
echo "   token acquired"

echo "== 2. creating the SmartAgri application =="
APP=$(curl -s -X POST "$KEYROCK/v1/applications" \
  -H "Content-Type: application/json" -H "X-Auth-token: $TOKEN" \
  -d "{\"application\":{
        \"name\":\"SmartAgri\",
        \"description\":\"Multi-zone smart agriculture dashboard\",
        \"redirect_uri\":\"$CALLBACK\",
        \"url\":\"http://localhost:5000\",
        \"grant_type\":[\"authorization_code\",\"refresh_token\"],
        \"token_types\":[\"jwt\",\"permanent\"]
      }}")

APP_ID=$(echo "$APP"     | jq -r '.application.id')
CLIENT_SECRET=$(echo "$APP" | jq -r '.application.secret')

if [ "$APP_ID" = "null" ] || [ -z "$APP_ID" ]; then
  echo "FAILED to create application. Response:"; echo "$APP" | jq .; exit 1
fi
echo "   app id: $APP_ID"

echo "== 3. creating roles =="
ROLE_ADMIN=$(curl -s -X POST "$KEYROCK/v1/applications/$APP_ID/roles" \
  -H "Content-Type: application/json" -H "X-Auth-token: $TOKEN" \
  -d '{"role":{"name":"Admin"}}' | jq -r '.role.id')
ROLE_VIEWER=$(curl -s -X POST "$KEYROCK/v1/applications/$APP_ID/roles" \
  -H "Content-Type: application/json" -H "X-Auth-token: $TOKEN" \
  -d '{"role":{"name":"Viewer"}}' | jq -r '.role.id')
echo "   Admin  : $ROLE_ADMIN"
echo "   Viewer : $ROLE_VIEWER"

echo "== 4. creating users =="
create_user() {  # $1 username  $2 email  $3 password
  curl -s -X POST "$KEYROCK/v1/users" \
    -H "Content-Type: application/json" -H "X-Auth-token: $TOKEN" \
    -d "{\"user\":{\"username\":\"$1\",\"email\":\"$2\",\"password\":\"$3\"}}" \
    | jq -r '.user.id'
}
USER_OPERATOR=$(create_user "operator" "$OPERATOR_EMAIL" "$OPERATOR_PASS")
USER_VIEWER=$(create_user   "viewer"   "$VIEWER_EMAIL"   "$VIEWER_PASS")
echo "   operator: $USER_OPERATOR"
echo "   viewer  : $USER_VIEWER"

echo "== 5. assigning roles =="
curl -s -X PUT "$KEYROCK/v1/applications/$APP_ID/users/$USER_OPERATOR/roles/$ROLE_ADMIN" \
  -H "X-Auth-token: $TOKEN" >/dev/null
curl -s -X PUT "$KEYROCK/v1/applications/$APP_ID/users/$USER_VIEWER/roles/$ROLE_VIEWER" \
  -H "X-Auth-token: $TOKEN" >/dev/null
echo "   done"

cat <<SUMMARY

=====================================================================
 Add these to your .env file next to docker-compose.yml:

 KEYROCK_CLIENT_ID=$APP_ID
 KEYROCK_CLIENT_SECRET=$CLIENT_SECRET

 Accounts:
   $OPERATOR_EMAIL / $OPERATOR_PASS   -> Admin  (can send commands)
   $VIEWER_EMAIL / $VIEWER_PASS   -> Viewer (read only)

 Keyrock web UI: $KEYROCK  ($ADMIN_EMAIL / $ADMIN_PASS)
=====================================================================
SUMMARY
