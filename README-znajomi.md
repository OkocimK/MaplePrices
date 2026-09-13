# Tracker cen scrolli (Old School Maple)

Program robi zrzuty okna gry, gdy przeglądasz sklepiki na FM, odczytuje z nich nazwy i ceny
i wysyła je na wspólną stronę https://osmsfm.duckdns.org/. Nie dotyka gry: nie czyta jej
pamięci, nie klika, nie wysyła pakietów. To tylko zrzut ekranu i OCR.

## Instalacja (raz)

1. Zainstaluj Pythona 3.12 z https://www.python.org/downloads/ (zaznacz „Add python.exe to PATH").
2. Rozpakuj ten folder gdziekolwiek, uruchom `install.bat`, poczekaj.

## Użycie

1. Gra w oknie (nie pełny ekran), rozdzielczość 1920×1080. Przy innej program nic nie odczyta.
2. Uruchom `run.bat`. Przy pierwszym starcie poda adres serwera i zapyta o **token** (dostaniesz
   od właściciela strony) i **nick** (podpis Twoich danych).
3. **F9** włącza zbieranie, drugi raz **F9** wyłącza. Włączaj tylko wtedy, gdy przeklikujesz
   sklepiki. Kursor może leżeć na oknie sklepu, wiersz pod kursorem jest pomijany i dobierany
   w następnej klatce.
4. **Ctrl+F9** kończy. Podgląd na żywo: http://localhost:8778/ w przeglądarce.

Dane idą na serwer co 5 minut podczas zbierania i po wyłączeniu F9. Gdy nie ma sieci, zostają
lokalnie w `data/` i pójdą przy następnej okazji.

## Co jest zbierane

Nazwa przedmiotu, cena, czy oferta jest wykupiona (wyszarzona), nick właściciela sklepu, tytuł
sklepu, mapa i kanał, czas do zniknięcia sklepu, mały wycinek wiersza (obrazek) do sprawdzenia
odczytu, oraz Twój nick jako podpis. Nic z Twojej postaci ani z czatu.
