# Śledzenie cen scrolli w MapleStory Worlds: plan działania

Stan: plan, nic jeszcze nie napisane. Data: 12 września 2026.

## Cel

Wiedzieć, ile wart jest dany scroll, kiedy chcę go kupić albo sprzedać. Źródłem są
**ceny z cudzych sklepików** oglądane ręcznie podczas przeklikiwania Free Marketu. Sowa nie jest
dostępna, więc zapis musi dziać się sam, w tle, gdy ja tylko otwieram kolejne sklepy.

## Wymagania, ustalone

- Osobne okno, może wisieć na drugim monitorze. Nie musi być on top.
- Zwykły zrzut ekranu przez system. Zero ingerencji w klienta, pliki, pamięć, pakiety. Bez
  Overwolfa i bez hooków w proces gry (uzasadnienie: Platform Operation Policy 4-4-4, zakaz
  „unauthorized programs, automated means, interfaces").
- Przełącznik On/Off. Włączone tylko wtedy, gdy przeklikuję sklepiki. Wyłączone znaczy zero
  zrzutów.
- Priorytet: scrolle. Inne przedmioty mogą być zbierane przy okazji, ale nie projektujemy pod nie.
- Miejsce (mapa, kanał, właściciel sklepu): mile widziane, nieobowiązkowe.

## Czego to NIE robi

- Nie klika, nie chodzi, nie otwiera sklepów. Jedyne wejście to obraz ekranu.
- Nie zna cen transakcji, tylko **ceny wystawienia**. „Wartość" scrolla to więc dolna krawędź
  ofert, nie cena, za którą ktoś faktycznie kupił. Trzeba to pamiętać przy sprzedaży.
- Nie działa w trybie pełnoekranowym exclusive. Gra ma chodzić w oknie albo borderless.

## Architektura

Trzy części, wszystkie lokalne, jeden proces Pythona plus strona w przeglądarce.

```
gra (okno)  --zrzut co ~500 ms--> capture --> recognizer --> SQLite --> HTTP :8778 --> viewer.html
                                     ^                                       |
                                     +--------- On/Off (hotkey, przycisk) ---+
```

### 1. Capture

- Znajduje okno gry po tytule (pywin32), bierze jego prostokąt, zrzuca zawartość przez `mss`
  (kopia z kompozytora Windows, tak samo robi OBS w trybie window capture).
- Pętla co ~500 ms, ale tylko gdy przełącznik jest On. Klatka jest odrzucana od razu, jeśli
  hash obrazu nie zmienił się od poprzedniej (nic nowego nie otwarłem).
- Przełącznik: globalny skrót klawiszowy (np. F9, przez `keyboard`) i przycisk w viewerze.
  Stan widać w tytule okna viewera i w kolorze paska.

### 2. Recognizer

Wszystko liczone offline na zapisanych klatkach, żeby dało się kalibrować bez gry.

1. **Detekcja okna sklepu**: template matching (OpenCV) na stałym fragmencie ramki sklepiku
   (pasek tytułu albo róg). Brak dopasowania = klatka do kosza. To jest też filtr „czy w ogóle
   patrzę na sklep".
2. **Podział na wiersze**: offset wierszy względem ramki jest stały w UI gry, więc po znalezieniu
   ramki wiersze wycina się z góry ustalonych prostokątów. Ile wierszy naraz, ustali kalibracja.
3. **Cena**: cyfry i przecinek z fontu gry dopasowywane glif po glifie (template matching, font
   jest stały, bitmapowy). Cel: 100% na próbkach, bo błędna cena jest gorsza niż brak ceny.
   Jeśli font okaże się wygładzany (Unity w MSW), zapasowo Tesseract na wycinku powiększonym 3×
   z whitelistą `0-9,`.
4. **Nazwa przedmiotu**: OCR z wycinka, a potem **dopasowanie do słownika** nazw scrolli
   (odległość Levenshteina do najbliższej znanej nazwy). Słownik jest skończony, około 150 pozycji,
   więc OCR może się mylić w pojedynczych literach i nadal trafimy. Nazwa spoza słownika
   ląduje jako „nieznane" z surowym tekstem, do ręcznego przypisania w viewerze.
   ⚠️ Ikony scrolli nie nadają się do rozpoznawania: 10%, 60% i 100% tej samej rzeczy mają
   identyczną grafikę.
5. **Ilość** (`x N`): to samo co cena, mały słownik glifów.
6. **Deduplikacja**: klucz obserwacji to (właściciel sklepu jeśli czytelny, nazwa, cena, ilość).
   Ten sam klucz w ciągu 10 minut = ta sama oferta, nie dopisujemy. Ten sam klucz po dniu =
   nowa obserwacja, bo „cena się nie zmieniła" też jest informacją.
7. **Ślad do audytu**: każda obserwacja zapisuje wycinek wiersza jako mały PNG. W viewerze
   można podejrzeć, z czego wzięła się liczba, i skasować pomyłkę jednym kliknięciem. Bez tego
   nie będę ufał własnym danym.

### 3. Store i viewer

- SQLite, dwie tabele:
  - `items(id, name, category, aliases)`: słownik scrolli, ręcznie zasiany, rozszerzany z UI.
  - `obs(id, ts, item_id, raw_name, price, qty, owner, map, channel, crop_path)`.
- Mały serwer HTTP w tym samym procesie (`http.server` albo `bottle`, nic większego), endpointy
  `GET /api/items`, `GET /api/obs?item=`, `POST /api/toggle`, `DELETE /api/obs/:id`.
- `viewer.html` w stylu kalkulatora (ten sam papier i pomarańcz, można wziąć `styles.css`):
  - pasek stanu On/Off,
  - tabela scrolli: liczba obserwacji, **najniższa oferta z ostatnich 24 h**, mediana z 7 dni,
    mediana z 30 dni, ostatnio widziany, mały sparkline,
  - po kliknięciu wiersza: lista obserwacji z wycinkami, przycisk usuń, przycisk „przypisz
    nazwę" dla nieznanych,
  - podpowiedź: **kup** poniżej dolnego kwartyla z 7 dni, **sprzedaj** tuż pod najniższą
    aktualną ofertą. To heurystyka, nie prawda objawiona, podpis w UI ma to mówić.

## Etapy

Każdy etap kończy się czymś, co da się sprawdzić bez następnego.

- **M0, próbki.** Skrypt `grab.py`: hotkey zapisuje zrzut okna gry do `samples/`. Zbieram
  20 do 30 zrzutów różnych sklepików ze scrollami, w tym: długie nazwy, ceny 5-, 7- i 9-cyfrowe,
  lista przewinięta, sklep z jednym przedmiotem, sklep bez scrolli. To rozstrzyga wszystkie
  pytania z sekcji „Nieznane" poniżej. **Nic dalej nie ma sensu bez tego.**
- **M1, ramka i wiersze.** Na próbkach: detekcja okna sklepu i wycinanie wierszy. Wynik:
  folder z wycinkami, przeglądam okiem, zero fałszywych ramek.
- **M2, liczby.** Glify cyfr z próbek, dopasowanie, test na wszystkich próbkach z ręcznie
  spisanymi cenami. Cel: 100%. Potem nazwy: OCR plus słownik, cel: każdy scroll z próbek trafia
  do właściwej pozycji.
- **M3, pętla na żywo.** Capture, przełącznik, dedup, zapis do SQLite z wycinkami. Test:
  15 minut chodzenia po FM, potem sprawdzam, czy w bazie jest to, co widziałem, i nic więcej.
- **M4, viewer.** Tabela, szczegóły, usuwanie, przypisywanie nazw, podpowiedzi kup/sprzedaj.
- **M5, miejsce (opcjonalnie).** Nazwa mapy z minimapy (stały róg, stały font), kanał
  z paska, właściciel sklepu z tytułu okna. To samo podejście co ceny, tylko inne prostokąty.

## Co już wiadomo po pierwszym zrzucie (12 września 2026)

Jeden testowy zrzut z `grab.py` na otwartym Hired Merchancie w FM1, okno gry 1920×1009:

- Świat to **Old School Maple**, tytuł okna `MapleStory Worlds-Old School Maple`.
- `mss` widzi okno gry, jasność 169, nie ma czarnego prostokąta. Bez Windows Graphics Capture.
- **UI sklepu jest klasyczny**: lista po lewej, 5 wierszy widocznych naraz, suwak, każdy wiersz
  to ikona, nazwa w pierwszej linii, `[moneta] 3,333,333 mesos` w drugiej. Wykupione pozycje
  są wyszarzone (nadal czytelne, ale to nie jest aktualna oferta, trzeba je odróżniać po kolorze).
- **Font jest wygładzany**, wygląda na Noto Sans w rozmiarze około 16 px, nie bitmapowy. Czyli
  ceny raczej przez Tesseract na powiększeniu, nie przez glify. Do sprawdzenia w M2.
- Pasek tytułu sklepu daje właściciela: `voda's Hired Merchant : S> ...` plus licznik czasu.
  Minimapa w lewym górnym rogu daje mapę i kanał: `Hidden Street Free Market<1>`. Oba stałe
  prostokąty, M5 jest tańsze niż zakładałem.
- Nazwy nie wyglądają na obcinane przy tej szerokości (`[Mastery Book] Boomerang Step` weszło
  w całości), ale dłuższe nazwy scrolli trzeba zobaczyć na próbkach.
- **Okno sklepu otwiera się zawsze w tym samym miejscu** (potwierdzone przez użytkownika), więc
  detekcja ramki to tylko tani strażnik „czy sklep jest otwarty", a wiersze wycina się ze
  stałych prostokątów. Zmierzone na zrzucie 1920×1009 (obszar klienta, bez paska tytułu okna):
  - pasek tytułu sklepu: y 170..210, x 480..1445; właściciel po lewej, licznik czasu po prawej,
  - lista: x 480..890, pierwszy separator y=452, **skok wiersza 75 px**, 5 wierszy,
  - w wierszu k (k=0..4): nazwa y 455+75k..480+75k, cena y 490+75k..515+75k, tekst od x=555,
    ikona x 480..550, moneta przed ceną x 555..580,
  - suwak x 865..885, strzałki na górze i dole listy.
  Do potwierdzenia na innych próbkach, że te liczby nie pływają.
- **Wykupione oferty są wyszarzone i to jest osobny, cenny sygnał.** Użytkownik: takie oferty
  często mają zaniżone ceny, bo właśnie dlatego zeszły. Czyli szary wiersz to nie „brak
  oferty", tylko „za tyle ktoś kupił". Rozróżnienie po kolorze tekstu jest trywialne: aktywny
  tekst ma RGB około (42, 44, 46), wyszarzony około (131, 131, 131).

### Wykupione a aktywne, jak to liczyć

Obserwacja dostaje flagę `sold` (0/1). W viewerze dwie osobne kolumny:

- **Asks** (`sold=0`): najniższa oferta z 24 h, mediana 7 dni. To jest „za ile ludzie chcą".
- **Sold** (`sold=1`): mediana i max z 7 dni. To jest „za ile schodzi". Zaniżone, bo tanie
  schodzą pierwsze, więc traktować jako dolną granicę wartości.

Podpowiedzi: **sprzedaj** między medianą sold a najniższym aktywnym askiem (poniżej asków,
powyżej tego, co już zeszło). **Kup** przy cenie bliskiej medianie sold, wszystko poniżej to
okazja. Wykupiony wiersz wisi w sklepie aż właściciel go nie zdejmie, więc dedup po
(właściciel, nazwa, cena, sold) z oknem 10 minut nadal wystarcza.

## Stan po M1 i M2 na 52 próbkach (12 września 2026)

Pliki: `shopframe.py` (geometria, flagi pusty/wykupiony), `glyphs.py` + `price_glyphs.json`
(ceny), `names.py` (nazwy scrolli), `scrolls.json` (lista z osmlib, tylko pomocnicza).

- **Geometria trzyma się we wszystkich 52 próbkach.** Strażnik „sklep otwarty" patrzy na
  separatory listy, wystarczą 4 z 5, bo kursor gry potrafi zasłonić jeden.
- **Ceny: winocr odpadł, dopasowanie glifów zostaje.** Windows OCR czyta nazwy świetnie, ale
  w ~10% wycinków cen zwraca samo „mesos" i gubi liczbę, niezależnie od skali, marginesu,
  binaryzacji i odwrócenia kolorów; do tego dokleja jedynkę z przodu przy „999,999". Wzorce
  cyfr uczone z 197 wierszy, które winocr przeczytał w całości, odczytały wszystkie 237
  wierszy zgodnie z tym, co widać. Kontrola przecinków co trzy cyfry odrzuca wiersz
  z kursorem na liczbie. ⚠️ RapidOCR i winocr w jednym procesie wywalają Pythona po cichu,
  nie łączyć.
- **Nazwy: gramatyka zamiast słownika.** Słownik z osmlib nie zna wielu scrolli z tego świata
  (Earring for LUK, Shield for LUK, Gun, Accuracy, „Overall" bez „Armor", zwykłe 30% i 70%).
  `names.parse` rozbiera `(Dark) scroll for <część> for <stat> <procent>%` i każdy człon
  dopasowuje osobno. Procent z tekstu, a gdy obcięty, z koloru ikony: 10% złoty, 30%
  fioletowy, 60% czerwono-pomarańczowy, 70% szarobrązowy. 100% jeszcze nie widziany.
- **Zostają dwa źródła strat, oba nie z OCR:**
  1. **Kursor gry** rysowany na tekście (musi leżeć na oknie, żeby dało się przewijać).
     Rozwiązanie w M3: pozycja kursora z systemu (`GetCursorPos`), wiersz pod kursorem jest
     w tej klatce pomijany, następna klatka go dobierze.
  2. **Obcięty stat** przy długich nazwach („Dark scroll for Overall Armor for", „Scroll for
     Two-handed Sword for"). Wpis ląduje jako niepełny („? 30%"). Do rozstrzygnięcia
     tooltipem: gra po najechaniu pokazuje pełną nazwę, a wiersz pod kursorem znamy z jego
     pozycji. Wymaga próbek z otwartym tooltipem.
- **Ilość** (cyfra na ikonie) nie czyta się OCR-em wcale. Zrobić jak ceny, wzorce glifów,
  później; na razie zapisujemy bez ilości.

## Stan po M3 i M4 (12 września 2026): pętla, baza, viewer

Pliki: `recognize.py` (klatka -> obserwacje), `tracker.py` (pętla, SQLite, HTTP), `viewer.html`.
Baza i wycinki w `data/` (poza gitem), replay do `data/replay.sqlite` z wycinkami w
`data/replay_crops/`, żywa baza to `data/prices.sqlite` i `data/prices_crops/`.

```
D:\Godot\Maple\pricetrack\.venv\Scripts\python.exe D:\Godot\Maple\pricetrack\tracker.py
```
F9 włącza/wyłącza, Ctrl+F9 kończy, podgląd na http://localhost:8778/ (może stać na drugim
monitorze). Test offline: `tracker.py --replay "samples/*.png" --db data/replay.sqlite --serve`,
to samo siedzi w `.claude/launch.json` jako `pricetrack-replay`.

