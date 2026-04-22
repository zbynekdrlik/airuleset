#!/usr/bin/env python3
"""
Claude Code Session Repair Tool

Fixes the branch-selection bug where Claude Code resumes on the wrong
conversation branch after restart. Works by:

1. Scanning for corrupted sessions (UUID collisions from file-history-snapshot)
2. Finding the LATEST conversation branch (by timestamp of last user/assistant message)
3. Truncating the file to ONLY that branch + metadata

The old file is backed up to /tmp/session-repair/ before any changes.

Usage:
    python repair-session.py <jsonl-file>           # diagnose
    python repair-session.py <jsonl-file> --fix      # truncate to latest branch
    python repair-session.py --scan                  # scan all projects
    python repair-session.py --scan --fix            # fix all corrupted
"""

import json
import sys
import shutil
from pathlib import Path
from datetime import datetime
from collections import Counter


def find_latest_branch(path: Path) -> dict:
    """Find the latest conversation branch and return its chain indices."""
    lines = path.read_text().splitlines()

    uuid_to_idx = {}
    entries = []
    children = {}

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            entries.append(None)
            continue
        try:
            d = json.loads(line)
            uid = d.get('uuid', '')
            parent = d.get('parentUuid', '')
            tp = d.get('type', '')
            ts = d.get('timestamp', '')
            sidechain = d.get('isSidechain', False)
            entries.append({
                'idx': i, 'uuid': uid, 'parent': parent,
                'type': tp, 'ts': ts, 'sidechain': sidechain
            })
            if uid:
                uuid_to_idx[uid] = i
                if parent:
                    children.setdefault(parent, []).append(uid)
        except:
            entries.append(None)

    # Find leaf nodes (uuids not referenced as parent by anyone)
    all_uuids = set(uuid_to_idx.keys())
    parent_uuids = set(children.keys())
    leaves = all_uuids - parent_uuids

    # Find the latest leaf by timestamp (only user/assistant, non-sidechain)
    best_leaf = None
    best_ts = ''
    for uid in leaves:
        idx = uuid_to_idx[uid]
        e = entries[idx]
        if e and not e['sidechain'] and e['type'] in ('user', 'assistant', 'system'):
            if e['ts'] > best_ts:
                best_ts = e['ts']
                best_leaf = uid

    if not best_leaf:
        return {'error': 'No valid leaf found', 'lines': len(lines)}

    # Trace backwards from best leaf
    chain_indices = set()
    current = best_leaf
    visited = set()
    while current and current not in visited:
        visited.add(current)
        if current in uuid_to_idx:
            chain_indices.add(uuid_to_idx[current])
            e = entries[uuid_to_idx[current]]
            if e:
                current = e['parent']
            else:
                break
        else:
            break

    # Also include metadata entries after the chain (custom-title, agent-name, etc.)
    max_chain = max(chain_indices) if chain_indices else 0
    for i in range(max_chain + 1, len(entries)):
        chain_indices.add(i)

    # Get content preview of the leaf
    leaf_idx = uuid_to_idx[best_leaf]
    leaf_line = lines[leaf_idx].strip()
    content_preview = ''
    try:
        d = json.loads(leaf_line)
        msg = d.get('message', {})
        c = msg.get('content', '')
        if isinstance(c, list):
            for item in c:
                if isinstance(item, dict) and item.get('type') == 'text':
                    content_preview = item.get('text', '')[:100]
                    break
        elif isinstance(c, str):
            content_preview = c[:100]
    except:
        pass

    return {
        'total_lines': len(lines),
        'chain_size': len(chain_indices),
        'chain_indices': chain_indices,
        'leaf_idx': leaf_idx,
        'leaf_ts': best_ts,
        'leaf_content': content_preview,
        'lines': lines,
    }


def scan_session(path: Path) -> dict:
    """Quick scan for corruption indicators."""
    uuid_first = {}
    collisions = 0
    total = 0

    with open(path) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                d = json.loads(line)
                uid = d.get('uuid', '')
                mid = d.get('messageId', '')

                if uid and uid not in uuid_first:
                    uuid_first[uid] = i

                if d.get('type') == 'file-history-snapshot' and mid:
                    if mid in uuid_first and uuid_first[mid] != i:
                        collisions += 1
            except:
                pass

    size_mb = path.stat().st_size / (1024 * 1024)
    return {
        'file': path.name,
        'project': path.parent.name,
        'total': total,
        'collisions': collisions,
        'corrupted': collisions > 0,
        'size_mb': round(size_mb, 1),
    }


def fix_session(path: Path) -> bool:
    """Truncate session to latest branch only."""
    result = find_latest_branch(path)

    if 'error' in result:
        print(f"  ERROR: {result['error']}")
        return False

    lines = result['lines']
    chain = result['chain_indices']

    print(f"  Latest branch: {result['chain_size']} entries, leaf at idx {result['leaf_idx']}")
    print(f"  Leaf: {result['leaf_ts'][:19]} — {result['leaf_content']}")

    if result['chain_size'] >= result['total_lines'] * 0.9:
        print(f"  Skipping — chain covers >90% of file, likely not corrupted")
        return False

    # Backup
    backup_dir = Path('/tmp/session-repair')
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = backup_dir / f"{path.stem}_{ts}.jsonl"
    shutil.copy2(path, backup)
    print(f"  Backup: {backup}")

    # Write only chain entries
    kept = sorted(chain)
    new_lines = [lines[i] + '\n' if not lines[i].endswith('\n') else lines[i] + '\n'
                 for i in kept if i < len(lines) and lines[i].strip()]

    with open(path, 'w') as f:
        for i in kept:
            if i < len(lines) and lines[i].strip():
                f.write(lines[i] if lines[i].endswith('\n') else lines[i] + '\n')

    new_size = path.stat().st_size / (1024 * 1024)
    old_size = sum(len(l) for l in lines) / (1024 * 1024)
    print(f"  Truncated: {result['total_lines']} → {len(kept)} lines ({round(old_size, 1)}MB → {round(new_size, 1)}MB)")
    return True


def find_all_sessions() -> list:
    """Find all JSONL session files."""
    base = Path.home() / '.claude' / 'projects'
    if not base.exists():
        return []
    return sorted(base.glob('*/*.jsonl'), key=lambda p: p.stat().st_mtime, reverse=True)


def main():
    args = sys.argv[1:]
    do_fix = '--fix' in args
    args = [a for a in args if a != '--fix']

    if '--scan' in args:
        sessions = find_all_sessions()
        print(f"Scanning {len(sessions)} sessions...\n")

        corrupted = []
        for path in sessions:
            if path.stat().st_size < 1024:
                continue  # skip tiny files
            result = scan_session(path)
            if result['corrupted']:
                print(f"  CORRUPTED: {result['project']}/{result['file']} "
                      f"({result['size_mb']}MB, {result['collisions']} collisions)")
                corrupted.append(path)
                if do_fix:
                    fix_session(path)
                    print()

        print(f"\n{'='*60}")
        print(f"Total: {len(sessions)} sessions, {len(corrupted)} corrupted")
        if corrupted and not do_fix:
            print("Run with --fix to repair corrupted sessions")
        return

    if not args:
        print(__doc__)
        sys.exit(1)

    path = Path(args[0])
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    result = scan_session(path)
    print(f"Session: {result['file']}")
    print(f"  Size: {result['size_mb']}MB, {result['total']} entries")
    print(f"  Collisions: {result['collisions']}")
    print(f"  Corrupted: {result['corrupted']}")

    if do_fix:
        print()
        fix_session(path)


if __name__ == '__main__':
    main()
