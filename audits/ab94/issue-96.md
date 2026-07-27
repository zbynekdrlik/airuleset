## Čo sa stalo

Hneď po tom, ako #92 zapol tvrdé blokovanie v `stop-check-prose-violations.sh`
(commit `c7d935b`), gate zablokoval **správu supervízora, ktorá o tom pravidle
iba REFEROVALA** — vymenovala zakázané tvary v prehľade toho, čo hook odteraz
blokuje. Žiadna obchádzka nebola ponúknutá; text bol popis, nie ponuka.

## Prečo je to presne opakovanie #80 a #91

Rovnaká trieda chyby, tretíkrát:

- **#80** — `block-main-implementation.sh` klasifikoval telo heredocu ako príkaz.
- **#91** — injekčný hook sa spustil na vlastnom `gh issue comment`, ktorého telo
  iba POPISOVALO spúšťaciu tabuľku (65 KB injekcie).
- **teraz** — Stop gate klasifikuje správu, ktorá o zakázaných frázach REFERUJE,
  ako keby ich ponúkala.

Vzor: klasifikátor nerozlišuje **použitie** od **zmienky** (use vs mention).

## Dôsledok

Nemožno referovať o vlastných pravidlách. Každý status, completion report alebo
playbook zápis, ktorý cituje, čo hook blokuje, je sám zablokovaný. To priamo
bije na `project-playbook-maintenance.md` a na `/mdreview`, ktorých náplňou je
o pravidlách písať.

## Akceptačné kritérium

RED test = presne tá zablokovaná správa (popis, nie ponuka) prejde.
GREEN nesmie otvoriť dieru: skutočná ponuka obchádzky musí blokovať ďalej.

Overiť CORPUS REPLAY, nie ručným smoke listom — prehrať reálne historické
assistant správy z transkriptov cez zmenený klasifikátor a vykázať, koľko
predtým prechádzajúcich teraz blokuje a koľko predtým blokovaných teraz prejde.

## Návrh smeru (nie záväzný)

Zmienka má stabilné signály, ktoré ponuka nemá: fráza v backtickoch, v bloku
kódu, v citácii, alebo vedľa mena hooku/modulu. Ponuka je fráza v holej vete
adresovanej používateľovi. Rozhodnúť podľa replay čísel, nie podľa dojmu.

