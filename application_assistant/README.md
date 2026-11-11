# Lokal søknadsassistent

Dette er en lokal søknadsassistent for Google Chrome. GPT tolker ulike skjemastrukturer, mens lokal kode validerer planen og utfører bare handlingene brukeren har valgt:

```text
Brukerklikk i Chrome
→ synlige felt leses
→ GPT tolker felt, grupper og alternativer
→ localhost validerer planen mot lokal profil og sikkerhetsregler
→ brukeren kontrollerer og velger
→ valgte felt fylles
→ brukeren sender eventuelt søknaden manuelt
```

Den daglige jobbsøkingen og GitHub Actions kjører uavhengig av denne løsningen.

## Arkitektur og sikkerhet

- `browser_extension/` er en Manifest V3-utvidelse med `activeTab`, `scripting`, lokal lagring og tilgang til `127.0.0.1:8765`. Den har ikke `<all_urls>`-tilgang eller OpenAI-nøkkel.
- `local_service/` lytter bare på localhost, godtar bare den konfigurerte Chrome-utvidelsens origin og krever et tilfeldig lokalt token.
- `local_data/` inneholder den virkelige profilen, CV, karakterutskrifter og eventuelle godkjente brev. Hele mappen er git-ignorert.
- Regelmotoren lager først en sikker reserveplan. GPT tolker deretter alle synlige felt som ikke er kontrollknapper, også ukjente feltnavn, radiofelt og vanlige avkrysningsvalg.
- GPT mottar feltnavn, feltgrupper, hjelpetekst, alternativer, begrenset nettsidetekst, navnene på tilgjengelige profilnøkler og et begrenset sett ikke-sensitive fakta. Kontaktverdiene løses lokalt og sendes ikke til OpenAI av profilen.
- Nettsidetekst og alle feltetiketter sendes som eksplisitt upålitelig data. De kan ikke overstyre tjenestens instruksjoner.
- API-kall bruker OpenAI Responses API med et strengt JSON Schema, `store=False`, ett automatisk nytt forsøk og konfigurerbart tidsavbrudd. Se [offisiell OpenAI-dokumentasjon for Responses API](https://developers.openai.com/api/reference/cli/resources/responses/methods/create).
- Høy sikkerhet og en dokumentert lokal verdi kan forhåndsvelges. Dette gjelder også eksplisitt lagret kjønn og fødselsdato; GPT får bare profilnøkkelen, mens verdien forblir lokal. Generert tekst og middels/lav sikkerhet krever kontroll. Juridiske svar og samtykker aktiveres aldri automatisk.
- CV og karakterutskrift hentes først fra localhost når brukeren trykker **Fyll inn valgte felt**. Dokumentinnhold sendes ikke til GPT, men nettleseren kan laste det opp til den åpne arbeidsgiversiden etter dette klikket. Bare allowlistede dokumenttyper under `local_data/`, maksimalt 10 MB, kan hentes.
- Dersom API-et mangler eller feiler, beholdes reserveplanen. Kandidatprofilen leses på nytt ved hver analyse, så lokale profilendringer krever ikke omstart.

## 1. Installer Python-avhengigheter

Fra repositoryroten:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Opprett lokal kandidatprofil

```bash
mkdir -p application_assistant/local_data
cp application_assistant/examples/candidate_profile.example.json \
  application_assistant/local_data/candidate_profile.json
```

Rediger `application_assistant/local_data/candidate_profile.json` og erstatt alle eksempelverdier med dokumenterte opplysninger. Legg standard-CV og karakterutskrift i samme mappe, og oppdater `documents.standard_cv` og `documents.bachelor_transcript` med filnavnene. Dokumenter utenfor `local_data/` blir ikke utlevert av localhost-tjenesten.

Profilen støtter:

- navn, e-post, telefon, bosted, LinkedIn, eksplisitt kjønn og fødselsdato
- skole, grad, studiestart, normert varighet og forventet fullføring
- dokumentert arbeidserfaring
- språk, ferdigheter og tilgjengelighet
- godkjente standardsvar
- filnavn til standard-CV, bachelorutskrift og eventuell masterutskrift

Ikke legg passord, innloggingsdata, fødselsnummer eller andre unødvendige sensitive opplysninger i profilen.

## 3. Installer Chrome-utvidelsen

1. Åpne `chrome://extensions` i Google Chrome.
2. Slå på **Developer mode**.
3. Velg **Load unpacked**.
4. Velg mappen `application_assistant/browser_extension`.
5. Kopier utvidelsens 32 tegn lange ID fra kortet i Chrome. ID-en brukes til å låse localhost-tjenesten til akkurat denne utvidelsen.

## 4. Start localhost-tjenesten

Bytt ut `DIN_UTVIDELSES_ID` med ID-en fra Chrome:

```bash
source .venv/bin/activate
python -m application_assistant.local_service.server \
  --extension-id DIN_UTVIDELSES_ID
```

Ved første oppstart opprettes et tilfeldig token i `application_assistant/local_data/service_token.txt` med lokale filrettigheter `0600`. Vis tokenet i en annen terminal:

```bash
cat application_assistant/local_data/service_token.txt
```

Åpne Jobbagent-utvidelsen, lim tokenet inn i **Lokalt tjenestetoken**, og trykk **Lagre**. Dette er ikke OpenAI-nøkkelen.

## 5. Start den lokale testportalen

I en ny terminal fra repositoryroten:

```bash
python3 -m http.server 8000 --directory application_assistant/test_site
```

Åpne [http://127.0.0.1:8000](http://127.0.0.1:8000) i Chrome.

## 6. Gjennomfør første sikre test

1. Trykk på Jobbagent-utvidelsen og velg **Fyll ut med Jobbagent**.
2. Kontroller oversikten over automatiske felt, forslag, manglende svar og dokumenter.
3. Dokumenterte profilfelt som GPT tolker med høy sikkerhet, kan være valgt som standard. Generert tekst og usikre forslag er aldri valgt som standard.
4. Skriv eller velg juridiske og andre svar som profilen mangler. Eksplisitt lagrede personopplysninger kan fylles lokalt; samtykker velges alltid av deg.
5. Trykk **Fyll inn valgte felt**. Valgt CV og karakterutskrift kan nå bli overført til den åpne arbeidsgiversiden.
6. Kontroller verdiene og dokumentnavnene direkte i siden. Samtykker skal fortsatt være urørt.
7. Ikke trykk send for å teste Jobbagent. Utvidelsen trykker aldri send; testsiden blokkerer dessuten innsending.

## GPT-basert feltforståelse

GPT-tolkning er aktivert som standard for søknadsassistenten, men krever en lokal API-nøkkel. Regelmotoren fungerer fortsatt uten nøkkel. Start tjenesten slik for full feltforståelse:

```bash
export OPENAI_API_KEY="din-openai-nøkkel"
export APPLICATION_ASSISTANT_MODEL="gpt-5.6-terra"
export APPLICATION_ASSISTANT_TIMEOUT_SECONDS="20"
python -m application_assistant.local_service.server \
  --extension-id DIN_UTVIDELSES_ID
```

`APPLICATION_ASSISTANT_AI` kan settes til `false` for å slå av API-kall. OpenAI mottar skjemakonteksten, navnene på tilgjengelige profilnøkler og et begrenset sett med utdanning, erfaring, språk, ferdigheter, tilgjengelighet og ikke-sensitive standardsvar. Kontaktverdier, CV-filen, OpenAI-nøkkelen og full kandidatprofil sendes ikke fra profilen. Nettsiden kan likevel selv vise personopplysninger i synlig tekst, så forhåndsvis hva slags portal du bruker. Ved manglende nøkkel, ugyldig svar, API-feil eller timeout fortsetter den sikre regelmotoren å fungere.

GPT returnerer én strukturert tolkning per synlig felt: betydning, handling, eventuell profilnøkkel, sikkerhetsnivå, sensitivitet, begrunnelse og dokumentasjonsnøkler. Lokal kode avviser ukjente felt-ID-er, oppdiktede profilnøkler, ugyldige alternativer og forsøk på å fylle samtykke eller sende skjemaet.

## Tester

```bash
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s application_assistant/tests -p 'test_*.py' -v
node --test application_assistant/tests/browser_extension.test.js
```

## Støttet i første versjon

- Google Chrome og den lokale testportalen
- vanlige tekstfelt og tekstområder
- tekstfelt, tekstområder, nedtrekkslister, radiogrupper og ikke-sensitive avkrysningsvalg
- GPT-tolkning basert på etikett, feltgruppe, hjelpetekst, alternativer og omkringliggende tekst
- lokale profilverdier uten at kontaktverdiene må sendes til modellen
- lokal utfylling av eksplisitt dokumentert kjønn, fødselsår, e-post og telefon uten å sende verdiene til GPT
- utledning av fullføringsår fra dokumentert studiestart og normert varighet
- redigerbare svar for øvrige manglende og sensitive felt
- forhåndsvisning og eksplisitt brukerutløst utfylling
- brukerutløst opplasting av konfigurert CV og bachelorutskrift fra den private lokalmappen
- AI-generering av redigerbare, sporbare forslag med lokal validering
- kontroll av faktisk feltverdi umiddelbart etter utfylling

## Bevisst utsatt

- automatisk opplasting uten det eksplisitte klikket **Fyll inn valgte felt**
- automatisk pålogging, CAPTCHA eller tofaktorautentisering
- garantert støtte for alle egendefinerte kontroller, iframe-baserte skjemaer eller lukkede shadow DOM-komponenter
- innlesing av PDF-/Word-CV og tidligere søknadsbrev
- automatisk innsending eller aksept av vilkår
- støtte for Firefox og Safari

Før en ekte portal prøves, bør vi velge én offentlig testannonse og kontrollere feltmappingen uten å sende søknaden.
