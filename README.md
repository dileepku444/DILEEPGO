# Raylogic MOD2U - 2-Channel WiFi Smart Switch (Relay) for Home Assistant

Home Assistant custom integration for the **Raylogic MOD2U** - a 2-channel
WiFi smart switch (relay type), running the Raylogic GO Protocol (TCP-HUB)
over port `5550`. Command frames reverse-engineered from a Docklight
capture and cross-checked against the official `Raylogic GO Protocol 0.4`
PDF.

## Install via HACS

1. HACS -> Integrations -> ⋮ menu (top right) -> **Custom repositories**
2. Repository: `https://github.com/dileepku444/DILEEPGO`
   Category: **Integration**
3. Search "Raylogic MOD2U" in HACS -> Download
4. Restart Home Assistant

## Install manually (without HACS)

1. Copy `custom_components/raylogic_mod2u/` into your HA
   `config/custom_components/` folder.
2. Add the `switch:` section below to `configuration.yaml`.
3. Restart Home Assistant.

## Configuration

```yaml
switch:
  - platform: raylogic_mod2u
    devices:
      - name: "MOD2U Switch"
        ip: 192.168.120.101
        port: 5550
```

| Field       | Required | Kya hai |
|-------------|----------|---------|
| `ip`        | Haan     | MOD2U switch ka IP address |
| `port`      | Haan     | TCP port (Docklight capture ke hisaab se `5550`) |
| `name`      | Nahi     | Device ka naam (default: `Raylogic MOD2U <ip>`) |
| `area`      | Nahi     | Command frame ka Area byte (default `"0C"` - Docklight se confirmed) |
| `device_id` | Nahi     | Command frame ka ID prefix (default `101` - Docklight se confirmed) |

Har device ke 2 channels (`Channel 1`, `Channel 2`) khud ban jaate hain.

### Naya MOD2U device add karna ho

```yaml
switch:
  - platform: raylogic_mod2u
    devices:
      - name: "MOD2U Switch"
        ip: 192.168.120.101
        port: 5550
      - name: "MOD2U Kitchen"
        ip: 192.168.120.102
        port: 5550
```

Agar naye device ka Docklight capture `area` ya `device_id` alag dikhaye,
to us entry mein `area:` / `device_id:` override kar dena.

## Protocol (Docklight se confirmed)

```
<ID>,<SeqNo>,*AR=<AddrHigh><Cmd:1A><Area><Level><AddrLow><CR>

101,559,*AR=001A0C0201<CR>   -> Channel 1 ON
101,560,*AR=001A0C0101<CR>   -> Channel 1 OFF
101,561,*AR=001A0C0202<CR>   -> Channel 2 ON
101,562,*AR=001A0C0102<CR>   -> Channel 2 OFF
```

- Device ID = `101`, Area byte = `0C`
- Level: `01` = OFF, `02` = ON (relay type - PDF ke "AREA CHANNEL DIRECT"
  section confirm karta hai)
- Keep-alive: `*KA=1<CR>` har **5 second** mein (idle-timeout se bachne ke liye)
- Sequence number `001` se `999` tak badhta hai, phir `001` pe restart -
  per-device persist hota hai (`device_state/<device>_seq.json`), taaki HA
  restart ke baad bhi continue rahe
- Push-based feedback: Raylogic GO mobile app se switch control karo to HA
  entity ka state turant sync ho jaata hai (polling nahi)

## License

MIT - dekho [`LICENSE`](LICENSE).
