# PDF fonts

Used by `pdf/generator.py` for all Persian/RTL PDF reports.

## Registration order

1. **Vazirmatn** (preferred) — `Vazirmatn-Regular.ttf` + `Vazirmatn-Bold.ttf`  
   SIL Open Font License 1.1 — see `OFL-Vazirmatn.txt` (from [rastikerdar/vazirmatn](https://github.com/rastikerdar/vazirmatn) v33.003).
2. **Tahoma** (system) — if Vazirmatn files are missing or fail to register.
3. **DejaVuSans** (last resort) — `DejaVuSans.ttf` + `DejaVuSans-Bold.ttf` bundled for offline hosts without Tahoma.

Ship Vazirmatn in this folder so `install.sh` / any server works offline without downloading fonts.
