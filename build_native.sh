#!/bin/zsh
set -eu
cd -- "${0:A:h}"
mkdir -p .build
staging=$(mktemp -d "$PWD/.build/native.XXXXXX")
# The Swift module cache stores absolute paths: keep one cache per folder location, so moving the
# project (2026-10-20: Downloads -> ~/Projects) never reuses a cache made for the old path.
cache="$PWD/.build/module-cache-$(print -rn -- "$PWD" | /usr/bin/shasum | cut -c1-10)"
trap 'rm -rf -- "$staging"' EXIT
app_bundle="$staging/Mac刷闪助手.app"
mkdir -p "$app_bundle/Contents/MacOS" "$app_bundle/Contents/Resources" .build
/usr/bin/xcrun swiftc -parse-as-library -swift-version 5 -target arm64-apple-macos14.0 -O -module-cache-path "$cache" Native/*.swift -o "$app_bundle/Contents/MacOS/ShinyHunt" -framework SwiftUI -framework AppKit
cat > "$app_bundle/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>ShinyHunt</string>
<key>CFBundleIdentifier</key><string>local.shinyhunt.mac</string>
<key>CFBundleName</key><string>Mac刷闪助手</string>
<key>CFBundleDisplayName</key><string>Mac刷闪助手</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>2.0</string>
<key>CFBundleVersion</key><string>20</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
/usr/bin/codesign --force --sign - --requirements '=designated => identifier "local.shinyhunt.mac"' "$app_bundle"

/usr/bin/codesign --verify --strict "$app_bundle"
# Keep the installed app intact until compilation and signing both succeed.
if [[ -d 'Mac刷闪助手.app' ]]; then
  mv 'Mac刷闪助手.app' "$staging/previous.app"
fi
if ! mv "$app_bundle" 'Mac刷闪助手.app'; then
  [[ ! -d "$staging/previous.app" ]] || mv "$staging/previous.app" 'Mac刷闪助手.app'
  exit 1
fi
/usr/bin/shasum -a 256 Native/*.swift build_native.sh > .build/native.sha256
rm -f .ui-build-required
