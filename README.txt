enePath webadmin
================

Source of truth: https://github.com/natenbaptista/atp-web-admin
Default branch: main

The running app is /opt/enepath/webadmin. A git pull in a checkout
does not update the UI until update.sh copies the tree there.

Engineer update (AMP already installed)
---------------------------------------
  git clone https://github.com/natenbaptista/atp-web-admin.git
  cd atp-web-admin
  sudo ./update.sh

If the clone already exists:
  cd atp-web-admin
  ./pull-update.sh

That is: git pull --ff-only origin main && sudo ./update.sh
Then hard-refresh the browser (Ctrl+Shift+R).

Check
  Global Directory: green SPA, Show columns, Sort columns,
  contacts per page, "N contacts" / "Showing X of Y"
  Edit + Save works
  Lines search: type 24, table shows 2400...
  Line dropdown: 6000--1 / 6000--2
