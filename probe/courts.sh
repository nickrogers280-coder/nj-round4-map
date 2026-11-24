#!/bin/bash
mkdir -p probe/out
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
curl -sSL -A "$UA" --max-time 60 "https://www.njcourts.gov/courts/civil/affordable-housing/atlantic" -o probe/out/atlantic.html -w "atlantic %{http_code} %{size_download}\n"
curl -sSL -A "$UA" --max-time 60 "https://www.njcourts.gov/courts/civil/affordable-housing/monmouth" -o probe/out/monmouth.html -w "monmouth %{http_code} %{size_download}\n"
curl -sSL -A "$UA" --max-time 60 -r 0-2000 "https://library.njcourts.gov/files/civil/affordable-housing/monmouth/red-bank-boro/RedBankcertificationofcomplianceorderApril152026.pdf?VersionId=UZ9zUO.Vs4QDqX7ehIr4i0CQ20Q3.tAD" -o probe/out/pdfhead.bin -w "pdf %{http_code} %{size_download}\n"
ls -la probe/out
