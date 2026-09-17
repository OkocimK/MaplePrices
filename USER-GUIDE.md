# OSM FM price tracker (Old School Maple)

One file: `osmsfm-tracker.exe`. It screenshots the game window while you browse hired
merchants on the Free Market, reads item names and prices from the screenshots and uploads
them to the shared page https://osmsfm.duckdns.org/. It does not touch the game: no memory
reading, no clicking, no packets. Screenshot plus OCR, nothing else.

## Usage

1. Put `osmsfm-tracker.exe` in any folder (a `data\` folder with a local copy appears next to it).
2. Run the game at **1920x1080**, windowed or full screen. Other window sizes work too, but the
   game shrinks its UI with the window and small windows read worse. The minimap can be moved,
   collapsed or hidden (without it offers are stored without the map and the Free Market room number).
3. Run the exe. Windows SmartScreen may warn about an unknown publisher the first time:
   "More info", then "Run anyway". Or right-click the exe, Properties, "Unblock" before running.
   Antivirus software is sometimes suspicious of PyInstaller executables, that is all it is.
4. **F9** starts collecting, **F9** again stops. Turn it on only while you click through shops.
   The cursor may rest on the shop window; the row under it is skipped and picked up in the
   next frame.
5. **Ctrl+F9** quits. Live preview: http://localhost:8778/ in your browser.
6. If the console keeps saying 0 shops while you browse them, press **F10** with a shop open:
   it saves what the tracker sees to `data\debug\snap-*.png`. Send that file.

Nothing to type in: the server address and key are built in. Uploads are anonymous: the
program picks a random id like `anon-3f9c2a` and signs its uploads with it. If you want to
sign with a nickname instead, edit `data\config.json` (field `client`).

Data is uploaded every 5 minutes while collecting and once more when you stop. Without a
network connection it stays in `data\` and goes out next time.

## What is collected

Item name, price, whether the offer is sold out (greyed), the shop owner's name, shop title,
map and Free Market room, the shop's remaining time, a small crop of the row (image) to verify the
reading, and the random id of the program. Nothing about your character, account or chat.
