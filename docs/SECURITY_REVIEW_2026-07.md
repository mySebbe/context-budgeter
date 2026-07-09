# Sicherheitsreview: context-budgeter

**Datum:** 2026-07-09
**Scope:** Auswahl lokaler Repository-Dateien durch `scan_repository`, Ranking und Text-/JSON-Berichte.

## Sicherheitsziel

Der Scanner soll nur lesbare Textdateien innerhalb des angeforderten Repository-Roots in den Kontext aufnehmen. Er darf keine Symlinks oder Windows-Reparse-Points verfolgen, keine Pfade außerhalb des Roots öffnen und keine Binärdateien als Text ausgeben.

## Umgesetzte Kontrollen

- **Root-Grenze:** Jeder Kandidat wird lexikalisch und über den aufgelösten Pfad gegen den Root geprüft. Git-Pfade mit absoluten Komponenten oder `..` werden als `outside-root` verworfen.
- **Keine Links:** Dateien und Verzeichnisse werden per `lstat` geprüft. Symlinks und Reparse-Points werden vor dem Öffnen ausgeschlossen. Ein Linkziel außerhalb des Roots wird als `outside-root` gezählt.
- **Git-Auswahl:** In einem Git-Arbeitsbaum nutzt die Auswahl `git ls-files --cached --others --exclude-standard` ohne Shell. Damit werden Git-Ignore-Regeln einschließlich globaler und Repository-Ausschlüsse berücksichtigt. Bereits getrackte Dateien bleiben nach der Git-Semantik sichtbar.
- **Binärschutz:** Bekannte Binärsuffixe, NUL-Bytes und nicht dekodierbares UTF-8 führen deterministisch zu `binary`. Die Byte-Grenze wird vor dem Einlesen geprüft; ein Wachstum während des Lesens wird ebenfalls verworfen.
- **Begründete Ausschlüsse:** Text- und JSON-Berichte enthalten Gesamtzahl und Zähler je Grund, darunter `default-ignore`, `gitignore`, `symlink`, `outside-root`, `binary`, `too-large`, `unreadable` und `missing`.
- **Begrenzter Ranking-Read:** Der Ranking-Read prüft Linkstatus und aufgelösten Pfad erneut, nutzt dieselbe Byte-Grenze und öffnet mit `O_NOFOLLOW`, sofern das System diese Option anbietet. Der komplette Inhalt wird nicht dauerhaft in jedem `FileContext` gespeichert.

## Fallback und Grenzen

Wenn kein gültiger Git-Arbeitsbaum erkannt wird, die Git-Binärdatei fehlt oder die Git-Auflistung fehlschlägt, verwendet der Scanner einen sortierten `os.walk`-Fallback mit `followlinks=False`. Dieser Fallback nutzt die eingebauten Verzeichnisausschlüsse und einen konservativen Matcher für den Root `.gitignore`. Negierte Regeln (`!`) und verschachtelte `.gitignore`-Dateien werden dort absichtlich nicht interpretiert. Der Bericht kennzeichnet den Fallback und den konkreten Grund.

Die Ausschlusszählung ist eine Erklärmetrik, keine forensische Auflistung aller nicht sichtbaren Bytes: Git kann ein vollständig ignoriertes Verzeichnis als einen Eintrag melden. Getrackte Dateien können trotz `.gitignore` absichtlich in der Auswahl bleiben. Es gibt außerdem keine Geheimnis-Erkennung; ein zulässiger Text-Read kann Tokens, Passwörter oder Quellcode enthalten.

## Restrisiken

- Zwischen `lstat`, Root-Prüfung und dem Öffnen kann ein anderer Prozess den Pfad ändern (TOCTOU). Auf POSIX-Systemen wird, sofern verfügbar, `O_NOFOLLOW` beim Öffnen gesetzt; Windows bietet für diesen Codepfad keine gleichwertige portable Flag-Kontrolle.
- Das Tool vertraut dem lokalen Git-Client und dessen Konfiguration. Es führt nur fest codierte Git-Argumente ohne Shell aus, validiert aber nicht die Vertrauenswürdigkeit des Git-Arbeitsbaums.
- Berichte können sensible Inhalte indirekt durch Dateipfade, Ranking und ausgewählte Dateien offenlegen. Sie sollten vor Weitergabe geprüft und außerhalb öffentlicher Issues gespeichert werden.

## Validierung

Die lokale Prüfung für diese Änderung umfasst:

```text
python -m unittest discover -s tests -v
python -m ruff check .
python -m bandit -r src
python -m pip_audit
python -m build --sdist --wheel
```

Der Symlink-Test wird auf Windows ohne aktivierte Symlink-Berechtigung übersprungen und läuft auf Plattformen mit verfügbaren Symlinks. Die übrigen Tests prüfen Git-Auswahl, Fallback, Root- und Binärschutz sowie Text-/JSON-Ausschlüsse.
