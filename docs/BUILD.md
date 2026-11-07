# Сборка, установка и запуск

## 1. Сборка APK (TV Client)

Требования: JDK 17, Android SDK (platform `android-35`, build-tools 34+). Gradle скачивается wrapper'ом автоматически.

1. Укажите путь к SDK в `tv-app/local.properties`. Файл не хранится в git.
   ```properties
   sdk.dir=C\:/AndroidSDK
   ```
   На Linux, например: `sdk.dir=/home/user/Android/Sdk`. Вместо файла можно задать переменную `ANDROID_HOME`.
2. Соберите APK:
   ```bash
   cd tv-app
   ./gradlew testDebugUnitTest assembleDebug        # Linux
   gradlew.bat testDebugUnitTest assembleDebug      # Windows
   ```
3. Результат: `tv-app/app/build/outputs/apk/debug/app-debug.apk`.

`assembleRelease` собирает release-APK, подписанный debug-ключом, чтобы его можно было установить без настройки.
Для распространения подпишите своим ключом (`signingConfigs` в `app/build.gradle.kts`).

Юнит-тесты (`testDebugUnitTest`) сверяют Kotlin-реализацию протокола с тест-векторами из `protocol/test-vectors/crypto.json`.
Эти векторы сгенерированы Python-клиентом, так что тесты проверяют совместимость двух реализаций.

## 2. Установка APK на телевизор

### Через ADB по сети (рекомендуется)

1. На ТВ откройте *Настройки → Об устройстве* и 7 раз нажмите на «Сборка». Так включается режим разработчика.
2. Включите отладку по сети в *Настройки → Для разработчиков*:
   - на Android TV 9–10 это пункт **«Отладка по сети»** (порт 5555);
   - на Android 11+ это **«Беспроводная отладка»** с кодом сопряжения: сначала `adb pair <ip>:<порт> <код>`.
3. Установите APK с компьютера:
   ```bash
   adb connect 192.168.1.50:5555
   adb install -r app-debug.apk
   ```
   На ТВ появится запрос «Разрешить отладку?». Отметьте «Всегда разрешать» и подтвердите.

### Без ADB

Скопируйте APK на флешку и установите любым файловым менеджером на ТВ, например «X-plore» или «File Commander».
Для этого нужно разрешить установку из неизвестных источников.

### После установки

- Откройте **TVconnect** из списка приложений ТВ.
- Включите службу управления в *Настройки → Спец. возможности → TVconnect*. Если на ТВ нет этого пункта меню,
  включите её через ADB:
  ```bash
  adb shell settings put secure enabled_accessibility_services dev.tvconnect.tv/dev.tvconnect.tv.input.RemoteInputService
  ```
  ```bash
  adb shell settings put secure accessibility_enabled 1
  ```
- Необязательно: разрешите приложению показывать окна поверх других. Тогда запрос подключения появится, даже если на ТВ
  открыто другое приложение. При включённой службе Accessibility это и так работает.
  ```bash
  adb shell appops set dev.tvconnect.tv SYSTEM_ALERT_WINDOW allow
  ```
- Логи ТВ:
  ```bash
  adb logcat -s TVconnect
  ```
  ```bash
  adb shell run-as dev.tvconnect.tv cat files/logs/tvconnect.log
  ```
  Вторая команда работает только с debug-сборкой.

## 3. Запуск Desktop Client

Требования: Python 3.10+ (проверено на 3.14), Windows 10/11 или Linux с X11/Wayland. Для ADB-функций нужен `adb`
(Android platform-tools). Клиент ищет его в `PATH`, `ANDROID_HOME`, `%LOCALAPPDATA%\Android\Sdk`, или путь можно
указать в *Настройках*.

### Windows

```powershell
cd desktop
py -3 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m tvconnect_desktop
```

### Linux (Ubuntu / Fedora)

```bash
sudo apt install python3-venv libxcb-cursor0 android-tools-adb      # Ubuntu/Debian
sudo dnf install python3 xcb-util-cursor android-tools              # Fedora
cd desktop
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m tvconnect_desktop
```

Чтобы токены доверия хранились в связке ключей, на Linux нужен Secret Service: GNOME Keyring или KWallet. Без него клиент
работает, но ТВ будет спрашивать подтверждение при каждом подключении.

Если ТВ не находится автоматически, нажмите **«Добавить по IP…»** и введите адрес с экрана ТВ. Автопоиск может не
работать по двум причинам:
- роутер блокирует multicast или broadcast (например, включена изоляция клиентов);
- брандмауэр блокирует порты. Разрешите исходящие TCP 47800 и UDP 47801 и входящий UDP 5353 (mDNS).

### Управление в окне просмотра

