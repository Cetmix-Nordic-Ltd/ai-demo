# LiteLLM Gateway – käyttöönotto Cetmix Towerissa

Tällä Jet Templatella (`litellm_gateway`) Odoo 18 saadaan käyttämään Anthropicin Claude-malleja OCA:n
[`ai_connection_openai`](https://github.com/OCA/ai/tree/18.0/ai_connection_openai)-moduulin kautta.
LiteLLM on välityspalvelin, joka tarjoaa Claude-mallit OpenAI-yhteensopivan rajapinnan takana, jota moduuli
osaa käyttää.

Template-tiedosto: [`jet_template_litellm_gateway.yaml`](jet_template_litellm_gateway.yaml)

> **Tila:** gateway-template on käytössä testipalvelimella. Ollama-template (luku 11) ja paikallisten mallien
> lisäys `config.yaml`:iin ovat uusia, eikä niitä ole vielä ajettu Towerissa. Rakenne on tarkistettu skriptillä,
> ja `config.yaml` on renderöity esimerkkiarvoilla.

## Arkkitehtuuri lyhyesti

```
Odoo-jet ──(Docker-verkko "tower", http://<gateway>:4000)──► LiteLLM-gateway ──► Anthropic API
                                                                │
                                                                └─► oma tietokanta jaetussa Postgres-jetissä

Selain (vain sallitut IP:t) ──► Traefik (HTTPS) ──► LiteLLM-gateway (/ui ja API)

LiteLLM-gateway ──(Docker-verkko "tower", http://<ollama>:11434)──► Ollama-jet (paikalliset mallit, valinnainen)
```

- Gateway on oma jetinsä. Samalle palvelimelle voi tehdä useita, esimerkiksi yhden asiakasta kohden.
- Jokainen gateway saa oman tietokannan ja roolin palvelimen jaettuun Postgres-jettiin.
- Odoo käyttää gatewayta sisäverkon kautta. Liikenne ei kulje internetin eikä Traefikin kautta.
- Hallintanäkymä julkaistaan Traefikin kautta **vain**, jos jetille on annettu sallitut IP-osoitteet.
- Avaimet annetaan kontille ympäristömuuttujina, eikä niitä tallenneta palvelimen levylle.

## 1. Edellytykset

- Cetmix Tower 18.0, ajan tasalla oleva `cetmix-tower`-koodi.
- Towerissa on **Odoo Docker 2.0** -tuonnin jet-templatet `docker`, `postgres_docker` ja `traefik_docker`.
  Muut tämän templaten käyttämät jaetut tietueet (yleiset jet-komennot, Cloudflare-komennot, jaetut muuttujat,
  salaisuudet ja tagit) ovat YAML-tiedostossa mukana kopioina samoilla viittauksilla. *Skip record* -tuonnissa
  olemassa olevat jäävät ennalleen, ja puuttuvat luodaan.
- Kohdepalvelimella on käynnissä Docker-, Postgres Docker- ja Traefik Docker -jetit.
- **Traefik v3.** IP-rajaus käyttää v3:n `ipallowlist`-middlewarea. Traefik-templaten oletusversio on 3.
- Muuttujalla `jet_root` on globaali arvo, joka on **jetkohtainen hakemisto**, esim.
  `/opt/tower/jets/{{ tower.jet.reference }}`. Destroy poistaa tämän hakemiston, joten tarkista arvo.
- Anthropic API -avain ([console.anthropic.com](https://console.anthropic.com)).

## 2. Templaten tuonti

1. **Cetmix Tower > Tools > Import YAML** ja valitse `jet_template_litellm_gateway.yaml`.
2. **Process**. Rakenteelliset virheet näkyvät tässä vaiheessa.
3. *If Record Exists*: **Skip record**.
4. **Import**.

## 3. Salaisuudet

Kohdassa **Cetmix Tower > Settings > Keys and Secrets** aseta arvot seuraaville salaisuuksille.

**Käytä arvoissa vain kirjaimia ja numeroita** (master-avaimessa lisäksi alun `sk-`). Arvot päätyvät
komentojen lainausmerkkeihin, SQL-lauseisiin ja tietokannan osoitteeseen, joten erikoismerkit rikkovat
komennot. Esimerkkiarvo: `openssl rand -hex 32`.

| Salaisuus | Mihin | Huomio |
|---|---|---|
| `anthropic_api_key` | Oletus-Anthropic-avain | Käytössä kaikilla gatewayilla, joille ei ole annettu omaa |
| `litellm_master_key` | Oletus-pääkäyttäjäavain ja hallintanäkymän salasana | **Pakko alkaa `sk-`**, esim. `sk-` + `openssl rand -hex 32` |
| `litellm_salt_key` | Salaa gatewayn tietokantaan tallennetut tunnukset | **Älä vaihda koskaan käyttöönoton jälkeen** – tallennetut tunnukset muuttuisivat lukukelvottomiksi |
| `litellm_db_password` | Gatewayn Postgres-roolin salasana | Asetetaan uudelleen jokaisessa Buildissa |

Asiakaskohtaiset avaimet, ks. [kohta 5](#5-asiakaskohtaiset-avaimet).

## 4. Gatewayn luonti

1. Asenna template palvelimelle: palvelimen lomake tai templaten **Install**-painike.
2. Avaa **Launch Jet** -velho, valitse **LiteLLM Gateway** ja kohdetilaksi **Running**. Velho ajaa
   Prepare → Build → Start.
3. Aseta tarvittaessa jetin muuttujat ennen Buildia (taulukko alla).

### Jetin muuttujat

| Muuttuja | Oletus | Merkitys |
|---|---|---|
| `litellm_admin_allowed_ips` | tyhjä | Sallitut IP:t tai verkot hallintanäkymälle, pilkulla erotettuna ilman välilyöntejä, esim. `203.0.113.10/32,2001:db8::/48`. Tyhjä = ei julkista reittiä lainkaan. `0.0.0.0/0` ja `::/0` estetään. |
| `litellm_anthropic_key_secret` | `anthropic_api_key` | Minkä salaisuuden arvo on gatewayn Anthropic-avain |
| `litellm_master_key_secret` | `litellm_master_key` | Minkä salaisuuden arvo on gatewayn master-avain |
| `litellm_image` | `ghcr.io/berriai/litellm:main-stable` | LiteLLM-image. **Kiinnitä tuotannossa tietty versio.** |
| `litellm_extra_env` | tyhjä | Lisäympäristömuuttujat, yksi `KEY=VALUE` per rivi, esim. `NUM_WORKERS=2`. Ei salaisuuksia. |

Prepare asettaa seuraavat muuttujat automaattisesti jetin nimestä. Ne voi ohittaa asettamalla arvon jetille ennen Preparea.

| Muuttuja | Esimerkki | Merkitys |
|---|---|---|
| `litellm_db_name`, `litellm_db_user` | `acme_litellm` | Tietokanta ja rooli jaetussa Postgresissa |
| `litellm_internal_host` | `acme-litellm` | Gatewayn nimi Docker-verkossa |
| `litellm_internal_url` | `http://acme-litellm:4000` | **Tämä osoite annetaan Odoolle** |
| jetin URL | `https://jet-acme-litellm.<web_domain>` | Hallintanäkymän osoite. Luodaan vain, jos `web_domain` on asetettu (ja `no_url` ei). DNS, ks. alla. |

### DNS

Hallintanäkymän osoitteelle tarvitaan DNS-tietue, joka osoittaa palvelimen IP-osoitteeseen. Odoon kytkentä ei
tarvitse DNS:ää, koska se kulkee Docker-verkon kautta.

- **Cloudflare:** aseta palvelimelle tai globaalisti `dns_provider = cloudflare` ja salaisuudet `cloudflare_token`
  ja `cloudflare_zone_id`. Prepare luo A-tietueen ja Destroy poistaa sen. Template ei aseta DNS-palveluntarjoajaa
  itse.
- **Muu DNS-palvelin:** jätä `dns_provider` tyhjäksi. Luo A-tietue jetin osoitteelle (tai jokerimerkkitietue
  `*.<web_domain>`) itse ja poista se Destroyn jälkeen. Cloudflare-komennot ja -salaisuudet tulevat tuonnissa
  mukaan, mutta niitä ei ajeta.
- TLS-varmenne haetaan Let's Encryptiltä Traefikin `traefik_acme_mode`-asetuksen mukaan. Oletuksena (`file`)
  HTTP-haaste vaatii, että DNS osoittaa palvelimeen ja portti 80 on auki internetiin. IP-rajaus ei estä haastetta,
  koska se koskee vain HTTPS-reittiä.
- Jos `web_domain` puuttuu, jetille ei tule URL:ia eikä hallintanäkymää julkaista, vaikka IP-rajaus olisi
  asetettu. Silloin URL:n voi antaa jetille käsin ennen Rebuildia.

### Tarkistus

- Jetin lokinäkymä **LiteLLM readiness** näyttää JSONin, jossa `"status": "healthy"` ja tietokantayhteys kunnossa.
- Lokinäkymä **LiteLLM logs** näyttää kontin lokin.

## 5. Asiakaskohtaiset avaimet

Asiakkaille voi tehdä oman gatewayn omalla Anthropic-tilillä ja omalla master-avaimella.

1. Luo salaisuudet, esim. asiakkaalle Acme:
   - `anthropic_api_key_acme` = asiakkaan Anthropic-avain
   - `litellm_master_key_acme` = `sk-…`
2. Luo asiakkaalle oma gateway-jet ja aseta sille ennen Buildia:
   - `litellm_anthropic_key_secret = anthropic_api_key_acme`
   - `litellm_master_key_secret = litellm_master_key_acme`
3. Valmiille gatewaylle muutos tehdään **Stop → Rebuild → Start**.

Säännöt:

- Nimien on alettava `anthropic_api_key` tai `litellm_master_key`. Muuttujaa ei voi suunnata muihin
  salaisuuksiin.
- Build pysähtyy heti alussa, jos nimettyä salaisuutta ei ole, sillä ei ole arvoa tai master-avain ei ala `sk-`.
- Tuontivelho ei näytä asiakaskohtaisia salaisuuksia, koska niiden nimet tulevat muuttujasta. Ne luodaan käsin.
- **Anna jokaiselle asiakkaalle oma master-avain, jos asiakas saa pääsyn hallintanäkymään.** Muuten sama
  avain avaa muidenkin asiakkaiden gatewayt.

Tower 18.0 ei vielä tue jetkohtaisia salaisuusarvoja. Siksi salaisuus valitaan muuttujalla. Ominaisuus on
tulossa (cetmix/cetmix-tower PR #528). Kun se on yhdistetty, saman voi tehdä antamalla
`anthropic_api_key`:lle jetkohtaisen arvon.

## 6. Hallintanäkymä

1. Aseta jetille `litellm_admin_allowed_ips` ja tee **Stop → Rebuild → Start**.
2. Avaa `https://<jetin URL>/ui` ja kirjaudu: käyttäjä `admin`, salasana = gatewayn master-avain.

Muut IP-osoitteet saavat vastaukseksi 403. Rajaus koskee koko gatewayta, sekä hallintanäkymää että API:a.

Tunnetut rajoitukset:

- Rajaus perustuu siihen, että Traefik näkee asiakkaan oikean IP:n. Se toimii IPv4:llä. IPv6-yhteydessä
  Dockerin välityskerros voi näyttää lähteeksi oman osoitteensa, jolloin rajaus estää pääsyn.
- Jos Cloudflaren proxy-tila (oranssi pilvi) kytketään päälle tai eteen tulee muu välityspalvelin, Traefik
  näkee välittäjän IP:n ja rajaus pitää säätää uudelleen.

## 7. Odoon kytkentä

### 7.1 Moduulin asennus Odoo-jettiin

1. Lisää Odoo-jetin `addons.txt`-tiedostoon rivi:
   ```
   odoo-addon-ai_connection_openai
   ```
   Paketti löytyy PyPI:stä ja tuo mukanaan `ai_connection`-moduulin ja `openai`-kirjaston.
   Lähetä muokattu tiedosto palvelimelle jetin tiedostot-välilehdeltä.
2. Odoo-jetin toiminto **Request new build**.
3. Asenna Odoossa moduuli **AI Automation Openai** (`ai_connection_openai`).

### 7.2 Rajattu avain Odoolle

Älä anna Odoolle master-avainta, vaan luo sille oma rajattu avain (LiteLLM:ssä *virtual key*).

**Towerista (ei vaadi hallintanäkymää):**

1. Avaa gateway-jet ja valitse **Run Command** > **Create LiteLLM virtual key**.
2. Anna velhossa ajokohtaiset arvot:

   | Muuttuja | Esimerkki | Pakollinen |
   |---|---|---|
   | `litellm_key_alias` | `odoo-acme-test` | kyllä |
   | `litellm_key_models` | `claude-sonnet,claude-haiku` | ei (tyhjä = kaikki mallit) |
   | `litellm_key_max_budget` | `50` | ei (USD / 30 päivää, tyhjä = ei rajaa) |

3. Aja komento. Tuloste on JSONia, ja uusi avain (`sk-…`) on kentässä `"key"`.

Komento kutsuu gatewayn API:a Docker-verkon sisältä gatewayn master-avaimella, joka pysyy Towerin salaisuutena.
**Uusi avain jää kuitenkin selväkielisenä Towerin komentolokiin**, ja sen näkevät kaikki, jotka saavat lukea
komentolokeja. Avaimen voi perua LiteLLM:n API:lla (`POST /key/delete`, `{"keys": ["sk-…"]}`) tai hallintanäkymästä.

**Hallintanäkymästä** (kun se on julkaistu, ks. kohta 6):

1. **Teams**: tiimi asiakkaalle (valinnainen, helpottaa kulujen seurantaa).
2. **Virtual Keys > Create New Key**: avain Odoo-jetille. Rajaa samalla mallit ja tarvittaessa kuukausibudjetti.

### 7.3 AI Connection Odoossa

Kehittäjätila päälle, sitten **Asetukset > Tekninen > AI > AI Connection > Uusi**:

| Kenttä | Arvo |
|---|---|
| Name | esim. `Claude` |
| Kind | `OpenAI` |
| Model | `claude-sonnet`, `claude-opus` tai `claude-haiku` |
| Url | gateway-jetin `litellm_internal_url`, esim. `http://acme-litellm:4000` |
| Openai Api Key | kohdan 7.2 avain |

Mallit:

| Nimi Odoossa | Claude-malli | Käyttö |
|---|---|---|
| `claude-sonnet` | `claude-sonnet-5-5` | Hyvä oletus useimpiin tehtäviin |
| `claude-opus` | `claude-opus-5-5` | Vaativimmat tehtävät |
| `claude-haiku` | `claude-haiku-5-5` | Nopeat ja edulliset, yksinkertaiset tehtävät |

**Temperature-kentällä ei ole vaikutusta.** Claude 5.5 -mallit eivät hyväksy `temperature`-parametria, joten
gateway poistaa sen pyynnöistä.

## 8. Ylläpito

| Tehtävä | Miten | Toiminto |
|---|---|---|
| Mallilistan muutos | Muokkaa jetin `config.yaml`-tiedostoa Towerissa ja lähetä se palvelimelle | **Restart** |
| Salaisuuden, imagen, IP-rajauksen tai `litellm_extra_env`:n muutos | Muuta arvo | **Stop → Rebuild → Start** |
| LiteLLM-version päivitys | Muuta `litellm_image` | **Stop → Rebuild → Start** |
| Kontin poisto, tietojen säilytys | — | **Stop → Remove**. Tietokanta, avaimet ja kulut säilyvät. **Build** palauttaa gatewayn. |
| Lopullinen poisto | — | **Remove → Destroy**. Poistaa tietokannan, roolin, tiedostot ja hakemiston, sekä Cloudflare-DNS-tietueen, jos `dns_provider` on `cloudflare`. Muun DNS-palvelimen tietue poistetaan käsin. Postgres-jetin on oltava käynnissä. |

Huomioitavaa:

- **Restart** lataa vain `config.yaml`:n uudelleen. Ympäristömuuttujat ja salaisuudet tulevat voimaan vasta Rebuildilla.
- **Master-avaimen vaihto:** oletamme, että aiemmin luodut rajatut avaimet toimivat vaihdon jälkeenkin. Tätä ei ole
  varmistettu, joten kokeile ensin testigatewaylla.
- **Postgres-jetin pysäytys** ei pysäytä gatewayta. Gateway ei vastaa valmiustilakyselyyn, kunnes tietokanta on taas käytettävissä.

## 9. Vianetsintä

| Oire | Todennäköinen syy |
|---|---|
| Build pysähtyy riville *Check LiteLLM Anthropic key* | `litellm_anthropic_key_secret` osoittaa salaisuuteen, jota ei ole tai jolla ei ole arvoa tälle palvelimelle |
| Build pysähtyy riville *Check LiteLLM master key* | Kuten yllä, tai master-avain ei ala `sk-` |
| Start pysähtyy riville *Wait for LiteLLM* | Katso **LiteLLM logs**: yleisimmin tietokantayhteys. Tarkista `litellm_db_password` (vain kirjaimia ja numeroita). Toinen mahdollinen syy: Postgres-kontin nimessä on alaviiva, jota LiteLLM:n tietokantakirjasto ei ehkä hyväksy (ei varmistettu). |
| Hallintanäkymä antaa 403 | IP ei ole listalla, tai Traefik näkee eri IP:n (IPv6, Cloudflare-proxy) |
| Hallintanäkymä antaa 404 | `litellm_admin_allowed_ips` on tyhjä tai Rebuild on tekemättä |
| Odoo: virhe `temperature` | `config.yaml`:sta puuttuu `additional_drop_params: ["temperature"]` kyseiseltä mallilta |
| Odoo: yhteysvirhe | Odoo- ja gateway-jetit eivät ole samassa `tower`-verkossa, tai URL:ssa on alaviiva (käytä `litellm_internal_url`-arvoa) |

## 10. Tietoturva ja sopimukset

- Asiakkaiden data kulkee sen Anthropic-tilin kautta, jonka avain gatewayssa on. Jos kaikki asiakkaat käyttävät
  samaa avainta, se kuuluu huomioida asiakassopimuksissa ja niiden alihankkijaluettelossa.
- Jaetussa gatewayssa kaikkien asiakkaiden kulu- ja pyyntölokit ovat samassa tietokannassa. LiteLLM ei
  oletuksena tallenna kehotteiden sisältöä. Jos sopimus vaatii erillisyyttä, tee asiakkaalle oma gateway.
- Kaikki palvelimen Odoo-jetit pääsevät `tower`-verkossa gatewayhin. Pääsy suojataan avaimilla, ei verkolla.

## 11. Paikallinen avoimen lähdekoodin malli (Ollama)

Clauden rinnalle voi ottaa mallin, joka ajetaan omalla palvelimella eikä lähetä dataa ulos. Ajoon käytetään
[Ollamaa](https://ollama.com) omana jettinään (template-tiedosto
[`jet_template_ollama.yaml`](jet_template_ollama.yaml)). Gateway tarjoaa sen mallit samalla URL:lla ja avaimella
kuin Clauden, joten Odoon puolella tarvitaan vain toinen AI Connection.

### 11.1 Mitoitus

Tavallinen palvelin ilman näytönohjainta riittää pienille malleille, mutta vastaukset ovat hitaita.

Ollaman muistinkäyttö koostuu kolmesta osasta:

1. **Mallin painot**, eli latauskoko Ollaman mallikirjastossa: `qwen3:4b` 2,5 GB, `qwen3:8b` 5,2 GB.
2. **Kontekstin välimuisti (KV-cache).** Molemmissa malleissa on 36 kerrosta, 8 KV-päätä ja päädimensio 128,
   joten oletustarkkuudella (f16) jokainen kontekstin token vie 2 × 36 × 8 × 128 × 2 tavua = 144 KiB.
   16384 tokenin kontekstilla (templaten oletus) tämä on 2,25 GiB.
3. **Ollaman laskentamuisti**, arviolta muutama sata megatavua. Tälle ei ole dokumentoitua lukua.

| Malli | Painot | KV-cache 16k, f16 | Yhteensä, oletusasetukset | Yhteensä, säästöasetukset* |
|---|---|---|---|---|
| `qwen3:4b` (oletus) | 2,5 GB | 2,25 GiB | **n. 5–5,5 GB** | n. 4 GB |
| `qwen3:8b` | 5,2 GB | 2,25 GiB | **n. 8–8,5 GB** | n. 7 GB |

\* `OLLAMA_KV_CACHE_TYPE=q8_0` ja `OLLAMA_FLASH_ATTENTION=1`, konteksti 16384 (ks. 11.2).

- Luvut ovat vain Ollaman osuus. Odoo, Postgres ja käyttöjärjestelmä tarvitsevat muistinsa niiden lisäksi.
- `ollama_memory_limit` on asetettava vähintään taulukon arvoon. Muuten malli ei lataudu.
- Molemmat mallit tukevat työkalukutsuja (*tools* Ollaman mallikirjastossa), joten ne sopivat botin työkaluille.

**Nopeus:** julkaistua lukua suorittimelle ei ole, sillä nopeus riippuu suorittimesta ja muistin kaistasta. Mittaa
omalla palvelimella. Ennen kuin malli alkaa kirjoittaa, sen on luettava koko kehote (ohjeet, työkalujen kuvaukset,
keskusteluhistoria). Suorittimella tähän kuluu helposti kymmeniä sekunteja, ja jokainen työkalukutsu tekee uuden
kierroksen. Qwen3 myös päättelee oletuksena ennen vastausta, mikä hidastaa vastausta entisestään. `qwen3:8b` on
suunnilleen puolet hitaampi kuin `qwen3:4b`, koska sen painot ovat kaksi kertaa suuremmat.

### 11.1.1 Muistin säästökeinot

| Keino | Miten | Säästö | Haitta |
|---|---|---|---|
| KV-cachen kvantisointi | `ollama_extra_env`: `OLLAMA_KV_CACHE_TYPE=q8_0` ja `OLLAMA_FLASH_ATTENTION=1` (kumpikin omalle rivilleen) | KV-cache puolittuu: 16k:lla 2,25 → n. 1,1 GiB | Ollaman mukaan hyvin pieni tarkkuuden menetys |
| Vielä tiukempi kvantisointi | `OLLAMA_KV_CACHE_TYPE=q4_0` | KV-cache neljännekseen: n. 0,6 GiB | Huomattavampi tarkkuuden menetys, erityisesti pitkissä keskusteluissa |
| Lyhyempi konteksti | `ollama_context_length` = `8192` | KV-cache puolittuu | Botin kehote ylittää helposti 8k. Ollama pudottaa ylimenevän alun pois, ja botti "unohtaa" ohjeensa. Pienennä samalla botin *AI Bot History Limit* -arvoa ja työkalujen määrää. |
| Pienempi malli | `qwen3:1.7b` tai `qwen3:0.6b` | Painot noin puolet tai neljännes | Selvästi heikompi laatu ja epävarmemmat työkalukutsut |
| Muistin vapautus käyttämättömänä | `ollama_keep_alive` = esim. `10m` | Malli poistuu muistista, kun sitä ei käytetä | Seuraava kysymys odottaa mallin latausta |

Huomioitavaa:

- Kvantisoitu KV-cache toimii Ollaman dokumentaation mukaan vain Flash Attentionin kanssa. Ollama ottaa Flash
  Attentionin käyttöön automaattisesti, jos laite tukee sitä, mutta suorittimella tilanne ei ole varmistettu.
  Siksi `OLLAMA_FLASH_ATTENTION=1` asetetaan erikseen. Tarkista **Ollama loaded models** -lokista, että muistinkäyttö
  todella pieneni.
- `OLLAMA_KV_CACHE_TYPE` koskee kaikkia saman Ollama-jetin malleja.
- Kaikki muuttujat tulevat voimaan **Stop → Rebuild → Start** -ajolla. Ladattuja malleja ei tarvitse ladata uudelleen.
- Templatessa on valmiiksi `OLLAMA_NUM_PARALLEL=1`. Jokainen rinnakkainen pyyntö kasvattaisi kontekstin ja
  KV-cachen moninkertaiseksi.

### 11.2 Ollama-jetin luonti

1. Tuo `jet_template_ollama.yaml` kuten luvussa 2 (*Skip record*). Tiedosto on itsenäinen: siinä on mukana
   kopiot jaetuista tietueista. Salaisuuksia ei tarvita.
2. **Launch Jet** > template **Ollama** samalle palvelimelle kuin gateway. Yksi Ollama per palvelin.
3. Ennen Buildia tarkista muuttujat:

   | Muuttuja | Oletus | Selitys |
   |---|---|---|
   | `ollama_models` | `qwen3:4b` | Ladattavat mallit pilkulla erotettuina |
   | `ollama_memory_limit` | (tyhjä) | Esim. `6g` (qwen3:4b) tai `9g` (qwen3:8b), ks. 11.1. **Suositeltava**, ettei malli vie muistia Odoolta ja Postgresilta. |
   | `ollama_cpus` | (tyhjä) | Esim. `6` 8-ytimisellä palvelimella. Jätä Odoolle ja Postgresille vähintään 1–2 ydintä. |
   | `ollama_context_length` | `16384` | Kontekstin pituus. Botin kehote ylittää helposti 8k, ja Ollama pudottaa ylimenevän alun pois huomaamatta. |
   | `ollama_keep_alive` | `1h` | Kuinka kauan malli pysyy muistissa viimeisen pyynnön jälkeen |
   | `ollama_image` | `ollama/ollama:latest` | Lukitse testattuun versioon tuotannossa |

4. **Build** lataa imagen ja mallit (useita gigatavuja, kestää minuutteja). Sen jälkeen **Start**.
5. Prepare-ajon lokissa ja jetin muuttujassa `ollama_internal_url` on osoite, esim. `http://ollama-1:11434`.

Ollamassa ei ole tunnistautumista, joten jet ei julkaise porttia eikä Traefik-reittiä. Siihen pääsee vain
`tower`-verkon konteista. Lokinäkymät: **Ollama logs**, **Ollama models** (ladatut mallit) ja
**Ollama loaded models** (muistissa olevat mallit ja niiden muistinkäyttö).

Uuden mallin lisäys: lisää tagi `ollama_models`-muuttujaan ja aja **Pull models** (jet käynnissä).

### 11.3 Mallit gatewayhin

**Uusi gateway:** aseta gateway-jetille ennen Preparea `litellm_ollama_url` (= Ollama-jetin `ollama_internal_url`)
ja `litellm_ollama_models` (esim. `qwen3:4b`). Prepare kirjoittaa ne `config.yaml`:iin.

**Olemassa oleva gateway** (kuten testipalvelimella): `config.yaml` on irrotettu templatesta, joten muokkaa jetin
tiedostoa **Files**-välilehdellä ja lisää `model_list`:n loppuun (ennen riviä `litellm_settings:`):

```yaml
  - model_name: local-qwen3-4b
    litellm_params:
      model: ollama_chat/qwen3:4b
      api_base: http://ollama-1:11434
    model_info:
      supports_function_calling: true
```

Vaihda `api_base` Ollama-jetin `ollama_internal_url`:ksi. Lähetä tiedosto palvelimelle ja tee gatewaylle **Restart**.

Jos templatesta halutaan päivitetty versio uusia gatewayta varten, tuo `jet_template_litellm_gateway.yaml`
uudelleen. *Skip record* lisää uudet muuttujat, mutta ei päivitä olemassa olevaa `litellm_config`-tiedostopohjaa.
Sen päivittämiseksi tuo tiedosto *Update existing record* -tilassa tai kopioi pohjan sisältö käsin.

### 11.4 Odoo

- Jos Odoon avain on rajattu malleihin (`litellm_key_models`), luo uusi avain, jossa on myös `local-qwen3-4b`,
  tai lisää malli avaimelle hallintanäkymässä.
- Luo toinen AI Connection: sama Url ja avain, **Model** `local-qwen3-4b`. Temperature-kenttä toimii
  paikallisilla malleilla.
- Discuss-botin asetukset paikalliselle mallille: ks. moduulin [`ai_connection_discuss_bot` README](../ai_connection_discuss_bot/README.md), kohta
  *Paikallinen malli* (erillinen bottikäyttäjä, liitteet tekstinä, cron-aikakatkaisu).

### 11.5 Vianetsintä

| Oire | Todennäköinen syy |
|---|---|
| Odoo: `model not found` / 400 | Mallia ei ole gatewayn `config.yaml`:ssa, Restart puuttuu, tai avain ei salli mallia |
| Odoo: yhteysvirhe paikalliselle mallille | Ollama-jet ei ole käynnissä, tai `api_base` on väärä (katso `ollama_internal_url`) |
| Ensimmäinen vastaus kestää kauan, seuraavat nopeammin | Malli ladataan muistiin. `ollama_keep_alive` pitää sen muistissa. |
| Ollama ei lataa mallia (*out of memory*) | `ollama_memory_limit` on liian pieni mallille ja kontekstille. Pienennä mallia tai `ollama_context_length`:ia. |
| Botti vastaa *Sorry, I could not answer…* ja kyselyn virhe on *Processing was interrupted* | Odoon cron katkaisi vastaamisen kolmesti. Nosta Odoo-jetin `limit_time_real_cron`-arvoa (esim. 900). |
| Botti "unohtaa" ohjeet tai alun keskustelusta | Konteksti on liian lyhyt: nosta `ollama_context_length`-arvoa (Rebuild) |
