# Prosument (net-metering) dla Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/)
[![Validate](https://github.com/mryndak/ha_prosument/actions/workflows/validate.yml/badge.svg)](https://github.com/mryndak/ha_prosument/actions/workflows/validate.yml)

Integracja dla prosumentów rozliczanych w systemie opustów (net-metering, współczynnik 0,8 lub 0,7).
Pokazuje na bieżąco, ile energii zostało w wirtualnym magazynie u sprzedawcy, ile przepadnie
i za ile kWh trzeba będzie zapłacić.

Co pół roku (lub co okres rozliczeniowy) przepisujesz kilka liczb z rachunku, a resztę integracja
dolicza sama z Twojego licznika energii.

*English summary: a Home Assistant integration for Polish net-metering prosumers. You enter
the figures from your bill once per settlement period; the integration tracks the remaining
virtual energy storage, expiring energy and the estimated cost using any grid import/export
energy sensors. The UI is available in Polish and English.*

## Spis treści

- [Co potrafi](#co-potrafi)
- [Wymagania](#wymagania)
- [Jaki licznik mogę podłączyć](#jaki-licznik-mogę-podłączyć)
- [Instalacja](#instalacja)
- [Konfiguracja](#konfiguracja)
- [Wpisywanie kolejnych rachunków](#wpisywanie-kolejnych-rachunków)
- [Sensory](#sensory)
- [Jak to jest liczone](#jak-to-jest-liczone)
- [Wygasanie energii](#wygasanie-energii)
- [Porównanie rachunku z licznikiem](#porównanie-rachunku-z-licznikiem)
- [Przykładowa karta](#przykładowa-karta)
- [Rozwiązywanie problemów](#rozwiązywanie-problemów)
- [Ograniczenia](#ograniczenia)

## Co potrafi

- Liczy stan magazynu: energia z rachunku + oddana × współczynnik − pobrana.
- Uwzględnia bilansowanie godzinowe, tak jak rozlicza operator.
- Pilnuje terminu ważności energii (domyślnie 12 miesięcy) i pokazuje, co i kiedy przepadnie.
- Szacuje koszt energii pobranej ponad magazyn.
- Porównuje rachunek z danymi z Twojego licznika.
- Działa wstecz: rachunek przychodzi po czasie, a integracja i tak policzy wszystko od końca
  okresu rozliczeniowego, bo korzysta ze statystyk zapisanych w Home Assistant.
- Konfiguracja w całości z interfejsu, bez YAML-a.

## Wymagania

- Home Assistant 2024.12 lub nowszy.
- Dwa sensory energii z punktu przyłączenia do sieci: pobór i oddanie.
- Sensory muszą mieć statystyki długoterminowe, czyli `device_class: energy`
  oraz `state_class: total_increasing` lub `total`. Jeśli sensor da się dodać do panelu
  Energia jako „pobór z sieci” lub „oddanie do sieci”, to nadaje się także tutaj.

## Jaki licznik mogę podłączyć

Integracja nie rozmawia z żadnym urządzeniem. Czyta wyłącznie dwie encje Home Assistanta,
więc zadziała z każdym miernikiem, który ma własną integrację. Jednostką może być kWh, Wh lub MWh.

| Źródło danych | Uwagi |
|---|---|
| Miernik dwukierunkowy przy liczniku (np. Chint DTSU-666, Eastron SDM, Shelly 3EM / Pro 3EM) | Najlepsza opcja: mierzy w punkcie przyłączenia. |
| Falownik z własnym miernikiem sieci (np. Huawei, Sungrow, Deye, SolarEdge, Fronius) | Użyj sensorów „grid import / export”, a nie produkcji paneli. |
| Odczyt licznika operatora (port P1 / optyczny, integracje portali operatorów) | Dane najbliższe rachunkowi; portale bywają opóźnione o dobę. |

Trzy rzeczy, na które warto uważać:

- **Miejsce pomiaru.** Miernik musi stać między siecią a całą instalacją. Sensory produkcji
  paneli i zużycia domu nie wystarczą.
- **Bilansowanie międzyfazowe.** Licznik operatora sumuje trzy fazy w każdej chwili.
  Miernik, który liczy import i eksport osobno dla każdej fazy, zawyży obie wartości.
  Mierniki trójfazowe z sumarycznym licznikiem energii (jak DTSU-666) liczą poprawnie.
- **Jeden sensor ze znakiem.** Jeśli masz tylko moc w watach (dodatnia = pobór, ujemna = oddanie),
  zrób z niej dwa liczniki: dwa sensory szablonowe (część dodatnia i ujemna) oraz dwa pomocniki
  „Całka Riemanna”.

## Instalacja

### HACS (zalecane)

1. HACS → menu z trzema kropkami → **Niestandardowe repozytoria**.
2. Wklej `https://github.com/mryndak/ha_prosument`, typ: **Integracja**, kliknij Dodaj.
3. Wyszukaj „Prosument”, kliknij **Pobierz**.
4. Zrestartuj Home Assistant.

### Ręcznie

1. Pobierz repozytorium i skopiuj folder `custom_components/prosument`
   do `/config/custom_components/` w Home Assistant.
2. Zrestartuj Home Assistant.

## Konfiguracja

**Ustawienia → Urządzenia i usługi → Dodaj integrację → Prosument.**

### Krok 1: ustawienia

| Pole | Opis | Domyślnie |
|---|---|---|
| Energia pobrana z sieci | Licznik narastający poboru. | – |
| Energia oddana do sieci | Licznik narastający oddania. | – |
| Współczynnik odzysku | 0,8 dla instalacji do 10 kWp, 0,7 powyżej. | 0,8 |
| Cena 1 kWh brutto | Energia plus opłaty zmienne dystrybucji. Tylko do szacowania kosztu. | 1,10 zł |
| Ważność energii w magazynie | Po ilu miesiącach niewykorzystana energia przepada. 0 wyłącza wygasanie. | 12 |
| Bilansowanie godzinowe | W każdej godzinie liczy się tylko saldo poboru i oddania. | włączone |

### Krok 2: dane z ostatniego rachunku

| Pole | Opis | Wymagane |
|---|---|---|
| Początek okresu rozliczeniowego | Pierwszy dzień okresu z rachunku. Potrzebny do porównania rachunku z licznikiem. | nie |
| Koniec okresu rozliczeniowego | Ostatni dzień okresu z rachunku. Liczenie startuje od następnego dnia. | tak |
| Energia pozostała do wykorzystania | Ile kWh zostało w magazynie po rozliczeniu, **już po uwzględnieniu współczynnika**. | tak |
| Energia pobrana wg rachunku | Ilość za cały okres, po bilansowaniu. | nie |
| Energia oddana wg rachunku | Ilość za cały okres, po bilansowaniu, przed współczynnikiem. | nie |

Jeśli rachunek podaje pozostałą energię przed współczynnikiem (surową energię oddaną),
przemnóż ją przez 0,8 lub 0,7 przed wpisaniem.

Nie masz jeszcze rachunku? Podaj wczorajszą datę i magazyn 0. Integracja zacznie liczyć od dziś.

## Wpisywanie kolejnych rachunków

**Ustawienia → Urządzenia i usługi → Prosument → Konfiguruj.**

| Opcja | Co robi |
|---|---|
| Wpisz nowy rachunek | Dodaje rozliczenie. Od tej chwili liczenie startuje od nowego końca okresu. |
| Ustawienia | Zmiana encji, współczynnika, ceny, ważności energii i bilansowania. |
| Usuń ostatni rachunek | Cofa ostatni wpis; obliczenia wracają do poprzedniego rachunku. |

Pomyłka w rachunku? Wpisz go jeszcze raz z tą samą datą końca okresu, a zastąpi poprzedni wpis.
Integracja pamięta 12 ostatnich rachunków.

## Sensory

Wszystkie sensory należą do jednego urządzenia „Prosument”.

### Stan magazynu

| Sensor | Znaczenie |
|---|---|
| Magazyn – energia pozostała | kWh, które możesz jeszcze pobrać bez opłaty. |
| Energia do zapłaty | kWh pobrane ponad magazyn. |
| Szacowany koszt | Energia do zapłaty × cena 1 kWh. |
| Saldo magazynu | Wartość ze znakiem: dodatnia to zapas, ujemna to energia do zapłaty. |

Sensor „Magazyn – energia pozostała” ma w atrybutach datę startu liczenia, stan z rachunku,
współczynnik, cenę oraz listę partii energii z datami ważności.

### Wygasanie

| Sensor | Znaczenie |
|---|---|
| Najbliższe wygaśnięcie | Data, po której przepadnie najstarsza partia energii. |
| Energia wygasająca najbliżej | Ile kWh z tej partii jest jeszcze niewykorzystane. |
| Energia wygasła od rozliczenia | kWh, które przepadły od ostatniego rachunku. |

### Liczniki od ostatniego rozliczenia

| Sensor | Znaczenie |
|---|---|
| Pobrana od rozliczenia | Pobór po bilansowaniu godzinowym. |
| Oddana od rozliczenia | Oddanie po bilansowaniu godzinowym, przed współczynnikiem. |
| Pobrana od rozliczenia (licznik) | Surowy przyrost licznika, do porównania ze wskazaniami na rachunku. |
| Oddana od rozliczenia (licznik) | Jak wyżej, dla oddania. |

### Kontrola rachunku

| Sensor | Znaczenie |
|---|---|
| Rachunek – różnica magazynu | Magazyn z rachunku minus magazyn wyliczony z licznika. |
| Rachunek – różnica energii pobranej | Pobór z rachunku minus pobór z licznika za okres rachunku. |
| Rachunek – różnica energii oddanej | Oddanie z rachunku minus oddanie z licznika za okres rachunku. |

## Jak to jest liczone

1. Integracja pobiera z rejestratora godzinowe przyrosty obu liczników, od dnia po końcu
   okresu z ostatniego rachunku do teraz.
2. Przy włączonym bilansowaniu w każdej godzinie zostaje tylko saldo. Przykład: w godzinie
   pobrano 2,0 kWh i oddano 0,5 kWh, więc liczy się 1,5 kWh poboru i 0 kWh oddania.
3. Stan magazynu:

   ```
   saldo = magazyn z rachunku + oddana × współczynnik − pobrana
   ```

4. Saldo dodatnie to „energia pozostała”. Saldo ujemne to „energia do zapłaty”,
   a po przemnożeniu przez cenę: „szacowany koszt”.

Przykład: na rachunku zostało 800 kWh, od tego czasu oddałeś 500 kWh i pobrałeś 1000 kWh.
`800 + 500 × 0,8 − 1000 = 200 kWh` zostaje w magazynie.

Dane odświeżają się co 10 minut. Home Assistant zapisuje statystyki po zamknięciu każdej
godziny, więc bieżąca godzina pojawia się z opóźnieniem do około godziny.

## Wygasanie energii

Energia w magazynie ma termin ważności, liczony od końca okresu rozliczeniowego, w którym
do niego trafiła. Przy ustawieniu 12 miesięcy nadwyżka z okresu do 30.06.2026 jest ważna
do 30.06.2027.

- **Partie.** Magazyn dzieli się na partie na podstawie kolejnych rachunków. Jeśli stan wzrósł
  względem poprzedniego rachunku, przyrost jest nową partią. Jeśli spadł, energia schodzi
  od najstarszej partii.
- **Kolejność.** Pobór ponad bieżące oddanie zużywa najpierw najstarszą partię.
- **Przepadanie.** W dniu ważności niewykorzystana część partii przepada. Nadwyżka z bieżącego
  okresu jej nie ratuje.

Przykład: masz partię 300 kWh ważną do 31.12 i 500 kWh ważne do 30.06. Do końca grudnia
pobrałeś netto 100 kWh. Z pierwszej partii przepada 200 kWh, a w magazynie zostaje 500 kWh.

Przy jednym wpisanym rachunku cały magazyn jest jedną partią. Jeśli część energii pochodzi
z wcześniejszego okresu, wpisz także poprzedni rachunek, a podział wyliczy się sam.

## Porównanie rachunku z licznikiem

Żeby sprawdzić rachunek, wpisz go z początkiem okresu oraz energią pobraną i oddaną.
Sensory „Rachunek – …” pokażą różnicę między rachunkiem a licznikiem, a w atrybutach
znajdziesz komplet liczb z obu źródeł.

- Różnice energii pobranej i oddanej działają od pierwszego rachunku, o ile Home Assistant
  ma statystyki licznika z tamtego okresu.
- Różnica magazynu wymaga dwóch rachunków, bo potrzebuje stanu początkowego.
- Kilka procent różnicy jest normalne: miernik domowy ma inną klasę dokładności niż licznik
  operatora i stoi w innym miejscu instalacji.

## Przykładowa karta

Nazwy encji mogą się u Ciebie różnić; sprawdź je na stronie urządzenia „Prosument”.

```yaml
type: entities
title: Prosument
entities:
  - entity: sensor.prosument_magazyn_energia_pozostala
  - entity: sensor.prosument_energia_do_zaplaty
  - entity: sensor.prosument_szacowany_koszt
  - entity: sensor.prosument_najblizsze_wygasniecie
  - entity: sensor.prosument_energia_wygasajaca_najblizej
```

## Rozwiązywanie problemów

| Objaw | Przyczyna i rozwiązanie |
|---|---|
| Licznika nie ma na liście wyboru | Encja nie ma `device_class: energy`. Popraw integrację miernika lub dodaj klasę w dostosowaniu encji. |
| Błąd „nie jest licznikiem narastającym” | Encja nie ma `state_class`, więc HA nie zapisuje statystyk. |
| Magazyn się nie zmienia | Sprawdź atrybut `godzin_w_statystykach`. Zero oznacza brak statystyk od daty rozliczenia. |
| Wartości zaczynają się później niż rachunek | Miernik został dodany do HA po końcu okresu rozliczeniowego; wcześniejszych danych nie ma. |
| Pobór i oddanie są zamienione | Zamień encje w Konfiguruj → Ustawienia. |
| Duża różnica względem rachunku | Sprawdź miejsce montażu miernika i bilansowanie międzyfazowe (patrz wyżej). |

## Ograniczenia

- Obsługuje wyłącznie system opustów (net-metering). Net-billing nie jest obsługiwany.
- Podział magazynu na partie wynika z wpisanych rachunków, a nie z danych sprzedawcy,
  więc daty i ilości wygasającej energii są przybliżeniem.
- Koszt to szacunek z jednej ceny za kWh. Nie uwzględnia opłat stałych ani taryf strefowych.
- Rozliczenie następuje za cały okres, więc saldo w trakcie okresu to stan na dziś, a nie prognoza.

Projekt nie jest powiązany z żadnym sprzedawcą ani operatorem energii. Wiążące są dane z rachunku.

## Rozwój

```
python tests/test_calc.py
```

## Licencja

Copyright (C) 2026 Mateusz Ryndak

Program jest wolnym oprogramowaniem na licencji [GNU GPL v3](LICENSE). Możesz go używać, zmieniać i rozpowszechniać,
pod warunkiem że wersje pochodne pozostaną na tej samej licencji i będą udostępniane
z kodem źródłowym. Program jest udostępniany bez żadnej gwarancji.
