Našlo sa pri prehratí 687 reálnych gk príkazov cez hook v rámci #80 (tam už nespravené — je to samostatný, úzky tvar).

`hooks/block-main-implementation.sh` klasifikuje príkaz po **statementoch** a v každom statemente už len jeho **prvý stupeň pipeline** (#80). Čo nevidí: príkazovú substitúciu `$( … )`. Ak je vnútri nej pipeline s reducerom, vnútorný `grep`/`awk` sa vyhodnotí ako samostatný statement a zablokuje sa, hoci navonok ide o lacný jednoriadkový výstup.

Reálny prípad z gk (health-check, blokovaný omylom):

```bash
echo "=== liveness ==="
for u in https://erp.montalu.sk/web/health …; do
  printf "%s -> %s\n" "$u" "$(curl -s -o /dev/null -w '%{http_code}' -m 15 "$u")"
done
… "$(curl -s … | grep -oP '(?<=.version.: .)[0-9.]+')"
```

Frekvencia v korpuse: 1 z 687 (0,15 %), takže to loop nezastavuje — ale je to falošný poplach a falošné poplachy sú presne to, čo v #80 viedlo k trvalému vypnutiu hooku.

**Návrh:** v klasifikátore pred segmentáciou vystrihnúť obsah `$( … )` a `` ` … ` `` (rovnako, ako sa už vystrihujú telá heredocov), alebo ho klasifikovať rekurzívne ako vnútorný príkaz s tou istou pipeline logikou (tú už `bash -c '…'` vetva vie). Pridať RED test s tvarom vyššie.

