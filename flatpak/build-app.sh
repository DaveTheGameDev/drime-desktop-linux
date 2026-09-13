#!/bin/sh
# Install the app into a prefix (the Flatpak counterpart of the spec's %install and
# debian/rules). Run from the repository root:  sh flatpak/build-app.sh /app
set -eu
PREFIX=${1:-/app}
ID=io.github.davethegamedev.DrimeDesktop
version=$(sed -n 's/^Version:[[:space:]]*//p' drime-desktop.spec | head -1)

# Python package: a private directory on PYTHONPATH (set by the manifest's --env), so it
# does not depend on the runtime's site-packages layout.
lib="$PREFIX/lib/drime-desktop/drime_desktop"
install -d "$lib"
install -m 0644 src/drime_desktop/*.py "$lib/"
sed -i "s/@VERSION@/$version/" "$lib/__init__.py"

install -d "$PREFIX/bin"
cat > "$PREFIX/bin/drime-desktop" <<'EOF'
#!/bin/sh
exec python3 -m drime_desktop.cli "$@"
EOF
chmod 0755 "$PREFIX/bin/drime-desktop"

# Only icons named after the app ID are exported to the host, so the launcher must use
# that name; inside the sandbox the app keeps using "drime-desktop" (a second copy).
install -D -m 0644 "desktop/$ID.desktop" "$PREFIX/share/applications/$ID.desktop"
sed -i "s/^Icon=.*/Icon=$ID/" "$PREFIX/share/applications/$ID.desktop"
install -D -m 0644 assets/drime.png "$PREFIX/share/icons/hicolor/512x512/apps/$ID.png"
install -D -m 0644 assets/drime.png "$PREFIX/share/icons/hicolor/512x512/apps/drime-desktop.png"
install -D -m 0644 "assets/$ID.metainfo.xml" "$PREFIX/share/metainfo/$ID.metainfo.xml"
