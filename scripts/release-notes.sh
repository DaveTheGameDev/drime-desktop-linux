#!/bin/sh
# Print the GitHub release notes for a version: the download-guidance header
# (with the exact RPM and DEB filenames), the %changelog entry from the spec,
# and a Full Changelog link. Used by the release workflow and `make release`.
#   usage: scripts/release-notes.sh <version> <rpm-filename>
set -e
version=$1; rpm=$2
deb="drime-desktop_${version}_all.deb"
flatpak="drime-desktop-${version}.flatpak"
repo=DaveTheGameDev/drime-desktop-linux
cd "$(dirname "$0")/.."
sed -e "s/drime-desktop-<version>-1.fcNN.noarch.rpm/$rpm/" -e "s/drime-desktop_<version>_all.deb/$deb/" \
    -e "s/drime-desktop-<version>.flatpak/$flatpak/g" \
    .github/release-header.md
printf '\n## What'"'"'s changed\n\n'
# The %changelog block for this version, with wrapped lines joined.
awk -v v="$version" '
  /^\* / { on = ($0 ~ " - " v "-[0-9]+$"); next }
  on && /^- / { if (item != "") print item; item = $0; next }
  on && /^  / { sub(/^ +/, " "); item = item $0; next }
  END { if (item != "") print item }' drime-desktop.spec
prev=$(git tag --sort=-v:refname | sed -n "/^v$version\$/{n;p;}")
printf '\n**Full Changelog**: https://github.com/%s/%s\n' "$repo" \
  "$([ -n "$prev" ] && echo "compare/$prev...v$version" || echo "commits/v$version")"
