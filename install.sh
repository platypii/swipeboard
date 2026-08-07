#!/usr/bin/env bash
# Install Swipeboard for the current user. No sudo, no system files touched.
#
#   ./install.sh              autostart + panel launcher
#   ./install.sh --uninstall  remove both
#
# Onboard's autostart is deliberately left alone -- see README. Disable it only
# once you have driven Swipeboard on the real touchscreen.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOSTART="$HOME/.config/autostart/swipeboard.desktop"
LAUNCHER="$HOME/.local/share/applications/swipeboard.desktop"

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -fv "$AUTOSTART" "$LAUNCHER"
    echo "removed. running instances are untouched."
    exit 0
fi

mkdir -p "$(dirname "$AUTOSTART")" "$(dirname "$LAUNCHER")"

cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=Swipeboard
Comment=Swipe-typing on-screen keyboard
Exec=$REPO/bin/swipeboard
Icon=input-keyboard
Terminal=false
X-MATE-Autostart-enabled=true
EOF

cat > "$LAUNCHER" <<EOF
[Desktop Entry]
Type=Application
Name=Swipeboard
Comment=Swipe-typing on-screen keyboard
Exec=$REPO/bin/swipeboard
Icon=input-keyboard
Terminal=false
Categories=Utility;Accessibility;
EOF

echo "installed:"
echo "  $AUTOSTART"
echo "  $LAUNCHER   (drag onto the MATE panel for a launcher)"
echo
echo "start now:  $REPO/bin/swipeboard &"
