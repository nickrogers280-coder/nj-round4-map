# Owner names (data/owners.csv)

The statewide NJOGIS parcel layer blanks OWNER_NAME (Daniel's Law), and the statewide
NJACTB search was shut down in 2023. Owner of record comes from each county tax board's
own search, by block and lot. These sites block cloud servers (GitHub Actions times out),
so lookups run in Nick's Chrome, with the search page open in a tab.

| County | Search | Status |
|---|---|---|
| Monmouth | https://tax1.co.monmouth.nj.us/cgi-bin/prc6.cgi?menu=index&ms_user=monm&passwd=data&district=1301&mode=11 (POST to inf.cgi: srch_type=1, select_cc=1301, district=<mun code>, block, lot, ms_ln=1000) | Done 2026-11-24 (346 lots) |
| Bergen | https://taxrecords-nj.com/pub/cgi/prc6.cgi?district=0200&ms_user=ctb02 | To do (Nick setting up Chrome) |
| Morris | https://mcweb1.co.morris.nj.us/MCTaxBoard/SearchTaxRecords.aspx | To do (Nick setting up Chrome) |

`data/owners.csv` columns: mun_code, block, lot, qual, owner_name, source, retrieved.
The Map sites workflow merges it into each lot's `owner` (matched on MOD-IV code + block + lot)
and shows "Tax record: <source>, <date>" under the name.