- Replay 52 próbek: 19 sklepów, 207 obserwacji, 87 scrolli, 8 niepełnych, zero wyjątków.
- Pomijanie wierszy pod kursorem jest napisane (`recognize.CURSOR_BOX`, pozycja z
  `GetCursorPos`), ale **nie było jeszcze sprawdzone na żywo**: próbki nie niosą pozycji
  kursora. Jeśli w żywej bazie będą lądować nazwy w rodzaju „Capur INT", prostokąt kursora
  jest za mały albo hotspot leży gdzie indziej.
- Klatka identyczna z poprzednią (hash) jest pomijana, więc statyczny widok kosztuje jedno
  rozpoznanie. Rozpoznanie klatki to ~7 wywołań winocr plus glify, ok. 150 ms.
- Viewer: ask i sold osobno, podpowiedzi kup/sprzedaj jak w sekcji wyżej, kliknięcie wiersza
  pokazuje obserwacje z wycinkami i przyciskiem usuń. Nazwy z „?" (obcięty stat/procent)
  na czerwono.

## Pierwsza żywa sesja (12 września 2026, 10 minut po FM1..FM6)

1028 ofert z 99 sklepów, 542 scrolle, 163 wykupione, **zero śmieci po kursorze**
(pomijanie wiersza pod kursorem działa). Niepełnych 88 (8,6%). Co z tego wyszło:

