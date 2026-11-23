# AI Connection Discuss Bot (demo)

Claude (tai mikä tahansa `ai_connection`-yhteys) keskustelijaksi Odoon Discussiin ja tietueiden viestiketjuihin.
Tehty LiteLLM-gatewayn kanssa käytettäväksi: Odoo → `ai_connection_openai` → LiteLLM → Claude.
Ks. [../../tower/README_litellm_gateway.md](../../tower/README_litellm_gateway.md).

> **Tila:** demo. Python- ja XML-tiedostot on tarkistettu staattisesti, mutta moduulia ei ole vielä asennettu
> eikä sen testejä ajettu Odoossa.

## Mitä se tekee

| Missä | Milloin botti vastaa | Miten vastaus näkyy |
|---|---|---|
| Yksityiskeskustelu botin kanssa | Jokaiseen viestiin | Viestinä keskustelussa |
| Kanava tai ryhmä, jossa botti on jäsenenä | Kun botti @mainitaan | Viestinä kanavalla |
| Tietueen viestiketju (asiakas, tilaus, tiketti…) | Kun botti @mainitaan | **Sisäisenä muistiinpanona**, joka ilmoittaa kysyjälle. Mitään ei lähetetä tietueen ulkoisille seuraajille. |

- Botille lähetetään viestiketjun viimeiset viestit (oletus 20). Tietueilla mukana ovat myös sähköpostit.
- Ketju luetaan **kysyjän käyttöoikeuksilla**, joten botti ei näe viestejä, joita kysyjä ei näe.
- Botti vastaa vain sisäisille käyttäjille, ei portaalikäyttäjille eikä verkkosivun livechat-vierailijoille.
- Vastaus tehdään taustalla cronissa. Kanavalla botti näkyy kirjoittamassa sillä aikaa.
- Botti voi **lukea** Odoon tietoja työkaluilla (alla) **kysyjän käyttöoikeuksilla**. Se ei voi luoda, muuttaa
  eikä poistaa mitään.

## Työkalut

| Työkalu | Mitä tekee | Esimerkkikysymys |
|---|---|---|
| `get_date` (OCA `ai_tool`) | Päivän päivämäärä | "Mitä myytiin viime kuussa?" |
| `find_models` | Etsii Odoon mallin teknisen nimen | – (botti käyttää itse) |
| `get_model_fields` | Mallin kentät, tyypit ja valintojen arvot | – (botti käyttää itse) |
| `search_records` | Hakee tietueita ja niiden kenttiä hakuehdolla (max 50) | "Mitkä tarjoukset ovat vielä luonnoksena?" |
| `aggregate_records` | Laskee summia ja lukumääriä ryhmiteltynä | "Paljonko myimme asiakkaalle X per kuukausi tänä vuonna?" |
| `get_current_record` | Lukee sen tietueen kentät, jonka viestiketjussa botti mainittiin | "@Claude milloin tämä tilaus on luvattu toimittaa?" |

Työkalut valitaan käyttäjälomakkeella (*AI Bot Tools*). Moduulin luomalla Claude-botilla ne ovat oletuksena kaikki päällä.

### Miten oikeudet on varmistettu

- Jokainen työkalu ajetaan **kysyjän käyttäjänä**: Odoon käyttöoikeudet, tietuesäännöt ja kenttien ryhmärajaukset
  pätevät. Jos kysyjä ei saa nähdä jotain, työkalu palauttaa käyttöoikeusvirheen, ja botti kertoo sen.