| ПК | ТВ |
|---|---|
| Левый клик / удержание / перетаскивание | тап / long press / свайп |
| Правая кнопка мыши · Esc | BACK |
| Средняя кнопка мыши | HOME |
| Колесо | прокрутка (или D-pad ↑↓ при включённом «Колесо = D-pad») |
| Стрелки, Enter, Backspace, Delete, Tab, Home, End, PgUp/PgDn, F1–F12 | соответствующие `KEYCODE_*` |
| Набор текста | ввод в поле (кириллица — только через службу Accessibility) |
| Ctrl+V · Shift+Insert · «📋 Вставить с ПК» | вставить текст из буфера ПК в поле на ТВ (в позицию курсора, заменяя выделение) |
| Ctrl+C · «⧉ Копировать с ТВ» | скопировать на ПК выделенный текст поля на ТВ (или весь текст поля / элемента в фокусе) |
| «Синхр. буфер» | всё, что вы копируете на ПК, сразу попадает в буфер ТВ (вставка через меню поля на ТВ) |
| Ctrl+F | полный экран |
| Ctrl+W | отключиться |

### Диагностика без GUI

```bash
python -m tvconnect_desktop.tools.probe --host 192.168.1.50 --mode native --seconds 10 --out frame.png
python -m tvconnect_desktop.tools.probe --host 192.168.1.50 --mode adb --key 3
```

Утилита выводит код сопряжения, состояние, число кадров, RTT и сохраняет последний кадр в PNG.

## 4. Сборка десктоп-клиента в исполняемый файл (PyInstaller)

```powershell
scripts\build_desktop_win.ps1          # → desktop\dist\TVconnect\TVconnect.exe и desktop\dist\TVconnect-win64.zip
```

При сборке в `_internal\adb` автоматически кладётся `adb.exe` с DLL. Источник — adb, найденный на машине сборки, или
папка из `$env:TVCONNECT_ADB_DIR`. Если на целевом ПК уже установлен свой adb, клиент использует его: два adb разных
версий на одном ПК мешают друг другу.

### Перенос на другой Windows-ПК

1. Скопируйте `TVconnect-win64.zip` (~97 МБ) и распакуйте в любую папку. Python и Android SDK не нужны.
2. Запустите `TVconnect\TVconnect.exe`. Не переносите exe отдельно от папки `_internal`.
3. Если SmartScreen предупредит, что приложение неизвестное (exe не подписан), нажмите «Подробнее» → «Выполнить в любом
   случае».
4. Брандмауэр Windows спросит про доступ к сети. Разрешите для **частных сетей**, иначе не будет работать автопоиск ТВ.
5. При первом подключении:
   - на ТВ подтвердите сопряжение с новым ПК и сверьте код;
   - когда клиент подключит ADB, на ТВ появится запрос «Разрешить отладку?». Отметьте «Всегда разрешать».

Встроенный adb — бинарник Android SDK Platform-Tools, его лицензии лежат в `_internal\adb\NOTICE.txt`. Архив подходит
для собственных ПК. Для публичного распространения проверьте условия лицензии Android SDK или попросите пользователей
ставить platform-tools самостоятельно.
```bash
scripts/build_desktop_linux.sh         # → desktop/dist/TVconnect-linux-x86_64.tar.gz
```

Сборка делается на той ОС, для которой предназначена: PyInstaller не умеет кросс-компиляцию. На Windows Linux-версию
можно собрать в WSL. Root не нужен: если нет `python3-venv`, скрипт сам поставит pip, а adb скачает из официальных
platform-tools.

```bash
cd ~ && git clone https://github.com/Obn4zh/TVconnect.git && cd TVconnect
bash scripts/build_desktop_linux.sh
```

Собирайте в домашней папке WSL, а не в `/mnt/c` или `/mnt/z`: там сборка заметно медленнее.

Linux-бинарник работает на дистрибутивах с glibc не старее, чем на машине сборки. Сборка из Ubuntu 24.04 (glibc 2.39)
запускается на Ubuntu 24.04+, Debian 13+, Fedora 40+, Linux Mint 22+. Для более старых систем соберите на самой старой
целевой системе.

### Перенос на другой Linux-ПК

```bash
tar xzf TVconnect-linux-x86_64.tar.gz
./TVconnect/TVconnect
```

На некоторых системах для работы под X11 нужна библиотека курсора Qt. В Wayland-сессии (по умолчанию в Ubuntu и
Fedora) обычно хватает того, что уже установлено.

```bash
sudo apt install libxcb-cursor0          # Ubuntu / Debian / Mint
sudo dnf install xcb-util-cursor         # Fedora
```

Ярлык в меню приложений: поправьте `Exec=` в `TVconnect/tvconnect.desktop` на полный путь к `TVconnect/TVconnect` и
скопируйте файл в `~/.local/share/applications/`. Токены доверия хранятся в Secret Service (GNOME Keyring / KWallet);
без него ТВ будет спрашивать подтверждение при каждом подключении.

## 5. Тесты

```bash
cd desktop
.venv/bin/python -m pytest              # протокол, крипто-векторы, валидация ADB, маршрутизация ввода
```
```bash
cd tv-app
./gradlew testDebugUnitTest             # Kotlin-протокол против тех же векторов
```

Если вы меняете криптографию, сгенерируйте векторы заново (`python tests/gen_vectors.py`) и прогоните обе группы тестов.