- **Ikony bywają z innego wiersza.** W tej samej sekundzie, w tym samym sklepie, „Scroll for
  Gun for ATT 10%" ma czerwoną ikonę 60%, a sąsiedni wiersz złotą. Do tego pusty slot ikony
  przy pełnym tekście. Gra po otwarciu sklepu i po przewinięciu przez chwilę pokazuje nowe
  napisy ze starymi ikonami. Stąd **potwierdzanie**: oferta wchodzi do bazy dopiero, gdy ten
  sam klucz (właściciel, nazwa, cena, wykupiony) pojawi się w dwóch kolejnych rozpoznanych
  klatkach albo klatka nie zmieni się przez jeden okres (`tracker.process`, `process_same`).
  Replay próbek ma `confirm=False`, bo to pojedyncze klatki.
- **Procent z ikony przez `icons.py`**, wzorce chromy (R-G, G-B) uczone z żywej bazy, osobno
  aktywne i wyblakłe. Leave-one-out na 274 wierszach: 250 dobrze, 10 „nie wiem", 14 to złe
  etykiety z punktu wyżej. Kolor średni z `names.pct_from_icon` poszedł do kosza, bo
  fioletowe i brązowe ikony mają za mało nasyconych pikseli, a wyblakłe zlewają się.
  ⚠️ Wzorce trzeba przebudować (`icons.py build data/prices.sqlite`), gdy w bazie będzie
  dużo więcej wierszy albo pojawi się nowa klasa; na razie 100% wykupionych ma 2 próbki.
