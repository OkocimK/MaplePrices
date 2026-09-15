# Tracker cen scrolli (Old School Maple)

Jeden plik: `osmsfm-tracker.exe`. Robi zrzuty okna gry, gdy przeglądasz sklepiki na FM,
odczytuje z nich nazwy i ceny i wysyła na wspólną stronę https://osmsfm.duckdns.org/.
Nie dotyka gry: nie czyta jej pamięci, nie klika, nie wysyła pakietów. Zrzut ekranu i OCR.

## Użycie

1. Wrzuć `osmsfm-tracker.exe` do dowolnego folderu (obok powstanie `data\` z lokalną kopią).
2. Gra w oknie (nie pełny ekran), rozdzielczość 1920×1080. Przy innej program nic nie odczyta.
3. Uruchom exe. Windows przy pierwszym razie może pokazać SmartScreen („nieznany wydawca"):
   „Więcej informacji", „Uruchom mimo to". Antywirus też bywa czujny na PyInstallera.
4. **F9** włącza zbieranie, drugi raz **F9** wyłącza. Włączaj tylko, gdy przeklikujesz
   sklepiki. Kursor może leżeć na oknie sklepu, wiersz pod kursorem jest pomijany i dobierany
   w następnej klatce.
5. **Ctrl+F9** kończy. Podgląd na żywo: http://localhost:8778/ w przeglądarce.

Nic nie trzeba wpisywać: adres serwera i klucz są w programie. Dane są anonimowe: program
losuje sobie identyfikator w rodzaju `anon-3f9c2a` i nim podpisuje wysyłkę. Jeśli chcesz się
podpisać nickiem, wpisz go w `data\config.json` (pole `client`).

Dane idą na serwer co 5 minut podczas zbierania i po wyłączeniu F9. Bez sieci zostają
lokalnie w `data\` i pójdą przy następnej okazji.

## Co jest zbierane

Nazwa przedmiotu, cena, czy oferta jest wykupiona (wyszarzona), nick właściciela sklepu, tytuł
sklepu, mapa i kanał, czas do zniknięcia sklepu, mały wycinek wiersza (obrazek) do sprawdzenia
odczytu, oraz losowy identyfikator programu. Nic z Twojej postaci, konta ani z czatu.
