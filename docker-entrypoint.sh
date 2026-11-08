#!/bin/sh
# 容器入口：可用 SAFEORBIT_ADMIN_USERNAME / SAFEORBIT_ADMIN_PASSWORD 一次性初始化管理员，
# 已存在则跳过（幂等）。随后把参数交给 safeorbit CLI。
set -e

DB="${SAFEORBIT_DB:-/data/safeorbit.db}"

if [ -n "$SAFEORBIT_ADMIN_USERNAME" ] && [ -n "$SAFEORBIT_ADMIN_PASSWORD" ]; then
    SAFEORBIT_USER_PASSWORD="$SAFEORBIT_ADMIN_PASSWORD" \
        safeorbit user init --username "$SAFEORBIT_ADMIN_USERNAME" --db "$DB" >/dev/null
fi

exec safeorbit "$@"