- **Dark scroll to zawsze 30% albo 70%** (użytkownik). Czerwone i złote ikony przy „Dark
  scroll for Crossbow / Gloves / Knuckler" to ten sam efekt opóźnionego ładowania ikon.
  Dlatego `names.DARK_PCTS`: procent z tekstu spoza 30/70 przy ciemnym scrollu idzie do
  kosza, a ikona ciemnego scrolla może dać tylko 30 albo 70, inaczej „nie wiem"
  (`icons.Icons.pct(..., allowed=)`). `icons.py build` robi dwa przebiegi i wyrzuca próbki
  słabo pasujące do własnej klasy, żeby takie ikony nie psuły wzorców.
- **OCR szarego tekstu gubi końcowy token.** Na 98 wykupionych: jeden wariant ~52 trafień,
  trzy warianty (kontrast, zwykły, skala 2) 73; reszta to obcięte nazwy. `recognize.ocr_name`.
- `reprocess.py` przelicza niepełne wiersze z zapisanych wycinków po zmianie parsera albo
  wzorców; po tej sesji naprawił 34 z 88. Zostały **54 wiersze z obciętym statem** (5%):
  One-Handed Sword, Two-handed Sword, Dark Overall Armor, Dark Two-handed Axe/BW. Tego
  z wiersza nie da się odczytać, to jest zadanie na tooltip (M5b).

