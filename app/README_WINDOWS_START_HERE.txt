================================================================================
  WINDOWS - HOW TO START THE APP (The Vape Workshop Calculator)
================================================================================

PYTHON VERSION (recommended for Pauly / VM testing)
  - Use Python 3.12 or 3.13. Some dependencies may not yet support 3.14.
  - The launchers try "py -3.13" then "py -3.12" first, then fall back to
    python / py -3. If you use 3.14 or newer you will see a WARNING but the
    script continues; if pip fails install 3.12 from python.org.

WHAT YOU NEED
  - Windows 10 or 11
  - Python 3.12 or 3.13 from https://www.python.org/downloads/
    When the installer asks, turn ON: "Add python.exe to PATH"
    Optional: install the "py" launcher and multiple versions so py -3.12 works.

LOCAL CONFIG (.env)
  - Optional file .env next to app.py: copy ENV_TEMPLATE.txt and edit.
  - Never commit .env. See LAUNCH_QA_ENV.txt for variable meanings.

WHAT TO DO
  1. Extract the ZIP to a normal folder on your PC (for example C:\Calc\app).
     Do NOT try to run the app from inside the ZIP file.

  2. Open that folder so you see files like:
       app.py
       requirements.txt
       Start_TVW_Calculator.bat
       (You may also see Start_Old_Goat_Lab.bat - it does the same thing.)

  3. Double-click:  Start_TVW_Calculator.bat

  4. The FIRST time you run it, Windows may take several minutes because it:
       - creates a folder named "venv" (your private copy of Python packages)
       - downloads and installs Flask and other required packages

  5. Your browser should open to the app. If not, go to:
       http://127.0.0.1:5000/recipes

PHONE / TABLET ON SAME Wi-Fi (development testing only)
  - See README_DEVICE_TESTING.txt in this folder.
  - Double-click Start_TVW_Device_Testing.bat (port 5055, host 0.0.0.0).
  - Or run: venv\Scripts\python.exe app.py --host 0.0.0.0 --port 5055

IMPORTANT
  - Always run the .bat from the same folder as app.py. If you move files around,
    double-click the .bat from the new app folder.
  - Do not delete requirements.txt or the venv folder unless you want to force
    a full reinstall (the launcher can recreate venv if you delete it).

LAB BRIDGE (TVW_ENABLE_LAB_MODULES)
  - The default **Start_TVW_Calculator.bat** turns the Lab bridge ON by setting
    TVW_ENABLE_LAB_MODULES=1 before starting Python. That registers inventory,
    mix, library, and related Lab routes merged into this app.
  - For **TVW-only** QA (community + TVW routes without Lab mix/inventory), edit
    the .bat and change that line to:  set "TVW_ENABLE_LAB_MODULES=0"
    Or start Python yourself after:  set TVW_ENABLE_LAB_MODULES=0
  - Full flag list and a **manual browser QA order** are in:
      LAUNCH_QA_ENV.txt
    in this same folder.

GUIDED MIX (development)
  - Guided Mix bench behaviour is governed by:
      docs/GUIDED_MIX_BENCH_RULES.md
  - Read and update that file when changing pour flow, nicotine policy, PG/VG
    logic, or bench UI. It is a working design reference, not final product docs.
  - Related: docs/mixing_guide_v2_launch_qa.md, docs/guided_mix_shared_core_audit.md

LAUNCHER TEST IF SOMETHING FAILS
  - Open Command Prompt (cmd.exe), not only PowerShell.
  - cd /d "C:\path\to\your\extracted\app"   (folder that contains app.py and the .bat)
  - Run:  Start_TVW_Calculator.bat
    or:   Start_Old_Goat_Lab.bat
  - If it fails, copy the FULL console output text and send it. Messages like
    "... was unexpected at this time" mean the batch file ran; the full log helps.

TROUBLESHOOTING
  - "Python is not installed": install Python and enable "Add to PATH", then try again.
  - "requirements.txt was not found": you are not in the right folder, or files are missing;
    extract the package again.
  - Antivirus sometimes blocks pip downloads; try again or allow Python in your antivirus.

================================================================================
