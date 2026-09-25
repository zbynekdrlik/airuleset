# shellcheck shell=bash
# SOURCED, never executed. Read one STRING field of a FLAT hook payload (a
# JSON object of scalars, which is what Claude Code sends a hook) with jq, and
# fall back to a FORK-FREE parser when jq fails (airuleset #1152, the #835
# class). No shebang and no `set` here: this is a library, and the hooks that
# source it already run under `set -euo pipefail`.
#
# Why this exists: a hook that reads its payload with `printf | jq ... ||
# echo <default>` silently takes the default when one jq SPAWN fails. Under
# `pytest -n auto` fork pressure that is real (#835 proved it for a PreToolUse
# hook). In notify-discord-pending.sh it dropped the ✅ ping (an empty message
# means "no marker", which deletes the pending) or misrouted it to the
# "unknown" session. The fallback uses only bash builtins, so it cannot fail
# the same way, and the caller is told which path answered so it can report
# the degradation loudly. A python3 fallback would fork too.
#
# json_str_field <payload> <key>
#   Sets JSON_FIELD_VALUE and JSON_FIELD_VIA (jq | fallback).
#   0  read: a string, or "" for null (and, via jq, for an absent key)
#   1  unreadable: jq failed and the value is not a JSON string or null,
#      or <key> is not a plain [A-Za-z0-9_] name
#   2  absent: jq failed and the payload has no "<key>": at all
#   The fallback matches the first "<key>": in the text, so it is only exact
#   for a flat object; a key nested in an inner object could shadow it.

json_str_field() {
    local _jf_out _jf_re
    JSON_FIELD_VIA=jq
    JSON_FIELD_VALUE=""
    [[ $2 =~ ^[A-Za-z0-9_]+$ ]] || return 1
    if _jf_out=$(printf '%s' "$1" | jq -r --arg k "$2" '.[$k] // empty' 2>/dev/null); then
        JSON_FIELD_VALUE="$_jf_out"
        return 0
    fi
    JSON_FIELD_VIA=fallback
    # "<key>" : "<body>". The body is a run of escape pairs or of characters
    # that are neither a quote nor a backslash, so an escaped quote inside a
    # string value can never end the match early, and a key name quoted inside
    # another value (always backslash-escaped in JSON) can never match here.
    _jf_re='"'"$2"'"[[:space:]]*:[[:space:]]*"((\\.|[^"\\])*)"'
    if [[ $1 =~ $_jf_re ]]; then
        _json_unescape "${BASH_REMATCH[1]}" || return 1
        # The jq path's `$(...)` drops trailing newlines; give the same value.
        while [[ $_JSON_FIELD_OUT == *$'\n' ]]; do
            _JSON_FIELD_OUT="${_JSON_FIELD_OUT%$'\n'}"
        done
        JSON_FIELD_VALUE="$_JSON_FIELD_OUT"
        return 0
    fi
    _jf_re='"'"$2"'"[[:space:]]*:[[:space:]]*null([[:space:]]*[,}])'
    [[ $1 =~ $_jf_re ]] && return 0
    _jf_re='"'"$2"'"[[:space:]]*:'
    [[ $1 =~ $_jf_re ]] && return 1
    return 2
}

