# TVconnect Protocol v1

Транспорт: TCP, порт **47800**. Одно соединение несёт control, video и input.

## 1. Записи (records)

До завершения handshake (plaintext):

```
u32 BE  len            — длина (type + payload), 1..16 MiB
u8      type
bytes   payload
```

После handshake (encrypted):

```
u32 BE  len            — длина ciphertext (включая 16-байтовый GCM tag)
bytes   AES-256-GCM(key_dir, nonce, plaintext = type || payload, aad = 4 байта len)
```

`nonce = 0x00000000 || counter (u64 BE)`. Отдельный ключ и счётчик для каждого направления, счётчик начинается с 0
и увеличивается на 1 на каждую запись. Ошибка расшифровки → немедленный разрыв.

| type | имя | payload |
|---|---|---|
| 0x01 | CONTROL | UTF-8 JSON, поле `type` обязательно |
| 0x02 | VIDEO | `u8 flags` · `u64 BE pts_us` · H.264 Annex-B (flags: `1` = keyframe, `2` = config SPS/PPS) |
| 0x03 | INPUT | UTF-8 JSON, поле `t` обязательно |

## 2. Handshake

1. **D→T** `hello` (plaintext):
   `{"type":"hello","proto":1,"client_id":"<32 hex>","client_name":"PC","eph_pub":"<b64 P-256 uncompressed, 65 B>"}`
2. **T→D** `hello_ack` (plaintext):
   `{"type":"hello_ack","proto":1,"tv_id":"<32 hex>","tv_name":"...","model":"...","eph_pub":"<b64>","id_pub":"<b64 SPKI DER>","sig":"<b64 DER ECDSA>"}`
   либо `{"type":"error","code":"busy|disabled|proto_mismatch|rate_limited","message":"..."}`.
3. Обе стороны вычисляют:
   ```
   TH     = SHA256("TVconnect-v1" || u32(len(hello)) || hello || u32(65) || tv_eph_pub || u32(len(id_pub)) || id_pub)
   shared = ECDH-P256(eph_D, eph_T)                      (32 байта, X-координата)
   okm    = HKDF-SHA256(ikm=shared, salt=TH, info="tvc1 session keys", L=68)
   k_d2t  = okm[0:32]   k_t2d = okm[32:64]
   SAS    = u32BE(okm[64:68]) mod 1 000 000, 6 цифр
   ```
   `hello` — это ровно те байты payload, что были отправлены. `sig = ECDSA-P256-SHA256(id_key, TH)`.
   Десктоп проверяет подпись; если ТВ уже известен — сравнивает SHA-256(id_pub) с запомненным (pinning).
4. Дальше всё зашифровано. **D→T** одно из:
   - `{"type":"auth","token":"<b64 32 B>"}` → `{"type":"auth_result","ok":true}` или `{"ok":false,"reason":"unknown_client"}`
     (после отказа клиент может отправить `pair_request` в том же соединении);
   - `{"type":"pair_request"}` → ТВ показывает диалог с именем ПК и SAS →
     `{"type":"pair_result","accepted":true,"token":"<b64>"|null}` (token только при «Разрешить и запомнить»)
     или `{"accepted":false,"reason":"denied|timeout"}`.
5. **T→D** `{"type":"session_ready","screen":{"w":1920,"h":1080},"caps":{...},"sdk":30}`.

ТВ хранит только `SHA-256(token)`; десктоп хранит токен в OS keyring.

## 3. Control (после session_ready)

| Направление | type | Поля |
|---|---|---|
| D→T | `start_video` | `max_w, max_h, fps, bitrate` |
| D→T | `stop_video` | — |
| D→T | `request_keyframe` | — |
| D→T | `ping` | `t` (число, эхо) |
| T→D | `pong` | `t` |
| T→D | `video_status` | `state`: `consent_pending` / `running` / `denied` / `stopped` / `error`, `w, h, fps`, `message` |
| T→D | `caps` | `accessibility`, `dpad_native`, `text_native` |
| T→D | `input_result` | `t`, `ok`, `reason` (только при отказе; `gesture_cancelled` — система отменила жест) |
| D→T | `clipboard_set` | `text` (≤ 64 КБ), `paste` — `true`: вставить в поле в фокусе, `false`: только положить в буфер ТВ |
| T→D | `clipboard_result` | `clipboard` (положено в буфер ТВ), `paste`, `pasted`, `reason`: `no_focus` / `accessibility_disabled` / `failed` |
| D→T | `clipboard_get` | — |
| T→D | `clipboard` | `text` (или `null`), `source`: `focused` (выделение/текст элемента в фокусе) / `clipboard` / `none` |
| оба | `bye` | `reason` |
| оба | `error` | `code`, `message` |

## 4. Input (D→T)

Координаты нормализованы в диапазон 0..1 относительно экрана ТВ.

```json
{"t":"tap","x":0.5,"y":0.5}
{"t":"long_press","x":0.5,"y":0.5}
{"t":"swipe","x1":0.5,"y1":0.7,"x2":0.5,"y2":0.3,"ms":300}
{"t":"scroll","x":0.5,"y":0.5,"dy":1}           // dy>0 — колесо вверх
{"t":"key","code":4}                             // Android KEYCODE_*
{"t":"text","s":"привет"}
{"t":"cursor","x":0.5,"y":0.5}                   // показать курсор-оверлей
```

## 5. Discovery

- mDNS/DNS-SD: `_tvconnect._tcp.local.`, TXT `id`, `name`, `model`, `proto`.
- UDP 47801: клиент шлёт `TVC_DISCOVER 1` (broadcast), ТВ отвечает unicast JSON
  `{"type":"tvc_announce","proto":1,"tv_id":"...","name":"...","model":"...","port":47800}`.

## 6. Версионирование

`proto` — мажорная версия. При несовпадении ТВ отвечает `error/proto_mismatch`. Новые поля добавляются только как
опциональные; неизвестные `type`/`t` игнорируются (с записью в лог).