## Przedawnianie ofert (12 września 2026)

Licznik w prawym górnym rogu sklepu to **godziny:minuty** (ten sam kupiec pokazywał 40:55
na zrzutach zrobionych kilka minut od siebie). `recognize.parse_timer` czyta go z OCR
(znosi „27215" i „22·.46" po dwukropku), a `Store.add` zapisuje `expires` = czas zrzutu +
licznik. Wiersze bez licznika (nieczytelny OCR, stara baza) dostają domyślne 24 h.
Viewer: kolumna „aktualne" i „ask min teraz" liczą tylko oferty, których sklep jeszcze stoi
**i** których nie widziano później jako wykupione (ostatni stan po kluczu właściciel,
nazwa, cena). Checkbox „tylko z aktualnymi ofertami" chowa resztę; historia zostaje
w medianach z 7 dni i w liście obserwacji (wygasłe na szaro z datą wygaśnięcia).
Kolumna `expires` dochodzi do starej bazy przez `ALTER TABLE` przy starcie.

## Druga żywa sesja (12 września 2026, 45 minut, cały FM na jednym kanale... i dalej)

3650 ofert z 436 sklepów, 1701 scrolli, 714 wykupionych, 5222 klatki rozpoznane.

- **Bezpiecznik na nieświeże ikony zadziałał 308 razy** (5,9% klatek). Resztkowa
  niezgodność ikony z tekstem w zapisanych wierszach: 4 z 683 (0,6%), w pierwszej sesji
  było 5,1%. Wierszy z procentem z ikony w takich klatkach: 2 na 814.
  ⚠️ Jeśli ikona jest trwale źle klasyfikowana (5 takich na 886 w teście), sklep jest
  „nieświeży" przez cały pobyt i wiersze bez procentu w tekście z tego sklepu nie wchodzą.
  Koszt mały, ale gdyby rósł, ograniczyć bezpiecznik do pierwszych klatek po zmianie sklepu.