# _json_unescape <json string body>  ->  _JSON_FIELD_OUT; rc 1 if it cannot run.
# Handles \" \\ \/ \b \f \n \r \t and \uXXXX, including UTF-16 surrogate
# pairs (a lone surrogate, invalid UTF-16, becomes U+FFFD). Builtins only:
# the body is split ONCE on its backslashes (`${s//…}` and `${s%%\\*}` loops
# are quadratic in bash, measured 0.7 s per pass on 120 KB). Every part after
# the first starts with its escaped character, except that an EMPTY part is
# "\\" and the part after it is plain text. Code points become UTF-8 bytes by
# arithmetic, so the result does not depend on the locale.
_json_unescape() {
    local p hex cp hi=-1 lit=0
    local -a parts
    mapfile -d '\' -t parts <<<"$1" || return 1
    (( ${#parts[@]} > 0 )) || { _JSON_FIELD_OUT=""; return 0; }
    parts[-1]="${parts[-1]%$'\n'}"            # `<<<` appends one newline
    _JSON_FIELD_OUT="${parts[0]}"
    for p in "${parts[@]:1}"; do
        if (( hi >= 0 )); then                 # a high surrogate waits
            if [[ $p =~ ^u[dD][c-fC-F][0-9a-fA-F]{2} ]]; then
                cp=$(( 0x10000 + ((hi - 0xD800) << 10) + (16#${p:1:4} - 0xDC00) ))
                _json_utf8 "$cp"
                _JSON_FIELD_OUT+="$_JSON_FIELD_U8${p:5}"
                hi=-1
                continue
            fi
            _JSON_FIELD_OUT+=$'\xef\xbf\xbd'   # lone high surrogate
            hi=-1
        fi
        if (( lit )); then                     # plain text after "\\"
            _JSON_FIELD_OUT+="$p"
            lit=0
            continue
        fi
        case $p in
            '') _JSON_FIELD_OUT+='\'; lit=1 ;;
            n*) _JSON_FIELD_OUT+=$'\n'"${p:1}" ;;
            t*) _JSON_FIELD_OUT+=$'\t'"${p:1}" ;;
            r*) _JSON_FIELD_OUT+=$'\r'"${p:1}" ;;
            b*) _JSON_FIELD_OUT+=$'\b'"${p:1}" ;;
            f*) _JSON_FIELD_OUT+=$'\f'"${p:1}" ;;
            u*)
                hex="${p:1:4}"
                if [[ ! $hex =~ ^[0-9a-fA-F]{4}$ ]]; then
                    _JSON_FIELD_OUT+="\\$p"            # not valid JSON; keep it
                    continue
                fi
                cp=$(( 16#$hex ))
                if (( cp >= 0xD800 && cp <= 0xDBFF )) && [[ -z ${p:5} ]]; then
                    hi=$cp
                    continue
                fi
                if (( cp >= 0xD800 && cp <= 0xDFFF )); then
                    _JSON_FIELD_OUT+=$'\xef\xbf\xbd'"${p:5}"
                    continue
                fi
                _json_utf8 "$cp"
                _JSON_FIELD_OUT+="$_JSON_FIELD_U8${p:5}"
                ;;
            *) _JSON_FIELD_OUT+="$p" ;;          # \"  \/: the char itself stays
        esac
    done
    (( hi < 0 )) || _JSON_FIELD_OUT+=$'\xef\xbf\xbd'
    return 0
}

# _json_utf8 <code point>  ->  _JSON_FIELD_U8 (its UTF-8 bytes). Fork-free.
# Cached per code point: a report escaped with \uXXXX repeats few distinct
# characters many times.
declare -gA _JSON_FIELD_U8_CACHE=()
_json_utf8() {
    local cp=$1 b o
    local -a bytes
    if [[ -n ${_JSON_FIELD_U8_CACHE[$cp]+x} ]]; then
        _JSON_FIELD_U8="${_JSON_FIELD_U8_CACHE[$cp]}"
        return 0
    fi
    if (( cp < 0x80 )); then
        bytes=("$cp")
    elif (( cp < 0x800 )); then
        bytes=($(( 0xC0 | cp >> 6 )) $(( 0x80 | (cp & 0x3F) )))
    elif (( cp < 0x10000 )); then
        bytes=($(( 0xE0 | cp >> 12 )) $(( 0x80 | ((cp >> 6) & 0x3F) )) \
               $(( 0x80 | (cp & 0x3F) )))
    else
        bytes=($(( 0xF0 | cp >> 18 )) $(( 0x80 | ((cp >> 12) & 0x3F) )) \
               $(( 0x80 | ((cp >> 6) & 0x3F) )) $(( 0x80 | (cp & 0x3F) )))
    fi
    _JSON_FIELD_U8=""
    for b in "${bytes[@]}"; do
        printf -v o '%03o' "$b"
        printf -v b "\\$o"
        _JSON_FIELD_U8+="$b"
    done
    _JSON_FIELD_U8_CACHE[$cp]="$_JSON_FIELD_U8"
}
