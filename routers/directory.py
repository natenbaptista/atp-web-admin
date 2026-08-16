"""
routers/directory.py — /directory/*

1. Global LDAP/system directory search (users + lines via ATP)
   GET  /directory          → search page (SPA)
   GET  /directory/search   → AJAX search (JSON, up to 200 results)

2. Global Directory management (portable SQLite + txt file)
   GET  /directory/global                      → list all entries (JSON)
   POST /directory/global/add                  → add entry (multipart)
   GET  /directory/global/{id}                 → get single entry (JSON)
   POST /directory/global/{id}/edit            → update entry (multipart)
   POST /directory/global/{id}/delete          → delete entry
   POST /directory/global/delete-multiple      → bulk delete
   POST /directory/global/upload-profile-images → bulk upload profile pics
   POST /directory/global/upload-company-logos  → bulk upload logos
   GET  /directory/global/image/{type}/{filename} → serve image
   POST /directory/global/import               → import CSV or colon txt
   GET  /directory/global/export               → export CSV
   GET  /directory/global/export-profile-images → export ZIP
   GET  /directory/global/export-company-logos  → export ZIP
   POST /directory/global/update-global        → regenerate txt file

Storage: SQLite at {GLOBAL_DIR_PATH}/global_directory.db
         (default GLOBAL_DIR_PATH=/home/atp/global_directory)
The txt file is written ONLY by POST /directory/global/update-global.
"""