- Taustasyy: OCA:n `ai_tool` ajaa työkalurekisterin pääkäyttäjänä, koska tavalliset käyttäjät eivät saa lukea
  `ir.model`-taulua (korjaus 19.0:aan: OCA/ai PR #125). Tämän moduulin työkalut vaihtavat siksi itse kysyjän
  käyttäjäksi, ja ilman kysyjää ne kieltäytyvät.
- Botille kelpaavat **vain** tämän moduulin lukutyökalut ja `get_date`. Esimerkiksi OCA:n `post_message` kirjoittaisi
  pääkäyttäjän oikeuksin, joten se suljetaan pois sekä lomakkeella että ajon aikana.
- Yhdessä kysymyksessä on enintään 10 tekoälykierrosta, ja hakutuloksia rajataan (50 tietuetta, 40 kenttää,
  pitkät tekstit lyhennetään), ettei yksittäinen kysymys kasva hallitsemattoman suureksi.
- Jokainen työkalukutsu argumentteineen ja lopputuloksineen tallentuu kyselylle (*AI Bot Requests > Tool calls*).
  Ylläpitäjä näkee siitä, mitä dataa tekoäly haki.

## Asennus

1. Odoo-jetin `addons.txt`:ään `odoo-addon-ai_connection_openai` ja tämä moduuli addons-polkuun
   (esim. Git Projectiin omana lähteenään).
2. **Request new build** Odoo-jetille.
3. Asenna moduuli **AI Connection Discuss Bot**.

Moduuli tarvitsee Odoon cron-säikeen. Odoo Docker 2.0 -templatessa `odoo_max_cron_threads` = 1 riittää.

## Käyttöönotto

1. **AI Connection** (kehittäjätila, Asetukset > Tekninen > AI > AI Connection): Kind `OpenAI`,
   Url = gatewayn `litellm_internal_url`, Model esim. `claude-sonnet`, API-avaimena Odoolle luotu LiteLLM-avain.
2. **Asetukset > Käyttäjät > Claude > välilehti AI Bot**:
   - **AI Bot Connection** = edellä luotu yhteys. Botti alkaa vastata vasta tämän jälkeen.
   - **AI Bot Tools**: mitä Odoon tietoja botti saa lukea. Tyhjä = ei työkaluja, botti näkee vain viestiketjun.
   - **AI Bot History Limit**: montako viimeisintä viestiä lähetetään mukana.
   - **AI Bot Instructions**: lisäohjeet, esim. yrityksen äänensävy tai asiat, joita botti ei saa luvata.

Botteja voi olla useita: mikä tahansa käyttäjä, jolle on asetettu AI Bot Connection, toimii bottina.
Moduuli luo valmiiksi käyttäjän **Claude** ilman salasanaa, eli sillä ei voi kirjautua.

## Käyttö

- **Discuss > Aloita keskustelu > Claude** ja kirjoita.
- Kanavalla: lisää Claude kanavan jäseneksi ja mainitse `@Claude`.
- Tietueella: kirjoita viestiketjuun esim. `@Claude tiivistä tämän asiakkaan viimeisimmät sähköpostit`.

## Seuranta

**Asetukset > Tekninen > AI > AI Bot Requests** (vain ylläpitäjät): jokainen kysymys, mitä tekoälylle lähetettiin,
työkalukutsut, vastaus, token-määrät ja virheet. Virhetilanteessa käyttäjä saa keskusteluun lyhyen ilmoituksen, ja syy näkyy täällä.

## Tietosuoja

Viestiketjun sisältö, myös asiakkaiden sähköpostit, lähetetään gatewayn kautta Anthropicille. Samoin lähtevät
työkalujen hakemat tiedot, eli kaikki, mitä kysyjä saa Odoossa nähdä ja mitä botti päättää hakea. Varmista, että se on
sovittu asiakkaan kanssa, ennen kuin otat botin käyttöön tuotannossa. Työkalut voi rajata pois botilta kokonaan.

## Rajoitukset ja jatkokehitys

- Vastaus näytetään pelkkänä tekstinä. Markdown-muotoilua ei muunneta, joten järjestelmäkehote pyytää välttämään sitä.
- Liitteitä (PDF, kuvat) ei vielä lähetetä tekoälylle.
- Työkalut ovat lukuoikeuksilla. Kirjoittavat työkalut (esim. tehtävän tai muistiinpanon luonti) vaatisivat
  vahvistuksen käyttäjältä ennen suoritusta.
- `ai_connection`:ssa on virhe: jos työkalu nostaa poikkeuksen, sen tulosta ei lisätä keskusteluun, ja seuraava
  tekoälykutsu kaatuu. Tämän moduulin työkalut eivät siksi koskaan nosta poikkeusta, vaan palauttavat virheen
  tuloksena. Virhe kannattaa ilmoittaa OCA/ai-repoon.

## Testit

Testit mockaavat tekoälykutsun, joten ne eivät tarvitse gatewayta. `tests/test_bot_tools.py` ajaa myös työkalukutsun
`ai_connection`:n oman silmukan läpi valeasiakkaalla ja tarkistaa, että työkalu ajettiin kysyjän käyttäjänä.

```
odoo-bin -d <testikanta> -i ai_connection_discuss_bot --test-tags /ai_connection_discuss_bot --stop-after-init
```
