enePath webadmin — Line dropdown appearances
===========================================

This archive is the finished routers/lines.py. No patch. No scripts.

On the AMP box:

  cd ~/atp/deploy/webadmin
  tar xzf enepath-mla-lines.tgz
  sudo ./update.sh

If you have no update.sh:

  sudo cp routers/lines.py /opt/enepath/webadmin/routers/lines.py
  sudo systemctl restart enepath-webadmin

Check: Mc.Gyver button Line dropdown should list 6000--1, 6000--2 (not 6000).
