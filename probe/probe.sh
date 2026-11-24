#!/bin/bash
# One-off: see what the county tax-board record search returns for Little Ferry (0230) Block 25 Lot 2.
mkdir -p probe/out
UA="Mozilla/5.0 (X11; Linux x86_64) nj-round4-map research"
curl -sSL -A "$UA" --max-time 30 "http://tax1.co.monmouth.nj.us/cgi-bin/prc6.cgi?menu=index&ms_user=monm&passwd=data&district=0230&mode=11" -o probe/out/menu.html -w "menu %{http_code}\n"
curl -sSL -A "$UA" --max-time 30 "http://tax1.co.monmouth.nj.us/cgi-bin/inf.cgi?ms_user=monm&passwd=data&srch_type=1&out_type=1&district=0230&block=25&lot=2" -o probe/out/inf.html -w "inf %{http_code}\n"
curl -sSL -A "$UA" --max-time 30 "http://tax1.co.monmouth.nj.us/cgi-bin/prc6.cgi?ms_user=monm&passwd=data&srch_type=1&adv=1&out_type=1&district=0230&block=25&lot=2" -o probe/out/prc6.html -w "prc6 %{http_code}\n"
ls -la probe/out
