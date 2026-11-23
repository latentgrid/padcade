"""Version ordering shared by Settings and the command-line updater."""
import re


def newer_release(installed, tag):
    def version(text):
        if not isinstance(text, str) or len(text) > 128:
            return None
        core = r'(0|[1-9][0-9]*)'
        identifiers = r'[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*'
        found = re.fullmatch(rf'v?{core}\.{core}\.{core}(?:-({identifiers}))?(?:\+{identifiers})?', text)
        if not found:
            return None
        major, minor, patch, preview = found.groups()
        parts = preview.split('.') if preview else []
        if any(p.isdigit() and len(p) > 1 and p.startswith('0') for p in parts):
            return None
        return (int(major), int(minor), int(patch), not preview,
                tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts))

    current, offered = version(installed), version(tag)
    return current is not None and offered is not None and offered > current


if __name__ == '__main__':
    import sys
    sys.exit(0 if newer_release(*sys.argv[1:]) else 1)
