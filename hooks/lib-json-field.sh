# shellcheck shell=bash
# SOURCED, never executed. Read one top-level STRING field of a hook's JSON
# payload with jq, and fall back to a FORK-FREE parser when jq fails
# (airuleset #1152, the #835 class). No shebang and no `set` here: this is a
# library, and the hooks that source it already run under
# `set -euo pipefail`.
#
# Why this exists: a hook that reads its payload with `printf | jq ... ||
# echo <default>` silently takes the default when one jq SPAWN fails. Under
# `pytest -n auto` fork pressure that is real (#835 proved it for a PreToolUse
# hook). In notify-discord-pending.sh it dropped the ✅ ping (an empty message
# means "no marker", which deletes the pending) or misrouted it to the
# "unknown" session. The fallback below uses only bash builtins, so it cannot
# fail the same way, and the caller is told which path answered so it can
# report the degradation loudly.
#
# json_str_field <payload> <key>
#   Sets JSON_FIELD_VALUE and JSON_FIELD_VIA (jq | fallback).
#   Returns 0 when the field was read. An absent or null field reads as "" via
#   jq. Returns 1 when the payload is unreadable: jq failed AND the fallback
#   could not find and decode a "<key>": "<string>" pair.

json_str_field() {
    local _jf_out
    JSON_FIELD_VIA=jq
    if _jf_out=$(printf '%s' "$1" | jq -r --arg k "$2" '.[$k] // empty' 2>/dev/null); then
        JSON_FIELD_VALUE="$_jf_out"
        return 0
    fi
    JSON_FIELD_VIA=fallback
    JSON_FIELD_VALUE=""
    # "<key>" : "<body>". The body is a run of escape pairs or of characters
    # that are neither a quote nor a backslash, so an escaped quote inside a
    # string value can never end the match early, and a key name quoted inside
    # another value (always backslash-escaped in JSON) can never match here.
    local _jf_re='"'"$2"'"[[:space:]]*:[[:space:]]*"((\\.|[^"\\])*)"'
    [[ $1 =~ $_jf_re ]] || return 1
    _json_unescape "${BASH_REMATCH[1]}" || return 1
    # The jq path's `$(...)` drops trailing newlines; give the same value.
    while [[ $_JU == *$'\n' ]]; do _JU="${_JU%$'\n'}"; done
    JSON_FIELD_VALUE="$_JU"
    return 0
}

# _json_unescape <json string body>  ->  _JU (decoded); rc 1 if it cannot run.
# Handles \" \\ \/ \b \f \n \r \t and \uXXXX, including UTF-16 surrogate
# pairs, with builtins only. Linear time: the body is split ONCE on its
# backslashes (`${s//…}` and `${s%%\\*}` loops are quadratic in bash, which
# measured 0.7 s per pass on 120 KB). Every part after the first starts with
# the escaped character, except that an EMPTY part means "\\" and the part
# after it is plain text. Code points are encoded to UTF-8 bytes by
# arithmetic, so the result does not depend on the locale.
_json_unescape() {
    local p c rest i n hi lo
    local hex_re='^[0-9a-fA-F]{4}' low_re='^u[dD][c-fC-F][0-9a-fA-F]{2}'
    local -a parts
    mapfile -d '\' -t parts <<<"$1" || return 1
    n=${#parts[@]}
    (( n > 0 )) || { _JU=""; return 0; }
    parts[n-1]="${parts[n-1]%$'\n'}"         # `<<<` appends one newline
    _JU="${parts[0]}"
    i=1
    while (( i < n )); do
        p="${parts[i]}"
        if [[ -z $p ]]; then                  # "\\": an escaped backslash
            _JU+='\'
            (( i + 1 < n )) && _JU+="${parts[i+1]}"
            (( i += 2 ))
            continue
        fi
        c="${p:0:1}"
        rest="${p:1}"
        case $c in
            n) _JU+=$'\n' ;;
            t) _JU+=$'\t' ;;
            r) _JU+=$'\r' ;;
            b) _JU+=$'\b' ;;
            f) _JU+=$'\f' ;;
            u)
                if [[ $rest =~ $hex_re ]]; then
                    hi=$(( 16#${rest:0:4} ))
                    rest="${rest:4}"
                    if (( hi >= 0xD800 && hi <= 0xDBFF )) && [[ -z $rest ]] \
                       && (( i + 1 < n )) && [[ ${parts[i+1]} =~ $low_re ]]; then
                        (( i += 1 ))
                        lo=$(( 16#${parts[i]:1:4} ))
                        rest="${parts[i]:5}"
                        hi=$(( 0x10000 + ((hi - 0xD800) << 10) + (lo - 0xDC00) ))
                    fi
                    _utf8_bytes "$hi"
                    _JU+="$_U8"
                else
                    _JU+='\u'                 # not valid JSON; keep it as text
                fi
                ;;
            *) _JU+="$c" ;;                   # \"  \/  (and any other char)
        esac
        _JU+="$rest"
        (( i += 1 ))
    done
    return 0
}

# _utf8_bytes <code point>  ->  _U8 (its UTF-8 byte sequence). Fork-free.
_utf8_bytes() {
    local cp=$1 b o
    local -a bytes
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
    _U8=""
    for b in "${bytes[@]}"; do
        printf -v o '%03o' "$b"
        printf -v b "\\$o"
        _U8+="$b"
    done
}
