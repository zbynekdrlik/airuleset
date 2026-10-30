"""The bytes of the project-account `gh-app-shim` (#1190/#1199): constants only,
split from `cli_project_gh_token` (#1270), which renders it. A byte change rewrites
the shim on every account at its next install."""

PROJECT_SHIM_MARKER = "airuleset project-account GitHub App token shim (#1190)"
# (the same literal as cli_gh_rate.PROJECT_APP_SHIM_MARKER, test-locked)

_SHIM = r"""#!/usr/bin/env bash
# airuleset project-account GitHub App token shim (#1190) — MANAGED by the
# #1184 account bootstrap (cli_project_gh_token.render_gh_app_shim); do not
# edit. The rate-guard wrapper at ~/.local/bin/gh chains over it
# (gh -> gh-app-shim -> the real gh, cli_gh_rate #1087). The controller
# minter (airuleset.py project-gh-token) delivers a 1-hour token scoped to this
# account's repo into ~/.config/gh-app-tokens/primary every 30 minutes, and
# (#1199) an issues-only token for zbynekdrlik/airuleset beside it.
set -u
_dir="$HOME/.config/gh-app-tokens"
# #1199: a call that targets zbynekdrlik/airuleset (a -R/--repo value, or a
# `gh api` path under repos/zbynekdrlik/airuleset, or a POSITIONAL airuleset
# issue/PR URL) uses the issues-only airuleset token; everything else uses
# primary. Only a -R/--repo VALUE, an api path or a positional URL decides,
# so title/body text (a URL included) never routes.
_is_air() {
  local v="${1,,}"
  v="${v#https://}"; v="${v#http://}"; v="${v#www.}"; v="${v#github.com/}"
  v="${v%/}"; v="${v%.git}"
  [ "$v" = "zbynekdrlik/airuleset" ]
}
_tok="primary"
_prev=""
_sub="${1:-}"
for _a in "$@"; do
  case "$_prev" in -R|--repo) _is_air "$_a" && _tok="zbynekdrlik__airuleset" ;; esac
  case "$_a" in
    --repo=*) _is_air "${_a#--repo=}" && _tok="zbynekdrlik__airuleset" ;;
    -R?*) _is_air "${_a#-R}" && _tok="zbynekdrlik__airuleset" ;;
  esac
  case "$_prev" in
    -*) ;;    # a flag's value (--body/--title <url>) never routes
    *) case "${_a,,}" in
         https://github.com/zbynekdrlik/airuleset/issues/*|https://github.com/zbynekdrlik/airuleset/pull/*)
           _tok="zbynekdrlik__airuleset" ;;
       esac ;;
  esac
  if [ "$_sub" = api ]; then
    _p="${_a,,}"; _p="${_p#https://api.github.com}"; _p="${_p#/}"
    case "$_p" in
      repos/zbynekdrlik/airuleset|repos/zbynekdrlik/airuleset/*|"repos/zbynekdrlik/airuleset?"*)
        _tok="zbynekdrlik__airuleset" ;;
    esac
  fi
  _prev="$_a"
done
if [ "$_tok" != primary ] && [ ! -r "$_dir/$_tok" ]; then
  echo "gh: no airuleset issues token at $_dir/$_tok — the App newlevel-project-accounts must include zbynekdrlik/airuleset and the controller must mint it; see \`airuleset.py accounts status\` on the controller (#1199)" >&2
  _tok="primary"
fi
if [ -r "$_dir/$_tok" ]; then
  IFS= read -r GH_TOKEN < "$_dir/$_tok" || true
  export GH_TOKEN
  _exp="$(cat "$(readlink -f "$_dir/$_tok").expires" 2>/dev/null || true)"
  if [ -n "$_exp" ] && [ "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \> "$_exp" ]; then
    echo "gh: project-account token $_tok expired at $_exp — the controller timer (project-gh-token.timer) is not refreshing it (#1190)" >&2
  fi
else
  echo "gh: no project-account token at $_dir/primary — run on the controller: airuleset.py project-gh-token mint <account> (#1190)" >&2
fi
# The REAL gh binary: never a #! script (the rate-guard wrapper or this shim),
# so no exec loop (#1051).
_is_real() { [ -f "$1" ] && [ -x "$1" ] && [ "$(head -c2 "$1" 2>/dev/null)" != "#!" ]; }
_real=""
if _is_real "$HOME/.local/bin/gh-upstream"; then
  _real="$HOME/.local/bin/gh-upstream"
else
  IFS=':' read -ra _parts <<< "${PATH:-}"
  for _d in "${_parts[@]}"; do
    [ -n "$_d" ] || continue
    if _is_real "$_d/gh"; then _real="$_d/gh"; break; fi
  done
fi
[ -n "$_real" ] || { echo "gh: no real gh binary found (project-account App shim, #1190)" >&2; exit 127; }
exec "$_real" "$@"
"""
