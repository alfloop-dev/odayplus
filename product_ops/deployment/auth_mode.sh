#!/usr/bin/env bash
#
# Single source of truth for the deployment-time authentication mode.
#
# Password-first is the default; OIDC is an optional provider. Every consumer in
# the release path -- the fail-closed preflight, the Web secret bindings, and
# the Web runtime environment -- must read the same resolved mode. When those
# consumers each derived "is OIDC on?" from a different input, a half-configured
# release could bind the client secret without injecting the issuer, or inject
# the issuer without binding its secret.
#
# Resolution order, first match wins:
#   1. ODP_AUTH_MODE          authoritative; "local" or "oidc"
#   2. ODP_AUTH_OIDC_ENABLED  legacy boolean alias
#   3. ODP_WEB_OIDC_ISSUER    pre-contract deployments only ever set the OIDC
#                             inputs, so a configured issuer keeps them on OIDC
#                             until they opt into an explicit mode
#
# Sourced by product_ops/deployment/deploy_cloud_run_waji.sh; it must not set
# shell options or run anything at source time.

# Resolves the mode and exports ODP_AUTH_MODE and ODP_AUTH_OIDC_ENABLED as a
# consistent pair. Returns non-zero on an invalid or self-contradicting
# configuration so the caller fails before any Cloud Run mutation.
resolve_auth_mode() {
  local mode="${ODP_AUTH_MODE:-}"
  local legacy_flag="${ODP_AUTH_OIDC_ENABLED:-}"

  case "${legacy_flag}" in
    ''|true|false) ;;
    *)
      echo "Error: ODP_AUTH_OIDC_ENABLED must be 'true' or 'false', got '${legacy_flag}'." >&2
      return 1
      ;;
  esac

  if [ -n "${mode}" ]; then
    case "${mode}" in
      local|oidc) ;;
      *)
        echo "Error: ODP_AUTH_MODE must be 'local' or 'oidc', got '${mode}'." >&2
        return 1
        ;;
    esac
    # An explicit mode and an explicit legacy flag that disagree is a split
    # configuration, not a default. Refuse rather than pick a winner.
    if [ -n "${legacy_flag}" ]; then
      local expected_flag="false"
      [ "${mode}" = "oidc" ] && expected_flag="true"
      if [ "${legacy_flag}" != "${expected_flag}" ]; then
        echo "Error: ODP_AUTH_MODE=${mode} conflicts with ODP_AUTH_OIDC_ENABLED=${legacy_flag}." >&2
        return 1
      fi
    fi
  elif [ -n "${legacy_flag}" ]; then
    if [ "${legacy_flag}" = "true" ]; then
      mode="oidc"
    else
      mode="local"
    fi
  elif [ -n "${ODP_WEB_OIDC_ISSUER:-}" ]; then
    mode="oidc"
  else
    mode="local"
  fi

  if [ "${mode}" = "oidc" ]; then
    local missing="" name
    for name in ODP_WEB_OIDC_ISSUER ODP_WEB_OIDC_CLIENT_ID ODP_WEB_OIDC_CLIENT_SECRET_SECRET; do
      if [ -z "${!name:-}" ]; then
        missing="${missing} ${name}"
      fi
    done
    if [ -n "${missing}" ]; then
      echo "Error: OIDC mode requires complete configuration; missing:${missing}." >&2
      return 1
    fi
  fi

  ODP_AUTH_MODE="${mode}"
  if [ "${mode}" = "oidc" ]; then
    ODP_AUTH_OIDC_ENABLED="true"
  else
    ODP_AUTH_OIDC_ENABLED="false"
  fi
  export ODP_AUTH_MODE ODP_AUTH_OIDC_ENABLED
}
