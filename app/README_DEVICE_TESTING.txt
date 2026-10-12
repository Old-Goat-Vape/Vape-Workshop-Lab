================================================================================
  PHONE / TABLET TESTING ON YOUR HOME Wi-Fi (development only)
================================================================================

Use this when you want to open The Vape Workshop Calculator on a phone or
tablet while developing on your laptop. This is for PRIVATE LAN testing only —
not production and not public internet hosting.

QUICK START (Windows)
  1. Run Start_TVW_Calculator.bat once if you have not created venv yet.
  2. Double-click:  Start_TVW_Device_Testing.bat
  3. Read the console — it prints URLs like:
       http://192.168.x.x:5055/
  4. On your phone/tablet (same Wi-Fi), open that URL in the browser.

MANUAL COMMAND (from the app folder)
  venv\Scripts\python.exe app.py --host 0.0.0.0 --port 5055

  Or after activating venv:
  python app.py --host 0.0.0.0 --port 5055

STEP BY STEP
  1. Connect laptop and phone/tablet to the SAME Wi-Fi network.

  2. Start Flask bound to all local interfaces (not internet-public by itself):
       --host 0.0.0.0 --port 5055

  3. Find your laptop IPv4 address:
       Open Command Prompt and run:  ipconfig
       Look for "Wireless LAN adapter Wi-Fi" → IPv4 Address
       Example: 192.168.1.42

  4. On the phone/tablet browser open:
       http://LAPTOP-IP:5055/
       Example: http://192.168.1.42:5055/

  5. Windows Firewall (first time):
       When Windows asks, allow Python on PRIVATE networks only.
       Do not expose this dev server on Public networks.
       Settings → Privacy & security → Windows Security → Firewall
       → Allow an app → Python → Private only.

SECURITY NOTES
  - 0.0.0.0 means "listen on your LAN interfaces" — devices on your Wi-Fi can
    reach the app. It does NOT publish the app to the internet.
  - Never use this mode on untrusted networks (coffee shop Wi-Fi, etc.).
  - For daily desktop use, keep using Start_TVW_Calculator.bat (127.0.0.1:5000).

NORMAL DESKTOP RUN (unchanged)
  Start_TVW_Calculator.bat  →  http://127.0.0.1:5000/recipes

TROUBLESHOOTING
  - Phone cannot connect: confirm same Wi-Fi, correct IPv4, firewall allowed.
  - Page loads but login fails: use http:// not https:// for local dev.
  - IP changed: Wi-Fi DHCP may assign a new address — run ipconfig again.

================================================================================