- **Niepełne 119 z 1701 (7%)**, wszystkie to obcięty stat (One-Handed / Two-handed Sword,
  Dark Overall Armor). Nic tu OCR nie zrobi, to jest M5b.
- **Minimapa przy dwucyfrowym kanale**: OCR skleja linie („Hidden Streeti Free Market<12>"),
  a w 321 wierszach kanał nie wyszedł wcale. `parse_minimap` bierze teraz nazwę od słowa
  „Free" i kanał z ostatniej grupy cyfr; tracker odkłada wycinek minimapy i licznika do
  `data/debug/`, gdy któreś jest nieczytelne, żeby było co obejrzeć.
- Licznik sklepu nieczytelny w 22 wierszach (0,6%), zakres wygaśnięć 15 min do 47,9 h.
- Wzorce ikon przebudowane z 886 wierszy z tej sesji (poprzednie z 274 leżą w temp).

## Publikacja: https://osmsfm.duckdns.org/ i wspólna baza (13 września 2026)

Plan jest taki, że trackera dostają znajomi, więc dane nie mogą jechać przez ssh. Na hoście
chodzi **odbiornik** `server/ingest.py` (biblioteka standardowa, systemd `osmsfm-ingest`,
`www-data`, 127.0.0.1:8781, nginx przekazuje tam `/api/`). Tracker po stronie klienta
wysyła wiersze paczkami po 150 przez HTTPS z tokenem (`sync.py`, config w `data/config.json`,
postęp w `data/sync.json`), z wycinkiem PNG w base64. Serwer deduplikuje po (właściciel,
nazwa, cena, wykupiony) w oknie ±10 min wokół `ts` wiersza, więc ta sama oferta widziana
przez dwie osoby wchodzi raz, zapisuje wycinki do `/var/www/osmsfm/crops/<id serwera>.png`
i po każdej paczce podmienia atomowo `data.json`, z którego czyta `index.html`.

- `store.py` to wspólna baza dla klienta i serwera, bez Pillow. Kolumna `client` = nick.
- Wysyłka w tle (`tracker.Uploader`): co 5 min podczas zbierania, po wyłączeniu F9 i na
  koniec; przy braku sieci wiersze czekają lokalnie. `--no-sync` wyłącza.
- `publish.py` wgrywa już tylko kod serwera, unit, `index.html` i (z `--nginx`) konfigurację;
  zakłada token w `/etc/osmsfm/token`, gdy go nie ma, i wypisuje go. Baza serwera:
  `/var/lib/osmsfm/prices.sqlite`.
- Paczka dla znajomych: `make_dist.py` -> `dist/osmsfm-tracker.zip` (kod, wzorce, viewer,
  `install.bat`, `run.bat`, `README-znajomi.md`). Pierwszy `run.bat` pyta o token i nick.
  Wymaga Pythona 3.12 z python.org; exe przez PyInstaller to opcja na później, winocr
  i pywin32 pakują się kapryśnie.
- Zasiew: lokalna baza z sesji 2 (3650 wierszy) wysłana przez `sync.py` w 46 s.
- Lokalny viewer (:8778) dalej pokazuje lokalną bazę; „usuń" działa tylko lokalnie, serwer
  nie ma jeszcze moderacji. `MemoryCurrent` usługi ~100 MB zaraz po zasiewie (w tym cache
  plików), limit 150 MB w unicie.

## M5b, tooltip dla obciętych nazw (do zrobienia)

Gra po najechaniu na wiersz pokazuje dymek z pełną nazwą. Kursor i tak leży na oknie.
Plan: w klatce z kursorem nad wierszem szukać ramki tooltipa obok kursora, czytać z niej
pierwszą linię, dopasować do wiersza pod kursorem po pozycji, i **dopisać pełną nazwę do
oferty potwierdzonej z tego wiersza** (klucz po cenie i wykupieniu, bo obcięta nazwa jest
niejednoznaczna). Wymaga 3 do 5 zrzutów z `grab.py` z otwartym tooltipem nad scrollem.

## Nieznane, rozstrzyga M0

- Jak wygląda okno sklepu w tym świecie MSW: czy to klasyczny UI, czy autorski, czy nazwy są
  obcinane po N znakach (wtedy słownik jest jeszcze ważniejszy).
- Czy UI skaluje się z rozdzielczością. Jeśli tak, gra ma chodzić w jednej ustalonej
  rozdzielczości, a szablony robimy pod nią.
- Czy font jest bitmapowy (template matching) czy wygładzany (Tesseract).
- Czy widać nazwę właściciela i mapę bez dodatkowych kliknięć.
- Czy `mss` widzi zawartość okna gry, czy dostaje czarny prostokąt. Jeśli czarny: Windows
  Graphics Capture przez pakiet `windows-capture`, nadal bez hooków.

## Stos

Python 3.12, `mss`, `pywin32`, `opencv-python`, `numpy`, `keyboard`, `sqlite3` z biblioteki
standardowej, opcjonalnie `pytesseract`. Viewer: HTML + CSS + JS bez frameworka, jak kalkulator.
Katalog: `D:\Godot\Maple\pricetrack\`. Nie jedzie na serwer, `deploy.ps1` wysyła tylko pięć
plików kalkulatora, więc nic nie trzeba wykluczać.

## Zasady z reszty projektu, obowiązują też tutaj

- Bez em dasha w prozie i UI.
- Plików nie edytować przez PowerShell (cp1250 zjada `×`, `−`, `≤`).
- Rzeczy zgadnięte oznaczać i spisywać, nie zamykać bez pomiaru.
