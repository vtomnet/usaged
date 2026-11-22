#!/bin/sh
# build + install Usagebar.app into ~/Applications (run on the mac that shows the menu bar)
set -e
cd "$(dirname "$0")"
app=build/Usagebar.app
rm -rf build && mkdir -p $app/Contents/MacOS $app/Contents/Resources
cp Info.plist $app/Contents/
for f in logos/*.svg; do rsvg-convert -f pdf "$f" -o "$app/Contents/Resources/$(basename "$f" .svg).pdf"; done
xcrun swiftc -O -parse-as-library app.swift -o $app/Contents/MacOS/usagebar
codesign -s - --force $app
pkill -x usagebar || true
rm -rf ~/Applications/Usagebar.app && cp -R $app ~/Applications/
open ~/Applications/Usagebar.app
