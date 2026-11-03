# Do popular services open from Russia? Hourly open data

**Published by ProxysVPN, a paid VPN service.** This is open data from the hourly checks behind [proxysvpn.com/rabotaet-li](https://proxysvpn.com/rabotaet-li) (Russian). It is not an independent study and not a ranking of VPN services.

Every hour the same set of measurement points in Russia opens the main page of each service over HTTPS: YouTube, Telegram, WhatsApp, Instagram, Facebook, X, TikTok, Discord, ChatGPT, Gemini, Signal, Viber, Twitch, Roblox and LinkedIn. The points are public probes of the [Globalping](https://globalping.io) network, mostly servers in data centres of different cities and networks. A point that cannot open ya.ru or www.google.com is left out: its own connection is broken. This repository stores every result, so the history builds up month by month.

## Latest month

<!-- summary:en:start -->
Month 2026-11 (UTC), share of hourly checks without a VPN:

| Service | Checks | Opened | Partly | Did not open | Refused (403/451) | No data |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| YouTube | 1 | 0% | 0% | 100% | 0% | 0% |
| Telegram | 1 | 0% | 0% | 100% | 0% | 0% |
| WhatsApp | 1 | 0% | 0% | 100% | 0% | 0% |
| Instagram | 1 | 0% | 0% | 100% | 0% | 0% |
| Facebook | 1 | 0% | 0% | 100% | 0% | 0% |
| X (Twitter) | 1 | 0% | 100% | 0% | 0% | 0% |
| TikTok | 1 | 100% | 0% | 0% | 0% | 0% |
| Discord | 1 | 0% | 100% | 0% | 0% | 0% |
| ChatGPT | 1 | 0% | 0% | 0% | 100% | 0% |
| Gemini | 1 | 0% | 0% | 0% | 0% | 100% |
| Signal | 1 | 100% | 0% | 0% | 0% | 0% |
| Viber | 1 | 0% | 0% | 100% | 0% | 0% |
| Twitch | 1 | 100% | 0% | 0% | 0% | 0% |
| Roblox | 1 | 100% | 0% | 0% | 0% | 0% |
| LinkedIn | 1 | 0% | 100% | 0% | 0% | 0% |

Updated automatically from `data/summary.csv`.
<!-- summary:en:end -->

## What the data measures

- **Whether the main page of a service answers over HTTPS from points in Russia, without a VPN.** `works`: it opened from every counted point; `partial`: from some; `blocked`: from none (time-out, reset, wrong certificate or a name that does not resolve); `refused`: the service itself answered 403 or 451; `unknown`: too few points answered to tell.
- **Not every home or mobile connection.** The points are mostly data-centre servers. A block at a single home provider or region, and mobile internet under a carrier whitelist, are not seen.
- **Not whether the app works in full.** One page of each service is opened; calls, uploads and video are not tested.
- **Through ProxysVPN.** `data/through-proxysvpn/` holds the same check made by us from our own server in Moscow through ProxysVPN. That file measures our own service; it is not a comparison with other VPN services.
- **No server addresses.** The data names services, cities and networks only. The collector refuses any document with an IP address.

## Files

| Path | What it is |
| --- | --- |
| `data/hourly/YYYY-MM.csv` | `checked_at, service, verdict`: one row per hourly check and service, without a VPN. |
| `data/snapshots/YYYY-MM.csv` | `checked_at, service, verdict, ok, refused, blocked, dns, counted`: how many points gave each result, for the runs this repository saw. |
| `data/through-proxysvpn/YYYY-MM.csv` | `checked_at, exit, service, outcome`: the check through ProxysVPN; `exit` is the country of the ProxysVPN node, `outcome` is `ok`, `refused`, `blocked`, `dns` or `failed`. |
| `data/services.csv` | `service, name, host`: the services and the address each check opens. |
| `data/summary.csv` | `month, service, checks, works, partial, blocked, refused, unknown`: counts per month. |
| `latest.json` | The newest document, schema `proxysvpn-ru-services/1`, including the result of each point. |
| `CITATION.cff` | How to cite the data. |
| `scripts/collect.py` | The collector: fetches, checks and stores. Python standard library only. |

Times are UTC. A field is never renamed or removed without a new `schema` value.

## Using it

```python
import csv
from collections import Counter
from pathlib import Path

verdicts = Counter()
for month in sorted(Path("data/hourly").glob("*.csv")):
    with month.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["service"] == "youtube":
                verdicts[row["verdict"]] += 1
print(verdicts)
```

## How it is collected

`.github/workflows/collect.yml` runs every hour: tests, then `scripts/collect.py` (fetch `proxysvpn.com/rabotaet-li.json`, validate, merge into the CSV files, refresh the tables above), then a commit. The site keeps the last 24 checks in the document, so an hour the job misses is filled in by the next run.

## Licence and citation

The data is [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) (file `LICENSE`). Reuse it freely with attribution: **"ProxysVPN, proxysvpn.com/rabotaet-li"**. Measurements without a VPN are made through the Globalping network. The collector code (`scripts/`, `tests/`, `.github/`) is MIT (file `LICENSE-CODE`).

Questions: [proxysvpn.com/en/support](https://proxysvpn.com/en/support).

## По-русски

**Публикует ProxysVPN — платный VPN-сервис.** Это открытые данные ежечасных проверок со страницы [proxysvpn.com/rabotaet-li](https://proxysvpn.com/rabotaet-li). Это не независимое исследование и не рейтинг VPN.

Каждый час одни и те же точки в России открывают по HTTPS главную страницу каждого сервиса: YouTube, Telegram, WhatsApp, Instagram, Facebook, X, TikTok, Discord, ChatGPT, Gemini, Signal, Viber, Twitch, Roblox и LinkedIn. Точки — публичная сеть [Globalping](https://globalping.io), в основном серверы в дата-центрах разных городов и сетей. Точку, с которой не открылись Яндекс или Google, мы не считаем: у неё неисправна сама связь.

<!-- summary:ru:start -->
Месяц 2026-11 (UTC), доля ежечасных проверок без VPN:

| Сервис | Проверок | Открывался | Частично | Не открывался | Отказ (403/451) | Нет данных |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| YouTube | 1 | 0% | 0% | 100% | 0% | 0% |
| Telegram | 1 | 0% | 0% | 100% | 0% | 0% |
| WhatsApp | 1 | 0% | 0% | 100% | 0% | 0% |
| Instagram | 1 | 0% | 0% | 100% | 0% | 0% |
| Facebook | 1 | 0% | 0% | 100% | 0% | 0% |
| X (Twitter) | 1 | 0% | 100% | 0% | 0% | 0% |
| TikTok | 1 | 100% | 0% | 0% | 0% | 0% |
| Discord | 1 | 0% | 100% | 0% | 0% | 0% |
| ChatGPT | 1 | 0% | 0% | 0% | 100% | 0% |
| Gemini | 1 | 0% | 0% | 0% | 0% | 100% |
| Signal | 1 | 100% | 0% | 0% | 0% | 0% |
| Viber | 1 | 0% | 0% | 100% | 0% | 0% |
| Twitch | 1 | 100% | 0% | 0% | 0% | 0% |
| Roblox | 1 | 100% | 0% | 0% | 0% | 0% |
| LinkedIn | 1 | 0% | 100% | 0% | 0% | 0% |

Обновляется автоматически из `data/summary.csv`.
<!-- summary:ru:end -->

- **Что измеряется:** открывается ли главная страница сервиса без VPN с точек в России. `works` — со всех точек, `partial` — с части, `blocked` — ни с одной, `refused` — сам сервис ответил отказом (403 или 451), `unknown` — ответило слишком мало точек.
- **Чего нет:** домашнего и мобильного интернета отдельных операторов и регионов, в том числе белых списков. Звонки, загрузки и видео не проверяются.
- **Через ProxysVPN:** папка `data/through-proxysvpn/` — та же проверка с нашего сервера в Москве через ProxysVPN. Это замер нашего сервиса, а не сравнение с другими VPN.
- **Адресов серверов здесь нет.**
- **Лицензия:** CC BY 4.0, ссылка при использовании: «ProxysVPN, proxysvpn.com/rabotaet-li». Код сборщика — MIT.
